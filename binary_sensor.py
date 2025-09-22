from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory

from .const import (
    CONF_HOST,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    shared = hass.data[DOMAIN][entry.entry_id]
    device = shared["device_coordinator"]
    host = entry.options.get(CONF_HOST, entry.data.get(CONF_HOST)) or "unknown"

    entities = [
        UniFiWanInternet(device, entry, host),
        UniFiActiveWanUp(device, entry, host),
        UniFiWan1Link(device, entry, host),
        UniFiWan2Link(device, entry, host),
        UniFiSpeedtestInProgress(shared, host),
    ]
    async_add_entities(entities)


def _get_gateway(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not data or "data" not in data or not isinstance(data["data"], list):
        return None
    for t in ("udm", "ugw"):
        for dev in data["data"]:
            if isinstance(dev, dict) and dev.get("type") == t:
                return dev
    return None


class UniFiBaseBinary(CoordinatorEntity, BinarySensorEntity):
    def __init__(self, coordinator, entry: ConfigEntry, host: str):
        super().__init__(coordinator)
        self._entry = entry
        self._host = host

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
    def available(self) -> bool:
        return super().available and bool(self.coordinator.data)


class UniFiWanInternet(UniFiBaseBinary):
    _attr_name = "UniFi WAN Internet"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def unique_id(self):
        return f"{self._host}_wan_internet"

    @property
    def is_on(self):
        gw = _get_gateway(self.coordinator.data)
        if not gw:
            return False
        uplink = gw.get("uplink") or {}
        return bool(uplink.get("up"))


class UniFiActiveWanUp(UniFiBaseBinary):
    _attr_name = "UniFi Active WAN Up"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def unique_id(self):
        return f"{self._host}_active_wan_up"

    @property
    def is_on(self):
        gw = _get_gateway(self.coordinator.data)
        if not gw:
            return False
        uplink = gw.get("uplink") or {}
        return bool(uplink.get("up"))


class UniFiWan1Link(UniFiBaseBinary):
    _attr_name = "UniFi WAN1 Link"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def unique_id(self):
        return f"{self._host}_wan1_link"

    @property
    def is_on(self):
        gw = _get_gateway(self.coordinator.data)
        if not gw:
            return False
        w1 = gw.get("wan1") or {}
        return bool(w1.get("up"))


class UniFiWan2Link(UniFiBaseBinary):
    _attr_name = "UniFi WAN2 Link"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def unique_id(self):
        return f"{self._host}_wan2_link"

    @property
    def is_on(self):
        gw = _get_gateway(self.coordinator.data)
        if not gw:
            return False
        w2 = gw.get("wan2") or {}
        return bool(w2.get("up"))


class UniFiSpeedtestInProgress(BinarySensorEntity):
    _attr_name = "UniFi Speedtest In Progress"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:progress-clock"
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, shared, host):
        self._shared = shared
        self._host = host
        self._unsub = None

    @property
    def unique_id(self):
        return f"{self._host}_speedtest_in_progress"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._host)},
            "name": f"UniFi WAN ({self._host})",
            "manufacturer": "Ubiquiti",
            "model": "UDM/UGW",
            "configuration_url": f"https://{self._host}/",
        }

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
