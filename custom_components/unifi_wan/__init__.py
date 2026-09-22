"""The UniFi WAN integration.

Setup, teardown and the domain's services. The work itself lives in
api (HTTP), models (parsing), coordinator (polling), speedtest (the run
and attribution state machine) and runtime (what an entry carries).
"""

from __future__ import annotations

import logging
from typing import Any, Final

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo

from .api import UnifiWanClient
from .const import (
    ATTR_KEEP,
    ATTR_WAN,
    CONF_API_KEY,
    CONF_AUTO_SPEEDTEST,
    CONF_AUTO_SPEEDTEST_MINUTES,
    CONF_EXPECTED_DOWNLOAD,
    CONF_EXPECTED_UPLOAD,
    CONF_HOST,
    CONF_RATE_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SITE,
    CONF_VERIFY_SSL,
    DEFAULT_AUTO_SPEEDTEST,
    DEFAULT_AUTO_SPEEDTEST_MINUTES,
    DEFAULT_DUMP_KEEP,
    DEFAULT_EXPECTED_SPEED,
    DEFAULT_RATE_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SITE,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    LEGACY_CONF_DEVICE_INTERVAL,
    MAX_DUMP_KEEP,
    MAX_WAN_INTERFACES,
    MIN_RATE_INTERVAL,
    MIN_SCAN_INTERVAL,
    SERVICE_DUMP_RAW_DATA,
    SERVICE_RUN_SPEEDTEST,
)
from .coordinator import UniFiWanCoordinator, UniFiWanRatesCoordinator
from .models import expected_speed
from .runtime import UniFiWanConfigEntry, UniFiWanRuntimeData
from .speedtest import SpeedtestManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS: Final = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
]

SERVICE_RUN_SPEEDTEST_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_WAN): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_WAN_INTERFACES)
        )
    }
)

SERVICE_DUMP_RAW_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_KEEP, default=DEFAULT_DUMP_KEEP): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_DUMP_KEEP)
        )
    }
)

# Config keys whose change requires a full reload of the entry.
# CONF_AUTO_SPEEDTEST is deliberately excluded: the switch entity applies it
# live, so persisting it must not tear down every entity.
RELOAD_OPTION_KEYS: Final = (
    CONF_HOST,
    CONF_API_KEY,
    CONF_SITE,
    CONF_VERIFY_SSL,
    CONF_SCAN_INTERVAL,
    CONF_RATE_INTERVAL,
    CONF_AUTO_SPEEDTEST_MINUTES,
    # The comparison sensors read these once, at setup, so a change has to
    # rebuild the entry. Cheaper than making every sensor consult the
    # config entry on each state read for a figure that changes yearly.
    CONF_EXPECTED_DOWNLOAD,
    CONF_EXPECTED_UPLOAD,
)


def merged_option(entry: ConfigEntry, key: str, default: Any = None) -> Any:
    """Effective config value: options first, then data, then default.

    Setup and the options flow both resolve settings this way; centralising it
    keeps the two paths consistent.
    """
    return entry.options.get(key, entry.data.get(key, default))


def _reload_signature(entry: ConfigEntry) -> dict[str, Any]:
    """Snapshot of the config values that require a full reload when changed."""
    merged = {**entry.data, **entry.options}
    return {key: merged.get(key) for key in RELOAD_OPTION_KEYS}


def entry_runtimes(hass: HomeAssistant) -> list[UniFiWanRuntimeData]:
    """Every loaded entry's runtime data.

    Read from the config entries themselves rather than a parallel dict, so
    an entry that is unloaded or still setting up simply is not listed.
    """
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]


async def _async_migrate_registry(
    hass: HomeAssistant, entry: ConfigEntry, host: str, site: str
) -> None:
    """Migrate legacy host/site-based unique IDs and device identifiers to
    the config entry ID, so entities survive a host or site rename.
    """
    old_prefix = f"{host}_{site}_"
    new_prefix = f"{entry.entry_id}_"

    @callback
    def _migrate(entity_entry: er.RegistryEntry) -> dict[str, str] | None:
        if entity_entry.unique_id.startswith(old_prefix):
            return {
                "new_unique_id": new_prefix + entity_entry.unique_id[len(old_prefix) :]
            }
        return None

    try:
        await er.async_migrate_entries(hass, entry.entry_id, _migrate)
    except ValueError as e:
        _LOGGER.warning("Could not migrate legacy unique IDs: %s", e)

    dev_reg = dr.async_get(hass)
    # Legacy releases used a non-standard 3-tuple identifier. The legacy device
    # was created under this same config entry, so the entry-scoped lookup
    # (HA 2026.9+) finds it; older cores lack that method and still allow the
    # unscoped lookup without a deprecation warning.
    legacy_identifier = (DOMAIN, host, site)
    if hasattr(dev_reg, "async_get_device_by_identifier"):
        device = dev_reg.async_get_device_by_identifier(
            legacy_identifier,  # type: ignore[arg-type]
            entry.entry_id,
        )
    else:
        device = dev_reg.async_get_device(identifiers={legacy_identifier})  # type: ignore[arg-type]
    if device:
        dev_reg.async_update_device(
            device.id, new_identifiers={(DOMAIN, entry.entry_id)}
        )


async def async_setup_entry(hass: HomeAssistant, entry: UniFiWanConfigEntry) -> bool:
    """Set up one configured gateway."""
    options = entry.options or {}

    host = merged_option(entry, CONF_HOST)
    api_key = merged_option(entry, CONF_API_KEY)
    site = merged_option(entry, CONF_SITE, DEFAULT_SITE)
    verify_ssl = merged_option(entry, CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)

    # Clamped here as well as in the options flow: a value stored by an
    # older release, or edited by hand in .storage, would otherwise be taken
    # at face value - and zero means a poll with no interval at all.
    scan_seconds = max(
        MIN_SCAN_INTERVAL,
        int(
            options.get(
                CONF_SCAN_INTERVAL,
                options.get(LEGACY_CONF_DEVICE_INTERVAL, DEFAULT_SCAN_INTERVAL),
            )
        ),
    )
    rate_seconds = int(options.get(CONF_RATE_INTERVAL, DEFAULT_RATE_INTERVAL))
    if rate_seconds > 0:
        rate_seconds = max(MIN_RATE_INTERVAL, rate_seconds)

    auto_minutes = max(
        1,
        int(
            merged_option(
                entry, CONF_AUTO_SPEEDTEST_MINUTES, DEFAULT_AUTO_SPEEDTEST_MINUTES
            )
        ),
    )
    auto_enabled = bool(
        merged_option(entry, CONF_AUTO_SPEEDTEST, DEFAULT_AUTO_SPEEDTEST)
    )

    # Normalised the same way the options dialog stores them, so a value
    # written by an older release or edited by hand is read identically.
    expected_download = expected_speed(
        merged_option(entry, CONF_EXPECTED_DOWNLOAD, DEFAULT_EXPECTED_SPEED)
    )
    expected_upload = expected_speed(
        merged_option(entry, CONF_EXPECTED_UPLOAD, DEFAULT_EXPECTED_SPEED)
    )

    await _async_migrate_registry(hass, entry, host, site)

    client = UnifiWanClient(hass, host, api_key, site, verify_ssl)

    device_coordinator = UniFiWanCoordinator(hass, entry, client, scan_seconds)
    await device_coordinator.async_config_entry_first_refresh()

    gateway = device_coordinator.data.gateway
    if gateway is None:
        # Without the gateway there are no WAN sections, so the per-WAN
        # entities would never be created and the entry would sit there
        # half-built until someone reloaded it. Far better to let Home
        # Assistant retry: a console still booting answers this way.
        raise ConfigEntryNotReady(
            f"No UniFi gateway found on site {site!r}. The console answered, but "
            "none of the devices it reported looks like a gateway."
        )

    dev_meta: dict[str, Any] = {
        "sw_version": gateway.get("version") or gateway.get("firmware_version"),
        "model": gateway.get("model") or gateway.get("type") or "UDM/UGW",
        "mac": gateway.get("mac"),
    }
    wan_numbers = sorted(device_coordinator.data.wan)

    rates_coordinator: UniFiWanRatesCoordinator | None = None
    if dev_meta["mac"] and rate_seconds > 0:
        rates_coordinator = UniFiWanRatesCoordinator(
            hass, entry, client, dev_meta["mac"], rate_seconds
        )
        await rates_coordinator.async_config_entry_first_refresh()

    speedtest = SpeedtestManager(
        hass,
        entry,
        client,
        device_coordinator,
        rates_coordinator,
        wan_numbers,
        auto_enabled,
        auto_minutes,
    )

    entry.runtime_data = UniFiWanRuntimeData(
        client=client,
        device_coordinator=device_coordinator,
        rates_coordinator=rates_coordinator,
        speedtest=speedtest,
        host=host,
        site=site,
        dev_meta=dev_meta,
        device_info=DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"UniFi WAN ({host} / {site})",
            manufacturer="Ubiquiti",
            model=dev_meta["model"],
            sw_version=dev_meta["sw_version"],
            configuration_url=f"https://{host}/",
        ),
        wan_numbers=wan_numbers,
        expected_download=expected_download,
        expected_upload=expected_upload,
        reload_signature=_reload_signature(entry),
    )

    # After runtime_data, so a listener firing during platform setup finds
    # it, and after the platforms would fail: async_on_unload unwinds the
    # schedule if any of this raises.
    speedtest.async_setup()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    _async_register_services(hass)
    return True


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    """Register the domain's services once, for every configured gateway.

    The handlers look the loaded entries up on each call, so they work with
    several gateways and survive individual entries being unloaded.
    """
    if not hass.services.has_service(DOMAIN, SERVICE_RUN_SPEEDTEST):

        async def handle_run_speedtest(call: ServiceCall) -> None:
            wan_number = call.data.get(ATTR_WAN)
            for runtime in entry_runtimes(hass):
                runtime.speedtest.trigger(wan_number)

        hass.services.async_register(
            DOMAIN,
            SERVICE_RUN_SPEEDTEST,
            handle_run_speedtest,
            schema=SERVICE_RUN_SPEEDTEST_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_DUMP_RAW_DATA):

        async def handle_dump_raw_data(call: ServiceCall) -> ServiceResponse:
            # Imported here so the dump machinery is only loaded when it is
            # asked for.
            from .dump import async_dump_all

            return await async_dump_all(hass, call.data[ATTR_KEEP])

        hass.services.async_register(
            DOMAIN,
            SERVICE_DUMP_RAW_DATA,
            handle_dump_raw_data,
            schema=SERVICE_DUMP_RAW_DATA_SCHEMA,
            # Returns the paths written, so the file can be found without
            # digging through the log.
            supports_response=SupportsResponse.OPTIONAL,
        )


async def async_unload_entry(hass: HomeAssistant, entry: UniFiWanConfigEntry) -> bool:
    """Unload one gateway, and the services with the last of them."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok and not [
        loaded
        for loaded in hass.config_entries.async_loaded_entries(DOMAIN)
        if loaded.entry_id != entry.entry_id
    ]:
        for service in (SERVICE_RUN_SPEEDTEST, SERVICE_DUMP_RAW_DATA):
            if hass.services.has_service(DOMAIN, service):
                hass.services.async_remove(DOMAIN, service)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: UniFiWanConfigEntry) -> None:
    """Apply changed options, reloading only where one demands it."""
    runtime: UniFiWanRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is not None and _reload_signature(entry) == runtime.reload_signature:
        # Nothing that requires a fresh setup changed. The auto-speedtest
        # enable flag is the only live-managed option: the switch entity
        # applies it directly, and when it is changed through the options
        # dialog we apply it here too. Either way we avoid a full reload
        # that would briefly mark every entity unavailable.
        enabled = bool(
            merged_option(entry, CONF_AUTO_SPEEDTEST, DEFAULT_AUTO_SPEEDTEST)
        )
        runtime.speedtest.set_auto_enabled(enabled)
        return
    await hass.config_entries.async_reload(entry.entry_id)
