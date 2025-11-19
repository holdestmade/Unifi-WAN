from __future__ import annotations

from datetime import datetime, timezone, date
from dataclasses import dataclass
from typing import Any, Callable, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
    RestoreSensor,
    SensorEntityDescription,
)
from homeassistant.const import UnitOfTime, UnitOfDataRate, UnitOfInformation
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from .const import DOMAIN, CONF_MONTH_RESET_DAY, DEFAULT_MONTH_RESET_DAY
from .__init__ import UniFiWanData

@dataclass
class UniFiSensorDescription(SensorEntityDescription):
    """Description for UniFi Sensors."""
    value_fn: Callable[[UniFiWanData], Any] = lambda x: None

def _mbps(val): 
    try: return round(float(val) * 8 / 1_000_000, 2)
    except: return 0.0

def _ts_date(val):
    try: 
        ts = int(val)
        if ts > 0:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
    except: pass
    return None

def _wan_id(d: UniFiWanData):
    """Infer Active WAN ID."""
    u_ip = d.uplink.get("ip")
    if u_ip:
        if u_ip == d.wan1.get("ip"): return "WAN1"
        if u_ip == d.wan2.get("ip"): return "WAN2"
    if d.wan1.get("up") and not d.wan2.get("up"): return "WAN1"
    if d.wan2.get("up") and not d.wan1.get("up"): return "WAN2"
    return "WAN1" if d.wan1.get("up") else "Unknown"

def _wan_name(d: UniFiWanData):
    """Get Active WAN Name."""
    c = (d.uplink.get("comment") or "").strip()
    n = (d.uplink.get("name") or "").strip()
    if c and n and c.lower() != n.lower():
        return f"{c} ({n})"
    return c or n or "Unknown"

SENSORS: tuple[UniFiSensorDescription, ...] = (
    UniFiSensorDescription(
        key="wan_ipv4", name="UniFi WAN IPv4", icon="mdi:ip",
        value_fn=lambda d: d.uplink.get("ip") or "unknown"
    ),
    UniFiSensorDescription(
        key="wan_ipv6", name="UniFi WAN IPv6", icon="mdi:ip-network-outline",
        value_fn=lambda d: d.uplink.get("ip6") or "unknown"
    ),
    UniFiSensorDescription(
        key="wan1_ipv4", name="UniFi WAN1 IPv4", icon="mdi:ip",
        value_fn=lambda d: d.wan1.get("ip") or "unknown"
    ),
    UniFiSensorDescription(
        key="wan1_ipv6", name="UniFi WAN1 IPv6", icon="mdi:ip-network-outline",
        value_fn=lambda d: d.wan1.get("ip6") or "unknown"
    ),
    UniFiSensorDescription(
        key="wan2_ipv4", name="UniFi WAN2 IPv4", icon="mdi:ip",
        value_fn=lambda d: d.wan2.get("ip") or "unknown"
    ),
    UniFiSensorDescription(
        key="wan2_ipv6", name="UniFi WAN2 IPv6", icon="mdi:ip-network-outline",
        value_fn=lambda d: d.wan2.get("ip6") or "unknown"
    ),
    UniFiSensorDescription(
        key="wan_down_mbps", name="UniFi WAN Download", icon="mdi:download",
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        value_fn=lambda d: _mbps(d.uplink.get("rx_bytes-r", 0))
    ),
    UniFiSensorDescription(
        key="wan_up_mbps", name="UniFi WAN Upload", icon="mdi:upload",
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        value_fn=lambda d: _mbps(d.uplink.get("tx_bytes-r", 0))
    ),
    UniFiSensorDescription(
        key="speedtest_down", name="UniFi Speedtest Download", icon="mdi:download",
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        value_fn=lambda d: d.uplink.get("xput_down")
    ),
    UniFiSensorDescription(
        key="speedtest_up", name="UniFi Speedtest Upload", icon="mdi:upload",
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        value_fn=lambda d: d.uplink.get("xput_up")
    ),
    UniFiSensorDescription(
        key="speedtest_ping", name="UniFi Speedtest Ping", icon="mdi:timer",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        value_fn=lambda d: d.uplink.get("speedtest_ping")
    ),
    UniFiSensorDescription(
        key="speedtest_last_run", name="UniFi Speedtest Last Run", icon="mdi:clock-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: _ts_date(d.uplink.get("speedtest_lastrun"))
    ),
    UniFiSensorDescription(
        key="active_wan_id", name="UniFi Active WAN ID", icon="mdi:numeric",
        value_fn=_wan_id
    ),
    UniFiSensorDescription(
        key="active_wan_name", name="UniFi Active WAN Name", icon="mdi:wan",
        value_fn=_wan_name
    ),
)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    shared = hass.data[DOMAIN][entry.entry_id]
    rates_coord = shared.get("rates_coordinator") or shared["device_coordinator"]
    device_coord = shared["device_coordinator"]
    
    host = shared["host"]
    site = shared["site"]
    meta = shared["dev_meta"]
    devname = f"UniFi WAN ({host} / {site})"

    reset_day = int(entry.options.get(CONF_MONTH_RESET_DAY, DEFAULT_MONTH_RESET_DAY))

    entities = []
    
    for desc in SENSORS:
        coord = rates_coord if "mbps" in desc.key else device_coord
        entities.append(UniFiGenericSensor(coord, host, site, devname, meta, desc))

    for direction in ["down", "up"]:
        entities.append(UniFiTotalSensor(rates_coord, host, site, devname, meta, direction, "today"))
        entities.append(UniFiTotalSensor(rates_coord, host, site, devname, meta, direction, "month", reset_day))

    async_add_entities(entities)


class UniFiGenericSensor(CoordinatorEntity, SensorEntity):
    entity_description: UniFiSensorDescription

    def __init__(self, coordinator, host, site, devname, meta, description):
        super().__init__(coordinator)
        self._host = host
        self._site = site
        self._devname = devname
        self._meta = meta
        self.entity_description = description

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_{self.entity_description.key}"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._host, self._site)},
            "name": self._devname,
            "manufacturer": "Ubiquiti",
            "model": self._meta.get("model"),
            "sw_version": self._meta.get("sw_version"),
            "configuration_url": f"https://{self._host}/",
        }

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.coordinator.data)


class UniFiTotalSensor(CoordinatorEntity, RestoreSensor):
    _attr_state_class = SensorStateClass.TOTAL
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.MEGABYTES

    def __init__(self, coordinator, host, site, devname, meta, direction, period, reset_day=1):
        super().__init__(coordinator)
        self._host = host
        self._site = site
        self._devname = devname
        self._meta = meta
        self._direction = direction
        self._period = period
        self._reset_day = reset_day

        dir_str = "Download" if direction == "down" else "Upload"
        self._attr_name = f"UniFi WAN {dir_str} {period.title()}"
        self._attr_unique_id = f"{host}_{site}_wan_{direction}_{period}_total"

        if direction == "down":
            self._attr_icon = "mdi:download-circle" if period == "today" else "mdi:download-multiple"
        else:
            self._attr_icon = "mdi:upload-circle" if period == "today" else "mdi:upload-multiple"
        
        self._value_mb = 0.0
        self._base_bytes = None
        self._period_marker = None 

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._host, self._site)},
            "name": self._devname,
            "manufacturer": "Ubiquiti",
            "model": self._meta.get("model"),
        }

    @property
    def native_value(self):
        return round(self._value_mb, 2)

    def _get_current_marker(self):
        now = dt_util.now()
        if self._period == "today":
            return now.date().isoformat()
        
        d = now.date()
        if d.day >= self._reset_day:
            return date(d.year, d.month, self._reset_day).isoformat()
        
        m = d.month - 1 if d.month > 1 else 12
        y = d.year if d.month > 1 else d.year - 1
        return date(y, m, self._reset_day).isoformat()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in ("unknown", "unavailable"):
            try:
                self._value_mb = float(last_state.state)
                self._period_marker = last_state.attributes.get("period_marker")
                self._base_bytes = last_state.attributes.get("base_bytes")
            except:
                pass
        
        current_marker = self._get_current_marker()
        if self._period_marker != current_marker:
             self._value_mb = 0.0
             self._base_bytes = None
             self._period_marker = current_marker

    @callback
    def _handle_coordinator_update(self) -> None:
        data: UniFiWanData = self.coordinator.data
        key = "rx_bytes" if self._direction == "down" else "tx_bytes"
        current_bytes = data.uplink.get(key)
        
        if current_bytes is None:
            return

        current_bytes = int(current_bytes)
        current_marker = self._get_current_marker()

        if self._period_marker != current_marker:
            self._value_mb = 0.0
            self._base_bytes = current_bytes
            self._period_marker = current_marker
            self.async_write_ha_state()
            return

        if self._base_bytes is None:
            self._base_bytes = current_bytes
            self.async_write_ha_state()
            return

        if current_bytes < self._base_bytes:
            self._base_bytes = current_bytes
        
        delta = current_bytes - self._base_bytes
        if delta > 0:
            self._value_mb += delta / (1024 * 1024)
            self._base_bytes = current_bytes
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        return {
            "period_marker": self._period_marker,
            "base_bytes": self._base_bytes
        }