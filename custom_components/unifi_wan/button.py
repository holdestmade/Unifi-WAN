from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    shared = hass.data[DOMAIN][entry.entry_id]
    device_coord = shared["device_coordinator"]
    meta = shared.get("dev_meta", {})
    
    host = shared["host"]
    site = shared["site"]
    devname = f"UniFi WAN ({host} / {site})"

    async_add_entities([RunSpeedtestButton(device_coord, shared, host, site, devname, meta)])


class RunSpeedtestButton(CoordinatorEntity, ButtonEntity):
    _attr_name = "UniFi Run Speedtest"
    _attr_icon = "mdi:speedometer"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, shared, host, site, devname, meta):
        super().__init__(coordinator)
        self._shared = shared
        self._host = host
        self._site = site
        self._devname = devname
        self._meta = meta

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_run_speedtest"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._host, self._site)},
            "name": self._devname,
            "manufacturer": "Ubiquiti",
            "model": self._meta.get("model"),
        }

    async def async_press(self) -> None:
        runner = self._shared.get("run_speedtest_now")
        if callable(runner):
            await runner()