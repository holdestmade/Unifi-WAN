"""Attributing a finished speedtest to the WAN it ran on.

This is the part of the integration that has caused the most trouble, and
as a class rather than a stack of closures it can be driven directly: give
the manager a payload, tell it the coordinator refreshed, and read what it
recorded.
"""

from __future__ import annotations

from helpers import (
    FakeCoordinator,
    FakeEntry,
    gateway_payload,
    history,
    make_client,
    record,
)
from unifi_wan.models import extract_wan_data, parse_speedtest_history
from unifi_wan.speedtest import SpeedtestManager


def build(payload: dict, history_body: dict | None = None):
    data = extract_wan_data(payload)
    if history_body is not None:
        data.speedtest_history_raw = history_body
        data.per_wan_speedtest = parse_speedtest_history(history_body, data.wan)
    return data


def manager(data, wan_numbers=(1, 2), auto_enabled=False):
    coordinator = FakeCoordinator(data)
    if data is not None:
        data.speedtest_latched = coordinator.speedtest_results
    mgr = SpeedtestManager(
        hass=None,
        entry=FakeEntry(),
        client=make_client(),
        coordinator=coordinator,
        rates_coordinator=None,
        wan_numbers=list(wan_numbers),
        auto_enabled=auto_enabled,
        auto_minutes=60,
    )
    return mgr, coordinator


def refresh(mgr, coordinator, data):
    """Hand the manager a new payload as a coordinator refresh would."""
    data.speedtest_latched = coordinator.speedtest_results
    coordinator.data = data
    mgr._process_result()


TWO_WAN = {
    "uplink": {"up": True, "ip": "203.0.113.1"},
    "wan": {"ip": "203.0.113.1", "ifname": "eth8", "up": True},
    "wan2": {"ip": "198.51.100.7", "ifname": "eth9", "up": True},
}


# --------------------------------------------- the gateway's single result


def test_a_named_interface_attributes_the_result(dispatched):
    data = build(gateway_payload(**TWO_WAN))
    mgr, coord = manager(data)
    newer = build(
        gateway_payload(
            **TWO_WAN,
            speedtest_status={
                "xput_download": 480.0,
                "xput_upload": 52.0,
                "latency": 8,
                "rundate": 1_700_000_000,
                "source_interface": "if!eth9",
            },
        )
    )
    refresh(mgr, coord, newer)
    assert mgr.results[2]["down"] == 480.0
    assert mgr.results[2]["source"] == "source_interface"
    assert 1 not in mgr.results
    assert dispatched == [mgr.result_signal]


def test_an_unnamed_result_goes_to_the_active_wan(dispatched):
    data = build(gateway_payload(**TWO_WAN))
    mgr, coord = manager(data)
    refresh(
        mgr,
        coord,
        build(
            gateway_payload(
                **TWO_WAN,
                speedtest_status={"xput_download": 100.0, "rundate": 1_700_000_000},
            )
        ),
    )
    assert mgr.results[1]["source"] == "active_wan"


def test_a_result_never_moves_backwards(dispatched):
    data = build(gateway_payload(**TWO_WAN))
    mgr, coord = manager(data)
    refresh(
        mgr,
        coord,
        build(
            gateway_payload(
                **TWO_WAN,
                speedtest_status={"xput_download": 500.0, "rundate": 1_700_000_000},
            )
        ),
    )
    # A block caught mid-rewrite falls back to legacy fields describing a
    # much older run; that must not displace what is held.
    refresh(
        mgr,
        coord,
        build(
            gateway_payload(
                **TWO_WAN,
                speedtest_status={"xput_download": 9.0, "rundate": 1_600_000_000},
            )
        ),
    )
    assert mgr.results[1]["down"] == 500.0


def test_an_empty_result_is_not_recorded(dispatched):
    data = build(gateway_payload(**TWO_WAN))
    mgr, coord = manager(data)
    refresh(
        mgr,
        coord,
        build(gateway_payload(**TWO_WAN, speedtest_status={"rundate": 1_700_000_000})),
    )
    assert mgr.results == {}


def test_the_same_run_is_only_recorded_once(dispatched):
    data = build(gateway_payload(**TWO_WAN))
    mgr, coord = manager(data)
    later = build(
        gateway_payload(
            **TWO_WAN, speedtest_status={"xput_download": 1.0, "rundate": 1_700_000_000}
        )
    )
    refresh(mgr, coord, later)
    refresh(mgr, coord, later)
    assert dispatched == [mgr.result_signal]


# ------------------------------------------------- the requested interface


def test_requesting_a_wan_never_attributes_the_result(dispatched):
    """Firmware that ignores the request still tests the active uplink."""
    data = build(gateway_payload(**TWO_WAN))
    mgr, coord = manager(data)
    mgr._pending_wan = 2  # a run was asked for on WAN2
    refresh(
        mgr,
        coord,
        build(
            gateway_payload(
                **TWO_WAN,
                speedtest_status={"xput_download": 100.0, "rundate": 1_700_000_000},
            )
        ),
    )
    # Recorded against the active WAN, which is WAN1, not the one requested.
    assert 2 not in mgr.results
    assert mgr.results[1]["requested_wan"] == 2


def test_an_inferred_mismatch_is_not_evidence(dispatched):
    """Nothing was observed about which line ran, so the rotation stays on."""
    data = build(gateway_payload(**TWO_WAN))
    mgr, coord = manager(data)
    mgr._pending_wan = 2
    refresh(
        mgr,
        coord,
        build(
            gateway_payload(
                **TWO_WAN,
                speedtest_status={"xput_download": 100.0, "rundate": 1_700_000_000},
            )
        ),
    )
    assert mgr.per_wan_supported is None


def test_a_named_mismatch_is_evidence(dispatched):
    """The controller said it ran on another line, so stop cycling."""
    data = build(gateway_payload(**TWO_WAN))
    mgr, coord = manager(data)
    mgr._pending_wan = 2
    refresh(
        mgr,
        coord,
        build(
            gateway_payload(
                **TWO_WAN,
                speedtest_status={
                    "xput_download": 100.0,
                    "rundate": 1_700_000_000,
                    "source_interface": "eth8",
                },
            )
        ),
    )
    assert mgr.per_wan_supported is False
    assert mgr.results[1]["down"] == 100.0


def test_honouring_the_request_is_recorded(dispatched):
    data = build(gateway_payload(**TWO_WAN))
    mgr, coord = manager(data)
    mgr._pending_wan = 2
    refresh(
        mgr,
        coord,
        build(
            gateway_payload(
                **TWO_WAN,
                speedtest_status={
                    "xput_download": 100.0,
                    "rundate": 1_700_000_000,
                    "source_interface": "eth9",
                },
            )
        ),
    )
    assert mgr.per_wan_supported is True


# ---------------------------------------------------- the per-WAN records


def test_per_wan_records_are_used_as_they_stand(dispatched):
    data = build(gateway_payload(**TWO_WAN), history())
    mgr, coord = manager(data)
    refresh(
        mgr,
        coord,
        build(
            gateway_payload(**TWO_WAN),
            history(
                record("WAN", 1_700_000_000_000, down=500),
                record("WAN2", 1_700_000_100_000, down=40),
            ),
        ),
    )
    assert mgr.results[1]["down"] == 500
    assert mgr.results[2]["down"] == 40
    assert mgr.results[1]["source"] == "speedtest_api"


def test_a_half_written_record_is_completed(dispatched):
    """The controller fills a run in over several seconds, timestamp unchanged."""
    data = build(gateway_payload(**TWO_WAN), history())
    mgr, coord = manager(data)
    half = history(
        {"wan_networkgroup": "WAN", "time": 1_700_000_000_000, "download_mbps": 500}
    )
    refresh(mgr, coord, build(gateway_payload(**TWO_WAN), half))
    assert mgr.results[1]["up"] is None
    whole = history(record("WAN", 1_700_000_000_000, down=500, up=52))
    refresh(mgr, coord, build(gateway_payload(**TWO_WAN), whole))
    assert mgr.results[1]["up"] == 52


def test_a_newer_gateway_block_tops_up_a_stale_record(dispatched):
    """Not every firmware adds every run to the per-WAN history."""
    data = build(gateway_payload(**TWO_WAN), history())
    mgr, coord = manager(data)
    refresh(
        mgr,
        coord,
        build(
            gateway_payload(
                **TWO_WAN,
                speedtest_status={
                    "xput_download": 900.0,
                    "xput_upload": 90.0,
                    "rundate": 1_700_009_000,
                    "source_interface": "eth8",
                },
            ),
            history(record("WAN", 1_700_000_000_000, down=100)),
        ),
    )
    assert mgr.results[1]["down"] == 900.0


def test_an_older_gateway_block_does_not_displace_a_record(dispatched):
    data = build(gateway_payload(**TWO_WAN), history())
    mgr, coord = manager(data)
    refresh(
        mgr,
        coord,
        build(
            gateway_payload(
                **TWO_WAN,
                speedtest_status={
                    "xput_download": 5.0,
                    "rundate": 1_600_000_000,
                    "source_interface": "eth8",
                },
            ),
            history(record("WAN", 1_700_000_000_000, down=100)),
        ),
    )
    assert mgr.results[1]["down"] == 100


# ------------------------------------------------------ the tested server


def test_the_server_latches_only_on_hard_evidence(dispatched):
    data = build(gateway_payload(**TWO_WAN))
    mgr, coord = manager(data)
    refresh(
        mgr,
        coord,
        build(
            gateway_payload(
                **TWO_WAN,
                speedtest_status={
                    "xput_download": 100.0,
                    "rundate": 1_700_000_000,
                    "source_interface": "eth9",
                    "server": {"provider": "Exascale", "city": "Hull"},
                },
            )
        ),
    )
    assert mgr.results[2]["server_provider"] == "Exascale"


def test_an_unclaimed_server_is_not_guessed_at(dispatched):
    """A multi-WAN block naming no interface leaves the server unset."""
    data = build(gateway_payload(**TWO_WAN))
    mgr, coord = manager(data)
    refresh(
        mgr,
        coord,
        build(
            gateway_payload(
                **TWO_WAN,
                speedtest_status={
                    "xput_download": 100.0,
                    "rundate": 1_700_000_000,
                    "server": {"provider": "Exascale"},
                },
            )
        ),
    )
    assert mgr.results[1]["server_provider"] is None


def test_a_block_caught_mid_write_does_not_blank_the_server(dispatched):
    data = build(gateway_payload(**TWO_WAN))
    mgr, coord = manager(data)
    refresh(
        mgr,
        coord,
        build(
            gateway_payload(
                **TWO_WAN,
                speedtest_status={
                    "xput_download": 100.0,
                    "rundate": 1_700_000_000,
                    "source_interface": "eth9",
                    "server": {"provider": "Exascale"},
                },
            )
        ),
    )
    refresh(
        mgr,
        coord,
        build(
            gateway_payload(
                **TWO_WAN,
                speedtest_status={
                    "xput_download": 120.0,
                    "rundate": 1_700_000_500,
                    "source_interface": "eth9",
                },
            )
        ),
    )
    assert mgr.results[2]["server_provider"] == "Exascale"
    assert mgr.results[2]["down"] == 120.0


# -------------------------------------------------------------- scheduling


def test_the_auto_setting_is_applied_once(dispatched, monkeypatch):
    from unifi_wan import speedtest as speedtest_module

    armed: list[object] = []
    monkeypatch.setattr(
        speedtest_module,
        "async_track_time_interval",
        lambda hass, action, interval: armed.append(interval) or (lambda: None),
    )
    data = build(gateway_payload(**TWO_WAN))
    mgr, _ = manager(data)
    # Already off, so nothing to tell the switch about.
    mgr.set_auto_enabled(False)
    assert dispatched == []
    assert armed == []

    mgr.set_auto_enabled(True)
    assert mgr.auto_enabled is True
    assert dispatched == [mgr.auto_changed_signal]
    assert armed and armed[0].total_seconds() == 3600

    # Turning it off again disarms and tells the switch once more.
    mgr.set_auto_enabled(False)
    assert dispatched == [mgr.auto_changed_signal] * 2
    assert mgr._unsub_auto is None


async def test_the_rotation_cycles_the_lines_that_are_up(dispatched, monkeypatch):
    data = build(gateway_payload(**TWO_WAN))
    mgr, _ = manager(data)
    asked: list[int | None] = []
    monkeypatch.setattr(mgr, "async_run", lambda wan=None: _record(asked, wan))
    await mgr._auto_speedtest(None)
    await mgr._auto_speedtest(None)
    await mgr._auto_speedtest(None)
    assert asked == [1, 2, 1]


async def test_the_rotation_stops_once_it_is_pointless(dispatched, monkeypatch):
    data = build(gateway_payload(**TWO_WAN))
    mgr, _ = manager(data)
    mgr._per_wan_supported = False
    asked: list[int | None] = []
    monkeypatch.setattr(mgr, "async_run", lambda wan=None: _record(asked, wan))
    await mgr._auto_speedtest(None)
    assert asked == [None]


async def _record(sink, value):
    sink.append(value)


# ------------------------------------------------------------ a whole run


async def test_a_refused_command_does_not_wait_for_a_result(dispatched):
    data = build(gateway_payload(**TWO_WAN))
    mgr, coord = manager(data)
    mgr.client = make_client([(503, None)])
    waited = False

    async def _never(_before):
        nonlocal waited
        waited = True
        return []

    mgr._wait_for_result = _never
    await mgr.async_run()
    assert waited is False
    assert mgr.running is False


async def test_a_run_reports_finished_even_when_it_fails(dispatched):
    data = build(gateway_payload(**TWO_WAN))
    mgr, coord = manager(data)

    async def _boom(_before):
        raise RuntimeError("controller exploded")

    mgr._wait_for_result = _boom
    await mgr.async_run()
    assert mgr.running is False
    assert mgr._pending_wan is None


async def test_two_runs_do_not_overlap(dispatched):
    data = build(gateway_payload(**TWO_WAN))
    mgr, coord = manager(data)
    mgr.running = True
    calls = len(mgr.client._session.calls)
    await mgr.async_run()
    assert len(mgr.client._session.calls) == calls
