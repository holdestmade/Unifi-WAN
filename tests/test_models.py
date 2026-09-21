"""The parsing and the decisions made on it.

models.py imports no Home Assistant, so these run anywhere.
"""

from __future__ import annotations

import pytest
from helpers import gateway_payload, history, record
from unifi_wan.models import (
    attributed_server,
    attribution_source,
    extract_rates,
    extract_wan_data,
    find_gateway,
    gateway_result_wan,
    gateway_speedtest_wan,
    interface_to_wan_number,
    is_newer,
    normalise_interface,
    parse_speedtest_history,
    speedtest_epoch,
    wan_group_to_number,
)

# ------------------------------------------------------------ small helpers


@pytest.mark.parametrize(
    ("group", "expected"),
    [
        ("WAN", 1),
        ("wan", 1),
        (" WAN2 ", 2),
        ("WAN10", 10),
        ("LAN", None),
        ("WANX", None),
        (None, None),
        (2, None),
    ],
)
def test_wan_group_to_number(group, expected):
    assert wan_group_to_number(group) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("if!eth6", "eth6"), ("eth6", "eth6"), ("", None), ("  ", None), (None, None)],
)
def test_normalise_interface(raw, expected):
    assert normalise_interface(raw) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1_700_000_000, 1_700_000_000),
        (1_700_000_000_000, 1_700_000_000),  # milliseconds from the v2 API
        (0, None),
        (-5, None),
        ("nonsense", None),
        (None, None),
    ],
)
def test_speedtest_epoch(value, expected):
    assert speedtest_epoch(value) == expected


def test_is_newer_compares_across_units():
    # The same instant in milliseconds is not newer than it is in seconds.
    assert not is_newer(1_700_000_000_000, 1_700_000_000)
    assert is_newer(1_700_000_060_000, 1_700_000_000)
    assert is_newer(1_700_000_000, None)
    assert not is_newer(None, 1_700_000_000)


# ------------------------------------------------------------------- IPv6


def test_link_local_ipv6_is_not_reported():
    data = extract_wan_data(
        gateway_payload(wan={"ip": "203.0.113.1", "ip6": "fe80::1", "up": True})
    )
    assert data.wan[1].get("ip6") is None


def test_routable_ipv6_is_kept():
    data = extract_wan_data(
        gateway_payload(wan={"ip": "203.0.113.1", "ip6": "2001:db8::1", "up": True})
    )
    assert data.wan[1]["ip6"] == "2001:db8::1"


def test_routable_alternative_beats_a_link_local_ip6_key():
    data = extract_wan_data(
        gateway_payload(
            wan={"ip": "203.0.113.1", "ip6": "fe80::1", "ip6_address": "2001:db8::2"}
        )
    )
    assert data.wan[1]["ip6"] == "2001:db8::2"


def test_uplink_link_local_is_dropped():
    data = extract_wan_data(
        gateway_payload(uplink={"up": True, "ip": "203.0.113.1", "ip6": "fe80::9"})
    )
    assert data.uplink.get("ip6") is None


def test_uplink_borrows_ipv6_from_the_wan_with_the_same_address():
    data = extract_wan_data(
        gateway_payload(
            uplink={"up": True, "ip": "203.0.113.1"},
            wan={"ip": "203.0.113.1", "ip6": "2001:db8::5"},
        )
    )
    assert data.uplink["ip6"] == "2001:db8::5"


def test_uplink_does_not_borrow_another_lines_ipv6():
    data = extract_wan_data(
        gateway_payload(
            uplink={"up": True, "ip": "203.0.113.1"},
            wan={"ip": "198.51.100.7", "ip6": "2001:db8::5"},
        )
    )
    assert data.uplink.get("ip6") is None


# --------------------------------------------------------- gateway discovery


def test_gateway_found_by_type():
    assert find_gateway([{"type": "udm", "uplink": {}}])["type"] == "udm"


def test_gateway_found_by_shape_when_the_model_is_unknown():
    devices = [
        {"type": "usw", "model": "US24"},
        {"type": "brand-new-thing", "uplink": {"up": True}, "wan": {"ip": "1.2.3.4"}},
    ]
    assert find_gateway(devices)["type"] == "brand-new-thing"


def test_a_switch_is_never_mistaken_for_a_gateway():
    assert find_gateway([{"type": "usw", "model": "US24", "port_table": []}]) is None


def test_adopted_gateway_with_an_uplink_wins():
    devices = [
        {"type": "udm", "adopted": False, "model": "old"},
        {"type": "udm", "adopted": True, "uplink": {"up": True}, "model": "live"},
    ]
    assert find_gateway(devices)["model"] == "live"


# ------------------------------------------------------------- WAN sections


def test_malformed_last_wan_interfaces_does_not_fail_the_poll():
    data = extract_wan_data(
        gateway_payload(last_wan_interfaces={"WAN": None, "WAN2": {"alive": True}})
    )
    assert data.wan_alive == {1: False, 2: True}


def test_wan_numbers_fall_back_to_the_sections():
    data = extract_wan_data(
        gateway_payload(wan={"ip": "1.1.1.1"}, wan2={"ip": "2.2.2.2"})
    )
    assert sorted(data.wan) == [1, 2]


# --------------------------------------------------------- active WAN choice


def test_active_wan_matches_on_address_first():
    data = extract_wan_data(
        gateway_payload(
            uplink={"up": True, "ip": "198.51.100.7", "ifname": "eth8"},
            wan={"ip": "203.0.113.1", "ifname": "eth8"},
            wan2={"ip": "198.51.100.7", "ifname": "eth9"},
        )
    )
    assert data.active_wan == (2, "uplink_ip")


def test_active_wan_falls_back_to_the_interface_name():
    data = extract_wan_data(
        gateway_payload(
            uplink={"up": True, "name": "eth9"},
            wan={"ip": "203.0.113.1", "ifname": "eth8"},
            wan2={"ip": "198.51.100.7", "ifname": "eth9"},
        )
    )
    assert data.active_wan == (2, "uplink_ifname")


def test_active_wan_falls_back_to_the_only_alive_line():
    data = extract_wan_data(
        gateway_payload(
            uplink={"up": True},
            wan={"up": True},
            wan2={"up": True},
            last_wan_interfaces={"WAN": {"alive": False}, "WAN2": {"alive": True}},
        )
    )
    assert data.active_wan == (2, "only_wan_alive")


def test_active_wan_unresolved_is_said_so():
    data = extract_wan_data(
        gateway_payload(uplink={"up": True}, wan={"up": True}, wan2={"up": True})
    )
    assert data.active_wan == (None, "no_match")


def test_active_wan_is_computed_once():
    data = extract_wan_data(gateway_payload(wan={"ip": "203.0.113.1"}))
    assert data.active_wan is data.active_wan


# ------------------------------------------------------- interface matching


def test_interface_matches_a_section_before_a_naming_convention():
    wan = {1: {"ifname": "wan2"}, 2: {"ifname": "eth9"}}
    # "wan2" is WAN1's literal interface here, which beats the convention.
    assert interface_to_wan_number("wan2", wan) == 1


def test_pppoe_interface_resolves():
    assert interface_to_wan_number("if!ppp0", {1: {"ifname": "ppp0"}}) == 1


# ----------------------------------------------------------- speedtest block


def test_speedtest_read_from_the_gateway_block():
    data = extract_wan_data(
        gateway_payload(
            speedtest_status={
                "xput_download": 512.5,
                "xput_upload": 74.2,
                "latency": 11,
                "rundate": 1_700_000_000,
                "source_interface": "if!eth8",
                "server": {"provider": "Exascale", "city": "Hull"},
            }
        )
    )
    assert data.speedtest["down"] == 512.5
    assert data.speedtest["source_interface"] == "eth8"
    assert data.speedtest["server_provider"] == "Exascale"
    assert data.speedtest["server_city"] == "Hull"


def test_a_result_is_never_assembled_from_both_sources():
    """An empty block falls back to the uplink whole, never field by field."""
    data = extract_wan_data(
        gateway_payload(
            uplink={
                "up": True,
                "xput_down": 90.0,
                "xput_up": 9.0,
                "speedtest_lastrun": 1_600_000_000,
            },
            speedtest_status={},
        )
    )
    assert data.speedtest["down"] == 90.0
    assert data.speedtest["lastrun"] == 1_600_000_000


def test_the_server_sub_object_never_describes_the_subscriber():
    """The block's own "city" is the line's; only server.city is the server's."""
    data = extract_wan_data(
        gateway_payload(
            speedtest_status={
                "xput_download": 1.0,
                "rundate": 1_700_000_000,
                "city": "Leeds",
                "server": {"provider": "Exascale"},
            }
        )
    )
    assert data.speedtest["server_city"] is None
    assert data.speedtest["server_provider"] == "Exascale"


# --------------------------------------------------------------- ISP lookup


def test_geo_info_blocks_merge_without_overwriting():
    data = extract_wan_data(
        gateway_payload(
            geo_info={"WAN": {"isp_name": "Kcom", "asn": 12345}},
            last_geo_info={"WAN": {"isp_name": "Stale", "city": "Hull"}},
        )
    )
    assert data.geo_info[1]["isp_name"] == "Kcom"
    assert data.geo_info[1]["city"] == "Hull"


def test_each_wan_keeps_its_own_operator():
    data = extract_wan_data(
        gateway_payload(
            wan={"ip": "1.1.1.1"},
            wan2={"ip": "2.2.2.2"},
            geo_info={"WAN": {"isp_name": "Kcom"}, "WAN2": {"isp_name": "Vodafone"}},
        )
    )
    assert data.geo_info[1]["isp_name"] == "Kcom"
    assert data.geo_info[2]["isp_name"] == "Vodafone"


def test_blank_isp_strings_are_treated_as_absent():
    data = extract_wan_data(gateway_payload(geo_info={"WAN": {"isp_name": "   "}}))
    assert data.geo_info[1]["isp_name"] is None


# ------------------------------------------------------ per-WAN history


def test_history_keeps_the_newest_per_wan():
    parsed = parse_speedtest_history(
        history(
            record("WAN", 1_700_000_000_000, down=100),
            record("WAN", 1_700_000_900_000, down=300),
            record("WAN2", 1_700_000_500_000, down=50),
        ),
        {1: {}, 2: {}},
    )
    assert parsed[1]["down"] == 300
    assert parsed[1]["lastrun"] == 1_700_000_900
    assert parsed[2]["down"] == 50


def test_history_ignores_records_with_no_figures():
    parsed = parse_speedtest_history(
        {"data": [{"wan_networkgroup": "WAN", "time": 1_700_000_000_000}]}, {1: {}}
    )
    assert parsed == {}


def test_history_tolerates_junk():
    assert parse_speedtest_history(None, {}) == {}
    assert parse_speedtest_history({"data": "nope"}, {}) == {}


# --------------------------------------------------- attributing the block


def _two_wan(**kwargs):
    return extract_wan_data(
        gateway_payload(
            uplink={"up": True, "ip": "203.0.113.1"},
            wan={"ip": "203.0.113.1", "ifname": "eth8"},
            wan2={"ip": "198.51.100.7", "ifname": "eth9"},
            **kwargs,
        )
    )


def test_the_server_is_only_claimed_on_hard_evidence():
    named = _two_wan(
        speedtest_status={"xput_download": 1, "rundate": 10, "source_interface": "eth9"}
    )
    assert gateway_speedtest_wan(named) == 2
    # No interface named, and more than one WAN: nobody may claim it.
    unnamed = _two_wan(speedtest_status={"xput_download": 1, "rundate": 10})
    assert gateway_speedtest_wan(unnamed) is None


def test_throughput_falls_back_to_the_active_wan():
    data = _two_wan(speedtest_status={"xput_download": 1, "rundate": 10})
    assert gateway_result_wan(data, data.active_wan[0]) == 1


def test_a_matching_per_wan_record_stops_the_active_wan_claiming_the_block():
    data = _two_wan(speedtest_status={"xput_download": 1, "rundate": 1_700_000_000})
    # WAN2 recorded a run at the same moment, so the block is WAN2's.
    data.per_wan_speedtest = {2: {"lastrun": 1_700_000_030}}
    assert gateway_result_wan(data, 1) is None


def test_a_distant_per_wan_record_does_not_block_attribution():
    data = _two_wan(speedtest_status={"xput_download": 1, "rundate": 1_700_000_000})
    data.per_wan_speedtest = {2: {"lastrun": 1_699_000_000}}
    assert gateway_result_wan(data, 1) == 1


def test_single_wan_owns_its_block_outright():
    data = extract_wan_data(
        gateway_payload(
            wan={"ip": "203.0.113.1"},
            speedtest_status={"xput_download": 1, "rundate": 10},
        )
    )
    assert gateway_speedtest_wan(data) == 1
    assert attribution_source(data, 1) == "only_wan"


def test_attribution_source_names_the_evidence():
    data = _two_wan(
        speedtest_status={"xput_download": 1, "rundate": 10, "source_interface": "eth9"}
    )
    assert attribution_source(data, 2) == "source_interface"
    assert attribution_source(data, 1) == "active_wan"


def test_attributed_server_carries_forward_rather_than_blanking():
    previous = {
        "server_provider": "Exascale",
        "server_city": None,
        "server_country": None,
        "server_provider_url": None,
    }
    empty = dict.fromkeys(previous)
    assert attributed_server(empty, True, previous)["server_provider"] == "Exascale"


def test_attributed_server_never_takes_another_wans_server():
    theirs = {
        "server_provider": "Somebody Else",
        "server_city": None,
        "server_country": None,
        "server_provider_url": None,
    }
    assert attributed_server(theirs, False, None)["server_provider"] is None


# ------------------------------------------------------------- rate parsing


def test_rates_parse_stops_at_the_uplink():
    rates = extract_rates(
        gateway_payload(
            uplink={"up": True, "rx_bytes-r": 1_250_000, "tx_bytes-r": 125_000},
            wan={"ip": "203.0.113.1"},
            geo_info={"WAN": {"isp_name": "Kcom"}},
        )
    )
    assert rates.uplink["rx_bytes-r"] == 1_250_000
    # The expensive parts are not done on a poll that runs every second.
    assert rates.wan == {}
    assert rates.geo_info == {}
    assert rates.speedtest == {}


def test_parsing_an_empty_payload_is_safe():
    data = extract_wan_data(None)
    assert data.gateway is None
    assert data.wan == {}
    assert data.active_wan == (None, "no_match")
