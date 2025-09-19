from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    device = data["device_coordinator"]
    host = entry.data.get("host")

    entities = [
        UniFiWanIPv4(device, entry, host),
        UniFiWanIPv6(device, entry, host),
        UniFiWanDownMbps(device, entry, host),
        UniFiWanUpMbps(device, entry, host),
        UniFiSpeedtestDown(device, entry, host),
        UniFiSpeedtestUp(device, entry, host),
        UniFiSpeedtestPing(device, entry, host),
        UniFiSpeedtestLastRun(device, entry, host),
        UniFiActiveWanName(device, entry, host),
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


class UniFiBaseEntity(CoordinatorEntity, SensorEntity):
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


class UniFiWanIPv4(UniFiBaseEntity):
    _attr_name = "UniFi WAN IPv4"
    _attr_icon = "mdi:ip"

    @property
    def unique_id(self):
        return f"{self._host}_wan_ipv4"

    @property
    def native_value(self):
        gw = _get_gateway(self.coordinator.data)
        if not gw:
            return None
        uplink = gw.get("uplink") or {}
        return uplink.get("ip")


class UniFiWanIPv6(UniFiBaseEntity):
    _attr_name = "UniFi WAN IPv6"
    _attr_icon = "mdi:ip-network-outline"

    @property
    def unique_id(self):
        return f"{self._host}_wan_ipv6"

    @property
    def native_value(self):
        gw = _get_gateway(self.coordinator.data)
        if not gw:
            return None
        uplink = gw.get("uplink") or {}
        return uplink.get("ip6")


class UniFiWanDownMbps(UniFiBaseEntity):
    _attr_name = "UniFi WAN Download"
    _attr_native_unit_of_measurement = "Mbit/s"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_icon = "mdi:download"

    @property
    def unique_id(self):
        return f"{self._host}_wan_down_mbps"

    @property
    def native_value(self):
        gw = _get_gateway(self.coordinator.data)
        if not gw:
            return None
        uplink = gw.get("uplink") or {}
        rx_r = uplink.get("rx_bytes-r", 0)
        try:
            return round(float(rx_r) * 8 / 1_000_000, 2)
        except Exception:
            return None


class UniFiWanUpMbps(UniFiBaseEntity):
    _attr_name = "UniFi WAN Upload"
    _attr_native_unit_of_measurement = "Mbit/s"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_icon = "mdi:upload"

    @property
    def unique_id(self):
        return f"{self._host}_wan_up_mbps"

    @property
    def native_value(self):
        gw = _get_gateway(self.coordinator.data)
        if not gw:
            return None
        uplink = gw.get("uplink") or {}
        tx_r = uplink.get("tx_bytes-r", 0)
        try:
            return round(float(tx_r) * 8 / 1_000_000, 2)
        except Exception:
            return None


class UniFiSpeedtestDown(UniFiBaseEntity):
    _attr_name = "UniFi Speedtest Download"
    _attr_native_unit_of_measurement = "Mbit/s"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_icon = "mdi:download"

    @property
    def unique_id(self):
        return f"{self._host}_speedtest_down"

    @property
    def native_value(self):
        gw = _get_gateway(self.coordinator.data)
        if not gw:
            return None
        uplink = gw.get("uplink") or {}
        return float(uplink.get("xput_down", 0))

    @property
    def extra_state_attributes(self):
        gw = _get_gateway(self.coordinator.data)
        if not gw:
            return None
        uplink = gw.get("uplink") or {}
        return {
            "status": uplink.get("speedtest_status"),
            "ping_ms": uplink.get("speedtest_ping"),
        }


class UniFiSpeedtestUp(UniFiBaseEntity):
    _attr_name = "UniFi Speedtest Upload"
    _attr_native_unit_of_measurement = "Mbit/s"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_icon = "mdi:upload"

    @property
    def unique_id(self):
        return f"{self._host}_speedtest_up"

    @property
    def native_value(self):
        gw = _get_gateway(self.coordinator.data)
        if not gw:
            return None
        uplink = gw.get("uplink") or {}
        return float(uplink.get("xput_up", 0))


class UniFiSpeedtestPing(UniFiBaseEntity):
    _attr_name = "UniFi Speedtest Ping"
    _attr_native_unit_of_measurement = "ms"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.DURATION

    @property
    def unique_id(self):
        return f"{self._host}_speedtest_ping"

    @property
    def native_value(self):
        gw = _get_gateway(self.coordinator.data)
        if not gw:
            return None
        uplink = gw.get("uplink") or {}
        return float(uplink.get("speedtest_ping", 0))


class UniFiSpeedtestLastRun(UniFiBaseEntity):
    _attr_name = "UniFi Speedtest Last Run"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def unique_id(self):
        return f"{self._host}_speedtest_last_run"

    @property
    def native_value(self):
        gw = _get_gateway(self.coordinator.data)
        if not gw:
            return None
        uplink = gw.get("uplink") or {}
        ts = uplink.get("speedtest_lastrun")
        if ts is None:
            return None
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except Exception:
            return None


class UniFiActiveWanName(CoordinatorEntity, SensorEntity):
    _attr_name = "UniFi Active WAN Name"

    def __init__(self, coordinator, entry, host):
        super().__init__(coordinator)
        self._entry = entry
        self._host = host

    @property
    def unique_id(self):
        return f"{self._host}_active_wan_name"

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

    @property
    def native_value(self):
        data = self.coordinator.data
        if not data or "data" not in data:
            return None
        # find gateway device
        gw = None
        for t in ("udm", "ugw"):
            for dev in data["data"]:
                if dev.get("type") == t:
                    gw = dev
                    break
            if gw:
                break
        if not gw:
            return None

        uplink = gw.get("uplink") or {}
        comment = (uplink.get("comment") or "").strip()
        name = (uplink.get("name") or "").strip()

        if comment and name and comment.lower() != name.lower():
            return f"{comment} - ({name})"
        return comment or name or None

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        gw = next((d for d in data.get("data", []) if d.get("type") in ("udm", "ugw")), {})
        uplink = gw.get("uplink") or {}
        return {
            "uplink_comment": uplink.get("comment"),
            "uplink_name": uplink.get("name"),
            "uplink_ip": uplink.get("ip"),
            "uplink_up": uplink.get("up"),
        }

