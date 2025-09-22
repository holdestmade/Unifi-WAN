from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    shared = hass.data[DOMAIN][entry.entry_id]
    host = entry.data.get("host")
    async_add_entities([UniFiAutoSpeedtestSwitch(shared, host)])


class UniFiAutoSpeedtestSwitch(SwitchEntity):
    _attr_name = "UniFi Auto Speedtest Enabled"
    _attr_icon = "mdi:speedometer-slow"

    def __init__(self, shared, host):
        self._shared = shared
        self._host = host

    @property
    def unique_id(self):
        return f"{self._host}_auto_speedtest_enabled"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._host)},
            "name": f"UniFi WAN ({self._host})",
            "manufacturer": "Ubiquiti",
            "model": "UDM/UGW",
            "configuration_url": f"https://{self._host}/",
        }

    @property
    def is_on(self) -> bool:
        return bool(self._shared.get("auto_enabled", False))

    async def async_turn_on(self, **kwargs):
        self._shared["enable_auto"]()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._shared["disable_auto"]()
        self.async_write_ha_state()
