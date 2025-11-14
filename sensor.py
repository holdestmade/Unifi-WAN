from __future__ import annotations

import logging
from datetime import datetime, timezone, date
from typing import Any, Optional, Tuple

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
    RestoreSensor,
)
from homeassistant.const import UnitOfTime, UnitOfDataRate, UnitOfInformation
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from .const import (
    CONF_HOST,
    CONF_SITE,
    DOMAIN,
    CONF_MONTH_RESET_DAY,
    DEFAULT_MONTH_RESET_DAY,
)

_LOGGER = logging.getLogger(__name__)


def _pick_gateway(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Pick the primary gateway device (UDM/UGW)."""
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
    """Return WAN subsection; for primary use wan1 then legacy wan."""
    if not gw:
        return None
    if which == "wan1":
        return gw.get("wan1") or gw.get("wan")
    if which == "wan2":
        return gw.get("wan2")
    if which == "wan":
        return gw.get("wan")
    return None


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _infer_active_wan_id(gw: dict[str, Any] | None) -> Tuple[Optional[str], dict]:
    """Decide which WAN (wan1/wan2/wan) is active using several heuristics."""
    debug: dict[str, Any] = {}
    if not gw:
        return None, debug

    uplink = gw.get("uplink") or {}
    u_ip = uplink.get("ip")
    u_if = uplink.get("ifname")
    u_name = uplink.get("name")
    u_comment = uplink.get("comment")

    w1 = _wan_section(gw, "wan1") or {}
    w2 = _wan_section(gw, "wan2") or {}

    debug.update(
        {
            "uplink_ip": u_ip,
            "uplink_ifname": u_if,
            "uplink_name": u_name,
            "uplink_comment": u_comment,
            "wan1_ip": w1.get("ip"),
            "wan2_ip": w2.get("ip"),
            "wan1_ifname": w1.get("ifname"),
            "wan2_ifname": w2.get("ifname"),
            "wan1_up": w1.get("up"),
            "wan2_up": w2.get("up"),
        }
    )

    # 1) IP match
    if u_ip:
        if u_ip == w1.get("ip"):
            debug["match"] = "ip==wan1.ip"
            return "WAN1", debug
        if u_ip == w2.get("ip"):
            debug["match"] = "ip==wan2.ip"
            return "WAN2", debug

    # 2) ifname match
    if u_if:
        if u_if == w1.get("ifname"):
            debug["match"] = "ifname==wan1.ifname"
            return "WAN1", debug
        if u_if == w2.get("ifname"):
            debug["match"] = "ifname==wan2.ifname"
            return "WAN2", debug

    # 3) name/comment match (best-effort)
    u_names = {_norm(u_name), _norm(u_comment)}
    w1_names = {_norm(w1.get("name")), _norm(w1.get("comment"))}
    w2_names = {_norm(w2.get("name")), _norm(w2.get("comment"))}
    if u_names & w1_names:
        debug["match"] = "name/comment≈wan1"
        return "WAN1", debug
    if u_names & w2_names:
        debug["match"] = "name/comment≈wan2"
        return "WAN2", debug

    # 4) Fallback: if only one is up
    w1_up = bool(w1.get("up"))
    w2_up = bool(w2.get("up"))
    if w1_up and not w2_up:
        debug["match"] = "fallback:wan1.up"
        return "WAN1", debug
    if w2_up and not w1_up:
        debug["match"] = "fallback:wan2.up"
        return "WAN2", debug

    # Legacy single-WAN key (wan)
    w = _wan_section(gw, "wan") or {}
    if w.get("up"):
        debug["match"] = "legacy:wan.up"
        return "WAN", debug

    debug["match"] = "unknown"
    return None, debug


def _current_billing_period_start(now: datetime, reset_day: int) -> date:
    """Return the start date for the current billing month given a reset day."""
    reset_day = max(1, min(reset_day, 31))
    d = now.date()
    if d.day >= reset_day:
        return date(d.year, d.month, reset_day)
    # Previous month
    if d.month == 1:
        return date(d.year - 1, 12, reset_day)
    return date(d.year, d.month - 1, reset_day)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up UniFi WAN sensors from a config entry."""
    shared = hass.data[DOMAIN][entry.entry_id]
    device = shared["device_coordinator"]
    rates = shared.get("rates_coordinator") or device
    meta = shared.get("dev_meta", {})

    host = entry.options.get(CONF_HOST, entry.data.get(CONF_HOST)) or "unknown"
    site = entry.options.get(CONF_SITE, entry.data.get(CONF_SITE, "default")) or "default"
    devname = f"UniFi WAN ({host} / {site})"

    reset_day = int(
        entry.options.get(
            CONF_MONTH_RESET_DAY,
            entry.data.get(CONF_MONTH_RESET_DAY, DEFAULT_MONTH_RESET_DAY),
        )
    )
    if reset_day < 1:
        reset_day = 1
    if reset_day > 31:
        reset_day = 31

    entities = [
        # Basic link info
        UniFiWanIPv4(device, entry, host, site, devname, meta),
        UniFiWanIPv6(device, entry, host, site, devname, meta),
        # WAN rate sensors (fast coordinator)
        UniFiWanDownMbps(rates, entry, host, site, devname, meta),
        UniFiWanUpMbps(rates, entry, host, site, devname, meta),
        # Totals (MB native; UI can override to GB if desired)
        UniFiWanDownloadToday(rates, entry, host, site, devname, meta),
        UniFiWanUploadToday(rates, entry, host, site, devname, meta),
        UniFiWanDownloadMonth(rates, entry, host, site, devname, meta, reset_day),
        UniFiWanUploadMonth(rates, entry, host, site, devname, meta, reset_day),
        # Speedtest sensors (normal cadence)
        UniFiSpeedtestDown(device, entry, host, site, devname, meta),
        UniFiSpeedtestUp(device, entry, host, site, devname, meta),
        UniFiSpeedtestPing(device, entry, host, site, devname, meta),
        UniFiSpeedtestLastRun(device, entry, host, site, devname, meta),
        UniFiActiveWanName(device, entry, host, site, devname, meta),
        UniFiActiveWanId(device, entry, host, site, devname, meta),
    ]
    async_add_entities(entities)


class UniFiBaseEntity(CoordinatorEntity, SensorEntity):
    """Base entity for UniFi WAN sensors."""

    def __init__(
        self, coordinator, entry: ConfigEntry, host: str, site: str, devname: str, meta: dict[str, Any]
    ):
        super().__init__(coordinator)
        self._entry = entry
        self._host = host
        self._site = site
        self._devname = devname
        self._meta = meta or {}

    @property
    def device_info(self):
        info: dict[str, Any] = {
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

    @property
    def extra_state_attributes(self):
        gw = _pick_gateway(self.coordinator.data) or {}
        uplink = gw.get("uplink") or {}
        return {
            "raw_rx_bytes_r": uplink.get("rx_bytes-r"),
            "uplink_ifname": uplink.get("ifname"),
            "uplink_ip": uplink.get("ip"),
        }


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

    @property
    def extra_state_attributes(self):
        gw = _pick_gateway(self.coordinator.data) or {}
        uplink = gw.get("uplink") or {}
        return {
            "raw_tx_bytes_r": uplink.get("tx_bytes-r"),
            "uplink_ifname": uplink.get("ifname"),
            "uplink_ip": uplink.get("ip"),
        }


class _BaseTotalMB(UniFiBaseEntity, RestoreSensor):
    """Base for MB totals (today / month) derived from rx_bytes-r / tx_bytes-r."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.MEGABYTES

    def __init__(self, coordinator, entry, host, site, devname, meta, direction: str):
        UniFiBaseEntity.__init__(self, coordinator, entry, host, site, devname, meta)
        RestoreSensor.__init__(self)
        self._direction = direction  # "down" or "up"
        self._value_mb: float = 0.0
        self._last_update: Optional[datetime] = None

    @property
    def native_value(self):
        return round(self._value_mb, 3)

    def _get_rate_bytes_per_sec(self) -> Optional[float]:
        gw = _pick_gateway(self.coordinator.data)
        uplink = (gw or {}).get("uplink") or {}
        key = "rx_bytes-r" if self._direction == "down" else "tx_bytes-r"
        val = uplink.get(key)
        if val is None:
            return None
        try:
            return float(val)
        except Exception:
            return None

    def _integrate(self, now: datetime):
        rate = self._get_rate_bytes_per_sec()
        if rate is None:
            self._last_update = now
            return

        if self._last_update is not None:
            delta_seconds = (now - self._last_update).total_seconds()
        else:
            delta_seconds = float(
                self.coordinator.update_interval.total_seconds()
                if self.coordinator.update_interval
                else 0
            )
        if delta_seconds <= 0:
            self._last_update = now
            return

        try:
            bytes_delta = rate * delta_seconds
            self._value_mb += bytes_delta / (1024 * 1024)
        except Exception:
            pass

        self._last_update = now


class UniFiWanDownloadToday(_BaseTotalMB):
    _attr_name = "UniFi WAN Download Today"
    _attr_icon = "mdi:download-circle"

    def __init__(self, coordinator, entry, host, site, devname, meta):
        super().__init__(coordinator, entry, host, site, devname, meta, "down")
        self._day_str: str | None = None

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_wan_download_today_total"

    async def async_added_to_hass(self):
        await UniFiBaseEntity.async_added_to_hass(self)
        last_state = await self.async_get_last_state()
        now = dt_util.now()
        today_str = now.date().isoformat()

        if last_state and last_state.state not in ("unknown", "unavailable", None):
            try:
                self._value_mb = float(last_state.state)
            except Exception:
                self._value_mb = 0.0
            self._day_str = last_state.attributes.get("day") or today_str
            lu = last_state.attributes.get("last_update")
            if isinstance(lu, str):
                try:
                    self._last_update = datetime.fromisoformat(lu)
                except Exception:
                    self._last_update = now
            else:
                self._last_update = now
        else:
            self._value_mb = 0.0
            self._day_str = today_str
            self._last_update = now

    @callback
    def _handle_coordinator_update(self) -> None:
        now = dt_util.now()
        today_str = now.date().isoformat()

        same_day = self._day_str == today_str
        if not same_day:
            # New day: reset
            self._value_mb = 0.0
            self._day_str = today_str
            self._last_update = now
            same_day = True  # after reset, treat as same-day for clamp logic

        old_value = self._value_mb
        self._integrate(now)

        # Clamp: do not allow decrease within the same day
        if same_day and self._value_mb < old_value:
            self._value_mb = old_value

        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        return {
            "day": self._day_str,
            "last_update": self._last_update.isoformat() if self._last_update else None,
        }


class UniFiWanUploadToday(_BaseTotalMB):
    _attr_name = "UniFi WAN Upload Today"
    _attr_icon = "mdi:upload-circle"

    def __init__(self, coordinator, entry, host, site, devname, meta):
        super().__init__(coordinator, entry, host, site, devname, meta, "up")
        self._day_str: str | None = None

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_wan_upload_today_total"

    async def async_added_to_hass(self):
        await UniFiBaseEntity.async_added_to_hass(self)
        last_state = await self.async_get_last_state()
        now = dt_util.now()
        today_str = now.date().isoformat()

        if last_state and last_state.state not in ("unknown", "unavailable", None):
            try:
                self._value_mb = float(last_state.state)
            except Exception:
                self._value_mb = 0.0
            self._day_str = last_state.attributes.get("day") or today_str
            lu = last_state.attributes.get("last_update")
            if isinstance(lu, str):
                try:
                    self._last_update = datetime.fromisoformat(lu)
                except Exception:
                    self._last_update = now
            else:
                self._last_update = now
        else:
            self._value_mb = 0.0
            self._day_str = today_str
            self._last_update = now

    @callback
    def _handle_coordinator_update(self) -> None:
        now = dt_util.now()
        today_str = now.date().isoformat()

        same_day = self._day_str == today_str
        if not same_day:
            self._value_mb = 0.0
            self._day_str = today_str
            self._last_update = now
            same_day = True

        old_value = self._value_mb
        self._integrate(now)

        if same_day and self._value_mb < old_value:
            self._value_mb = old_value

        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        return {
            "day": self._day_str,
            "last_update": self._last_update.isoformat() if self._last_update else None,
        }


class UniFiWanDownloadMonth(_BaseTotalMB):
    _attr_name = "UniFi WAN Download This Month"
    _attr_icon = "mdi:download-multiple"

    def __init__(self, coordinator, entry, host, site, devname, meta, reset_day: int):
        super().__init__(coordinator, entry, host, site, devname, meta, "down")
        self._reset_day = max(1, min(int(reset_day), 31))
        self._period_start: date | None = None

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_wan_download_month_total"

    async def async_added_to_hass(self):
        await UniFiBaseEntity.async_added_to_hass(self)
        last_state = await self.async_get_last_state()
        now = dt_util.now()
        current_start = _current_billing_period_start(now, self._reset_day)

        if last_state and last_state.state not in ("unknown", "unavailable", None):
            try:
                self._value_mb = float(last_state.state)
            except Exception:
                self._value_mb = 0.0
            ps = last_state.attributes.get("period_start")
            try:
                self._period_start = (
                    date.fromisoformat(ps) if isinstance(ps, str) else current_start
                )
            except Exception:
                self._period_start = current_start
            lu = last_state.attributes.get("last_update")
            if isinstance(lu, str):
                try:
                    self._last_update = datetime.fromisoformat(lu)
                except Exception:
                    self._last_update = now
            else:
                self._last_update = now
        else:
            self._value_mb = 0.0
            self._period_start = current_start
            self._last_update = now

        if self._period_start != current_start:
            self._value_mb = 0.0
            self._period_start = current_start

    @callback
    def _handle_coordinator_update(self) -> None:
        now = dt_util.now()
        current_start = _current_billing_period_start(now, self._reset_day)

        same_period = self._period_start == current_start
        if not same_period:
            self._value_mb = 0.0
            self._period_start = current_start
            self._last_update = now
            same_period = True

        old_value = self._value_mb
        self._integrate(now)

        # Clamp: do not allow decrease within the same billing period
        if same_period and self._value_mb < old_value:
            self._value_mb = old_value

        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        return {
            "period_start": self._period_start.isoformat() if self._period_start else None,
            "reset_day": self._reset_day,
            "last_update": self._last_update.isoformat() if self._last_update else None,
        }


class UniFiWanUploadMonth(_BaseTotalMB):
    _attr_name = "UniFi WAN Upload This Month"
    _attr_icon = "mdi:upload-multiple"

    def __init__(self, coordinator, entry, host, site, devname, meta, reset_day: int):
        super().__init__(coordinator, entry, host, site, devname, meta, "up")
        self._reset_day = max(1, min(int(reset_day), 31))
        self._period_start: date | None = None

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_wan_upload_month_total"

    async def async_added_to_hass(self):
        await UniFiBaseEntity.async_added_to_hass(self)
        last_state = await self.async_get_last_state()
        now = dt_util.now()
        current_start = _current_billing_period_start(now, self._reset_day)

        if last_state and last_state.state not in ("unknown", "unavailable", None):
            try:
                self._value_mb = float(last_state.state)
            except Exception:
                self._value_mb = 0.0
            ps = last_state.attributes.get("period_start")
            try:
                self._period_start = (
                    date.fromisoformat(ps) if isinstance(ps, str) else current_start
                )
            except Exception:
                self._period_start = current_start
            lu = last_state.attributes.get("last_update")
            if isinstance(lu, str):
                try:
                    self._last_update = datetime.fromisoformat(lu)
                except Exception:
                    self._last_update = now
            else:
                self._last_update = now
        else:
            self._value_mb = 0.0
            self._period_start = current_start
            self._last_update = now

        if self._period_start != current_start:
            self._value_mb = 0.0
            self._period_start = current_start

    @callback
    def _handle_coordinator_update(self) -> None:
        now = dt_util.now()
        current_start = _current_billing_period_start(now, self._reset_day)

        same_period = self._period_start == current_start
        if not same_period:
            self._value_mb = 0.0
            self._period_start = current_start
            self._last_update = now
            same_period = True

        old_value = self._value_mb
        self._integrate(now)

        if same_period and self._value_mb < old_value:
            self._value_mb = old_value

        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        return {
            "period_start": self._period_start.isoformat() if self._period_start else None,
            "reset_day": self._reset_day,
            "last_update": self._last_update.isoformat() if self._last_update else None,
        }


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
    _attr_icon = "mdi:timer"

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
    _attr_icon = "mdi:clock-outline"

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
    _attr_icon = "mdi:wan"

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_active_wan_name"

    @property
    def native_value(self):
        gw = _pick_gateway(self.coordinator.data)
        uplink = (gw or {}).get("uplink") or {}
        comment = (uplink.get("comment") or "").strip()
        name = (uplink.get("name") or "").strip()
        if comment and name and _norm(comment) != _norm(name):
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


class UniFiActiveWanId(UniFiBaseEntity):
    _attr_name = "UniFi Active WAN ID"
    _attr_icon = "mdi:numeric"

    @property
    def unique_id(self):
        return f"{self._host}_{self._site}_active_wan_id"

    @property
    def native_value(self):
        gw = _pick_gateway(self.coordinator.data)
        wan_id, _ = _infer_active_wan_id(gw)
        return wan_id  # "WAN1" / "WAN2" / "WAN" / None

    @property
    def extra_state_attributes(self):
        gw = _pick_gateway(self.coordinator.data)
        wan_id, dbg = _infer_active_wan_id(gw)
        sec: dict[str, Any] = {}
        if wan_id == "WAN1":
            sec = _wan_section(gw, "wan1") or {}
        elif wan_id == "WAN2":
            sec = _wan_section(gw, "wan2") or {}
        elif wan_id == "WAN":
            sec = _wan_section(gw, "wan") or {}

        return {
            "source_section": dbg.get("match"),
            "uplink_ip": dbg.get("uplink_ip"),
            "uplink_ifname": dbg.get("uplink_ifname"),
            "uplink_name": dbg.get("uplink_name"),
            "uplink_comment": dbg.get("uplink_comment"),
            "section_ip": sec.get("ip"),
            "section_ifname": sec.get("ifname"),
            "section_type": sec.get("type"),
            "section_up": sec.get("up"),
        }
