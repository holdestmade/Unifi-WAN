from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfDataRate, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry

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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    shared = hass.data[DOMAIN][entry.entry_id]
    device = shared["device_coordinator"]
    meta = shared.get("dev_meta", {})

    host = entry.options.get(CONF_HOST, entry.data.get(CONF_HOST)) or "unknown"
    site = entry.options.get(CONF_SITE, entry.data.get(CONF_SITE, "default")) or "default"
    devname = f"UniFi WAN ({host} / {site})"

    entities = [
        UniFiWanIPv4(device, entry, host, site, devname, meta),
        UniFiWanIPv6(device, entry, host, site, devname, meta),
        UniFiWanDownMbps(device, entry, host, site, devname, meta),
        UniFiWanUpMbps(device, entry, host, site, devname, meta),
        UniFiSpeedtestDown(device, entry, host, site, devname, meta),
        UniFiSpeedtestUp(device, entry, host, site, devname, meta),
        UniFiSpeedtestPing(device, entry, host, site, devname, meta),
        UniFiSpeedtestLastRun(device, entry, host, site, devname, meta),
        UniFiActiveWanName(device, entry, host, site, devname, meta),
    ]
    async_add_entities(entities)


class UniFiBaseEntity(CoordinatorEntity, SensorEntity):
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


class UniFiWanIPv4(UniFiBaseEntity):
    _attr_name = "UniFi WAN IPv4"
    _attr_icon = "mdi:ip"

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_wan_ipv4"

    @property
    def native_value(self):
        gw = _pick_gateway(self.coordinator.data)
        return (gw or {}).get("uplink", {}).get("ip")


class UniFiWanIPv6(UniFiBaseEntity):
    _attr_name = "UniFi WAN IPv6"
    _attr_icon = "mdi:ip-network-outline"

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_wan_ipv6"

    @property
    def native_value(self):
        gw = _pick_gateway(self.coordinator.data)
        return (gw or {}).get("uplink", {}).get("ip6")


class UniFiWanDownMbps(UniFiBaseEntity):
    _attr_name = "UniFi WAN Download"
    _attr_native_unit_of_measurement = UnitOfDataRate.MEGABITS_PER_SECOND
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_icon = "mdi:download"

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_wan_down_mbps"

    @property
    def native_value(self):
        gw = _pick_gateway(self.coordinator.data)
        rx_r = ((gw or {}).get("uplink") or {}).get("rx_bytes-r", 0)
        try:
            return round(float(rx_r) * 8 / 1_000_000, 2)
        except Exception:
            return None


class UniFiWanUpMbps(UniFiBaseEntity):
    _attr_name = "UniFi WAN Upload"
    _attr_native_unit_of_measurement = UnitOfDataRate.MEGABITS_PER_SECOND
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_icon = "mdi:upload"

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_wan_up_mbps"

    @property
    def native_value(self):
        gw = _pick_gateway(self.coordinator.data)
        tx_r = ((gw or {}).get("uplink") or {}).get("tx_bytes-r", 0)
        try:
            return round(float(tx_r) * 8 / 1_000_000, 2)
        except Exception:
            return None


class UniFiSpeedtestDown(UniFiBaseEntity):
    _attr_name = "UniFi Speedtest Download"
    _attr_native_unit_of_measurement = UnitOfDataRate.MEGABITS_PER_SECOND
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_icon = "mdi:download"

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_speedtest_down"

    @property
    def native_value(self):
        gw = _pick_gateway(self.coordinator.data)
        uplink = (gw or {}).get("uplink") or {}
        ts = int(uplink.get("speedtest_lastrun") or 0)
        if ts == 0:
            return None
        try:
            return float(uplink.get("xput_down", 0))
        except Exception:
            return None

    @property
    def extra_state_attributes(self):
        gw = _pick_gateway(self.coordinator.data)
        uplink = (gw or {}).get("uplink") or {}
        ts = int(uplink.get("speedtest_lastrun") or 0)
        if ts == 0:
            return {"status": None, "ping_ms": None}
        return {
            "status": uplink.get("speedtest_status"),
            "ping_ms": uplink.get("speedtest_ping"),
        }


class UniFiSpeedtestUp(UniFiBaseEntity):
    _attr_name = "UniFi Speedtest Upload"
    _attr_native_unit_of_measurement = UnitOfDataRate.MEGABITS_PER_SECOND
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_icon = "mdi:upload"

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_speedtest_up"

    @property
    def native_value(self):
        gw = _pick_gateway(self.coordinator.data)
        uplink = (gw or {}).get("uplink") or {}
        ts = int(uplink.get("speedtest_lastrun") or 0)
        if ts == 0:
            return None
        try:
            return float(uplink.get("xput_up", 0))
        except Exception:
            return None


class UniFiSpeedtestPing(UniFiBaseEntity):
    _attr_name = "UniFi Speedtest Ping"
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.DURATION

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_speedtest_ping"

    @property
    def native_value(self):
        gw = _pick_gateway(self.coordinator.data)
        uplink = (gw or {}).get("uplink") or {}
        ts = int(uplink.get("speedtest_lastrun") or 0)
        if ts == 0:
            return None
        try:
            return float(uplink.get("speedtest_ping", 0))
        except Exception:
            return None


class UniFiSpeedtestLastRun(UniFiBaseEntity):
    _attr_name = "UniFi Speedtest Last Run"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_speedtest_last_run"

    @property
    def native_value(self):
        gw = _pick_gateway(self.coordinator.data)
        ts = int(((gw or {}).get("uplink") or {}).get("speedtest_lastrun") or 0)
        if ts == 0:
            return None
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None


class UniFiActiveWanName(UniFiBaseEntity):
    _attr_name = "UniFi Active WAN Name"

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_active_wan_name"

    @property
    def native_value(self):
        gw = _pick_gateway(self.coordinator.data)
        uplink = (gw or {}).get("uplink") or {}
        comment = (uplink.get("comment") or "").strip()
        name = (uplink.get("name") or "").strip()
        if comment and name and comment.lower() != name.lower():
            return f"{comment} - ({name})"
        return comment or name or None

    @property
    def extra_state_attributes(self):
        gw = _pick_gateway(self.coordinator.data) or {}
        uplink = gw.get("uplink") or {}
        return {
            "uplink_comment": uplink.get("comment"),
            "uplink_name": uplink.get("name"),
            "uplink_ip": uplink.get("ip"),
            "uplink_up": uplink.get("up"),
        }
