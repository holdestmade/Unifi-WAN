from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    PLATFORMS,
    CONF_HOST,
    CONF_API_KEY,
    CONF_SITE,
    CONF_VERIFY_SSL,
    CONF_SCAN_INTERVAL,
    DEFAULT_SITE,
    DEFAULT_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL,
    LEGACY_CONF_DEVICE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class UnifiWanClient:
    def __init__(self, hass: HomeAssistant, host: str, api_key: str, site: str, verify_ssl: bool):
        self._hass = hass
        self.host = host.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.site = site or DEFAULT_SITE
        self.verify_ssl = verify_ssl
        self._session = async_get_clientsession(hass, self.verify_ssl)

    def _url(self, path: str) -> str:
        return f"https://{self.host}/proxy/network/api/s/{self.site}/{path}"

    async def get_json(self, path: str) -> dict:
        url = self._url(path)
        headers = {"X-API-Key": self.api_key}
        async with self._session.get(url, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise UpdateFailed(f"HTTP {resp.status} for {url}: {text[:200]}")
            return await resp.json(content_type=None)

    async def get_devices(self) -> dict:
        return await self.get_json("stat/device")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = entry.data
    options = entry.options or {}
    host = options.get(CONF_HOST, data.get(CONF_HOST))
    api_key = options.get(CONF_API_KEY, data.get(CONF_API_KEY))
    site = options.get(CONF_SITE, data.get(CONF_SITE, DEFAULT_SITE))
    verify_ssl = options.get(CONF_VERIFY_SSL, data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))
    scan_seconds = int(options.get(CONF_SCAN_INTERVAL, options.get(LEGACY_CONF_DEVICE_INTERVAL, DEFAULT_SCAN_INTERVAL)))
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

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "device_coordinator": device_coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
