from __future__ import annotations
import asyncio
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_HOST


def _get_gateway(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not data or "data" not in data or not isinstance(data["data"], list):
        return None
    for t in ("udm", "ugw"):
        for dev in data["data"]:
            if isinstance(dev, dict) and dev.get("type") == t:
                return dev
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    shared = hass.data[DOMAIN][entry.entry_id]
    device_coord = shared["device_coordinator"]
    client = shared["client"]
    host = entry.options.get(CONF_HOST, entry.data.get(CONF_HOST)) or "unknown"

    async_add_entities([RunSpeedtestButton(device_coord, client, host)])


class RunSpeedtestButton(CoordinatorEntity, ButtonEntity):
    _attr_name = "UniFi Run Speedtest Now"
    _attr_icon = "mdi:speedometer"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, client, host):
        super().__init__(coordinator)
        self._client = client
        self._host = host
        self._lock = asyncio.Lock()

    @property
    def unique_id(self):
        return f"{self._host}_run_speedtest"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._host)},
            "name": f"UniFi WAN ({self._host})",
            "manufacturer": "Ubiquiti",
            "model": "UDM/UGW",
            "configuration_url": f"https://{self._host}/",
        }

    async def async_press(self) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            shared = self.hass.data[DOMAIN][self.coordinator.config_entry.entry_id]
            runner = shared.get("run_speedtest_now")
            if callable(runner):
                await runner()
                return

            await shared["set_speedtest_running"](True)
            try:
                gw = _get_gateway(self.coordinator.data)
                if not gw:
                    raise RuntimeError("No gateway device found in /stat/device payload")
                mac = gw.get("mac")
                if not mac:
                    raise RuntimeError("Gateway MAC not found")
                await self._client.run_speedtest(mac)
                await asyncio.sleep(8)
                await self.coordinator.async_request_refresh()
            finally:
                await shared["set_speedtest_running"](False)
