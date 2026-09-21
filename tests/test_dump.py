"""Writing and pruning the unredacted dumps."""

from __future__ import annotations

import json
from datetime import datetime

from unifi_wan.dump import WITHHELD, _filename, _prefix, _safe, _write_and_prune


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
