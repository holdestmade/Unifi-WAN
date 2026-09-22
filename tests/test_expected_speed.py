"""Comparing a speedtest against what the line is sold as."""

from __future__ import annotations

import pytest
from helpers import gateway_payload
from unifi_wan.const import (
    MAX_EXPECTED_SPEED,
    SPEED_COMPARISON_OPTIONS,
    SPEED_TOLERANCE,
)
from unifi_wan.models import (
    expected_speed,
    extract_wan_data,
    speed_comparison,
    speed_tolerance,
)
from unifi_wan.sensor import _expected_speed_descriptions

# ---------------------------------------------------- reading the option


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (500, 500.0),
        ("500", 500.0),
        (67.5, 67.5),
        (0, 0.0),
        (-10, 0.0),
        (None, 0.0),
        ("nonsense", 0.0),
        ("", 0.0),
        (MAX_EXPECTED_SPEED + 1000, MAX_EXPECTED_SPEED),
    ],
)
def test_the_option_is_normalised(stored, expected):
    assert expected_speed(stored) == expected


# ------------------------------------------------------- the comparison


@pytest.mark.parametrize(
    ("measured", "verdict"),
    [
        (500.0, "Expected"),  # exactly the figure
        (510.0, "Expected"),  # exactly 2% over, still met
        (490.0, "Expected"),  # exactly 2% under, still met
        (509.9, "Expected"),
        (490.1, "Expected"),
        (510.1, "Faster"),
        (600.0, "Faster"),
        (489.9, "Slower"),
        (100.0, "Slower"),
        (0.0, "Slower"),
        # Inside the old 5% band but outside the current 2% one, which is
        # the whole point of having narrowed it.
        (520.0, "Faster"),
        (480.0, "Slower"),
    ],
)
def test_the_tolerance_band(measured, verdict):
    """The boundary counts as met: 2% down is the line still delivering."""
    assert speed_comparison(measured, 500.0) == verdict


def test_every_verdict_is_one_the_sensor_declares():
    for measured in (100.0, 500.0, 900.0):
        assert speed_comparison(measured, 500.0) in SPEED_COMPARISON_OPTIONS


@pytest.mark.parametrize(
    ("measured", "expected"),
    [
        (None, 500.0),  # no speedtest result yet
        (500.0, 0),  # option left unset
        (500.0, None),
        (500.0, -5),
        ("nonsense", 500.0),
        (500.0, "nonsense"),
    ],
)
def test_nothing_to_compare_is_unknown(measured, expected):
    assert speed_comparison(measured, expected) is None


def test_the_band_matches_the_configured_tolerance():
    """Guards against the constant and the tests drifting apart."""
    from unifi_wan.const import SPEED_TOLERANCE

    edge = 500.0 * (1 + SPEED_TOLERANCE)
    assert speed_comparison(edge, 500.0) == "Expected"
    assert speed_comparison(edge + 0.1, 500.0) == "Faster"


def test_the_tolerance_is_adjustable():
    assert speed_comparison(510.0, 500.0, tolerance=0.01) == "Faster"
    assert speed_comparison(510.0, 500.0, tolerance=0.5) == "Expected"


def test_a_slow_line_is_judged_on_its_own_scale():
    """2% of 10 Mbit/s is a fifth of a megabit, not 10."""
    assert speed_comparison(10.2, 10.0) == "Expected"
    assert speed_comparison(10.3, 10.0) == "Faster"


# ----------------------------------------------------------- the sensors


def _data(down=None, up=None):
    status = {"rundate": 1_700_000_000}
    if down is not None:
        status["xput_download"] = down
    if up is not None:
        status["xput_upload"] = up
    data = extract_wan_data(
        gateway_payload(
            uplink={"up": True, "ip": "203.0.113.1"},
            wan={"ip": "203.0.113.1", "ifname": "eth8"},
            speedtest_status=status,
        )
    )
    data.speedtest_latched = {}
    return data


def _by_key(expected_download=500.0, expected_upload=50.0):
    return {
        d.key: d
        for d in _expected_speed_descriptions(expected_download, expected_upload)
    }


def test_four_sensors_are_created():
    keys = sorted(_by_key())
    assert keys == [
        "isp_down_vs_expected",
        "isp_expected_down",
        "isp_expected_up",
        "isp_up_vs_expected",
    ]


def test_the_sensors_are_named_as_asked():
    names = sorted(d.name for d in _expected_speed_descriptions(500.0, 50.0))
    assert names == [
        "UniFi WAN ISP Download Speed Status",
        "UniFi WAN ISP Expected Download Speed",
        "UniFi WAN ISP Expected Upload Speed",
        "UniFi WAN ISP Upload Speed Status",
    ]


def test_the_expected_sensors_report_the_configured_figure():
    by = _by_key(500.0, 50.0)
    data = _data(down=1.0, up=1.0)
    assert by["isp_expected_down"].value_fn(data) == 500.0
    assert by["isp_expected_up"].value_fn(data) == 50.0


def test_the_expected_sensors_are_unknown_when_unset():
    by = _by_key(0.0, 0.0)
    data = _data(down=1.0, up=1.0)
    assert by["isp_expected_down"].value_fn(data) is None
    assert by["isp_expected_up"].value_fn(data) is None


def test_the_sensors_exist_even_with_nothing_configured():
    """Filling the option in later must not change which entities exist."""
    assert len(_expected_speed_descriptions(0.0, 0.0)) == 4


def test_the_comparison_reads_the_speedtest_result():
    by = _by_key(500.0, 50.0)
    # 505 is +1% and 47 is -6%, either side of the 2% band.
    data = _data(down=505.0, up=47.0)
    assert by["isp_down_vs_expected"].value_fn(data) == "Expected"
    assert by["isp_up_vs_expected"].value_fn(data) == "Slower"


def test_the_comparison_is_unknown_without_a_result():
    by = _by_key(500.0, 50.0)
    assert by["isp_down_vs_expected"].value_fn(_data()) is None


def test_the_comparison_is_unknown_without_an_expected_figure():
    by = _by_key(0.0, 0.0)
    assert by["isp_down_vs_expected"].value_fn(_data(down=512.0)) is None


def test_the_comparison_shows_its_arithmetic():
    from unifi_wan.const import SPEED_TOLERANCE

    by = _by_key(500.0, 50.0)
    attrs = by["isp_down_vs_expected"].attributes_fn(_data(down=550.0))
    assert attrs["expected_mbps"] == 500.0
    assert attrs["measured_mbps"] == 550.0
    assert attrs["difference_mbps"] == 50.0
    assert attrs["difference_percent"] == 10.0
    # Read from the constant, so the attribute cannot disagree with the
    # band the state was decided by.
    assert attrs["tolerance_percent"] == SPEED_TOLERANCE * 100


def test_the_attributes_survive_having_nothing_to_compare():
    by = _by_key(0.0, 0.0)
    attrs = by["isp_down_vs_expected"].attributes_fn(_data())
    assert attrs["expected_mbps"] is None
    assert attrs["measured_mbps"] is None
    assert "difference_mbps" not in attrs


def test_the_comparison_sensors_declare_their_states():
    for key in ("isp_down_vs_expected", "isp_up_vs_expected"):
        assert _by_key()[key].options == SPEED_COMPARISON_OPTIONS


def test_the_new_keys_do_not_collide_with_existing_sensors():
    from unifi_wan.sensor import SENSORS

    existing = {d.key for d in SENSORS}
    assert existing.isdisjoint(_by_key())


# ------------------------------------------------- the tolerance option


@pytest.mark.parametrize(
    ("stored", "fraction"),
    [
        (2, 0.02),
        (2.0, 0.02),
        ("2.5", 0.025),
        (0, 0.0),  # only an exact match counts, which is a real choice
        (5, 0.05),
        (-1, 0.0),  # clamped to the floor
        (500, 0.5),  # clamped to the ceiling
        (None, 0.02),  # unreadable falls back to the default, never to zero
        ("nonsense", 0.02),
    ],
)
def test_the_tolerance_option_is_normalised(stored, fraction):
    assert speed_tolerance(stored) == pytest.approx(fraction)


def test_an_unreadable_tolerance_does_not_become_zero():
    """Zero would make every result Faster or Slower, which is the worst
    thing a mangled option could quietly do.
    """
    assert speed_tolerance("") == pytest.approx(SPEED_TOLERANCE)


def test_a_configured_tolerance_reaches_the_sensors():
    by = {
        d.key: d for d in _expected_speed_descriptions(500.0, 50.0, speed_tolerance(10))
    }
    data = _data(down=540.0, up=1.0)
    # 540 is +8%, inside a 10% band but outside the 2% default.
    assert by["isp_down_vs_expected"].value_fn(data) == "Expected"
    assert by["isp_down_vs_expected"].attributes_fn(data)["tolerance_percent"] == 10.0


def test_a_zero_tolerance_demands_an_exact_match():
    by = {
        d.key: d for d in _expected_speed_descriptions(500.0, 50.0, speed_tolerance(0))
    }
    assert by["isp_down_vs_expected"].value_fn(_data(down=500.0)) == "Expected"
    assert by["isp_down_vs_expected"].value_fn(_data(down=500.1)) == "Faster"
    assert by["isp_down_vs_expected"].value_fn(_data(down=499.9)) == "Slower"


def test_the_sensors_use_the_default_when_not_told_otherwise():
    by = {d.key: d for d in _expected_speed_descriptions(500.0, 50.0)}
    attrs = by["isp_down_vs_expected"].attributes_fn(_data(down=500.0))
    assert attrs["tolerance_percent"] == SPEED_TOLERANCE * 100
