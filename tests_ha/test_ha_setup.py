"""Setting an entry up, tearing it down, and what it registers on the way.

Every test here runs async_setup_entry through Home Assistant's config
entry manager against the mock console, so the platforms - sensor,
binary_sensor, button and switch - are forwarded and loaded for real.
"""

from __future__ import annotations

from console import HOST, SITE, MockConsole, one_wan_gateway
from harness import DOMAIN, entry_data, make_entry, setup_entry
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er


def entity_id(hass: HomeAssistant, platform: str, entry, key: str) -> str:
    found = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert found is not None, f"no {platform} entity for {key}"
    return found


async def test_a_two_wan_gateway_sets_up_every_platform(
    hass: HomeAssistant, console: MockConsole
) -> None:
    entry = await setup_entry(hass, make_entry(hass))
    assert entry.state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    platforms = {
        e.domain for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert platforms == {"sensor", "binary_sensor", "button", "switch"}

    state = hass.states.get(entity_id(hass, "sensor", entry, "wan_ipv4"))
    assert state.state == "203.0.113.1"
    assert hass.states.get(entity_id(hass, "sensor", entry, "active_wan_id")).state == (
        "WAN1"
    )
    assert hass.states.get(
        entity_id(hass, "sensor", entry, "speedtest_down")
    ).state == ("500.0")
    assert hass.states.get(entity_id(hass, "sensor", entry, "wan2_ipv4")).state == (
        "198.51.100.7"
    )
    # The live-rate sensor reads the fast poll: 1 250 000 B/s is 10 Mbit/s.
    assert hass.states.get(entity_id(hass, "sensor", entry, "wan_down_mbps")).state == (
        "10.0"
    )
    assert hass.states.get(
        entity_id(hass, "binary_sensor", entry, "wan2_internet")
    ).state == ("on")
    assert hass.states.get(
        entity_id(hass, "binary_sensor", entry, "speedtest_in_progress")
    ).state == ("off")
    # One gateway-wide button and one per WAN.
    for key in ("run_speedtest", "run_speedtest_wan1", "run_speedtest_wan2"):
        assert hass.states.get(entity_id(hass, "button", entry, key)) is not None
    switch = hass.states.get(entity_id(hass, "switch", entry, "auto_speedtest_enabled"))
    assert switch.state == "off"

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device is not None
    assert device.model == "UDMPRO"
    assert device.sw_version == "4.0.6"


async def test_a_one_wan_gateway_has_no_per_wan_entities(
    hass: HomeAssistant, console: MockConsole
) -> None:
    console.gateway = one_wan_gateway()
    entry = await setup_entry(hass, make_entry(hass))
    registry = er.async_get(hass)
    keys = {
        e.unique_id.removeprefix(f"{entry.entry_id}_")
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert "run_speedtest" in keys
    assert not {k for k in keys if k.startswith("wan1") or k.startswith("wan2")}
    assert "run_speedtest_wan1" not in keys


async def test_no_gateway_on_the_site_retries_setup(
    hass: HomeAssistant, console: MockConsole
) -> None:
    console.gateway = None
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_a_rejected_key_at_setup_starts_reauth(
    hass: HomeAssistant, console: MockConsole
) -> None:
    console.status["stat/device"] = 401
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_an_unreachable_console_retries_setup(
    hass: HomeAssistant, console: MockConsole
) -> None:
    console.status["stat/device"] = 502
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload_and_reload(hass: HomeAssistant, console: MockConsole) -> None:
    entry = await setup_entry(hass, make_entry(hass))
    ipv4 = entity_id(hass, "sensor", entry, "wan_ipv4")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get(ipv4).state == "unavailable"
    assert not hass.services.has_service(DOMAIN, "run_speedtest")
    assert not hass.services.has_service(DOMAIN, "dump_raw_data")

    console.gateway["uplink"]["ip"] = "203.0.113.99"
    console.gateway["wan1"]["ip"] = "203.0.113.99"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(ipv4).state == "203.0.113.99"
    assert hass.services.has_service(DOMAIN, "run_speedtest")


async def test_two_entries_share_the_services(
    hass: HomeAssistant, console: MockConsole, aioclient_mock
) -> None:
    second_console = MockConsole(host="192.0.2.20")
    second_console.register(aioclient_mock)
    first = await setup_entry(hass, make_entry(hass))
    second = await setup_entry(
        hass, make_entry(hass, data=entry_data(host="192.0.2.20"))
    )

    assert await hass.config_entries.async_unload(first.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, "run_speedtest")
    assert hass.services.has_service(DOMAIN, "dump_raw_data")

    assert await hass.config_entries.async_unload(second.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, "run_speedtest")
    assert not hass.services.has_service(DOMAIN, "dump_raw_data")


async def test_legacy_unique_ids_and_device_are_migrated(
    hass: HomeAssistant, console: MockConsole
) -> None:
    entry = make_entry(hass)
    registry = er.async_get(hass)
    legacy = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{HOST}_{SITE}_wan_ipv4",
        config_entry=entry,
        suggested_object_id="legacy_wan_ipv4",
    )
    devices = dr.async_get(hass)
    legacy_device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, HOST, SITE)},  # the pre-1.0 3-tuple
        name="legacy",
    )

    await setup_entry(hass, entry)

    migrated = registry.async_get(legacy.entity_id)
    assert migrated is not None
    assert migrated.unique_id == f"{entry.entry_id}_wan_ipv4"
    # The entity kept its id, so dashboards and history still point at it.
    assert hass.states.get(legacy.entity_id).state == "203.0.113.1"

    device = devices.async_get(legacy_device.id)
    assert device.identifiers == {(DOMAIN, entry.entry_id)}
    # And no second device was created alongside it.
    assert len(dr.async_entries_for_config_entry(devices, entry.entry_id)) == 1
