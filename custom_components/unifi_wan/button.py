from __future__ import annotations
import asyncio
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_HOST, CONF_SITE


def _pick_gateway(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    for t in ("udm", "ugw"):
        for dev in data:
            if isinstance(dev, dict) and dev.get("type") == t:
                return dev
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    shared = hass.data[DOMAIN][entry.entry_id]
    device_coord = shared["device_coordinator"]
    meta = shared.get("dev_meta", {})
    host = entry.options.get(CONF_HOST, entry.data.get(CONF_HOST)) or "unknown"
    site = entry.options.get(CONF_SITE, entry.data.get(CONF_SITE, "default")) or "default"
    devname = f"UniFi WAN ({host} / {site})"

    async_add_entities([RunSpeedtestButton(device_coord, shared, host, site, devname, meta)])


class RunSpeedtestButton(CoordinatorEntity, ButtonEntity):
    _attr_name = "UniFi Run Speedtest Now"
    _attr_icon = "mdi:speedometer"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, shared, host: str, site: str, devname: str, meta: dict[str, Any]):
        super().__init__(coordinator)
        self._shared = shared
        self._host = host
        self._site = site
        self._devname = devname
        self._meta = meta or {}
        self._lock = asyncio.Lock()

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_run_speedtest"

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

    async def async_press(self) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            runner = self._shared.get("run_speedtest_now")
            if callable(runner):
                await runner()
            else:
                gw = _pick_gateway(self.coordinator.data)
                if not gw:
                    return
                mac = gw.get("mac")
                if not mac:
                    return
                await self._shared["set_speedtest_running"](True)
                try:
                    await self._shared["client"].run_speedtest(mac)
                    await asyncio.sleep(8)
                    await self.coordinator.async_request_refresh()
                finally:
                    await self._shared["set_speedtest_running"](False)
