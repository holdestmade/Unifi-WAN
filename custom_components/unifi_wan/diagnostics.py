"""Diagnostics support for UniFi WAN.

Adds a "Download diagnostics" button to the integration page that returns
what the controller actually sent, alongside what the integration made of
it. Nearly every issue raised against this integration has come down to
that comparison, and this needs no logger configuration to produce.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from . import UniFiWanData, UniFiWanRuntimeData, resolve_active_wan

# Applied recursively by key, to the controller payload as well as to the
# config entry. Structural fields the WAN logic turns on - ifname,
# physical_ports, port_idx, up/enable flags, speedtest figures, timestamps,
# wan_networkgroup, source_interface - are deliberately left intact.
#
# Addresses are redacted, which also hides whether two of them matched, so
# the "derived" section reports the outcome of those comparisons instead.
TO_REDACT: set[str] = {
    # Credentials and where the console lives
    "api_key",
    "host",
    "x_aes_gcm_keys",
    "x_authkey",
    "x_fingerprint",
    "x_ssh_hostkey_fingerprint",
    "x_vwirekey",
    # Addressing
    "ip",
    "ip6",
    "ipv6",
    "ip6_address",
    "ip6_addresses",
    "ipv6_addresses",
    "lan_ip",
    "wan_ip",
    "gateway",
    "gateway_v6",
    "dns",
    "nameservers",
    "nameservers_dynamic",
    # Hardware and account identifiers
    "mac",
    "ap_mac",
    "gw_mac",
    "sw_mac",
    "bssid",
    "serial",
    "serial_number",
    "_id",
    "anon_id",
    "anonymous_id",
    "device_id",
    "hash_id",
    "site_id",
    "hostname",
    # Speedtest server location, which locates the subscriber too
    "provider_url",
    "city",
    "lat",
    "lon",
    "latitude",
    "longitude",
}


def _device_summary(devices: list[dict], gateway: dict[str, Any] | None) -> list[dict]:
    """Everything on the site other than the gateway, named but not dumped.

    Only the gateway's own payload drives this integration, so the rest is
    reduced to enough context to spot a misidentified gateway.
    """
    gateway_id = id(gateway) if gateway else None
    summary = []
    for device in devices:
        if not isinstance(device, dict) or id(device) == gateway_id:
            continue
        summary.append(
            {
                "type": device.get("type"),
                "model": device.get("model"),
                "adopted": device.get("adopted"),
                "has_uplink": "uplink" in device,
            }
        )
    return summary


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime: UniFiWanRuntimeData = hass.data[DOMAIN][entry.entry_id]
    data: UniFiWanData | None = runtime.device_coordinator.data

    try:
        integration = await async_get_integration(hass, DOMAIN)
        version = str(integration.version)
    except Exception:  # pragma: no cover - version is a nicety, not a feature
        version = None

    diagnostics: dict[str, Any] = {
        "integration": {
            "version": version,
            "gateway_model": runtime.dev_meta.get("model"),
            "gateway_firmware": runtime.dev_meta.get("sw_version"),
        },
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
    }

    if data is None:
        diagnostics["error"] = "coordinator has no data"
        return diagnostics

    active_wan, match_reason = resolve_active_wan(data)
    diagnostics["derived"] = {
        # What the integration concluded, so a wrong conclusion can be told
        # apart from wrong data.
        "wan_numbers": runtime.wan_numbers,
        "active_wan": active_wan,
        "match_reason": match_reason,
        "wan_alive": data.wan_alive,
        "wan_status": data.wan_status,
        "speedtest": data.speedtest,
        "per_wan_speedtest": data.per_wan_speedtest,
        # What the sensors are actually showing, which can lag the above.
        "latched_speedtest_results": runtime.speedtest_results,
        "per_wan_api_available": data.speedtest_history_raw is not None,
        "targeted_speedtest_supported": runtime.client.targeted_speedtest_supported,
        "auto_speedtest_enabled": runtime.auto_enabled,
        "speedtest_running": runtime.get_speedtest_running(),
    }
    diagnostics["controller"] = {
        # The gateway verbatim: WAN sections, uplink, speedtest-status,
        # port_table and everything else it reports.
        "gateway_device": async_redact_data(data.gateway or {}, TO_REDACT),
        # The per-WAN speedtest API's raw response, the source of the
        # per-WAN sensors.
        "speedtest_history": async_redact_data(
            data.speedtest_history_raw or {}, TO_REDACT
        ),
        "other_devices": _device_summary(data.devices, data.gateway),
    }
    return diagnostics
