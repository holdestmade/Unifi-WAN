"""The two services, the buttons, the auto-speedtest switch and schedule.

The speedtest wait is shortened by the fast_speedtest fixture; everything
else - the service registry, the entity platforms, the entry's background
tasks, the time-interval tracker - is Home Assistant's own.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from console import MockConsole, one_wan_gateway, run_on
from harness import DOMAIN, entry_data, make_entry, setup_entry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

pytestmark = pytest.mark.usefixtures("fast_speedtest")

HONOURS_TARGET = {"eth8": ("eth8", "WAN"), "eth9": ("eth9", "WAN2")}


def entity_id(hass: HomeAssistant, platform: str, entry, key: str) -> str:
    found = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert found is not None, f"no {platform} entity for {key}"
    return found


def targeted(console: MockConsole) -> list[str | None]:
    """The interface each speedtest command named, None for a plain one."""
    return [(data or {}).get("interface_name") for _, data in console.speedtest_posts()]


async def settle(hass: HomeAssistant) -> None:
    """Wait for the entry's background speedtest tasks as well."""
    await hass.async_block_till_done(wait_background_tasks=True)


# ------------------------------------------------------------ run_speedtest


async def test_the_service_runs_a_targeted_speedtest_and_records_it(
    hass: HomeAssistant, console: MockConsole
) -> None:
    console.on_post = run_on(HONOURS_TARGET, down=300.0, up=30.0)
    entry = await setup_entry(hass, make_entry(hass))
    progress = entity_id(hass, "binary_sensor", entry, "speedtest_in_progress")
    seen: list[str] = []

    @callback
    def record(event) -> None:
        seen.append(event.data["new_state"].state)

    unsub = async_track_state_change_event(hass, [progress], record)
    await hass.services.async_call(DOMAIN, "run_speedtest", {"wan": 2}, blocking=True)
    await settle(hass)
    unsub()

    assert targeted(console) == ["eth9"]
    assert console.speedtest_posts()[0][0] == "cmd/devmgr/speedtest"
    assert seen == ["on", "off"]
    down = hass.states.get(entity_id(hass, "sensor", entry, "wan2_speedtest_down"))
    assert down.state == "300.0"
    assert down.attributes["attributed_by"] == "speedtest_api"
    # WAN1's result is untouched by a run on WAN2.
    wan1 = hass.states.get(entity_id(hass, "sensor", entry, "wan1_speedtest_down"))
    assert wan1.state == "500.0"
    assert entry.runtime_data.client.targeted_speedtest_supported is True


async def test_the_service_without_a_wan_runs_every_entrys_gateway(
    hass: HomeAssistant, console: MockConsole, aioclient_mock
) -> None:
    second = MockConsole(host="192.0.2.20")
    second.register(aioclient_mock)
    console.on_post = run_on(HONOURS_TARGET, down=300.0, up=30.0)
    second.on_post = run_on(HONOURS_TARGET, down=100.0, up=10.0)
    await setup_entry(hass, make_entry(hass))
    await setup_entry(hass, make_entry(hass, data=entry_data(host="192.0.2.20")))

    await hass.services.async_call(DOMAIN, "run_speedtest", {}, blocking=True)
    await settle(hass)

    assert targeted(console) == [None]
    assert targeted(second) == [None]


async def test_a_wan_the_gateway_does_not_have_is_refused(
    hass: HomeAssistant, console: MockConsole
) -> None:
    """Asking a two-WAN gateway for WAN3 used to switch targeted runs off.

    The console refuses an interface it does not have, and that refusal
    reads as the console refusing targeted runs altogether - so one bad
    call disabled them, and the rotation with them, for the session.
    """
    console.on_post = run_on(HONOURS_TARGET, down=300.0, up=30.0)
    entry = await setup_entry(hass, make_entry(hass))

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "run_speedtest", {"wan": 3}, blocking=True
        )
    assert err.value.translation_key == "unknown_wan"
    await settle(hass)
    assert console.speedtest_posts() == []

    await hass.services.async_call(DOMAIN, "run_speedtest", {"wan": 2}, blocking=True)
    await settle(hass)
    assert entry.runtime_data.client.targeted_speedtest_supported is True
    assert targeted(console) == ["eth9"]


async def test_a_wan_goes_only_to_the_gateways_that_have_it(
    hass: HomeAssistant, console: MockConsole, aioclient_mock
) -> None:
    single = MockConsole(host="192.0.2.20", gateway=one_wan_gateway())
    single.register(aioclient_mock)
    console.on_post = run_on(HONOURS_TARGET, down=300.0, up=30.0)
    single.on_post = run_on(HONOURS_TARGET, down=100.0, up=10.0)
    await setup_entry(hass, make_entry(hass))
    await setup_entry(hass, make_entry(hass, data=entry_data(host="192.0.2.20")))

    await hass.services.async_call(DOMAIN, "run_speedtest", {"wan": 2}, blocking=True)
    await settle(hass)
    assert targeted(console) == ["eth9"]
    assert single.speedtest_posts() == []


async def test_a_refused_command_ends_the_run_at_once(
    hass: HomeAssistant, console: MockConsole
) -> None:
    console.on_post = lambda _c, _p, _d: (
        200,
        {"meta": {"rc": "error", "msg": "api.err.Busy"}},
    )
    entry = await setup_entry(hass, make_entry(hass))
    await hass.services.async_call(DOMAIN, "run_speedtest", {}, blocking=True)
    await settle(hass)
    assert entry.runtime_data.speedtest.running is False
    progress = entity_id(hass, "binary_sensor", entry, "speedtest_in_progress")
    assert hass.states.get(progress).state == "off"


# ------------------------------------------------------------ dump_raw_data


async def test_the_dump_service_writes_an_unredacted_file(
    hass: HomeAssistant, console: MockConsole, tmp_path: Path
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await setup_entry(hass, make_entry(hass))

    response = await hass.services.async_call(
        DOMAIN, "dump_raw_data", {"keep": 5}, blocking=True, return_response=True
    )
    await hass.async_block_till_done()

    assert response["directory"] == str(tmp_path / "unifi_wan_dumps")
    [written] = response["files"]
    assert written["entry_id"] == entry.entry_id
    dump = json.loads(Path(written["path"]).read_text(encoding="utf-8"))
    assert dump["controller"]["stat_device"]["status"] == 200
    assert dump["controller"]["v2_speedtest"]["status"] == 200
    assert dump["controller"]["port_forwards"]["status"] == 200
    assert dump["controller"]["stat_device_gateway"]["status"] == 200
    assert dump["entry"]["data"]["api_key"].startswith("**WITHHELD")
    assert dump["parsed"]["gateway"]["mac"] == "aa:bb:cc:dd:ee:ff"
    assert dump["parsed"]["uplink"]["ip"] == "203.0.113.1"
    assert dump["derived"]["active_wan"] == 1
    assert written["bytes"] == Path(written["path"]).stat().st_size


async def test_the_dump_service_prunes_to_keep(
    hass: HomeAssistant, console: MockConsole, tmp_path: Path, freezer
) -> None:
    hass.config.config_dir = str(tmp_path)
    await setup_entry(hass, make_entry(hass))
    for _ in range(3):
        await hass.services.async_call(
            DOMAIN, "dump_raw_data", {"keep": 2}, blocking=True, return_response=True
        )
        freezer.tick(timedelta(seconds=1))
    files = sorted((tmp_path / "unifi_wan_dumps").iterdir())
    assert len(files) == 2


async def test_two_dumps_in_one_second_are_both_kept(
    hass: HomeAssistant, console: MockConsole, tmp_path: Path, freezer
) -> None:
    """The filename has one-second resolution; a second dump in the same
    second used to overwrite the first while both calls reported success.
    """
    hass.config.config_dir = str(tmp_path)
    await setup_entry(hass, make_entry(hass))
    paths = []
    for _ in range(2):
        response = await hass.services.async_call(
            DOMAIN, "dump_raw_data", {"keep": 10}, blocking=True, return_response=True
        )
        paths.append(response["files"][0]["path"])
    assert len(set(paths)) == 2
    assert all(Path(path).exists() for path in paths)
    assert len(list((tmp_path / "unifi_wan_dumps").iterdir())) == 2


async def test_the_dump_does_not_hand_live_state_to_the_writer_thread(
    hass: HomeAssistant, console: MockConsole, tmp_path: Path, monkeypatch
) -> None:
    """_snapshot deep-copies the parsed data because the JSON is
    serialised in an executor thread while the event loop keeps running.
    The derived section used to pass the live results dict alongside it,
    which the speedtest manager mutates in place on every attributed run.
    """
    from custom_components.unifi_wan import dump

    hass.config.config_dir = str(tmp_path)
    entry = await setup_entry(hass, make_entry(hass))
    handed_over: list[dict] = []
    real = dump._write_and_prune

    def capture(path, payload, prefix, keep):
        handed_over.append(payload)
        return real(path, payload, prefix, keep)

    monkeypatch.setattr(dump, "_write_and_prune", capture)
    await hass.services.async_call(
        DOMAIN, "dump_raw_data", {}, blocking=True, return_response=True
    )
    live = entry.runtime_data.speedtest.results
    handed = handed_over[0]["derived"]["latched_speedtest_results"]
    assert id(handed) != id(live), "the writer thread was given the live dict"


# ---------------------------------------------------------- button, switch


async def test_the_per_wan_button_targets_its_wan(
    hass: HomeAssistant, console: MockConsole
) -> None:
    console.on_post = run_on(HONOURS_TARGET, down=300.0, up=30.0)
    entry = await setup_entry(hass, make_entry(hass))
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": entity_id(hass, "button", entry, "run_speedtest_wan2")},
        blocking=True,
    )
    await settle(hass)
    assert targeted(console) == ["eth9"]

    console.posts.clear()
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": entity_id(hass, "button", entry, "run_speedtest")},
        blocking=True,
    )
    await settle(hass)
    assert targeted(console) == [None]


async def test_the_switch_applies_live_and_persists_without_a_reload(
    hass: HomeAssistant, console: MockConsole
) -> None:
    entry = await setup_entry(hass, make_entry(hass))
    runtime = entry.runtime_data
    switch = entity_id(hass, "switch", entry, "auto_speedtest_enabled")

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == "on"
    assert entry.options["auto_speedtest"] is True
    assert runtime.speedtest._unsub_auto is not None
    # The same runtime object: the update listener did not reload the entry.
    assert entry.runtime_data is runtime

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": switch}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == "off"
    assert entry.options["auto_speedtest"] is False
    assert runtime.speedtest._unsub_auto is None
    assert entry.runtime_data is runtime


async def test_the_schedule_rotates_across_the_wans_that_are_up(
    hass: HomeAssistant, console: MockConsole
) -> None:
    console.on_post = run_on(HONOURS_TARGET, down=300.0, up=30.0)
    await setup_entry(
        hass,
        make_entry(
            hass, data=entry_data(auto_speedtest=True, auto_speedtest_minutes=1)
        ),
    )
    # Not the freezer fixture: it stops the loop clock the speedtest's own
    # sleep runs on. Firing ever later times drives the interval tracker
    # while the loop keeps real time.
    start = dt_util.utcnow()
    for minute in (1, 2, 3):
        async_fire_time_changed(hass, start + timedelta(minutes=minute, seconds=1))
        await settle(hass)
    assert targeted(console) == ["eth8", "eth9", "eth8"]
