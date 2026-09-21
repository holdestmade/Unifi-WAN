"""What the sensors display, given a payload.

The value functions are pure functions of the parsed data, so they can be
called directly without creating entities.
"""

from __future__ import annotations

from datetime import UTC

from helpers import gateway_payload, history, record
from unifi_wan.models import extract_wan_data, parse_speedtest_history
from unifi_wan.sensor import (
    SENSORS,
    _active_speedtest,
    _active_speedtest_server,
    _displayed_speedtest,
    _mbps,
    _speedtest_interface,
    _ts_date,
    _wan_id,
    _wan_name,
)

TWO_WAN = {
    "uplink": {"up": True, "ip": "203.0.113.1"},
    "wan": {"ip": "203.0.113.1", "ifname": "eth8", "up": True},
    "wan2": {"ip": "198.51.100.7", "ifname": "eth9", "up": True},
}


def build(payload, history_body=None, latched=None):
    data = extract_wan_data(payload)
    if history_body is not None:
        data.per_wan_speedtest = parse_speedtest_history(history_body, data.wan)
    data.speedtest_latched = latched or {}
    return data


def by_key(key: str):
    return next(d for d in SENSORS if d.key == key)


# ----------------------------------------------------------- unit handling


def test_byte_rates_become_megabits():
    assert _mbps(1_250_000) == 10.0
    assert _mbps(None) is None
    assert _mbps("nonsense") is None


def test_a_millisecond_timestamp_still_renders_as_a_date():
    """The per-WAN API reports milliseconds; read as seconds it was a date
    far enough in the future to be discarded entirely.
    """
    rendered = _ts_date(1_700_000_000_000)
    assert rendered is not None
    assert rendered.year == 2023
    assert rendered.tzinfo is UTC


def test_no_timestamp_is_no_date():
    assert _ts_date(None) is None
    assert _ts_date(0) is None


# ------------------------------------------------------------ the addresses


def test_a_missing_address_is_unknown_not_the_word_unknown():
    data = build(gateway_payload(uplink={"up": True}))
    assert by_key("wan_ipv4").value_fn(data) is None


def test_an_address_is_reported():
    data = build(gateway_payload(uplink={"up": True, "ip": "203.0.113.1"}))
    assert by_key("wan_ipv4").value_fn(data) == "203.0.113.1"


# --------------------------------------------------------- WAN identification


def test_the_active_wan_is_named_with_its_chassis_port():
    data = build(
        gateway_payload(
            uplink={"up": True, "ip": "203.0.113.1"},
            wan={
                "ip": "203.0.113.1",
                "ifname": "eth8",
                "comment": "Virgin Fibre",
                "physical_ports": [9],
            },
        )
    )
    assert _wan_id(data) == "WAN1"
    assert _wan_name(data) == "Virgin Fibre (Port 9)"


def test_an_interface_name_is_never_read_as_a_port_number():
    """eth8 is the port labelled 9, so the raw name is shown, not converted."""
    data = build(
        gateway_payload(
            uplink={"up": True, "ip": "203.0.113.1"},
            wan={"ip": "203.0.113.1", "ifname": "eth8"},
        )
    )
    assert _wan_name(data) == "WAN1 (eth8)"


def test_an_unresolved_active_wan_says_so():
    data = build(
        gateway_payload(uplink={"up": True}, wan={"up": True}, wan2={"up": True})
    )
    assert _wan_id(data) == "Unknown"


# ------------------------------------------------- the gateway-wide result


def test_the_latched_result_is_what_is_shown():
    data = build(
        gateway_payload(**TWO_WAN),
        latched={1: {"down": 500.0, "up": 52.0, "lastrun": 1_700_000_000}},
    )
    assert _active_speedtest(data)["down"] == 500.0
    assert _speedtest_interface(data) == "WAN1"


def test_a_non_active_wans_result_never_reaches_these_sensors():
    data = build(
        gateway_payload(**TWO_WAN),
        history(record("WAN2", 1_700_009_000_000, down=40)),
    )
    # WAN1 is the active uplink and has never been tested.
    assert _active_speedtest(data) == {}
    assert _speedtest_interface(data) == "unknown"


def test_the_newer_of_the_two_sources_wins():
    data = build(
        gateway_payload(
            **TWO_WAN,
            speedtest_status={
                "xput_download": 900.0,
                "rundate": 1_700_009_000,
                "source_interface": "eth8",
            },
        ),
        history(record("WAN", 1_700_000_000_000, down=100)),
    )
    assert _active_speedtest(data)["down"] == 900.0


def test_a_fresh_but_empty_block_does_not_blank_a_good_result():
    """The gateway rewrites its block around a run."""
    data = build(
        gateway_payload(**TWO_WAN, speedtest_status={"rundate": 1_700_009_999}),
        latched={1: {"down": 500.0, "up": 52.0, "lastrun": 1_700_000_000}},
    )
    assert _active_speedtest(data)["down"] == 500.0


def test_with_no_active_wan_the_newest_record_is_shown_and_labelled():
    data = build(
        gateway_payload(
            uplink={"up": True},
            wan={"up": True, "ifname": "eth8"},
            wan2={"up": True, "ifname": "eth9"},
        ),
        history(
            record("WAN", 1_700_000_000_000, down=100),
            record("WAN2", 1_700_009_000_000, down=40),
        ),
    )
    result, wan_number = _displayed_speedtest(data)
    assert result["down"] == 40
    assert wan_number == 2
    assert _speedtest_interface(data) == "WAN2"


# ------------------------------------------------------------- the server


def test_the_server_is_read_from_what_was_latched():
    data = build(
        gateway_payload(**TWO_WAN),
        latched={1: {"server_provider": "Exascale", "down": 1.0, "lastrun": 1}},
    )
    assert _active_speedtest_server(data)["server_provider"] == "Exascale"


def test_the_gateway_wide_server_follows_the_throughput_it_arrived_with():
    """These sensors describe whatever result they are showing, so the block
    is read on the same terms its throughput is. The stricter rule applies
    to the per-WAN server sensors, which latch: see test_speedtest.py.
    """
    data = build(
        gateway_payload(
            **TWO_WAN,
            speedtest_status={
                "xput_download": 1.0,
                "rundate": 10,
                "server": {"provider": "Exascale"},
            },
        )
    )
    assert _active_speedtest_server(data)["server_provider"] == "Exascale"


def test_the_gateway_wide_server_is_dropped_when_the_block_is_another_lines():
    """A block a per-WAN record of the same moment claims for WAN2 is not
    the active line's, so nothing of it is shown here.
    """
    data = build(
        gateway_payload(
            **TWO_WAN,
            speedtest_status={
                "xput_download": 1.0,
                "rundate": 1_700_000_000,
                "server": {"provider": "Exascale"},
            },
        ),
        history(record("WAN2", 1_700_000_030_000, down=40)),
    )
    assert _active_speedtest_server(data).get("server_provider") is None


def test_the_gateway_wide_server_is_unset_when_no_uplink_resolves():
    data = build(
        gateway_payload(
            uplink={"up": True},
            wan={"up": True},
            wan2={"up": True},
            speedtest_status={"server": {"provider": "Exascale"}},
        )
    )
    assert _active_speedtest_server(data) == {}


# ------------------------------------------------------------ ISP sensors


def test_the_isp_sensors_follow_the_active_uplink():
    data = build(
        gateway_payload(
            **TWO_WAN,
            geo_info={"WAN": {"isp_name": "Kcom"}, "WAN2": {"isp_name": "Vodafone"}},
        )
    )
    assert by_key("wan_isp_name").value_fn(data) == "Kcom"


def test_the_isp_sensors_report_nothing_when_the_uplink_is_unresolved():
    data = build(
        gateway_payload(
            uplink={"up": True},
            wan={"up": True},
            wan2={"up": True},
            geo_info={"WAN": {"isp_name": "Kcom"}},
        )
    )
    assert by_key("wan_isp_name").value_fn(data) is None


# --------------------------------------------------------------- integrity


def test_every_sensor_survives_an_empty_payload():
    """A console that answers with nothing must not break a single sensor."""
    data = extract_wan_data(None)
    for description in SENSORS:
        description.value_fn(data)
        if description.attributes_fn is not None:
            description.attributes_fn(data)


def test_sensor_keys_are_unique():
    keys = [d.key for d in SENSORS]
    assert len(keys) == len(set(keys))
