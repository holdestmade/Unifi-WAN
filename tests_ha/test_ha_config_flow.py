"""The user, reconfigure, reauth and options flows, run to completion.

The previous release shipped a dialog that failed with "400: Bad Request"
while every test passed, because the only thing tested was building the
schema. Serialising that schema for the frontend is where it failed, so
the forms here are also opened through the HTTP API the frontend calls.
"""

from __future__ import annotations

import ssl
from typing import Any
from unittest.mock import Mock

import aiohttp
import pytest
from console import API_KEY, HOST, SITE, MockConsole
from harness import DOMAIN, entry_data, make_entry, setup_entry
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

NEW_HOST = "192.0.2.20"


def user_input(**overrides: Any) -> dict[str, Any]:
    data = {
        "host": HOST,
        "api_key": API_KEY,
        "site": SITE,
        "verify_ssl": False,
        "auto_speedtest": False,
        "auto_speedtest_minutes": 60,
    }
    data.update(overrides)
    return data


@pytest.fixture
def setups(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every async_setup_entry call Home Assistant makes, by entry id."""
    import custom_components.unifi_wan as integration

    calls: list[str] = []
    real = integration.async_setup_entry

    async def counting(hass, entry):
        calls.append(entry.entry_id)
        return await real(hass, entry)

    monkeypatch.setattr(integration, "async_setup_entry", counting)
    return calls


# ------------------------------------------------------------------ HTTP


async def test_every_form_opens_through_the_http_api(
    hass: HomeAssistant, console: MockConsole, hass_client
) -> None:
    """The regression that reached users: the frontend's own requests."""
    assert await async_setup_component(hass, "config", {})
    entry = await setup_entry(hass, make_entry(hass))
    client = await hass_client()

    resp = await client.post(
        "/api/config/config_entries/flow",
        json={"handler": DOMAIN, "show_advanced_options": False},
    )
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    assert body["step_id"] == "user"
    assert {f["name"] for f in body["data_schema"]} >= {"host", "api_key", "site"}

    resp = await client.post(
        "/api/config/config_entries/options/flow",
        json={"handler": entry.entry_id},
    )
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    assert body["step_id"] == "init"
    names = {f["name"] for f in body["data_schema"]}
    assert {
        "scan_interval",
        "rate_interval_seconds",
        "expected_download_mbps",
        "expected_upload_mbps",
        "speed_tolerance_percent",
    } <= names

    # An entry id in the body is how the frontend starts a reconfigure.
    resp = await client.post(
        "/api/config/config_entries/flow",
        json={"handler": DOMAIN, "entry_id": entry.entry_id},
    )
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    assert body["step_id"] == "reconfigure"
    assert {f["name"] for f in body["data_schema"]} >= {"host", "api_key", "site"}


async def test_the_options_dialog_submits_through_the_http_api(
    hass: HomeAssistant, console: MockConsole, hass_client
) -> None:
    assert await async_setup_component(hass, "config", {})
    entry = await setup_entry(hass, make_entry(hass))
    client = await hass_client()
    resp = await client.post(
        "/api/config/config_entries/options/flow", json={"handler": entry.entry_id}
    )
    flow_id = (await resp.json())["flow_id"]
    resp = await client.post(
        f"/api/config/config_entries/options/flow/{flow_id}",
        json={"scan_interval": 45, "expected_download_mbps": 500},
    )
    assert resp.status == 200, await resp.text()
    assert (await resp.json())["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.options["scan_interval"] == 45
    assert entry.options["expected_download_mbps"] == 500.0


# ------------------------------------------------------------------ user


async def test_the_user_step_creates_an_entry_that_loads(
    hass: HomeAssistant, console: MockConsole
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input(host=f"https://{HOST.upper()}/", auto_speedtest_minutes=30),
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"UniFi WAN ({HOST})"
    entry = result["result"]
    assert entry.unique_id == f"{HOST}-{SITE}"
    assert entry.data["host"] == HOST
    assert entry.data["auto_speedtest_minutes"] == 30
    assert entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize(
    ("setup_console", "error"),
    [
        (lambda c: c.status.__setitem__("stat/device", 401), "invalid_auth"),
        (lambda c: c.status.__setitem__("stat/device", 403), "invalid_auth"),
        (lambda c: setattr(c, "site", "elsewhere"), "invalid_site"),
        (lambda c: c.status.__setitem__("stat/device", 500), "cannot_connect"),
        (
            lambda c: c.raises.__setitem__(
                "stat/device", aiohttp.ClientConnectionError("refused")
            ),
            "cannot_connect",
        ),
        (lambda c: c.raises.__setitem__("stat/device", TimeoutError()), "timeout"),
        (
            lambda c: c.raises.__setitem__(
                "stat/device",
                aiohttp.ClientConnectorCertificateError(
                    Mock(), ssl.SSLCertVerificationError("certificate verify failed")
                ),
            ),
            "ssl_error",
        ),
    ],
)
async def test_the_user_step_explains_what_went_wrong(
    hass: HomeAssistant, console: MockConsole, setup_console, error: str
) -> None:
    setup_console(console)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_a_body_without_data_is_not_a_console(
    hass: HomeAssistant, console: MockConsole, aioclient_mock
) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"https://{HOST}/proxy/network/api/s/{SITE}/stat/device", json={"ok": True}
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input()
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_the_same_console_cannot_be_added_twice(
    hass: HomeAssistant, console: MockConsole
) -> None:
    await setup_entry(hass, make_entry(hass))
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input(host=f"https://{HOST}")
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_a_site_typed_with_a_trailing_space_still_loads(
    hass: HomeAssistant, console: MockConsole
) -> None:
    """The site used to be validated stripped and stored as typed, so
    " default " passed the probe and then every poll asked the console for
    a site it does not have."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input(site=" default ")
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].data["site"] == "default"
    assert result["result"].state is ConfigEntryState.LOADED


async def test_a_site_saved_with_a_trailing_space_in_options_still_loads(
    hass: HomeAssistant, console: MockConsole
) -> None:
    entry = await setup_entry(hass, make_entry(hass))
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"site": "default "}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["site"] == "default"
    assert entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize("flow", ["reconfigure", "add again"])
async def test_an_entry_made_before_host_normalisation(
    hass: HomeAssistant, aioclient_mock, flow: str
) -> None:
    """Until the July restructure the unique id was the host as typed.

    Such an entry is migrated to the normalised id when it is set up, so
    the user step recognises it as the console it is, and reconfigure,
    which no longer compares unique ids, follows it like any other.
    """
    console = MockConsole(host="console.example")
    console.register(aioclient_mock)
    entry = await setup_entry(
        hass,
        make_entry(
            hass,
            data=entry_data(host="Console.Example"),
            unique_id="Console.Example-default",
        ),
    )
    assert entry.unique_id == "console.example-default"
    if flow == "reconfigure":
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], connection_input(host="Console.Example")
        )
        await hass.async_block_till_done()
        assert result["reason"] == "reconfigure_successful"
    else:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input(host="Console.Example")
        )
        await hass.async_block_till_done()
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"


async def test_an_older_entry_is_migrated_once(
    hass: HomeAssistant, console: MockConsole
) -> None:
    entry = make_entry(
        hass,
        data=entry_data(host=HOST.upper(), site=" default "),
        options={"site": "default "},
        unique_id=f"{HOST.upper()}- default ",
    )
    assert entry.minor_version == 1
    await setup_entry(hass, entry)
    assert entry.minor_version == 2
    assert entry.unique_id == f"{HOST}-{SITE}"
    assert entry.data["site"] == "default"
    assert entry.options["site"] == "default"


async def test_a_migration_that_would_collide_keeps_the_old_id(
    hass: HomeAssistant, console: MockConsole
) -> None:
    """The duplicate the migration exists to prevent, already made."""
    await setup_entry(hass, make_entry(hass))
    duplicate = make_entry(hass, unique_id=f"{HOST.upper()}-{SITE}")
    await setup_entry(hass, duplicate)
    assert duplicate.minor_version == 2
    assert duplicate.unique_id == f"{HOST.upper()}-{SITE}"


async def test_setup_records_the_gateway_it_found(
    hass: HomeAssistant, console: MockConsole
) -> None:
    entry = await setup_entry(hass, make_entry(hass))
    assert entry.data["gateway_mac"] == "aa:bb:cc:dd:ee:ff"


# ----------------------------------------------------------- reconfigure


def connection_input(**overrides: Any) -> dict[str, Any]:
    """What the reconfigure form takes: the connection alone."""
    data = user_input(**overrides)
    for key in ("auto_speedtest", "auto_speedtest_minutes"):
        data.pop(key)
    return data


async def _revoke_key_and_poll(hass: HomeAssistant, console: MockConsole, entry):
    console.api_key = "rotated"
    await entry.runtime_data.device_coordinator.async_refresh()
    await hass.async_block_till_done()
    [flow] = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flow["context"]["source"] == "reauth"
    return flow


async def test_reconfigure_replaces_the_key_and_reloads(
    hass: HomeAssistant, console: MockConsole, setups: list[str]
) -> None:
    entry = await setup_entry(
        hass,
        make_entry(
            hass,
            options={"host": HOST, "api_key": API_KEY, "scan_interval": 45},
        ),
    )
    console.api_key = "rotated"
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], connection_input(api_key="rotated")
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["api_key"] == "rotated"
    # Connection overrides the options dialog stored are cleared, so the
    # merged view cannot keep serving the old key; the rest stays.
    assert "api_key" not in entry.options
    assert "host" not in entry.options
    assert entry.options["scan_interval"] == 45
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.client.api_key == "rotated"


async def test_reconfigure_follows_the_console_to_a_new_address(
    hass: HomeAssistant, console: MockConsole, aioclient_mock
) -> None:
    """Up to 1.11.0 the unique id compared here was built from the
    address, so any new host was a "different console" and the step
    that exists to follow a console to a new address always refused."""
    entry = await setup_entry(hass, make_entry(hass))
    moved = MockConsole(host=NEW_HOST)  # the same gateway, same MAC
    moved.register(aioclient_mock)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], connection_input(host=NEW_HOST)
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["host"] == NEW_HOST
    assert entry.unique_id == f"{NEW_HOST}-{SITE}"
    assert entry.title == f"UniFi WAN ({NEW_HOST})"
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.host == NEW_HOST


async def test_reconfigure_follows_the_gateway_to_another_site(
    hass: HomeAssistant, console: MockConsole
) -> None:
    entry = await setup_entry(hass, make_entry(hass))
    console.site = "renamed"
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], connection_input(site="renamed")
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.unique_id == f"{HOST}-renamed"
    assert entry.state is ConfigEntryState.LOADED


async def test_reconfigure_shows_only_what_it_saves(
    hass: HomeAssistant, console: MockConsole
) -> None:
    """The form used to show the speedtest schedule and drop whatever was
    entered in it; those fields live in the options dialog."""
    entry = await setup_entry(hass, make_entry(hass))
    result = await entry.start_reconfigure_flow(hass)
    shown = {str(key) for key in result["data_schema"].schema}
    assert shown == {"host", "api_key", "site", "verify_ssl"}


@pytest.mark.parametrize("change", ["host", "site"])
async def test_reconfigure_refuses_a_different_gateway(
    hass: HomeAssistant, console: MockConsole, aioclient_mock, change: str
) -> None:
    entry = await setup_entry(hass, make_entry(hass))
    if change == "host":
        other = MockConsole(host=NEW_HOST)
        other.register(aioclient_mock)
        submitted = connection_input(host=NEW_HOST)
    else:
        other = console
        console.site = "branch"
        submitted = connection_input(site="branch")
    other.gateway["mac"] = "aa:bb:cc:00:00:02"

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], submitted
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_console"
    assert entry.data["host"] == HOST
    assert entry.unique_id == f"{HOST}-{SITE}"


async def test_reconfigure_refuses_an_address_another_entry_holds(
    hass: HomeAssistant, console: MockConsole, aioclient_mock
) -> None:
    other = MockConsole(host=NEW_HOST)
    other.register(aioclient_mock)
    entry = await setup_entry(hass, make_entry(hass))
    await setup_entry(hass, make_entry(hass, data=entry_data(host=NEW_HOST)))
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], connection_input(host=NEW_HOST)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data["host"] == HOST


async def test_reconfigure_with_a_bad_key_shows_the_error(
    hass: HomeAssistant, console: MockConsole
) -> None:
    entry = await setup_entry(hass, make_entry(hass))
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], connection_input(api_key="wrong")
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data["api_key"] == API_KEY


# ---------------------------------------------------------------- reauth


async def test_a_revoked_key_mid_session_is_replaced_by_reauth(
    hass: HomeAssistant, console: MockConsole, setups: list[str]
) -> None:
    entry = await setup_entry(hass, make_entry(hass))
    flow = await _revoke_key_and_poll(hass, console, entry)

    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"api_key": "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"api_key": "rotated"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["api_key"] == "rotated"
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.client.api_key == "rotated"


@pytest.mark.parametrize("source", ["reauth", "reconfigure"])
async def test_a_successful_flow_reloads_the_entry_once(
    hass: HomeAssistant, console: MockConsole, setups: list[str], source: str
) -> None:
    """async_update_reload_and_abort schedules a reload itself, and the
    change it writes used to fire the update listener into reloading the
    entry a second time."""
    entry = await setup_entry(hass, make_entry(hass))
    if source == "reauth":
        flow = await _revoke_key_and_poll(hass, console, entry)
        flow_id, submitted = flow["flow_id"], {"api_key": "rotated"}
    else:
        console.api_key = "rotated"
        flow_id = (await entry.start_reconfigure_flow(hass))["flow_id"]
        submitted = connection_input(api_key="rotated")
    setups.clear()

    result = await hass.config_entries.flow.async_configure(flow_id, submitted)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert setups == [entry.entry_id]
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.reload_pending is False


async def test_a_reauth_with_the_same_key_still_reloads(
    hass: HomeAssistant, console: MockConsole, setups: list[str]
) -> None:
    """The console refused the key for a while and took it back. The poll
    stops at an auth failure, so the reload is what restarts it."""
    entry = await setup_entry(hass, make_entry(hass))
    console.status["stat/device"] = 401
    await entry.runtime_data.device_coordinator.async_refresh()
    await hass.async_block_till_done()
    [flow] = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    del console.status["stat/device"]
    setups.clear()
    await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"api_key": API_KEY}
    )
    await hass.async_block_till_done()
    assert setups == [entry.entry_id]
    assert entry.runtime_data.device_coordinator.last_update_success


async def test_reauth_updates_a_key_the_options_dialog_stored(
    hass: HomeAssistant, console: MockConsole
) -> None:
    entry = await setup_entry(
        hass, make_entry(hass, options={"api_key": API_KEY, "scan_interval": 30})
    )
    flow = await _revoke_key_and_poll(hass, console, entry)
    await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"api_key": "rotated"}
    )
    await hass.async_block_till_done()
    assert entry.options["api_key"] == "rotated"
    assert entry.data["api_key"] == "rotated"
    assert entry.state is ConfigEntryState.LOADED


# --------------------------------------------------------------- options


async def _submit_options(hass: HomeAssistant, entry, **values: Any):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], values
    )
    await hass.async_block_till_done()
    return result


async def test_options_are_saved_and_applied_by_a_reload(
    hass: HomeAssistant, console: MockConsole, setups: list[str]
) -> None:
    entry = await setup_entry(hass, make_entry(hass))
    result = await _submit_options(
        hass,
        entry,
        scan_interval=60,
        rate_interval_seconds=0,
        expected_download_mbps=500,
        expected_upload_mbps=40,
        speed_tolerance_percent=5,
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["scan_interval"] == 60
    assert entry.options["rate_interval_seconds"] == 0
    assert entry.options["speed_tolerance_percent"] == 5.0
    assert len(setups) == 2

    runtime = entry.runtime_data
    assert runtime.device_coordinator.update_interval.total_seconds() == 60
    assert runtime.rates_coordinator is None
    assert runtime.expected_download == 500.0
    assert runtime.speed_tolerance == 0.05

    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    down_status = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_isp_down_vs_expected"
    )
    up_status = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_isp_up_vs_expected"
    )
    # 500 measured against 500 expected; 50 against 40 is 25% over.
    assert hass.states.get(down_status).state == "Expected"
    assert hass.states.get(up_status).state == "Faster"
    # With the fast poll off the rate sensors read the site poll instead.
    rate = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_wan_down_mbps"
    )
    assert hass.states.get(rate).state == "10.0"


async def test_the_tolerance_is_stored_as_typed(
    hass: HomeAssistant, console: MockConsole
) -> None:
    """Not through the fraction and back: 7 used to become 7.000000000000001."""
    entry = await setup_entry(hass, make_entry(hass))
    await _submit_options(hass, entry, speed_tolerance_percent=7)
    assert entry.options["speed_tolerance_percent"] == 7.0
    assert entry.runtime_data.speed_tolerance == 0.07


async def test_changing_only_the_auto_setting_does_not_reload(
    hass: HomeAssistant, console: MockConsole, setups: list[str]
) -> None:
    entry = await setup_entry(hass, make_entry(hass))
    # Save once so every reload key is in options, then change the flag.
    await _submit_options(hass, entry)
    setups.clear()
    runtime = entry.runtime_data

    await _submit_options(hass, entry, auto_speedtest=True)
    assert setups == []
    assert entry.runtime_data is runtime
    assert runtime.speedtest.auto_enabled is True
    assert runtime.speedtest._unsub_auto is not None


async def test_a_new_host_moves_the_entrys_identity(
    hass: HomeAssistant, console: MockConsole, aioclient_mock
) -> None:
    entry = await setup_entry(hass, make_entry(hass))
    moved = MockConsole(host=NEW_HOST)
    moved.register(aioclient_mock)

    result = await _submit_options(hass, entry, host=NEW_HOST)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.unique_id == f"{NEW_HOST}-{SITE}"
    assert entry.title == f"UniFi WAN ({NEW_HOST})"
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.host == NEW_HOST
    assert moved.gets, "the reloaded entry polls the new address"


async def test_a_host_another_entry_holds_is_refused(
    hass: HomeAssistant, console: MockConsole, aioclient_mock
) -> None:
    other = MockConsole(host=NEW_HOST)
    other.register(aioclient_mock)
    entry = await setup_entry(hass, make_entry(hass))
    await setup_entry(hass, make_entry(hass, data=entry_data(host=NEW_HOST)))

    result = await _submit_options(hass, entry, host=NEW_HOST)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "already_configured"}
    assert entry.unique_id == f"{HOST}-{SITE}"
    assert "host" not in entry.options


async def test_a_blank_key_keeps_the_stored_one(
    hass: HomeAssistant, console: MockConsole
) -> None:
    entry = await setup_entry(hass, make_entry(hass))
    result = await _submit_options(hass, entry, api_key="")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["api_key"] == API_KEY
    assert entry.state is ConfigEntryState.LOADED


async def test_a_failed_validation_saves_nothing(
    hass: HomeAssistant, console: MockConsole
) -> None:
    entry = await setup_entry(hass, make_entry(hass))
    result = await _submit_options(hass, entry, api_key="wrong", scan_interval=90)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.options == {}
    # The form comes back with what was typed, not with the key.
    defaults = {
        str(k): k.default() for k in result["data_schema"].schema if callable(k.default)
    }
    assert defaults["scan_interval"] == 90
    assert "api_key" not in defaults
