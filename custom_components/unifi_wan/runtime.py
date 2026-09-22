"""What one configured gateway carries at runtime.

Stored on the config entry as ``entry.runtime_data`` rather than in
``hass.data``: the objects belong to the entry, are created with it and
die with it, and the typed entry alias below gives the platforms attribute
access with no string keys and no lookups that can miss.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .api import UnifiWanClient
from .coordinator import UniFiWanCoordinator, UniFiWanRatesCoordinator
from .speedtest import SpeedtestManager


@dataclass
class UniFiWanRuntimeData:
    """Per-config-entry runtime objects shared with the platform entities."""

    client: UnifiWanClient
    device_coordinator: UniFiWanCoordinator
    rates_coordinator: UniFiWanRatesCoordinator | None
    speedtest: SpeedtestManager
    host: str
    site: str
    dev_meta: dict[str, Any]
    device_info: DeviceInfo
    wan_numbers: list[int]
    # What the line is sold as, in Mbit/s, for the comparison sensors.
    # Zero means the option was left unset.
    expected_download: float
    expected_upload: float
    # How far either way still counts as meeting the figure, as a
    # fraction. Configured as a percentage.
    speed_tolerance: float
    reload_signature: dict[str, Any]


# Subscripted only for type checkers. ConfigEntry became generic in
# Home Assistant 2024.6; subscripting it at runtime would make this module
# unimportable on anything older, including the linters and test runners
# that never construct one.
if TYPE_CHECKING:
    UniFiWanConfigEntry = ConfigEntry[UniFiWanRuntimeData]
else:
    UniFiWanConfigEntry = ConfigEntry
