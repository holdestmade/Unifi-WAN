"""Harness for running the integration inside a real Home Assistant core.

Unlike tests/, which exercises the parsing and the speedtest state machine
with plain fakes, everything here goes through pytest-homeassistant-custom-
component's ``hass`` fixture: config entries are set up by Home Assistant's
own config entry manager, flows run through its flow manager and HTTP API,
entities are added by real entity platforms and the console is reached
through the aiohttp session Home Assistant hands the integration.

The integration is imported as ``custom_components.unifi_wan`` here, the way
Home Assistant's loader imports it. tests/ imports the same files as a
top-level ``unifi_wan`` package; the two never share module objects, so a
patch applied here must name the ``custom_components`` path.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from console import MockConsole  # noqa: E402
from harness import DOMAIN  # noqa: E402

# Imported before the hass fixture exists. Home Assistant mounts its config
# directory and imports "custom_components" from there unless the name is
# already taken, and the harness's config directory carries a package of
# that name - which would hide this repository's integration entirely.
import custom_components  # noqa: E402, F401


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let the loader find custom_components/unifi_wan."""


@pytest.fixture
def console(aioclient_mock: AiohttpClientMocker) -> MockConsole:
    """The console at HOST, answering for SITE."""
    mock = MockConsole()
    mock.register(aioclient_mock)
    return mock


@pytest.fixture
def fast_speedtest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the speedtest wait from five minutes to a fraction of a second.

    The loop sleeps SPEEDTEST_POLL_SECONDS between polls and gives up after
    SPEEDTEST_TIMEOUT_SECONDS of loop time, both read from the module's own
    namespace, so patching them there is enough.
    """
    from custom_components.unifi_wan import speedtest

    monkeypatch.setattr(speedtest, "SPEEDTEST_POLL_SECONDS", 0.01)
    monkeypatch.setattr(speedtest, "SPEEDTEST_TIMEOUT_SECONDS", 0.3)


@pytest.fixture(autouse=True)
async def unload_entries_at_teardown(hass: HomeAssistant) -> AsyncGenerator[None]:
    """Unload whatever a test left loaded, so no poll or schedule outlives it."""
    yield
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
