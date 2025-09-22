from __future__ import annotations
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_HOST, CONF_SITE, DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    shared = hass.data[DOMAIN][entry.entry_id]
    meta = shared.get("dev_meta", {})  # <-- NEW
    host = entry.options.get(CONF_HOST, entry.data.get(CONF_HOST)) or "unknown"
    site = entry.options.get(CONF_SITE, entry.data.get(CONF_SITE, "default")) or "default"
    devname = f"UniFi WAN ({host} / {site})"
    async_add_entities([UniFiAutoSpeedtestSwitch(shared, host, site, devname, meta)])


class UniFiAutoSpeedtestSwitch(SwitchEntity):
    _attr_name = "UniFi Auto Speedtest Enabled"
    _attr_icon = "mdi:speedometer-slow"

    def __init__(self, shared, host: str, site: str, devname: str, meta: dict[str, Any]):
        self._shared = shared
        self._host = host
        self._site = site
        self._devname = devname
        self._meta = meta or {}

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_auto_speedtest_enabled"

    @property
    def device_info(self):
        info = {
            "identifiers": {(DOMAIN, self._host, self._site)},
            "name": self._devname,
            "manufacturer": "Ubiquiti",
            "model": self._meta.get("model") or "UDM/UGW",
            "configuration_url": f"https://{self._host}/",
        }
        sw = self._meta.get("sw_version")
        if sw:
            info["sw_version"] = sw
        mac = (self._meta.get("mac") or "").upper()
        if mac:
            info["connections"] = {("mac", mac)}
        return info

    @property
    def is_on(self) -> bool:
        return bool(self._shared.get("auto_enabled", False))

    async def async_turn_on(self, **kwargs):
        self._shared["enable_auto"]()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._shared["disable_auto"]()
        self.async_write_ha_state()
