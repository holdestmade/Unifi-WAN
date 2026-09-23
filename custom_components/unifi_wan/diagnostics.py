"""Diagnostics support for UniFi WAN.

Adds a "Download diagnostics" button to the integration page that returns
what the controller actually sent, alongside what the integration made of
it. Nearly every issue raised against this integration has come down to
that comparison, and this needs no logger configuration to produce.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN, MAX_WAN_INTERFACES
from .models import UniFiWanData
from .runtime import UniFiWanConfigEntry

# Matches the marker Home Assistant's own diagnostics helper renders.
REDACTED: Final = "**REDACTED**"

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
    "guest_token",
    "syslog_key",
    "x_aes_gcm_keys",
    "x_authkey",
    "x_fingerprint",
    "x_inform_authkey",
    "x_ssh_hostkey_fingerprint",
    "x_vwirekey",
    # Addressing
    "address",
    "ip",
    "ip6",
    "ipv6",
    "ip6_address",
    "ip6_addresses",
    "ipv6_addresses",
    "ipv6_link_local_address",
    "lan_ip",
    "last_wan_ip",
    "wan_ip",
    "gateway",
    "gateway_v6",
    "dns",
    "nameservers",
    "nameservers_dynamic",
    # Hardware and account identifiers. "gw" and "sw" are short but are the
    # gateway's and switch's own identifiers in the controller payload.
    "mac",
    "ap_mac",
    "gw",
    "gw_mac",
    "sw",
    "sw_mac",
    "bssid",
    "chassis_id",
    "serial",
    "serial_number",
    "hardware_uuid",
    "sha_256",
    "dns_shield_server_list_hash",
    "_id",
    "oid",
    "anon_id",
    "anonymous_id",
    "connection_network_id",
    "device_domain",
    "device_id",
    "external_id",
    "hash_id",
    "native_networkconf_id",
    "site_id",
    "hostname",
    # Speedtest server location, which locates the subscriber too. The
    # "server_" spellings are the canonical names the integration copies
    # these into, and would otherwise slip past the raw ones above.
    "provider_url",
    "server_provider_url",
    "server_city",
    "city",
    "lat",
    "lon",
    "latitude",
    "longitude",
    # The gateway's per-WAN ISP lookup (its geo_info blocks). Together with
    # the location above, the operator and its AS number narrow a
    # subscriber down much as an address does. Redaction replaces values,
    # not keys, and leaves nulls alone, so whether the controller populated
    # each field - the thing worth debugging - is still visible.
    # "country_name" is left intact: a country on its own identifies nobody.
    "asn",
    "isp_asn",
    "isp",
    "isp_name",
    "isp_org",
    "isp_organization",
    "organization",
}

# A key ending in any of these is redacted whether or not it is listed above.
# The controller sends hundreds of fields and gains more with each firmware,
# so an exact list is always one release behind - and a field only turns out
# to be missing from it after someone has posted their diagnostics publicly.
# No field the WAN logic reads ends in any of these: the closest is
# "port_idx", which is not "_id", and "sw_version", which is not "sw".
REDACT_SUFFIXES: Final[tuple[str, ...]] = (
    "_id",
    "_uuid",
    "_token",
    "_key",
    "_authkey",
    "_hash",
    "_mac",
    "_ip",
    "_secret",
    "_password",
    "_fingerprint",
)


# Values that identify a subscriber whatever field they arrive in. The key
# lists above are the first line and will always trail the firmware; these
# shapes are the second, and they do not care what a new field is called.
#
# Deliberately narrow. Each pattern is anchored and matches a whole value,
# so a model name, an interface name or a status word cannot trip it.
_MAC_RE: Final = re.compile(r"[0-9a-f]{2}([:-])(?:[0-9a-f]{2}\1){4}[0-9a-f]{2}", re.I)
_IPV4_RE: Final = re.compile(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?:/\d{1,2})?")
# Either the "::" run only IPv6 has, or the full eight groups. "12:34:56"
# is a clock reading, not an address, and does not match either.
_IPV6_RE: Final = re.compile(
    r"(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?:%[0-9a-z]+)?(?:/\d{1,3})?", re.I
)
# Opaque identifiers: object ids, hashes, keys. Long enough that a colour,
# a short code or a serial fragment cannot reach it by accident.
_HEX_TOKEN_RE: Final = re.compile(r"[0-9a-f]{24,}", re.I)
_EMAIL_RE: Final = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

# Keys whose values are never judged by shape, because something harmless
# there legitimately looks like an address. Firmware versions are the
# reason this exists: "6.5.55.0" is a dotted quad and is exactly the field
# a bug report needs.
VALUE_SAFE_KEYS: Final[frozenset[str]] = frozenset(
    {"version", "sw_version", "firmware_version", "required_version", "board_rev"}
)
VALUE_SAFE_SUFFIXES: Final[tuple[str, ...]] = ("_version", "_rev")


def _should_redact(key: Any) -> bool:
    return isinstance(key, str) and (key in TO_REDACT or key.endswith(REDACT_SUFFIXES))


def _value_judged_by_shape(key: Any) -> bool:
    """Whether a value under this key may be redacted for how it looks."""
    if not isinstance(key, str):
        return True
    return not (key in VALUE_SAFE_KEYS or key.endswith(VALUE_SAFE_SUFFIXES))


def _looks_identifying(value: Any) -> bool:
    """Whether a value is an address or an opaque identifier on its face.

    Catches the field a firmware update introduced last week, which no
    list here has heard of yet, and which is the way a diagnostics file
    posted to a public issue actually leaks something.
    """
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if len(candidate) < 7:
        # Shorter than the shortest thing worth matching ("1.1.1.1"), so
        # nothing here can be an address.
        return False
    if _MAC_RE.fullmatch(candidate) or _EMAIL_RE.fullmatch(candidate):
        return True
    if _HEX_TOKEN_RE.fullmatch(candidate):
        return True
    if _IPV4_RE.fullmatch(candidate):
        # Every octet in range, so "6.5.55.300" stays a version string.
        octets = _IPV4_RE.fullmatch(candidate).groups()
        return all(int(octet) <= 255 for octet in octets)
    if "::" in candidate or candidate.count(":") == 7:
        return bool(_IPV6_RE.fullmatch(candidate))
    return False


def _redact_value(key: Any, value: Any) -> Any:
    """One value, redacted by its key's name or by its own shape."""
    if value is None or (isinstance(value, str) and not value):
        # Nulls and blanks reveal nothing, and replacing them only hides
        # that the controller left a field unset - which is often the
        # answer to the question being asked.
        return value
    if _should_redact(key):
        return REDACTED
    if isinstance(value, (Mapping, list)):
        return _redact(value, key)
    if _value_judged_by_shape(key) and _looks_identifying(value):
        return REDACTED
    return value


def _redact(data: Any, parent_key: Any = None) -> Any:
    """Recursively redact by key name, and by the shape of the value.

    Home Assistant's async_redact_data matches keys exactly, so this adds
    the suffix rules above and then a shape check, which is what stops a
    field nobody has seen before from carrying an address into a public
    issue.

    Items inside a list inherit the key the list arrived under, so a list
    of addresses under a safe-by-name key is still judged on its contents.
    """
    if isinstance(data, list):
        return [_redact_value(parent_key, item) for item in data]
    if not isinstance(data, Mapping):
        return data
    return {key: _redact_value(key, value) for key, value in data.items()}


# The blocks find_gateway weighs, reported per device as present or not.
_WAN_BLOCKS: Final[tuple[str, ...]] = (
    "last_wan_interfaces",
    "wan",
    *(f"wan{i}" for i in range(1, MAX_WAN_INTERFACES + 1)),
)


def _device_summary(devices: list[dict], gateway: dict[str, Any] | None) -> list[dict]:
    """Everything on the site other than the gateway, named but not dumped.

    Only the gateway's own payload drives this integration, so the rest is
    reduced to what find_gateway decides on, which is what it takes to
    spot a misidentified gateway - an Express in mesh mode taken for the
    router beside it, say. None of it identifies anyone.
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
                "mode": device.get("device_mode_override"),
                "uplink_depth": device.get("uplink_depth"),
                "wan_blocks": [key for key in _WAN_BLOCKS if key in device],
            }
        )
    return summary


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UniFiWanConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
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
            "data": _redact(dict(entry.data)),
            "options": _redact(dict(entry.options)),
        },
    }

    if data is None:
        diagnostics["error"] = "coordinator has no data"
        return diagnostics

    active_wan, match_reason = data.active_wan
    # Redacted like everything else: nothing here carries an address today,
    # but a field added later should not leak by having been overlooked.
    diagnostics["derived"] = _redact(
        {
            # What the integration concluded, so a wrong conclusion can be told
            # apart from wrong data.
            "wan_numbers": runtime.wan_numbers,
            "active_wan": active_wan,
            "match_reason": match_reason,
            "wan_alive": data.wan_alive,
            "wan_status": data.wan_status,
            "speedtest": data.speedtest,
            "per_wan_speedtest": data.per_wan_speedtest,
            # What the ISP sensors read: the gateway's geo_info blocks merged
            # down to one record per WAN. Shows at a glance which WANs the
            # gateway looked up and which fields it filled in.
            "geo_info": data.geo_info,
            # What the sensors are actually showing, which can lag the above.
            "latched_speedtest_results": runtime.speedtest.results,
            "per_wan_api_available": data.speedtest_history_raw is not None,
            "targeted_speedtest_supported": runtime.client.targeted_speedtest_supported,
            "per_wan_speedtest_honoured": runtime.speedtest.per_wan_supported,
            "auto_speedtest_enabled": runtime.speedtest.auto_enabled,
            "speedtest_running": runtime.speedtest.running,
        }
    )
    diagnostics["controller"] = {
        # The gateway verbatim: WAN sections, uplink, speedtest-status,
        # port_table and everything else it reports.
        "gateway_device": _redact(data.gateway or {}),
        # The per-WAN speedtest API's raw response, the source of the
        # per-WAN sensors.
        "speedtest_history": _redact(data.speedtest_history_raw or {}),
        "other_devices": _device_summary(data.devices, data.gateway),
    }
    return diagnostics
