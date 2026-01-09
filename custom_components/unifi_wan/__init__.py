from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from dataclasses import dataclass
from typing import Any, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, CALLBACK_TYPE
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.const import CONF_HOST, CONF_API_KEY, CONF_VERIFY_SSL

from .const import (
    DOMAIN,
    PLATFORMS,
    CONF_SITE,
    CONF_SCAN_INTERVAL,
    CONF_RATE_INTERVAL,
    CONF_AUTO_SPEEDTEST,
    CONF_AUTO_SPEEDTEST_MINUTES,
    DEFAULT_SITE,
    DEFAULT_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_RATE_INTERVAL,
    DEFAULT_AUTO_SPEEDTEST,
    DEFAULT_AUTO_SPEEDTEST_MINUTES,
    LEGACY_CONF_DEVICE_INTERVAL,
    SIGNAL_SPEEDTEST_RUNNING,
    GATEWAY_DEVICES,
    SERVICE_RUN_SPEEDTEST,
)

_LOGGER = logging.getLogger(__name__)

@dataclass
class UniFiWanData:
    """Structured data to avoid repeated list parsing in sensors."""
    devices: list[dict]
    gateway: dict[str, Any] | None
    uplink: dict[str, Any]
    wan: dict[int, dict[str, Any]]

class UnifiWanClient:
    """Simple HTTP client for UniFi Network endpoints."""

    def __init__(self, hass: HomeAssistant, host: str, api_key: str, site: str, verify_ssl: bool):
        self._hass = hass
        self.host = (host or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()
        self.site = site or DEFAULT_SITE
        self.verify_ssl = bool(verify_ssl)
        self._session = async_get_clientsession(hass, self.verify_ssl)

    def _url(self, path: str) -> str:
        return f"https://{self.host}/proxy/network/api/s/{self.site}/{path}"

    async def get_json(self, path: str) -> dict:
        url = self._url(path)
        headers = {"X-API-Key": self.api_key}
        try:
            async with self._session.get(url, headers=headers) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise UpdateFailed(f"HTTP {resp.status} for {url}: {text[:200]}")
                return await resp.json(content_type=None)
        except Exception as e:
            raise UpdateFailed(f"Connection error: {e}") from e

    async def post_json(self, path: str, payload: dict) -> dict:
        url = self._url(path)
        headers = {"X-API-Key": self.api_key}
        try:
            async with self._session.post(url, headers=headers, json=payload) as resp:
                text = await resp.text()
                if resp.status != 200:
                    _LOGGER.error("HTTP %s for %s: %s", resp.status, url, text[:200])
                    return {"ok": False}
                try:
                    return await resp.json(content_type=None)
                except Exception:
                    return {"ok": True}
        except Exception as e:
            _LOGGER.error("POST failed: %s", e)
            return {"ok": False}

    async def get_devices(self) -> dict:
        return await self.get_json("stat/device")

    async def get_device(self, mac: str) -> dict:
        return await self.get_json(f"stat/device/{mac}")

    async def run_speedtest(self, mac: str) -> dict:
        return await self.post_json("cmd/devmgr", {"cmd": "speedtest", "mac": mac})


def _extract_wan_data(payload: dict[str, Any] | None) -> UniFiWanData:
    """Process raw JSON into a structured object once."""
    devices = []
    if isinstance(payload, dict):
        devices = payload.get("data", []) or []
    
    gateway = None
    for t in GATEWAY_DEVICES:
        candidates = [d for d in devices if isinstance(d, dict) and d.get("type") == t]
        if candidates:
            candidates.sort(key=lambda d: (not d.get("adopted", True), "uplink" not in d))
            gateway = candidates[0]
            break
    
    uplink = (gateway.get("uplink") or {}) if gateway else {}
    wan_interfaces = (gateway.get("last_wan_interfaces") or {}).keys()

    wan_numbers = set()
    for wan_interface in wan_interfaces:
        if wan_interface == "WAN":
            wan_numbers.add(1)
        elif wan_interface.startswith("WAN"):
            wan_interface.add(int(wan_interface[3:]))

    wan = {}
    for wan_number in wan_numbers:
        if wan_number == 1:
            wan[1] = (gateway.get("wan1") or gateway.get("wan")) if gateway else {}
        else:
            wan[wan_number] = gateway.get("wan" + str(wan_number)) if gateway else {}

    return UniFiWanData(
        devices=devices,
        gateway=gateway,
        uplink=uplink,
        wan=wan
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = entry.data
    options = entry.options or {}

    host = options.get(CONF_HOST, data.get(CONF_HOST))
    api_key = options.get(CONF_API_KEY, data.get(CONF_API_KEY))
    site = options.get(CONF_SITE, data.get(CONF_SITE, DEFAULT_SITE))
    verify_ssl = options.get(CONF_VERIFY_SSL, data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))

    scan_seconds = int(options.get(CONF_SCAN_INTERVAL, options.get(LEGACY_CONF_DEVICE_INTERVAL, DEFAULT_SCAN_INTERVAL)))
    rate_seconds = int(options.get(CONF_RATE_INTERVAL, DEFAULT_RATE_INTERVAL))
    
    auto_minutes = int(options.get(CONF_AUTO_SPEEDTEST_MINUTES, data.get(CONF_AUTO_SPEEDTEST_MINUTES, DEFAULT_AUTO_SPEEDTEST_MINUTES)))
    auto_enabled = bool(options.get(CONF_AUTO_SPEEDTEST, data.get(CONF_AUTO_SPEEDTEST, DEFAULT_AUTO_SPEEDTEST)))

    client = UnifiWanClient(hass, host, api_key, site, verify_ssl)

    async def _update_devices() -> UniFiWanData:
        """Fetch and process data."""
        raw = await client.get_devices()
        return _extract_wan_data(raw)

    wan_numbers = (await _update_devices()).wan.keys()

    device_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_device",
        update_method=_update_devices,
        update_interval=timedelta(seconds=scan_seconds),
    )
    await device_coordinator.async_config_entry_first_refresh()

    dev_meta: dict[str, Any] = {"sw_version": None, "model": "UDM/UGW", "mac": None}
    if device_coordinator.data.gateway:
        gw = device_coordinator.data.gateway
        dev_meta["sw_version"] = gw.get("version") or gw.get("firmware_version")
        dev_meta["model"] = gw.get("model") or gw.get("type") or "UDM/UGW"
        dev_meta["mac"] = gw.get("mac")

    rates_coordinator: Optional[DataUpdateCoordinator] = None
    if dev_meta["mac"]:
        mac = dev_meta["mac"]
        async def _update_rates():
            raw = await client.get_device(mac)
            return _extract_wan_data(raw)

        rates_coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_rates",
            update_method=_update_rates,
            update_interval=timedelta(seconds=max(1, rate_seconds)),
        )
        await rates_coordinator.async_config_entry_first_refresh()

    entry_signal = f"{SIGNAL_SPEEDTEST_RUNNING}_{entry.entry_id}"
    speedtest_running: bool = False
    unsub_auto: Optional[CALLBACK_TYPE] = None

    def _dispatch_running():
        async_dispatcher_send(hass, entry_signal)

    async def set_speedtest_running(is_running: bool) -> None:
        nonlocal speedtest_running
        if speedtest_running == is_running:
            return
        speedtest_running = is_running
        _dispatch_running()

    async def _run_speedtest_now() -> None:
        """Trigger a speedtest."""
        gw_data = device_coordinator.data
        mac_local = gw_data.gateway.get("mac") if gw_data.gateway else None
        
        if not mac_local:
            await device_coordinator.async_request_refresh()
            gw_data = device_coordinator.data
            mac_local = gw_data.gateway.get("mac") if gw_data.gateway else None

        if not mac_local:
            _LOGGER.warning("Cannot run speedtest: No gateway found.")
            return

        await set_speedtest_running(True)
        try:
            await client.run_speedtest(mac_local)
            await asyncio.sleep(15) 
        except Exception as e:
            _LOGGER.error("Speedtest trigger failed: %s", e)
        finally:
            await device_coordinator.async_request_refresh()
            if rates_coordinator:
                await rates_coordinator.async_request_refresh()
            await set_speedtest_running(False)

    async def _auto_speedtest_callback(_now) -> None:
        await _run_speedtest_now()

    def _schedule_auto(enabled: bool) -> None:
        nonlocal unsub_auto
        if unsub_auto:
            unsub_auto()
            unsub_auto = None
        
        if enabled:
            unsub_auto = async_track_time_interval(
                hass, _auto_speedtest_callback, timedelta(minutes=max(1, auto_minutes))
            )
            _LOGGER.debug("Auto speedtest scheduled every %s min", auto_minutes)

    _schedule_auto(auto_enabled)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "device_coordinator": device_coordinator,
        "rates_coordinator": rates_coordinator,
        "host": host,
        "site": site,
        "dev_meta": dev_meta,
        "auto_unsub": unsub_auto,
        "manage_auto": _schedule_auto,
        "run_speedtest_now": _run_speedtest_now,
        "speedtest_running_signal": entry_signal,
        "get_speedtest_running": lambda: speedtest_running,
        "set_speedtest_running": set_speedtest_running,
        "wan_numbers": wan_numbers
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    async def handle_run_speedtest(call: ServiceCall):
        await _run_speedtest_now()

    hass.services.async_register(DOMAIN, SERVICE_RUN_SPEEDTEST, handle_run_speedtest)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    shared = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if shared:
        shared["manage_auto"](False)
        
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, SERVICE_RUN_SPEEDTEST)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)