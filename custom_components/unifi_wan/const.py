"""Constants for the UniFi WAN integration.

Deliberately free of Home Assistant imports, so that this module and the
parsing in models.py can be exercised by tests and tooling without a
Home Assistant install. PLATFORMS lives in __init__.py for that reason.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "unifi_wan"

CONF_HOST: Final = "host"
CONF_API_KEY: Final = "api_key"
CONF_SITE: Final = "site"
CONF_VERIFY_SSL: Final = "verify_ssl"

CONF_SCAN_INTERVAL: Final = "scan_interval"
DEFAULT_SCAN_INTERVAL: Final = 30
# The floor is applied at setup as well as in the options dialog: a value
# stored by an older release, or edited by hand, would otherwise be taken
# as given - and zero means a poll with no interval at all.
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 3600
LEGACY_CONF_DEVICE_INTERVAL: Final = "device_interval"

CONF_RATE_INTERVAL: Final = "rate_interval_seconds"
DEFAULT_RATE_INTERVAL: Final = 5
# Zero disables the fast poll entirely; anything above it is held to this
# floor, since a sub-second poll would spend the console's rate limit on
# nothing a dashboard can render.
MIN_RATE_INTERVAL: Final = 1
MAX_RATE_INTERVAL: Final = 600

# What the line is sold as, in Mbit/s, for comparing a speedtest against.
# Zero means "not configured": the integration has no way to know a
# subscriber's plan, so the comparison sensors stay unknown until told.
CONF_EXPECTED_DOWNLOAD: Final = "expected_download_mbps"
CONF_EXPECTED_UPLOAD: Final = "expected_upload_mbps"
DEFAULT_EXPECTED_SPEED: Final = 0.0
MAX_EXPECTED_SPEED: Final = 10000.0

# How far a result may sit from the expected figure and still count as
# meeting it, as a fraction. A line is never sold as an exact number and
# a speedtest is not a precise instrument, so a band is the only honest
# comparison; 2% either way is the default.
SPEED_TOLERANCE: Final = 0.02

# The three states the comparison sensors report. Spelled as they are
# displayed, because they are the sensor's state rather than a key.
SPEED_AS_EXPECTED: Final = "Expected"
SPEED_FASTER: Final = "Faster"
SPEED_SLOWER: Final = "Slower"
SPEED_COMPARISON_OPTIONS: Final[list[str]] = [
    SPEED_AS_EXPECTED,
    SPEED_FASTER,
    SPEED_SLOWER,
]

CONF_AUTO_SPEEDTEST: Final = "auto_speedtest"
CONF_AUTO_SPEEDTEST_MINUTES: Final = "auto_speedtest_minutes"
DEFAULT_AUTO_SPEEDTEST: Final = True
DEFAULT_AUTO_SPEEDTEST_MINUTES: Final = 60
MIN_AUTO_SPEEDTEST_MINUTES: Final = 1
MAX_AUTO_SPEEDTEST_MINUTES: Final = 10080

DEFAULT_SITE: Final = "default"
DEFAULT_VERIFY_SSL: Final = False

SIGNAL_SPEEDTEST_RUNNING: Final = f"{DOMAIN}_speedtest_running"
SIGNAL_AUTO_SPEEDTEST_CHANGED: Final = f"{DOMAIN}_auto_speedtest_changed"
SIGNAL_SPEEDTEST_RESULT: Final = f"{DOMAIN}_speedtest_result"
SERVICE_RUN_SPEEDTEST: Final = "run_speedtest"
SERVICE_DUMP_RAW_DATA: Final = "dump_raw_data"
ATTR_WAN: Final = "wan"
ATTR_KEEP: Final = "keep"

# Where unredacted dumps are written, under the Home Assistant config
# directory, and how many to keep per config entry before the oldest are
# removed. They are kept out of the config root because they are files the
# user is meant to find, copy off the host and then forget about.
DUMP_DIR_NAME: Final = "unifi_wan_dumps"
DEFAULT_DUMP_KEEP: Final = 10
MAX_DUMP_KEEP: Final = 100
# A dump past this size is worth a warning rather than an info line: it is
# mostly the site's device list, several are kept, and they sit in the
# config directory that gets backed up.
DUMP_SIZE_WARN_BYTES: Final = 20 * 1024 * 1024

# How long any single request to the console may take. Home Assistant's
# shared aiohttp session sets no timeout of its own, so without this the
# library default of five minutes applies and a console that accepts the
# connection but never answers stalls a poll for that long.
REQUEST_TIMEOUT_SECONDS: Final = 30

# Statuses that mean "this console does not offer that endpoint", as
# opposed to a request that simply failed. Only these are evidence worth
# remembering for the rest of the session; anything else (a refused
# connection, a 5xx, a rate limit) says nothing about what the console
# supports and must not disable a feature permanently.
UNSUPPORTED_STATUSES: Final[frozenset[int]] = frozenset({400, 401, 403, 404, 405})

# The same question for the per-WAN speedtest history, which is answered
# more strictly: 404/405 are the console saying the endpoint is not there,
# while 400/401/403 can equally be a key whose permissions changed, which a
# re-authentication fixes without a restart.
HISTORY_UNSUPPORTED_STATUSES: Final[frozenset[int]] = frozenset({404, 405})

# How long to wait for a triggered speedtest to finish, and how often to
# poll the controller for its result while waiting.
SPEEDTEST_TIMEOUT_SECONDS: Final = 300
SPEEDTEST_POLL_SECONDS: Final = 15

# How close a per-WAN speedtest record has to be, in seconds, to the
# gateway's own last-run block for the two to be the same run. Used only
# where the gateway names no interface: a record of the same moment then
# identifies the WAN that run was on, and stops the gateway-wide sensors
# showing a non-active line's throughput.
GATEWAY_RESULT_MATCH_SECONDS: Final = 120

# Known gateway "type" values, most specific first. A console running a
# model newer than this list is caught by models._looks_like_gateway, which
# matches on the shape of the payload instead.
GATEWAY_DEVICES: Final = [
    "udm",
    "ugw",
    "uxg",
    "uxg-pro",
    "ucg-ultra",
    "ucg",
    "efg",
    "udr",
]
MAX_WAN_INTERFACES: Final = 4

# The gateway's blocks of per-WAN ISP and geolocation details, in the order
# they are consulted. Each is keyed by WAN network group ("WAN", "WAN2"),
# the same convention as last_wan_interfaces. A later block only fills in a
# field the earlier ones left unset: "last_geo_info" in particular is a
# cut-down record that often carries the operator alone.
WAN_GEO_INFO_BLOCKS: Final[tuple[str, ...]] = (
    "geo_info",
    "active_geo_info",
    "last_geo_info",
)

# ISP and geolocation details to read out of those blocks, as
# {canonical name: spellings to try, best first}.
#
# The controller derives these by looking up each WAN's own public address,
# so they identify the operator of that line. They come only from these
# per-WAN blocks: the speedtest records carry no ISP fields at all, and the
# speedtest block's "server" sub-object is the far end of the test, not the
# subscriber's line - see SPEEDTEST_SERVER_FIELDS for that.
#
# Firmware differs over the spellings and a gateway that performs no lookup
# reports none of them, so every field is optional and an absent one stays
# unset.
WAN_ISP_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "isp_name": ("isp_name", "isp"),
    "isp_organization": ("isp_organization", "isp_org", "organization"),
    "asn": ("asn", "isp_asn"),
    "city": ("city",),
    "country_name": ("country_name", "country"),
}

# Which speedtest server the gateway tested against, read out of the
# "server" sub-object of its speedtest-status block, as
# {canonical name: spellings to try, best first}.
#
# Every name is prefixed "server_" so nothing here can be confused with the
# subscriber-side lookup above: "city" in this sub-object is the server's
# city, and labelling a line with it would be wrong twice over.
#
# Unlike the ISP details this is not reported per WAN. The gateway records
# one server, belonging to whichever run finished last, so it is attributed
# to a WAN on the same evidence as the throughput it arrived with.
SPEEDTEST_SERVER_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "server_provider": ("provider", "sponsor", "name"),
    "server_provider_url": ("provider_url", "url"),
    "server_city": ("city",),
    "server_country": ("country", "country_name"),
}
