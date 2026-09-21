"""The connectivity rule shared by the gateway-wide and per-WAN sensors."""

from __future__ import annotations

import pytest
from unifi_wan.binary_sensor import any_wan_has_internet, wan_has_internet
from unifi_wan.models import UniFiWanData


def data(wan: dict, alive: dict, uplink: dict | None = None) -> UniFiWanData:
    return UniFiWanData(
        devices=[],
        gateway={},
        uplink=uplink or {},
        wan=wan,
        wan_alive=alive,
        wan_status={},
        speedtest={},
    )


@pytest.mark.parametrize(
    ("section", "alive", "expected", "why"),
    [
        ({"up": True}, {1: True}, True, "linked and alive"),
        ({"up": False, "ip": "1.2.3.4"}, {1: True}, False, "unplugged, alive is stale"),
        ({"up": True}, {1: False}, False, "linked but not alive"),
        ({"up": True, "ip": "1.2.3.4"}, {}, True, "no alive flag, but has an address"),
        ({"up": True}, {}, False, "no alive flag and no address"),
        ({"ip": "1.2.3.4"}, {1: True}, True, "no link state reported, so trust alive"),
    ],
)
def test_one_wan(section, alive, expected, why):
    assert wan_has_internet(data({1: section}, alive), 1) is expected, why


def test_the_gateway_wide_sensor_applies_the_same_guard():
    """It used to trust the alive flag alone and could read on with every
    WAN down, contradicting all of its own per-WAN sensors.
    """
    d = data({1: {"up": False}, 2: {"up": False}}, {1: True, 2: True})
    assert any_wan_has_internet(d) is False
    assert [wan_has_internet(d, n) for n in (1, 2)] == [False, False]


def test_one_good_line_is_enough():
    d = data({1: {"up": False}, 2: {"up": True}}, {1: True, 2: True})
    assert any_wan_has_internet(d) is True


def test_no_wan_sections_falls_back_to_the_uplink():
    assert any_wan_has_internet(data({}, {}, uplink={"up": True})) is True
    assert any_wan_has_internet(data({}, {}, uplink={"up": False})) is False
