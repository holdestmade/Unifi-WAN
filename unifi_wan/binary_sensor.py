from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
    # noqa: E402
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory

from .const import CONF_HOST, CONF_SITE, DOMAIN

_LOGGER = logging.getLogger(__name__)


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


def _wan_section(gw: dict[str, Any] | None, which: str) -> dict[str, Any] | None:
    """
    Return the appropriate WAN section from the gateway dict.

    - Primary: try 'wan1' then fallback to legacy 'wan'
    - Secondary: 'wan2'
    """
    if not gw:
        return None
    if which == "wan1":
        return gw.get("wan1") or gw.get("wan")
    if which == "wan2":
        return gw.get("wan2")
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    shared = hass.data[DOMAIN][entry.entry_id]
    device = shared["device_coordinator"]
    meta = shared.get("dev_meta", {})
    host = entry.options.get(CONF_HOST, entry.data.get(CONF_HOST)) or "unknown"
    site = entry.options.get(CONF_SITE, entry.data.get(CONF_SITE, "default")) or "default"
    devname = f"UniFi WAN ({host} / {site})"

    entities = [
        UniFiWanInternet(device, entry, host, site, devname, meta),
        UniFiActiveWanUp(device, entry, host, site, devname, meta),
        UniFiWan1Link(device, entry, host, site, devname, meta),
        UniFiWan2Link(device, entry, host, site, devname, meta),
        UniFiSpeedtestInProgress(shared, host, site, devname, meta),
    ]
    async_add_entities(entities)


class UniFiBaseBinary(CoordinatorEntity, BinarySensorEntity):
    def __init__(self, coordinator, entry: ConfigEntry, host: str, site: str, devname: str, meta: dict[str, Any]):
        super().__init__(coordinator)
        self._entry = entry
        self._host = host
        self._site = site
        self._devname = devname
        self._meta = meta or {}

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
    def available(self) -> bool:
        return super().available and bool(self.coordinator.data)


class UniFiWanInternet(UniFiBaseBinary):
    _attr_name = "UniFi WAN Internet"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_wan_internet"

    @property
    def is_on(self):
        gw = _pick_gateway(self.coordinator.data)
        return bool((gw or {}).get("uplink", {}).get("up"))


class UniFiActiveWanUp(UniFiBaseBinary):
    _attr_name = "UniFi Active WAN Up"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_active_wan_up"

    @property
    def is_on(self):
        gw = _pick_gateway(self.coordinator.data)
        return bool((gw or {}).get("uplink", {}).get("up"))


class UniFiWan1Link(UniFiBaseBinary):
    _attr_name = "UniFi WAN1 Link"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_wan1_link"

    @property
    def is_on(self):
        gw = _pick_gateway(self.coordinator.data)
        s = _wan_section(gw, "wan1")
        return bool((s or {}).get("up"))

    @property
    def extra_state_attributes(self):
        gw = _pick_gateway(self.coordinator.data)
        s = _wan_section(gw, "wan1") or {}
        return {
            "source_section": "wan1" if "wan1" in (gw or {}) else ("wan" if "wan" in (gw or {}) else None),
            "ifname": s.get("ifname"),
            "ip": s.get("ip"),
            "type": s.get("type"),
        }


class UniFiWan2Link(UniFiBaseBinary):
    _attr_name = "UniFi WAN2 Link"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_wan2_link"

    @property
    def is_on(self):
        gw = _pick_gateway(self.coordinator.data)
        s = _wan_section(gw, "wan2")
        return bool((s or {}).get("up"))

    @property
    def extra_state_attributes(self):
        gw = _pick_gateway(self.coordinator.data)
        s = _wan_section(gw, "wan2") or {}
        return {
            "source_section": "wan2" if s else None,
            "ifname": s.get("ifname"),
            "ip": s.get("ip"),
            "type": s.get("type"),
        }


class UniFiSpeedtestInProgress(BinarySensorEntity):
    _attr_name = "UniFi Speedtest In Progress"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:progress-clock"
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, shared, host: str, site: str, devname: str, meta: dict[str, Any]):
        self._shared = shared
        self._host = host
        self._site = site
        self._devname = devname
        self._meta = meta or {}
        self._unsub = None

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_speedtest_in_progress"

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

    async def async_added_to_hass(self):
        signal = self._shared["speedtest_running_signal"]
        self._unsub = async_dispatcher_connect(self.hass, signal, self._signal_update)
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self):
        if self._unsub:
            self._unsub()
            self._unsub = None

    def _signal_update(self):
        self.schedule_update_ha_state()

    @property
    def is_on(self) -> bool:
        return bool(self._shared["get_speedtest_running"]())
