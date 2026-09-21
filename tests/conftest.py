"""Shared test setup.

The integration lives under custom_components/, which is not a package on
the path, so it is added here. Nothing else is needed: const.py and
models.py import no Home Assistant at all, and the modules that do are
exercised with plain fakes rather than a running core.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))


@pytest.fixture
def dispatched(monkeypatch) -> list[str]:
    """Record the signals the speedtest manager sends."""
    from unifi_wan import speedtest as speedtest_module

    sent: list[str] = []
    monkeypatch.setattr(
        speedtest_module,
        "async_dispatcher_send",
        lambda _hass, signal, *args: sent.append(signal),
    )
    return sent
