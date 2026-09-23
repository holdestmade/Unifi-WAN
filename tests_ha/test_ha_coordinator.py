"""The site poll, per-WAN history reuse, restore across a restart, and
diagnostics, all against a running core.
"""

from __future__ import annotations

import pytest
from console import HOST, T0, MockConsole, history_record
from harness import DOMAIN, make_entry, setup_entry
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    async_mock_restore_state_shutdown_restart,
    mock_restore_cache_with_extra_data,
)
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)


def entity_id(hass: HomeAssistant, platform: str, entry, key: str) -> str:
    found = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert found is not None, f"no {platform} entity for {key}"
    return found


def state(hass: HomeAssistant, entry, key: str, platform: str = "sensor") -> str:
    return hass.states.get(entity_id(hass, platform, entry, key)).state


async def poll(hass: HomeAssistant, entry) -> None:
    await entry.runtime_data.device_coordinator.async_refresh()
    await hass.async_block_till_done()


# --------------------------------------------------------------- the poll


async def test_a_failed_poll_marks_entities_unavailable_until_it_recovers(
    hass: HomeAssistant, console: MockConsole
) -> None:
    entry = await setup_entry(hass, make_entry(hass))
    console.status["stat/device"] = 503
    await poll(hass, entry)
    assert state(hass, entry, "wan_ipv4") == "unavailable"
    del console.status["stat/device"]
    await poll(hass, entry)
    assert state(hass, entry, "wan_ipv4") == "203.0.113.1"


async def test_a_failed_fast_poll_at_setup_retries_the_entry(
    hass: HomeAssistant, console: MockConsole
) -> None:
    console.status["stat/device/aa:bb:cc:dd:ee:ff"] = 500
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_a_failed_history_fetch_reuses_the_last_records(
    hass: HomeAssistant, console: MockConsole
) -> None:
    """The gateway's block here is WAN2's run, unlabelled.

    With the per-WAN records in hand a WAN2 record of the same moment
    identifies it and it stays off the active line's sensors. If a failed
    fetch dropped those records for a poll, the fallback route would show
    WAN2's 90 Mbit/s as the active WAN's result.
    """
    console.history = {
        "data": [
            history_record("WAN", T0, 500.0, 50.0),
            history_record("WAN2", T0 + 600, 90.0, 9.0),
        ]
    }
    console.finish_run(rundate=T0 + 600, down=90.0, up=9.0, iface=None)
    entry = await setup_entry(hass, make_entry(hass))
    coordinator = entry.runtime_data.device_coordinator
    assert state(hass, entry, "speedtest_down") == "500.0"

    console.status["v2:speedtest"] = 500
    await poll(hass, entry)
    assert coordinator.last_update_success
    assert coordinator.data.per_wan_speedtest[2]["down"] == 90.0
    assert entry.runtime_data.client.speedtest_history_supported
    assert state(hass, entry, "speedtest_down") == "500.0"
    assert state(hass, entry, "wan2_speedtest_down") == "90.0"

    # A 404 is the console saying the endpoint is not there: stop asking.
    console.status["v2:speedtest"] = 404
    await poll(hass, entry)
    assert not entry.runtime_data.client.speedtest_history_supported
    assert coordinator.data.per_wan_speedtest == {}
    asked = console.gets.count("v2:speedtest")
    await poll(hass, entry)
    assert console.gets.count("v2:speedtest") == asked


async def test_per_wan_sensors_show_the_history_as_soon_as_they_exist(
    hass: HomeAssistant, console: MockConsole
) -> None:
    """Setup's first refresh runs before the speedtest manager listens.

    Its records used to wait for the next poll to be attributed - a scan
    interval of "unknown" on every start, although the console had
    already answered with WAN2's result.
    """
    console.history["data"].append(history_record("WAN2", T0 + 60, 90.0, 9.0))
    entry = await setup_entry(hass, make_entry(hass))
    assert state(hass, entry, "wan2_speedtest_down") == "90.0"


async def test_a_half_written_gateway_block_is_completed_on_the_next_poll(
    hass: HomeAssistant, console: MockConsole
) -> None:
    """FAILS on a console without per-WAN history.

    The per-WAN route compares whole records because, as its own comment
    says, the controller fills a run's figures in over several seconds
    under one timestamp. The gateway route keys on the timestamp alone: it
    latches the first poll's partial result and ignores the completed
    block that follows, and the gateway-wide sensors then prefer that
    latched copy because it ties on timestamp and is listed first.
    """
    console.history = None
    entry = await setup_entry(hass, make_entry(hass))

    console.finish_run(rundate=T0 + 600, down=400.0, up=None, iface="eth8")
    await poll(hass, entry)
    assert state(hass, entry, "wan1_speedtest_down") == "400.0"

    console.gateway["speedtest-status"]["xput_upload"] = 40.0
    await poll(hass, entry)
    assert state(hass, entry, "wan1_speedtest_up") == "40.0"
    assert state(hass, entry, "speedtest_up") == "40.0"


# --------------------------------------------------------------- restore


async def test_per_wan_results_survive_a_restart(
    hass: HomeAssistant, console: MockConsole
) -> None:
    """Per-WAN speedtest sensors restore their value across a restart.

    Up to 1.11.0 the entity stored only {"version", "source"}, and
    RestoreSensor.async_get_last_sensor_data, which needs native_value in
    that same dict, read it back as nothing. On a console without the
    per-WAN API nothing re-reads the result after a restart, so the
    sensor sat at unknown until that WAN was tested again.
    """
    console.history = None
    entry = await setup_entry(hass, make_entry(hass))
    console.finish_run(rundate=T0 + 600, down=300.0, up=30.0, iface="eth9")
    await poll(hass, entry)
    wan2 = entity_id(hass, "sensor", entry, "wan2_speedtest_down")
    assert hass.states.get(wan2).state == "300.0"
    # A timestamp takes the other serialisation path, as a tagged dict.
    last_run = entity_id(hass, "sensor", entry, "wan2_speedtest_last_run")
    when = hass.states.get(last_run).state
    assert when.startswith("2025-10-09T")
    server = entity_id(hass, "sensor", entry, "wan2_speedtest_server_provider")

    stored = await async_mock_restore_state_shutdown_restart(hass)
    extra = stored.last_states[wan2].extra_data.as_dict()
    assert extra["native_value"] == 300.0, extra
    assert extra["version"] == 2
    assert extra["source"] == "source_interface"

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(wan2).state == "300.0"
    assert hass.states.get(last_run).state == when
    assert hass.states.get(server).state == "ExampleNet"
    assert hass.states.get(wan2).attributes["restored"] is True


@pytest.mark.parametrize(
    ("extra_data", "expected"),
    [
        # Written before ATTRIBUTION_VERSION 2: deliberately not trusted.
        ({"native_value": 123.0, "native_unit_of_measurement": "Mbit/s"}, "unknown"),
        # What the entity stores: its value, stamped with the version.
        (
            {
                "version": 2,
                "source": "source_interface",
                "native_value": 123.0,
                "native_unit_of_measurement": "Mbit/s",
            },
            "123.0",
        ),
    ],
)
async def test_which_stored_values_are_restored(
    hass: HomeAssistant, console: MockConsole, extra_data: dict, expected: str
) -> None:
    console.history = None
    entry = make_entry(hass, entry_id="01RESTORE0000000000000000A")
    wan2 = er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_wan2_speedtest_down",
        config_entry=entry,
        suggested_object_id="wan2_down",
    )
    mock_restore_cache_with_extra_data(
        hass, [(State(wan2.entity_id, "123.0"), extra_data)]
    )
    await setup_entry(hass, entry)
    assert hass.states.get(wan2.entity_id).state == expected


# ------------------------------------------------------------ diagnostics


async def test_diagnostics_download_is_redacted_and_serialisable(
    hass: HomeAssistant, console: MockConsole, hass_client
) -> None:
    entry = await setup_entry(hass, make_entry(hass))
    await poll(hass, entry)
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["entry"]["data"]["host"] == "**REDACTED**"
    assert diagnostics["entry"]["data"]["api_key"] == "**REDACTED**"
    gateway = diagnostics["controller"]["gateway_device"]
    assert gateway["mac"] == "**REDACTED**"
    assert gateway["uplink"]["ip"] == "**REDACTED**"
    assert gateway["wan2"]["ifname"] == "eth9"
    assert gateway["geo_info"]["WAN"]["isp_name"] == "**REDACTED**"
    assert gateway["version"] == "4.0.6"
    derived = diagnostics["derived"]
    assert derived["active_wan"] == 1
    assert derived["wan_numbers"] == [1, 2]
    assert derived["per_wan_api_available"] is True
    # Integer WAN keys come through the JSON view as strings.
    assert set(derived["latched_speedtest_results"]) == {"1"}
    assert diagnostics["integration"]["version"] == "1.11.0"
    assert diagnostics["controller"]["other_devices"] == [
        {"type": "usw", "model": "USW-24", "adopted": None, "has_uplink": False}
    ]
    assert HOST not in str(diagnostics)
