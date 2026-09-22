"""Config, options and reconfigure flows."""

from __future__ import annotations

import logging
import ssl
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_API_KEY,
    CONF_AUTO_SPEEDTEST,
    CONF_AUTO_SPEEDTEST_MINUTES,
    CONF_EXPECTED_DOWNLOAD,
    CONF_EXPECTED_UPLOAD,
    CONF_HOST,
    CONF_RATE_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SITE,
    CONF_SPEED_TOLERANCE,
    CONF_VERIFY_SSL,
    DEFAULT_AUTO_SPEEDTEST,
    DEFAULT_AUTO_SPEEDTEST_MINUTES,
    DEFAULT_EXPECTED_SPEED,
    DEFAULT_RATE_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SITE,
    DEFAULT_SPEED_TOLERANCE_PERCENT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    MAX_AUTO_SPEEDTEST_MINUTES,
    MAX_EXPECTED_SPEED,
    MAX_RATE_INTERVAL,
    MAX_SCAN_INTERVAL,
    MAX_SPEED_TOLERANCE_PERCENT,
    MIN_AUTO_SPEEDTEST_MINUTES,
    MIN_RATE_INTERVAL,
    MIN_SCAN_INTERVAL,
    MIN_SPEED_TOLERANCE_PERCENT,
    REQUEST_TIMEOUT_SECONDS,
)
from .models import expected_speed, speed_tolerance

_LOGGER = logging.getLogger(__name__)

API_KEY_SELECTOR = selector.selector({"text": {"type": "password"}})

VALIDATE_TIMEOUT = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)


def _number(
    minimum: float, maximum: float, step: float = 1, unit: str | None = None
) -> Any:
    """A bounded number field.

    The bounds are the ones setup enforces anyway, shown in the dialog so a
    rejected value is explained where it is typed rather than silently
    clamped afterwards. A fractional step is for the speeds, which are sold
    in halves as readily as whole numbers.
    """
    config = selector.NumberSelectorConfig(
        min=minimum,
        max=maximum,
        step=step,
        mode=selector.NumberSelectorMode.BOX,
    )
    if unit is not None:
        # Only when there is one. The key is optional but must be a string
        # when present, so passing None rejects the whole selector - and
        # with it the entire options form, which then fails to load at all.
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


class ValidationError(Exception):
    """Base class for validation failures; error_key maps to translations."""

    error_key = "cannot_connect"


class CannotConnect(ValidationError):
    """Error to indicate we cannot connect."""

    error_key = "cannot_connect"


class InvalidAuth(ValidationError):
    """Error to indicate the API key was rejected."""

    error_key = "invalid_auth"


class InvalidSite(ValidationError):
    """Error to indicate the API endpoint or site was not found (HTTP 404)."""

    error_key = "invalid_site"


class SSLCertError(ValidationError):
    """Error to indicate SSL certificate verification failed."""

    error_key = "ssl_error"


class Timeout(ValidationError):
    """Error to indicate the console did not answer in time."""

    error_key = "timeout"


def _clean_host(host: str) -> str:
    """Normalize a host: strip whitespace and scheme, lowercase, drop any path."""
    host = (host or "").strip().lower().removeprefix("https://").removeprefix("http://")
    return host.split("/", 1)[0]


def _unique_id(host: str, site: str) -> str:
    """One configured console and site, however its address was typed."""
    return f"{_clean_host(host)}-{(site or DEFAULT_SITE).strip()}"


async def _async_validate(
    hass: HomeAssistant, host: str, api_key: str, site: str, verify_ssl: bool
) -> None:
    """Probe /stat/device to check connectivity and basic shape."""
    host = _clean_host(host)
    api_key = (api_key or "").strip()
    site = (site or DEFAULT_SITE).strip()
    session = async_get_clientsession(hass, verify_ssl)
    url = f"https://{host}/proxy/network/api/s/{site}/stat/device"
    headers = {"X-API-Key": api_key}
    try:
        async with session.get(url, headers=headers, timeout=VALIDATE_TIMEOUT) as resp:
            if resp.status in (401, 403):
                raise InvalidAuth(f"HTTP {resp.status}")
            if resp.status == 404:
                raise InvalidSite(f"HTTP 404 for {url}")
            text = await resp.text()
            if resp.status != 200:
                raise CannotConnect(f"HTTP {resp.status}: {text[:200]}")
            js = await resp.json(content_type=None)
    except ValidationError:
        raise
    except (aiohttp.ClientSSLError, ssl.SSLError) as e:
        raise SSLCertError(str(e)) from e
    except TimeoutError as e:
        raise Timeout(
            f"No response from {host} within {REQUEST_TIMEOUT_SECONDS}s"
        ) from e
    except Exception as e:
        raise CannotConnect(str(e)) from e
    if not isinstance(js, dict) or "data" not in js:
        raise CannotConnect("Unexpected response shape")


def _connection_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """The connection fields, shared by the user and reconfigure steps."""
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_API_KEY): API_KEY_SELECTOR,
            vol.Optional(CONF_SITE, default=defaults.get(CONF_SITE, DEFAULT_SITE)): str,
            vol.Optional(
                CONF_VERIFY_SSL,
                default=defaults.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            ): bool,
            vol.Optional(
                CONF_AUTO_SPEEDTEST,
                default=defaults.get(CONF_AUTO_SPEEDTEST, DEFAULT_AUTO_SPEEDTEST),
            ): bool,
            vol.Optional(
                CONF_AUTO_SPEEDTEST_MINUTES,
                default=defaults.get(
                    CONF_AUTO_SPEEDTEST_MINUTES, DEFAULT_AUTO_SPEEDTEST_MINUTES
                ),
            ): _number(MIN_AUTO_SPEEDTEST_MINUTES, MAX_AUTO_SPEEDTEST_MINUTES),
        }
    )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = _clean_host(user_input[CONF_HOST])
            api_key = user_input[CONF_API_KEY]
            site = user_input.get(CONF_SITE, DEFAULT_SITE)
            verify_ssl = user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
            auto_enable = user_input.get(CONF_AUTO_SPEEDTEST, DEFAULT_AUTO_SPEEDTEST)
            auto_minutes = max(
                MIN_AUTO_SPEEDTEST_MINUTES,
                int(
                    user_input.get(
                        CONF_AUTO_SPEEDTEST_MINUTES, DEFAULT_AUTO_SPEEDTEST_MINUTES
                    )
                ),
            )

            try:
                await _async_validate(self.hass, host, api_key, site, verify_ssl)
            except ValidationError as e:
                _LOGGER.warning("Validation failed (%s): %s", e.error_key, e)
                errors["base"] = e.error_key
            else:
                await self.async_set_unique_id(_unique_id(host, site))
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

        return self.async_show_form(
            step_id="user",
            data_schema=_connection_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change where an existing entry points, keeping its entities.

        The options dialog can change these too, but only this step can
        move the entry's unique id with them, which is what stops a console
        that has been re-addressed from being configurable twice.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            host = _clean_host(user_input[CONF_HOST])
            site = user_input.get(CONF_SITE, DEFAULT_SITE)
            api_key = user_input[CONF_API_KEY]
            verify_ssl = user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
            try:
                await _async_validate(self.hass, host, api_key, site, verify_ssl)
            except ValidationError as e:
                _LOGGER.warning(
                    "Reconfigure validation failed (%s): %s", e.error_key, e
                )
                errors["base"] = e.error_key
            else:
                await self.async_set_unique_id(_unique_id(host, site))
                self._abort_if_unique_id_mismatch(reason="wrong_console")
                # Written to data and cleared from options, so the merged
                # view cannot keep serving the address this replaces.
                options = {
                    key: value
                    for key, value in entry.options.items()
                    if key not in (CONF_HOST, CONF_SITE, CONF_API_KEY, CONF_VERIFY_SSL)
                }
                return self.async_update_reload_and_abort(
                    entry,
                    title=f"UniFi WAN ({host})",
                    data_updates={
                        CONF_HOST: host,
                        CONF_SITE: site,
                        CONF_API_KEY: api_key,
                        CONF_VERIFY_SSL: verify_ssl,
                    },
                    options=options,
                )

        current = {**entry.data, **entry.options}
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_connection_schema(user_input or current),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthentication when the API key stops working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        host = entry.options.get(CONF_HOST, entry.data.get(CONF_HOST, ""))
        site = entry.options.get(CONF_SITE, entry.data.get(CONF_SITE, DEFAULT_SITE))
        verify_ssl = entry.options.get(
            CONF_VERIFY_SSL, entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
        )

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            try:
                await _async_validate(self.hass, host, api_key, site, verify_ssl)
            except ValidationError as e:
                _LOGGER.warning("Reauth validation failed (%s): %s", e.error_key, e)
                errors["base"] = e.error_key
            else:
                # The API key may also be stored in options (the options flow
                # writes it there); update both so the new key takes effect.
                new_options = dict(entry.options)
                if CONF_API_KEY in new_options:
                    new_options[CONF_API_KEY] = api_key
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_API_KEY: api_key},
                    options=new_options,
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): API_KEY_SELECTOR}),
            description_placeholders={"host": host},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlowHandler:
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlow):
    def _opt(self, key: str, default: Any = None) -> Any:
        """Current effective value: options first, then data, then default."""
        entry = self.config_entry
        return entry.options.get(key, entry.data.get(key, default))

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = _clean_host(user_input.get(CONF_HOST) or self._opt(CONF_HOST, ""))
            # An empty API key field means "keep the stored key"
            api_key = user_input.get(CONF_API_KEY) or self._opt(CONF_API_KEY, "")
            site = user_input.get(CONF_SITE) or self._opt(CONF_SITE, DEFAULT_SITE)
            verify_ssl = user_input.get(
                CONF_VERIFY_SSL, self._opt(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
            )
            scan_interval = max(
                MIN_SCAN_INTERVAL,
                int(user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
            )
            rate_interval = int(
                user_input.get(CONF_RATE_INTERVAL, DEFAULT_RATE_INTERVAL)
            )
            if rate_interval > 0:
                rate_interval = max(MIN_RATE_INTERVAL, rate_interval)
            auto_enable = user_input.get(
                CONF_AUTO_SPEEDTEST,
                self._opt(CONF_AUTO_SPEEDTEST, DEFAULT_AUTO_SPEEDTEST),
            )
            auto_minutes = max(
                MIN_AUTO_SPEEDTEST_MINUTES,
                int(
                    user_input.get(
                        CONF_AUTO_SPEEDTEST_MINUTES,
                        self._opt(
                            CONF_AUTO_SPEEDTEST_MINUTES, DEFAULT_AUTO_SPEEDTEST_MINUTES
                        ),
                    )
                ),
            )
            expected_download = expected_speed(
                user_input.get(
                    CONF_EXPECTED_DOWNLOAD,
                    self._opt(CONF_EXPECTED_DOWNLOAD, DEFAULT_EXPECTED_SPEED),
                )
            )
            expected_upload = expected_speed(
                user_input.get(
                    CONF_EXPECTED_UPLOAD,
                    self._opt(CONF_EXPECTED_UPLOAD, DEFAULT_EXPECTED_SPEED),
                )
            )

            # Stored as the percentage that was typed; the fraction is
            # derived where it is used.
            tolerance_percent = (
                speed_tolerance(
                    user_input.get(
                        CONF_SPEED_TOLERANCE,
                        self._opt(
                            CONF_SPEED_TOLERANCE, DEFAULT_SPEED_TOLERANCE_PERCENT
                        ),
                    )
                )
                * 100
            )

            new_options = {
                CONF_HOST: host,
                CONF_API_KEY: api_key,
                CONF_SITE: site,
                CONF_VERIFY_SSL: verify_ssl,
                CONF_SCAN_INTERVAL: scan_interval,
                CONF_RATE_INTERVAL: rate_interval,
                CONF_AUTO_SPEEDTEST: auto_enable,
                CONF_AUTO_SPEEDTEST_MINUTES: auto_minutes,
                CONF_EXPECTED_DOWNLOAD: expected_download,
                CONF_EXPECTED_UPLOAD: expected_upload,
                CONF_SPEED_TOLERANCE: tolerance_percent,
            }

            try:
                await _async_validate(self.hass, host, api_key, site, verify_ssl)
            except ValidationError as e:
                _LOGGER.warning("Options validation failed (%s): %s", e.error_key, e)
                errors["base"] = e.error_key

            if not errors and (error := self._async_move_entry(host, site)):
                errors["base"] = error

            if not errors:
                return self.async_create_entry(title="", data=new_options)

            return self.async_show_form(
                step_id="init",
                data_schema=self._schema(new_options),
                errors=errors,
            )

        return self.async_show_form(step_id="init", data_schema=self._schema())

    @callback
    def _async_move_entry(self, host: str, site: str) -> str | None:
        """Follow a host or site change with the entry's identity.

        The unique id and the title are both derived from these, so leaving
        them behind lets the same console be added a second time and leaves
        the entry named after an address it no longer uses. Returns an
        error key when another entry already holds the new identity.
        """
        entry = self.config_entry
        unique_id = _unique_id(host, site)
        if unique_id == entry.unique_id:
            return None
        for other in self.hass.config_entries.async_entries(DOMAIN):
            if other.entry_id != entry.entry_id and other.unique_id == unique_id:
                return "already_configured"
        self.hass.config_entries.async_update_entry(
            entry, unique_id=unique_id, title=f"UniFi WAN ({host})"
        )
        return None

    def _schema(self, overrides: dict[str, Any] | None = None) -> vol.Schema:
        o = overrides or {}

        def d(key: str, default: Any) -> Any:
            return o.get(key, self._opt(key, default))

        return vol.Schema(
            {
                vol.Optional(CONF_HOST, default=d(CONF_HOST, "")): str,
                vol.Optional(CONF_API_KEY): API_KEY_SELECTOR,
                vol.Optional(CONF_SITE, default=d(CONF_SITE, DEFAULT_SITE)): str,
                vol.Optional(
                    CONF_VERIFY_SSL, default=d(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
                ): bool,
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=d(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): _number(MIN_SCAN_INTERVAL, MAX_SCAN_INTERVAL),
                vol.Optional(
                    CONF_RATE_INTERVAL,
                    default=d(CONF_RATE_INTERVAL, DEFAULT_RATE_INTERVAL),
                ): _number(0, MAX_RATE_INTERVAL),
                vol.Optional(
                    CONF_AUTO_SPEEDTEST,
                    default=d(CONF_AUTO_SPEEDTEST, DEFAULT_AUTO_SPEEDTEST),
                ): bool,
                vol.Optional(
                    CONF_AUTO_SPEEDTEST_MINUTES,
                    default=d(
                        CONF_AUTO_SPEEDTEST_MINUTES, DEFAULT_AUTO_SPEEDTEST_MINUTES
                    ),
                ): _number(MIN_AUTO_SPEEDTEST_MINUTES, MAX_AUTO_SPEEDTEST_MINUTES),
                vol.Optional(
                    CONF_EXPECTED_DOWNLOAD,
                    default=d(CONF_EXPECTED_DOWNLOAD, DEFAULT_EXPECTED_SPEED),
                ): _number(0, MAX_EXPECTED_SPEED, step=0.1, unit="Mbit/s"),
                vol.Optional(
                    CONF_EXPECTED_UPLOAD,
                    default=d(CONF_EXPECTED_UPLOAD, DEFAULT_EXPECTED_SPEED),
                ): _number(0, MAX_EXPECTED_SPEED, step=0.1, unit="Mbit/s"),
                vol.Optional(
                    CONF_SPEED_TOLERANCE,
                    default=d(CONF_SPEED_TOLERANCE, DEFAULT_SPEED_TOLERANCE_PERCENT),
                ): _number(
                    MIN_SPEED_TOLERANCE_PERCENT,
                    MAX_SPEED_TOLERANCE_PERCENT,
                    step=0.1,
                    unit="%",
                ),
            }
        )
