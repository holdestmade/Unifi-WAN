from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, CALLBACK_TYPE
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    PLATFORMS,
    CONF_HOST,
    CONF_API_KEY,
    CONF_SITE,
    CONF_VERIFY_SSL,
    CONF_SCAN_INTERVAL,
    CONF_AUTO_SPEEDTEST,
    CONF_AUTO_SPEEDTEST_MINUTES,
    DEFAULT_SITE,
    DEFAULT_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_AUTO_SPEEDTEST,
    DEFAULT_AUTO_SPEEDTEST_MINUTES,
    LEGACY_CONF_DEVICE_INTERVAL,
    SIGNAL_SPEEDTEST_RUNNING,
)

_LOGGER = logging.getLogger(__name__)


class UnifiWanClient:
    def __init__(self, hass: HomeAssistant, host: str, api_key: str, site: str, verify_ssl: bool):
        self._hass = hass
        self.host = (host or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()
        self.site = site or DEFAULT_SITE
        self.verify_ssl = bool(verify_ssl)
        self._session = async_create_clientsession(hass, verify_ssl=self.verify_ssl)

    def _url(self, path: str) -> str:
        return f"https://{self.host}/proxy/network/api/s/{self.site}/{path}"

    async def get_json(self, path: str) -> dict:
        url = self._url(path)
        headers = {"X-API-Key": self.api_key}
        async with self._session.get(url, headers=headers) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise UpdateFailed(f"HTTP {resp.status} for {url}: {text[:200]}")
            return await resp.json(content_type=None)

    async def post_json(self, path: str, payload: dict) -> dict:
        url = self._url(path)
        headers = {"X-API-Key": self.api_key}
        async with self._session.post(url, headers=headers, json=payload) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise UpdateFailed(f"HTTP {resp.status} for {url}: {text[:200]}")
            try:
                return await resp.json(content_type=None)
            except Exception:
                return {"ok": resp.status == 200, "text": text[:200]}

    async def get_devices(self) -> dict:
        return await self.get_json("stat/device")

    async def run_speedtest(self, mac: str) -> dict:
        return await self.post_json("cmd/devmgr", {"cmd": "speedtest", "mac": mac})


def _pick_gateway(payload: dict[str, Any] | None) -> Optional[dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    devs = [d for d in data if isinstance(d, dict) and d.get("type") in ("udm", "ugw")]
    if not devs:
        return None
    devs.sort(key=lambda d: (not d.get("adopted", True), "uplink" not in d))
    return devs[0]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = entry.data
    options = entry.options or {}

    host = options.get(CONF_HOST, data.get(CONF_HOST))
    api_key = options.get(CONF_API_KEY, data.get(CONF_API_KEY))
    site = options.get(CONF_SITE, data.get(CONF_SITE, DEFAULT_SITE))
    verify_ssl = options.get(CONF_VERIFY_SSL, data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))

    scan_seconds = int(options.get(CONF_SCAN_INTERVAL, options.get(LEGACY_CONF_DEVICE_INTERVAL, DEFAULT_SCAN_INTERVAL)))

    auto_enabled_default = bool(options.get(CONF_AUTO_SPEEDTEST, data.get(CONF_AUTO_SPEEDTEST, DEFAULT_AUTO_SPEEDTEST)))
    auto_minutes = int(options.get(CONF_AUTO_SPEEDTEST_MINUTES, data.get(CONF_AUTO_SPEEDTEST_MINUTES, DEFAULT_AUTO_SPEEDTEST_MINUTES)))
    if auto_minutes < 1:
        auto_minutes = 1

    client = UnifiWanClient(hass, host, api_key, site, verify_ssl)

    async def _update_devices():
        return await client.get_devices()

    device_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_device",
        update_method=_update_devices,
        update_interval=timedelta(seconds=scan_seconds),
    )
    await device_coordinator.async_config_entry_first_refresh()

    dev_meta = {"sw_version": None, "model": "UDM/UGW", "mac": None}
    gw = _pick_gateway(device_coordinator.data)
    if gw:
        dev_meta["sw_version"] = gw.get("version") or gw.get("firmware_version")
        dev_meta["model"] = gw.get("model") or gw.get("type") or "UDM/UGW"
        dev_meta["mac"] = gw.get("mac")

    entry_signal = f"{SIGNAL_SPEEDTEST_RUNNING}_{entry.entry_id}"
    speedtest_running: bool = False
    unsub_auto: Optional[CALLBACK_TYPE] = None
    auto_enabled_runtime: bool = auto_enabled_default

    def _dispatch_running():
        async_dispatcher_send(hass, entry_signal)

    async def set_speedtest_running(is_running: bool) -> None:
        nonlocal speedtest_running
        if speedtest_running == is_running:
            return
        speedtest_running = is_running
        _dispatch_running()

    async def _run_speedtest_now() -> None:
        gw = _pick_gateway(device_coordinator.data)
        if not gw:
            _LOGGER.debug("Speedtest: no gateway yet, refreshing devices")
            await device_coordinator.async_request_refresh()
            gw = _pick_gateway(device_coordinator.data)
            if not gw:
                _LOGGER.warning("Speedtest: no gateway device found; skipping")
                return
        mac = gw.get("mac")
        if not mac:
            _LOGGER.warning("Speedtest: gateway MAC not found; skipping")
            return

        await set_speedtest_running(True)
        try:
            await client.run_speedtest(mac)
        except Exception as e:
            _LOGGER.warning("Speedtest failed: %s", e)
        finally:
            await asyncio.sleep(8)
            await device_coordinator.async_request_refresh()
            await set_speedtest_running(False)

    async def _auto_speedtest_callback(_now) -> None:
        if not auto_enabled_runtime:
            return
        await _run_speedtest_now()

    def _schedule_auto() -> None:
        nonlocal unsub_auto
        if unsub_auto:
            return
        unsub_auto = async_track_time_interval(hass, _auto_speedtest_callback, timedelta(minutes=auto_minutes))
        _LOGGER.info("UniFi WAN: auto speedtest enabled every %s minute(s)", auto_minutes)

    def _unschedule_auto() -> None:
        nonlocal unsub_auto
        if unsub_auto:
            unsub_auto()
            unsub_auto = None
            _LOGGER.info("UniFi WAN: auto speedtest disabled")

    if auto_enabled_runtime:
        _schedule_auto()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "device_coordinator": device_coordinator,
        "host": host,
        "site": site,
        "dev_meta": dev_meta,
        "auto_unsub": unsub_auto,
        "auto_enabled": auto_enabled_runtime,
        "enable_auto": lambda: (_schedule_auto(), hass.data[DOMAIN][entry.entry_id].update({"auto_enabled": True})),
        "disable_auto": lambda: (_unschedule_auto(), hass.data[DOMAIN][entry.entry_id].update({"auto_enabled": False})),
        "run_speedtest_now": _run_speedtest_now,
        "speedtest_running_signal": entry_signal,
        "get_speedtest_running": lambda: speedtest_running,
        "set_speedtest_running": set_speedtest_running,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    shared = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if shared and shared.get("auto_unsub"):
        shared["auto_unsub"]()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
