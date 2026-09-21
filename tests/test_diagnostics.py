"""Redaction: what a diagnostics file may and may not carry.

The file exists to be attached to a public issue, so the interesting
cases are the ones where a field nobody has seen before holds something
identifying, and the ones where over-redaction would destroy the answer
the bug report needs.
"""

from __future__ import annotations

import pytest
from unifi_wan.diagnostics import REDACTED, _looks_identifying, _redact


def test_known_keys_are_redacted():
    out = _redact({"api_key": "s3cret", "mac": "aa:bb:cc:dd:ee:ff"})
    assert out == {"api_key": REDACTED, "mac": REDACTED}


def test_suffix_rules_catch_unlisted_keys():
    out = _redact({"native_networkconf_id": "abc", "gw_mac": "x", "last_wan_ip": "y"})
    assert set(out.values()) == {REDACTED}


@pytest.mark.parametrize(
    "value",
    [
        "203.0.113.7",
        "192.168.1.1/24",
        "aa:bb:cc:dd:ee:ff",
        "AA-BB-CC-DD-EE-FF",
        "2001:db8::1",
        "fe80::1%eth0",
        "2001:0db8:0000:0000:0000:0000:0000:0001",
        "a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        "someone@example.com",
    ],
)
def test_identifying_shapes_are_recognised(value):
    assert _looks_identifying(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "UDMPRO",
        "eth8",
        "ppp0",
        "WAN2",
        "connected",
        "12:34:56",
        "Virgin Fibre",
        "United Kingdom",
        "",
        "1.2.3.4444",
        "999.1.1.1",
    ],
)
def test_harmless_values_are_left_alone(value):
    assert _looks_identifying(value) is False


def test_a_dotted_quad_version_looks_like_an_address_on_its_own():
    """Two layers, and this is the seam between them: the shape check says
    "6.5.55.0" could be an address, and only the key it arrived under
    decides that it is a firmware version. See the test below.
    """
    assert _looks_identifying("6.5.55.0") is True


def test_an_unknown_field_holding_an_address_is_still_redacted():
    """The drift case: firmware adds a field, no list here knows its name."""
    out = _redact({"brand_new_firmware_field": "203.0.113.7"})
    assert out["brand_new_firmware_field"] == REDACTED


def test_an_unknown_field_holding_a_mac_is_still_redacted():
    out = _redact({"peer": "aa:bb:cc:dd:ee:ff"})
    assert out["peer"] == REDACTED


def test_version_strings_survive_looking_like_addresses():
    """A dotted-quad firmware version is exactly what a bug report needs."""
    out = _redact(
        {"version": "6.5.55.0", "sw_version": "4.0.6", "board_rev": "1.2.3.4"}
    )
    assert out == {"version": "6.5.55.0", "sw_version": "4.0.6", "board_rev": "1.2.3.4"}


def test_the_fields_the_wan_logic_turns_on_are_untouched():
    payload = {
        "ifname": "eth8",
        "port_idx": 9,
        "up": True,
        "enable": True,
        "xput_download": 512.5,
        "latency": 11,
        "rundate": 1_700_000_000,
        "wan_networkgroup": "WAN2",
        "source_interface": "if!eth8",
        "physical_ports": [9],
        "country_name": "United Kingdom",
    }
    assert _redact(payload) == payload


def test_nulls_and_blanks_are_preserved():
    """Whether the controller populated a field is itself the finding."""
    out = _redact({"ip": None, "isp_name": "", "mac": None})
    assert out == {"ip": None, "isp_name": "", "mac": None}


def test_nested_structures_are_walked():
    out = _redact(
        {
            "uplink": {"ip": "203.0.113.1", "up": True},
            "geo_info": {"WAN": {"isp_name": "Kcom", "country_name": "UK"}},
        }
    )
    assert out["uplink"] == {"ip": REDACTED, "up": True}
    assert out["geo_info"]["WAN"]["isp_name"] == REDACTED
    assert out["geo_info"]["WAN"]["country_name"] == "UK"


def test_a_list_of_addresses_under_an_unknown_key_is_redacted():
    out = _redact({"resolvers_we_have_never_seen": ["1.1.1.1", "2001:db8::1"]})
    assert out["resolvers_we_have_never_seen"] == [REDACTED, REDACTED]


def test_a_list_of_harmless_values_survives():
    out = _redact({"physical_ports": [9, 10], "modes": ["auto", "manual"]})
    assert out == {"physical_ports": [9, 10], "modes": ["auto", "manual"]}


def test_a_list_of_devices_is_walked():
    out = _redact({"devices": [{"mac": "aa:bb:cc:dd:ee:ff", "type": "udm"}]})
    assert out["devices"][0] == {"mac": REDACTED, "type": "udm"}


def test_non_string_values_are_never_shape_matched():
    out = _redact({"count": 19216811, "ratio": 1.2345, "flag": False})
    assert out == {"count": 19216811, "ratio": 1.2345, "flag": False}
