from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "unifi_wan"

CONF_HOST: Final = "host"
CONF_API_KEY: Final = "api_key"
CONF_SITE: Final = "site"
CONF_VERIFY_SSL: Final = "verify_ssl"

CONF_SCAN_INTERVAL: Final = "scan_interval"
DEFAULT_SCAN_INTERVAL: Final = 30
LEGACY_CONF_DEVICE_INTERVAL: Final = "device_interval"

CONF_RATE_INTERVAL: Final = "rate_interval_seconds"
DEFAULT_RATE_INTERVAL: Final = 5

CONF_AUTO_SPEEDTEST: Final = "auto_speedtest"
CONF_AUTO_SPEEDTEST_MINUTES: Final = "auto_speedtest_minutes"
DEFAULT_AUTO_SPEEDTEST: Final = True
DEFAULT_AUTO_SPEEDTEST_MINUTES: Final = 60

DEFAULT_SITE: Final = "default"
DEFAULT_VERIFY_SSL: Final = False

PLATFORMS: Final = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
]

SIGNAL_SPEEDTEST_RUNNING: Final = f"{DOMAIN}_speedtest_running"
SIGNAL_AUTO_SPEEDTEST_CHANGED: Final = f"{DOMAIN}_auto_speedtest_changed"
SIGNAL_SPEEDTEST_RESULT: Final = f"{DOMAIN}_speedtest_result"
SERVICE_RUN_SPEEDTEST: Final = "run_speedtest"
ATTR_WAN: Final = "wan"

# How long to wait for a triggered speedtest to finish, and how often to
# poll the controller for its result while waiting.
SPEEDTEST_TIMEOUT_SECONDS: Final = 300
SPEEDTEST_POLL_SECONDS: Final = 15

GATEWAY_DEVICES: Final = ["udm", "ugw", "uxg", "uxg-pro", "ucg-ultra", "ucg"]
MAX_WAN_INTERFACES: Final = 4

# ISP and geolocation details the controller records alongside a speedtest
# result, as {canonical name: record spellings to try, best first}.
#
# These describe the line the test ran over - the public address it went out
# from and the operator that address belongs to - which is what identifies a
# WAN's ISP. They are only ever read from a speedtest record itself: the
# uplink section's own address is not evidence of what a test used, and the
# gateway block's "server" sub-object describes the speedtest server rather
# than the subscriber's line.
#
# Firmware differs over the spellings and older records carry none of them,
# so every field is optional and an absent one stays unset.
SPEEDTEST_ISP_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "isp_name": ("isp_name", "isp", "client_isp"),
    "isp_organization": ("isp_organization", "isp_org", "organization", "org"),
    "asn": ("asn", "isp_asn", "as_number"),
    "city": ("city", "client_city"),
    "country_name": ("country_name", "country", "client_country"),
    "ip": ("ip", "public_ip", "external_ip", "client_ip", "source_ip"),
}