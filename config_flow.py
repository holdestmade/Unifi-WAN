from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
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
)

_LOGGER = logging.getLogger(__name__)


async def _async_validate(hass: HomeAssistant, host: str, api_key: str, site: str, verify_ssl: bool) -> None:
    """Probe /stat/device to validate credentials/host/site."""
    host = (host or "").strip().rstrip("/")
    api_key = (api_key or "").strip()
    site = (site or DEFAULT_SITE).strip()
    session = async_get_clientsession(hass, verify_ssl)
    url = f"https://{host}/proxy/network/api/s/{site}/stat/device"
    headers = {"X-API-Key": api_key}
    async with session.get(url, headers=headers) as resp:
        text = await resp.text()
        if resp.status != 200:
            raise Exception(f"HTTP {resp.status}: {text[:200]}")
        js = await resp.json(content_type=None)
        if not isinstance(js, dict) or "data" not in js:
            raise Exception("Unexpected response shape")

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            api_key = user_input[CONF_API_KEY]
            site = user_input.get(CONF_SITE, DEFAULT_SITE)
            verify_ssl = user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
            auto_enable = user_input.get(CONF_AUTO_SPEEDTEST, DEFAULT_AUTO_SPEEDTEST)
            auto_minutes = int(user_input.get(CONF_AUTO_SPEEDTEST_MINUTES, DEFAULT_AUTO_SPEEDTEST_MINUTES))
            if auto_minutes < 1:
                auto_minutes = 1
            try:
                await _async_validate(self.hass, host, api_key, site, verify_ssl)
            except Exception as e:
                _LOGGER.warning("Validation failed: %s", e)
                errors["base"] = "cannot_connect"
            else:
                unique_id = f"{host.strip().rstrip('/')}-{(site or DEFAULT_SITE).strip()}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"UniFi WAN ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_API_KEY: api_key,
                        CONF_SITE: site,
                        CONF_VERIFY_SSL: verify_ssl,
                        CONF_AUTO_SPEEDTEST: auto_enable,
                        CONF_AUTO_SPEEDTEST_MINUTES: auto_minutes,
                    },
                )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_API_KEY): str,
                vol.Optional(CONF_SITE, default=DEFAULT_SITE): str,
                vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
                vol.Optional(CONF_AUTO_SPEEDTEST, default=DEFAULT_AUTO_SPEEDTEST): bool,
                vol.Optional(CONF_AUTO_SPEEDTEST_MINUTES, default=DEFAULT_AUTO_SPEEDTEST_MINUTES): int,
            }
        )
        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

    async def async_step_import(self, user_input: dict[str, Any] | None = None):
        return await self.async_step_user(user_input)

    async def async_step_reauth(self, user_input: dict[str, Any] | None = None):
        return await self.async_step_user(user_input)

    @staticmethod
    def async_get_options_flow(entry):
        return OptionsFlowHandler(entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            try:
                await _async_validate(
                    self.hass,
                    user_input.get(CONF_HOST, self.entry.options.get(CONF_HOST, self.entry.data.get(CONF_HOST))),
                    user_input.get(CONF_API_KEY, self.entry.options.get(CONF_API_KEY, self.entry.data.get(CONF_API_KEY))),
                    user_input.get(CONF_SITE, self.entry.options.get(CONF_SITE, self.entry.data.get(CONF_SITE, DEFAULT_SITE))),
                    user_input.get(CONF_VERIFY_SSL, self.entry.options.get(CONF_VERIFY_SSL, self.entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))),
                )
            except Exception as e:
                _LOGGER.warning("Options validation failed: %s", e)
                return self.async_show_form(step_id="init", data_schema=self._schema(), errors={"base": "cannot_connect"})
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(step_id="init", data_schema=self._schema())

    def _schema(self):
        opts = self.entry.options
        data = self.entry.data
        return vol.Schema(
            {
                vol.Optional(CONF_HOST, default=opts.get(CONF_HOST, data.get(CONF_HOST, ""))): str,
                vol.Optional(CONF_API_KEY, default=opts.get(CONF_API_KEY, data.get(CONF_API_KEY, ""))): str,
                vol.Optional(CONF_SITE, default=opts.get(CONF_SITE, data.get(CONF_SITE, DEFAULT_SITE))): str,
                vol.Optional(CONF_VERIFY_SSL, default=opts.get(CONF_VERIFY_SSL, data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))): bool,
                vol.Optional(CONF_SCAN_INTERVAL, default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)): int,
                vol.Optional(CONF_AUTO_SPEEDTEST, default=opts.get(CONF_AUTO_SPEEDTEST, data.get(CONF_AUTO_SPEEDTEST, DEFAULT_AUTO_SPEEDTEST))): bool,
                vol.Optional(CONF_AUTO_SPEEDTEST_MINUTES, default=opts.get(CONF_AUTO_SPEEDTEST_MINUTES, data.get(CONF_AUTO_SPEEDTEST_MINUTES, DEFAULT_AUTO_SPEEDTEST_MINUTES))): int,
            }
        )
