"""Writing and pruning the unredacted dumps."""

from __future__ import annotations

import json
from datetime import datetime

from helpers import gateway_payload, make_client
from unifi_wan.dump import (
    WITHHELD,
    _capture,
    _filename,
    _prefix,
    _safe,
    _snapshot,
    _write_and_prune,
)
from unifi_wan.models import extract_wan_data


def test_filenames_sort_chronologically():
    """Pruning relies on a lexical sort being a chronological one."""
    prefix = _prefix("default", "abcdef1234567890")
    names = [
        _filename(prefix, datetime(2026, 1, 2, 3, 4, 5)),
        _filename(prefix, datetime(2025, 12, 31, 23, 59, 59)),
        _filename(prefix, datetime(2026, 1, 2, 3, 4, 6)),
    ]
    assert sorted(names) == [names[1], names[0], names[2]]


def test_site_names_are_made_filename_safe():
    assert "/" not in _safe("some/site name")
    assert _safe(None) == "unknown"
    assert len(_safe("x" * 200)) <= 40


def test_the_oldest_dumps_are_pruned(tmp_path):
    prefix = _prefix("default", "entry123")
    for day in range(1, 6):
        path = tmp_path / _filename(prefix, datetime(2026, 1, day))
        _write_and_prune(path, {"day": day}, prefix, keep=3)
    kept = sorted(p.name for p in tmp_path.iterdir())
    assert len(kept) == 3
    # The three most recent survive.
    assert json.loads((tmp_path / kept[-1]).read_text())["day"] == 5


def test_another_entrys_dumps_are_not_evicted(tmp_path):
    mine = _prefix("default", "entry111")
    theirs = _prefix("default", "entry222")
    for day in range(1, 4):
        _write_and_prune(
            tmp_path / _filename(theirs, datetime(2026, 1, day)),
            {"d": day},
            theirs,
            keep=10,
        )
    for day in range(1, 6):
        _write_and_prune(
            tmp_path / _filename(mine, datetime(2026, 1, day)), {"d": day}, mine, keep=2
        )
    names = [p.name for p in tmp_path.iterdir()]
    assert sum(n.startswith(theirs) for n in names) == 3
    assert sum(n.startswith(mine) for n in names) == 2


def test_a_value_json_cannot_represent_still_lands_in_the_file(tmp_path):
    prefix = _prefix("default", "entry123")
    path = tmp_path / _filename(prefix, datetime(2026, 1, 1))
    size = _write_and_prune(path, {"when": datetime(2026, 1, 1)}, prefix, keep=1)
    assert size > 0
    assert "2026-01-01" in path.read_text()


def test_the_directory_is_created_on_demand(tmp_path):
    prefix = _prefix("default", "entry123")
    path = tmp_path / "nested" / "deeper" / _filename(prefix, datetime(2026, 1, 1))
    _write_and_prune(path, {"ok": True}, prefix, keep=1)
    assert path.exists()


def test_the_api_key_placeholder_cannot_read_as_missing_data():
    """A dump is unredacted; the one held-back field must say so plainly."""
    assert "WITHHELD" in WITHHELD
    assert "not controller data" in WITHHELD


# ------------------------------------------------------- the parsed snapshot


def _data_with_devices(count: int):
    payload = gateway_payload(wan={"ip": "203.0.113.1", "ifname": "eth8"})
    payload["data"].extend(
        {"type": "uap", "model": f"U6-{n}", "port_table": [{"port_idx": n}]}
        for n in range(count)
    )
    return extract_wan_data(payload)


def test_the_snapshot_leaves_out_the_device_list():
    """It is already in the stat/device capture verbatim, and copying it was
    the most expensive thing this service did.
    """
    snapshot = _snapshot(_data_with_devices(50))
    assert "devices" not in snapshot
    assert snapshot["device_count"] == 51


def test_the_snapshot_keeps_the_gateway_and_the_parsed_fields():
    snapshot = _snapshot(_data_with_devices(2))
    assert snapshot["gateway"]["model"] == "UDMPRO"
    assert snapshot["wan"][1]["ifname"] == "eth8"
    for field in ("uplink", "wan_alive", "wan_status", "speedtest", "geo_info"):
        assert field in snapshot


def test_the_snapshot_does_not_alias_live_data():
    """The JSON is written in an executor thread while the event loop keeps
    running, so serialising live objects would race the next poll.
    """
    data = _data_with_devices(1)
    data.speedtest_latched = {1: {"down": 100.0}}
    snapshot = _snapshot(data)

    data.wan[1]["ifname"] = "changed"
    data.speedtest_latched[2] = {"down": 999.0}
    data.uplink["ip"] = "changed"

    assert snapshot["wan"][1]["ifname"] == "eth8"
    assert snapshot["speedtest_latched"] == {1: {"down": 100.0}}
    assert snapshot["uplink"]["ip"] == "203.0.113.1"


def test_the_snapshot_survives_an_empty_payload():
    snapshot = _snapshot(extract_wan_data(None))
    assert snapshot["device_count"] == 0
    assert snapshot["gateway"] is None


# ----------------------------------------------------------- what is fetched


class _Runtime:
    """Enough of UniFiWanRuntimeData for _capture."""

    def __init__(self, client, mac: str | None = "aa:bb:cc:dd:ee:ff") -> None:
        self.client = client
        self.dev_meta = {"mac": mac}


async def test_every_endpoint_is_captured_with_its_status():
    """A dump's job is to answer "what does this console actually say", so
    each endpoint is recorded with the status code alongside the body.
    """
    client = make_client()
    endpoints = await _capture(_Runtime(client))

    assert set(endpoints) == {
        "stat_device",
        "v2_speedtest",
        "port_forwards",
        "stat_device_gateway",
    }
    assert all(capture["status"] == 200 for capture in endpoints.values())


async def test_the_forwarding_rules_are_asked_for_on_the_classic_api():
    """The rules live on the classic REST path, not the v2 API."""
    client = make_client()
    await _capture(_Runtime(client))

    urls = [url for _, url, _ in client._session.calls]
    assert "https://10.0.0.1/proxy/network/api/s/default/rest/portforward" in urls


async def test_a_console_that_refuses_the_rules_still_yields_a_dump():
    """Whether a local API key may read the forwarding rules differs by
    console, and the refusal is itself the finding - so it is recorded
    rather than allowed to sink the endpoints that did answer.
    """
    # stat_device, v2_speedtest, port_forwards, then the gateway-only path:
    # the order BASE_ENDPOINTS is gathered in.
    client = make_client([(200, {"data": []}), (200, {"data": []}), (403, None)])
    endpoints = await _capture(_Runtime(client))

    assert endpoints["port_forwards"]["status"] == 403
    assert endpoints["stat_device"]["status"] == 200
    assert endpoints["stat_device_gateway"]["status"] == 200


async def test_the_gateway_endpoint_says_why_it_was_skipped():
    """Without a MAC there is no per-device URL to call, and a missing key
    would read as the endpoint having failed.
    """
    endpoints = await _capture(_Runtime(make_client(), mac=None))
    assert "skipped" in endpoints["stat_device_gateway"]
