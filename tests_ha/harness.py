"""Config entry helpers shared by the real-core tests."""

from __future__ import annotations

from typing import Any

from console import API_KEY, HOST, SITE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "unifi_wan"


def entry_data(**overrides: Any) -> dict[str, Any]:
    """What the user step stores for a console at HOST."""
    data = {
        "host": HOST,
        "api_key": API_KEY,
        "site": SITE,
        "verify_ssl": False,
        # Off by default here so a test that does not care about the
        # schedule does not carry a timer; the tests about it turn it on.
        "auto_speedtest": False,
        "auto_speedtest_minutes": 60,
    }
    data.update(overrides)
    return data


def make_entry(
    hass: HomeAssistant,
    *,
    data: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    unique_id: str | None = None,
    entry_id: str | None = None,
) -> MockConfigEntry:
    data = data or entry_data()
    kwargs: dict[str, Any] = {}
    if entry_id is not None:
        kwargs["entry_id"] = entry_id
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"UniFi WAN ({data['host']})",
        data=data,
        options=options or {},
        unique_id=unique_id or f"{data['host']}-{data['site']}",
        **kwargs,
    )
    entry.add_to_hass(hass)
    return entry


async def setup_entry(
    hass: HomeAssistant, entry: MockConfigEntry, *, expect: bool = True
) -> MockConfigEntry:
    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert result is expect, entry.state
    return entry
