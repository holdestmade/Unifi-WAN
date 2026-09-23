"""A speedtest run from trigger to outcome, and what happens to one that
is still waiting when its entry goes away.

fast_speedtest shrinks the five-minute wait to 0.3 s of loop time, so the
timeout and retry paths run in full rather than being patched out.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from console import T0, MockConsole, accept_and_record_nothing, one_wan_gateway
from harness import DOMAIN, entry_data, make_entry, setup_entry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

pytestmark = pytest.mark.usefixtures("fast_speedtest")

# Just past the one-minute auto interval the scheduled tests configure.
_MINUTE = timedelta(minutes=1, seconds=1)


def entity_id(hass: HomeAssistant, platform: str, entry, key: str) -> str:
    found = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert found is not None, f"no {platform} entity for {key}"
    return found


def state(hass: HomeAssistant, entry, key: str) -> str:
    return hass.states.get(entity_id(hass, "sensor", entry, key)).state


def targeted(console: MockConsole) -> list[str | None]:
    return [(data or {}).get("interface_name") for _, data in console.speedtest_posts()]


async def settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done(wait_background_tasks=True)


async def run(hass: HomeAssistant, **data) -> None:
    await hass.services.async_call(DOMAIN, "run_speedtest", data, blocking=True)
    await settle(hass)


# ------------------------------------------------------------ outcomes


async def test_a_targeted_run_that_records_nothing_is_repeated_untargeted(
    hass: HomeAssistant, console: MockConsole, caplog: pytest.LogCaptureFixture
) -> None:
    """The retry path: accepted, nothing recorded, so ask again plainly."""
    runs = {"n": 0}

    def handler(console, path, data):
        if (data or {}).get("interface_name"):
            return 200, {"meta": {"rc": "ok"}}
        runs["n"] += 1
        console.finish_run(
            rundate=T0 + 600, down=410.0, up=41.0, iface="eth8", history_group="WAN"
        )
        return 200, {"meta": {"rc": "ok"}}

    console.on_post = handler
    entry = await setup_entry(hass, make_entry(hass))
    with caplog.at_level(logging.WARNING):
        await run(hass, wan=2)

    assert targeted(console) == ["eth9", None]
    assert runs["n"] == 1
    assert entry.runtime_data.client.targeted_speedtest_supported is False
    assert "recorded no result" in caplog.text
    assert state(hass, entry, "wan1_speedtest_down") == "410.0"
    assert entry.runtime_data.speedtest.running is False

    # And the session remembers: the next WAN2 request goes out plain.
    console.posts.clear()
    await run(hass, wan=2)
    assert targeted(console) == [None]


async def test_a_run_recorded_on_another_wan_stops_the_rotation(
    hass: HomeAssistant, console: MockConsole, caplog: pytest.LogCaptureFixture
) -> None:
    """Firmware that takes the interface and tests the active line anyway."""

    def handler(console, path, data):
        console.finish_run(
            rundate=T0 + 600, down=420.0, up=42.0, iface="eth8", history_group="WAN"
        )
        return 200, {"meta": {"rc": "ok"}}

    console.on_post = handler
    entry = await setup_entry(hass, make_entry(hass))
    with caplog.at_level(logging.WARNING):
        await run(hass, wan=2)

    manager = entry.runtime_data.speedtest
    assert manager.per_wan_supported is False
    assert "does not honour" in caplog.text
    assert state(hass, entry, "wan1_speedtest_down") == "420.0"
    # WAN2 is not credited with WAN1's throughput.
    assert state(hass, entry, "wan2_speedtest_down") == "unknown"


async def test_a_run_that_never_reports_times_out(
    hass: HomeAssistant, console: MockConsole, caplog: pytest.LogCaptureFixture
) -> None:
    console.gateway = one_wan_gateway()
    console.on_post = accept_and_record_nothing
    entry = await setup_entry(hass, make_entry(hass))
    with caplog.at_level(logging.WARNING):
        await run(hass)
    assert "did not report a result" in caplog.text
    assert targeted(console) == [None]
    assert entry.runtime_data.speedtest.running is False


async def test_an_unreachable_console_mid_run_is_reported_not_raised(
    hass: HomeAssistant, console: MockConsole, caplog: pytest.LogCaptureFixture
) -> None:
    console.status["cmd/devmgr"] = 502
    console.status["cmd/devmgr/speedtest"] = 502
    entry = await setup_entry(hass, make_entry(hass))
    with caplog.at_level(logging.WARNING):
        await run(hass, wan=2)
    # One targeted try, then the plain command for this run only.
    assert targeted(console) == ["eth9", None]
    assert entry.runtime_data.client.targeted_speedtest_supported is None
    assert "did not accept a speedtest" in caplog.text
    assert entry.runtime_data.speedtest.running is False


# ----------------------------------------------------- runs vs. unloading


async def test_unloading_cancels_a_manual_run_cleanly(
    hass: HomeAssistant, console: MockConsole, caplog: pytest.LogCaptureFixture
) -> None:
    console.on_post = accept_and_record_nothing
    entry = await setup_entry(hass, make_entry(hass))
    manager = entry.runtime_data.speedtest

    await hass.services.async_call(DOMAIN, "run_speedtest", {"wan": 2}, blocking=True)
    # Let the command go out and the wait begin.
    for _ in range(5):
        await hass.async_block_till_done()
    assert manager.running
    assert targeted(console) == ["eth9"]

    with caplog.at_level(logging.DEBUG, logger="custom_components.unifi_wan"):
        assert await hass.config_entries.async_unload(entry.entry_id)
        await settle(hass)
    assert "Speedtest cancelled" in caplog.text
    assert manager.running is False
    assert targeted(console) == ["eth9"], "nothing was sent after the unload"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_an_automatic_run_does_not_outlive_its_entry(
    hass: HomeAssistant, console: MockConsole
) -> None:
    """FAILS: a scheduled run keeps going after its entry is unloaded.

    Manual runs are started as the entry's background tasks, which Home
    Assistant cancels on unload; async_shutdown's docstring says every run
    is. The scheduled run is not: async_track_time_interval runs it as a
    hass-wide background task. After the unload its coordinator is shut
    down, so no poll can ever show a result, the wait times out, and the
    retry path sends the console a second, untargeted speedtest on behalf
    of an entry that no longer exists.
    """
    console.on_post = accept_and_record_nothing
    entry = await setup_entry(
        hass,
        make_entry(
            hass, data=entry_data(auto_speedtest=True, auto_speedtest_minutes=1)
        ),
    )
    async_fire_time_changed(hass, dt_util.utcnow().replace(microsecond=0) + _MINUTE)
    for _ in range(5):
        await hass.async_block_till_done()
    assert targeted(console) == ["eth8"], "the rotation's first run went out"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await settle(hass)
    assert targeted(console) == ["eth8"], "a command was sent after the unload"


async def test_a_reload_mid_run_leaves_one_manager_running(
    hass: HomeAssistant, console: MockConsole
) -> None:
    """FAILS for the same reason: a reload during a scheduled run leaves
    the old manager's run going next to the new entry, still able to send
    commands to the console. (Its signals carry the same entry id as the
    new entities', but those only re-read the new manager's state, so the
    new In Progress sensor itself stays correct.)
    """
    console.on_post = accept_and_record_nothing
    entry = await setup_entry(
        hass,
        make_entry(
            hass, data=entry_data(auto_speedtest=True, auto_speedtest_minutes=1)
        ),
    )
    old = entry.runtime_data.speedtest
    async_fire_time_changed(hass, dt_util.utcnow().replace(microsecond=0) + _MINUTE)
    for _ in range(5):
        await hass.async_block_till_done()
    assert old.running

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.speedtest is not old
    assert old.running is False, "the unloaded entry's run is still in progress"
    await settle(hass)
