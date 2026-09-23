"""Data shapes and the pure parsing the controller's payloads need.

Everything here is a plain function over dicts: no Home Assistant, no
network, no state. That is deliberate - the awkward parts of this
integration are all decisions about what the controller meant, and keeping
them free of I/O is what makes them testable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from .const import (
    DEFAULT_EXPECTED_SPEED,
    DEFAULT_SITE,
    DEFAULT_SPEED_TOLERANCE_PERCENT,
    GATEWAY_DEVICES,
    GATEWAY_RESULT_MATCH_SECONDS,
    MAX_EXPECTED_SPEED,
    MAX_SPEED_TOLERANCE_PERCENT,
    MAX_WAN_INTERFACES,
    MIN_SPEED_TOLERANCE_PERCENT,
    SPEED_AS_EXPECTED,
    SPEED_FASTER,
    SPEED_SLOWER,
    SPEED_TOLERANCE,
    SPEEDTEST_SERVER_FIELDS,
    WAN_GEO_INFO_BLOCKS,
    WAN_ISP_FIELDS,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class UniFiWanData:
    """Structured data to avoid repeated list parsing in sensors."""

    devices: list[dict]
    gateway: dict[str, Any] | None
    uplink: dict[str, Any]
    wan: dict[int, dict[str, Any]]
    wan_alive: dict[int, bool]
    wan_status: dict[int, str]
    speedtest: dict[str, Any]
    # Per-WAN speedtest results straight from the controller, keyed by WAN
    # number. Empty when the controller has no per-WAN speedtest API.
    per_wan_speedtest: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Per-WAN ISP and geolocation details from the gateway's own lookup,
    # keyed by WAN number. Empty when the gateway reports no such block.
    geo_info: dict[int, dict[str, Any]] = field(default_factory=dict)
    # The per-WAN speedtest API's last response, kept verbatim so diagnostics
    # can show what the controller actually returned. None when unavailable.
    speedtest_history_raw: dict[str, Any] | None = None
    # The per-WAN results this entry has latched, by WAN number. Shared by
    # reference with the runtime store, so the gateway-wide sensors read
    # exactly what the per-WAN sensors show rather than recomputing it.
    # Empty until the entry is set up, and for data built outside one.
    speedtest_latched: dict[int, dict[str, Any]] = field(default_factory=dict)

    @cached_property
    def active_wan(self) -> tuple[int | None, str]:
        """Which WAN is the active uplink, and how it was matched.

        Cached for the life of this object, which is one poll: around
        twenty sensors ask the same question of the same payload, and the
        answer cannot change until the next refresh replaces it. The cache
        lives outside the dataclass fields, so ``asdict`` still produces
        the payload alone.
        """
        return resolve_active_wan(self)


def _is_routable_ipv6(addr: str | None) -> bool:
    """True only for public/ULA IPv6 addresses worth exposing as a WAN IP.

    Skips link-local (fe80::/10), loopback (::1), unspecified (::) and
    obvious junk so we don't mislead users into thinking they have IPv6
    when their ISP only auto-assigned a link-local.
    """
    if not addr or not isinstance(addr, str):
        return False
    a = addr.strip().lower().split("%", 1)[0].split("/", 1)[0]
    if not a or ":" not in a:
        return False
    if a in ("::", "::1"):
        return False
    # fe80::/10 link-local spans the fe8/fe9/fea/feb prefixes.
    return not a.startswith(("fe8", "fe9", "fea", "feb"))


def get_ip6_from(data: dict[str, Any]) -> str | None:
    """Extract a routable IPv6 address from a data dict, trying multiple
    field names and formats. Link-local addresses are ignored.
    """
    for key in ("ip6", "ip6_address", "ipv6_address"):
        val = data.get(key)
        if isinstance(val, str) and _is_routable_ipv6(val):
            return val
    for key in ("ipv6", "ip6_addresses", "ipv6_addresses"):
        val = data.get(key)
        if val and isinstance(val, list):
            for entry in val:
                if isinstance(entry, str) and _is_routable_ipv6(entry):
                    return entry
                if isinstance(entry, dict):
                    addr = entry.get("address") or entry.get("ip6") or entry.get("ip")
                    if isinstance(addr, str) and _is_routable_ipv6(addr):
                        return addr
    return None


def wan_group_to_number(group: Any) -> int | None:
    """Map a controller WAN group name to a WAN number: "WAN" is WAN1,
    "WAN2" is WAN2, and so on.

    The single place this convention is spelled out. The controller uses it
    for last_wan_interfaces keys, for the per-WAN speedtest API's
    wan_networkgroup, for its geo_info blocks and for the interface names
    the speedtest command takes, so every one of those reads it from here.
    """
    if not isinstance(group, str):
        return None
    s = group.strip().upper()
    if s == "WAN":
        return 1
    if s.startswith("WAN") and s[3:].isdigit():
        return int(s[3:])
    return None


def normalise_interface(iface: Any) -> str | None:
    """Clean a controller-reported interface name.

    The speedtest block prefixes the interface with "if!" (e.g. "if!eth6")
    and reports an empty string when it has nothing to say.
    """
    if not isinstance(iface, str):
        return None
    s = iface.strip()
    if s.startswith("if!"):
        s = s[3:]
    return s or None


def first_present(entry: dict[str, Any], *keys: str) -> Any:
    """First key holding a non-null value.

    Not dict.get(a, dict.get(b)): the controller sends a key with an explicit
    null while an older spelling alongside it still carries the figure, and
    the default form would return that null instead of falling through.
    """
    for key in keys:
        value = entry.get(key)
        if value is not None:
            return value
    return None


def speedtest_epoch(value: Any) -> int | None:
    """Coerce a speedtest timestamp to epoch seconds.

    The v2 API reports milliseconds while the gateway block reports
    seconds; anything past the year 5138 in seconds is really milliseconds.
    """
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return ts // 1000 if ts > 100_000_000_000 else ts


def is_newer(candidate: Any, stored: Any) -> bool:
    """Whether one speedtest timestamp is later than another.

    Both sides are coerced through speedtest_epoch so a millisecond
    timestamp from the per-WAN API compares correctly against the gateway
    block's seconds. Anything uncomparable counts as not newer, so a result
    is only ever replaced on evidence that it has been superseded.
    """
    new_ts = speedtest_epoch(candidate)
    if new_ts is None:
        return False
    old_ts = speedtest_epoch(stored)
    return old_ts is None or new_ts > old_ts


def expected_speed(value: Any) -> float:
    """An expected-speed option as a plain number of Mbit/s.

    Anything unreadable, negative or absent becomes zero, which the
    comparison reads as "not configured" rather than as a line sold as
    zero. Setup and the options dialog both normalise through this, so a
    value stored by an older release or edited by hand is read the same
    way as one just typed.
    """
    try:
        speed = float(value)
    except (TypeError, ValueError):
        return DEFAULT_EXPECTED_SPEED
    if speed <= 0:
        return DEFAULT_EXPECTED_SPEED
    return min(speed, MAX_EXPECTED_SPEED)


def speed_tolerance_percent(value: Any) -> float:
    """A tolerance option as the percentage it is entered and stored as.

    Clamped to something meaningful: past half the expected figure the
    comparison stops saying anything. Zero is allowed and means only an
    exact match counts, which is a real choice rather than a way of
    switching the comparison off.

    Anything unreadable falls back to the default rather than to zero, so
    a mangled option does not silently turn every result into Faster or
    Slower.
    """
    try:
        percent = float(value)
    except (TypeError, ValueError):
        percent = DEFAULT_SPEED_TOLERANCE_PERCENT
    return min(max(percent, MIN_SPEED_TOLERANCE_PERCENT), MAX_SPEED_TOLERANCE_PERCENT)


def speed_tolerance(value: Any) -> float:
    """A tolerance option, as the fraction the comparison uses.

    Stored as a percentage, which is how the band is thought about; see
    speed_tolerance_percent for how it is read.
    """
    return speed_tolerance_percent(value) / 100


def normalise_host(host: Any) -> str:
    """A console address as typed, reduced to the host alone.

    Whitespace and any scheme are dropped, the name is lowercased and a
    path is cut off, so "https://UDM.local/" and "udm.local" are one
    console.
    """
    host = str(host or "").strip().lower()
    host = host.removeprefix("https://").removeprefix("http://")
    return host.split("/", 1)[0]


def normalise_site(site: Any) -> str:
    """A site name as typed, without the whitespace a paste brings along.

    Validation always probed the stripped name, so a padded one passed and
    then failed every poll; blank means the console's default site.
    """
    return str(site or "").strip() or DEFAULT_SITE


def config_unique_id(host: Any, site: Any) -> str:
    """One configured console and site, however its address was typed."""
    return f"{normalise_host(host)}-{normalise_site(site)}"


def same_mac(first: Any, second: Any) -> bool:
    """Whether two MAC addresses name the same interface, in any spelling."""

    def canonical(mac: Any) -> str:
        return str(mac or "").strip().lower().replace("-", ":")

    return bool(canonical(first)) and canonical(first) == canonical(second)


def speed_comparison(
    measured: Any, expected: Any, tolerance: float = SPEED_TOLERANCE
) -> str | None:
    """How a speedtest result compares with what the line is sold as.

    Returns "Faster" or "Slower" only when the result is outside the
    tolerance band either way, and "Expected" inside it - a line is never
    sold as an exact number and a speedtest is not a precise instrument,
    so anything tighter would flip between states on noise alone. The
    boundary itself counts as expected: exactly 2% down is still the line
    delivering what it promised.

    None where there is nothing to compare: no result yet, or no expected
    figure configured. Zero expected means not configured rather than a
    line sold as zero, so it answers None rather than "Faster" forever.
    """
    try:
        measured_value = float(measured)
        expected_value = float(expected)
    except (TypeError, ValueError):
        return None
    if expected_value <= 0:
        return None
    limit = expected_value * tolerance
    difference = measured_value - expected_value
    if difference > limit:
        return SPEED_FASTER
    if difference < -limit:
        return SPEED_SLOWER
    return SPEED_AS_EXPECTED


def _extract_geo_info(gateway: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    """Per-WAN ISP and geolocation details, keyed by WAN number.

    The gateway reports these in its own blocks (see WAN_GEO_INFO_BLOCKS),
    each keyed by WAN network group exactly as last_wan_interfaces is, so
    every WAN gets the operator of its own line rather than sharing
    whichever one was looked up last. The blocks overlap and disagree about
    how much they carry, so they are merged in order and an earlier block's
    value is never overwritten by a later one.

    Every field is optional: a gateway that performs no lookup reports none
    of them and the corresponding sensors stay unknown rather than being
    filled in from somewhere else. Blank strings are treated as absent -
    the controller uses them where it has nothing to report, and they would
    otherwise show as an empty sensor state.
    """
    merged: dict[int, dict[str, Any]] = {}
    for block_key in WAN_GEO_INFO_BLOCKS:
        block = (gateway or {}).get(block_key)
        if not isinstance(block, dict):
            continue
        for group, info in block.items():
            wan_number = wan_group_to_number(group)
            if wan_number is None or not isinstance(info, dict):
                continue
            target = merged.setdefault(wan_number, dict.fromkeys(WAN_ISP_FIELDS))
            for name, keys in WAN_ISP_FIELDS.items():
                if target.get(name) is not None:
                    continue
                value = first_present(info, *keys)
                if isinstance(value, str):
                    value = value.strip() or None
                target[name] = value
    return merged


def _extract_server_fields(status: dict[str, Any]) -> dict[str, Any]:
    """Which speedtest server the gateway's last run tested against.

    Read from the "server" sub-object alone, never from the block around
    it: the two use some of the same key names for different things, and
    the outer ones belong to the subscriber's line.

    Every field is optional - firmware that names no server reports none of
    them - so an absent one stays unset rather than being filled in from
    elsewhere. Blank strings are treated as absent for the same reason they
    are in the ISP lookup: the controller uses them where it has nothing to
    report.
    """
    raw = status.get("server")
    server = raw if isinstance(raw, dict) else {}
    fields: dict[str, Any] = {}
    for name, keys in SPEEDTEST_SERVER_FIELDS.items():
        value = first_present(server, *keys)
        if isinstance(value, str):
            value = value.strip() or None
        fields[name] = value
    return fields


def attributed_server(
    gateway_server: dict[str, Any],
    belongs_to_wan: bool,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    """The speedtest server to record against one WAN.

    Carried forward from what that WAN already had unless the gateway's
    block both belongs to it and names a server, so a block caught
    mid-write does not blank the sensors, and a run on one WAN never
    rewrites another's.

    Always returns every field, including when there is nothing to record.
    The caller compares the record it builds against the stored one to
    decide whether anything changed, and one missing these keys would
    differ on every refresh.
    """
    if belongs_to_wan and any(v is not None for v in gateway_server.values()):
        return dict(gateway_server)
    prev = previous or {}
    return {name: prev.get(name) for name in SPEEDTEST_SERVER_FIELDS}


def _extract_speedtest(
    gateway: dict[str, Any] | None, uplink: dict[str, Any]
) -> dict[str, Any]:
    """Normalise the gateway's speedtest result into a single dict.

    The authoritative source is the gateway's ``speedtest-status`` block,
    which is the only place the controller records *which* interface the
    test actually ran on (``source_interface``). The ``uplink`` section
    carries the same figures under older names, and firmware that reports
    no block at all is the reason it is read.

    The two are separate records of separate runs, so one result is taken
    whole from one of them and they are never merged field by field. The
    gateway rewrites its block around a run and can be caught with a field
    missing; filling that field in from the uplink section produced a result
    pairing today's throughput with the timestamp of whichever older run the
    legacy fields last caught - months earlier on firmware that no longer
    maintains them.
    """
    raw = gateway.get("speedtest-status") if gateway else None
    status = raw if isinstance(raw, dict) else {}
    result = {
        "down": status.get("xput_download"),
        "up": status.get("xput_upload"),
        "ping": status.get("latency"),
        "lastrun": status.get("rundate"),
        "status": status.get("status_summary"),
        # None when the controller does not say; never guessed.
        "source_interface": normalise_interface(status.get("source_interface")),
        # The far end of that run. Only the block's own "server" sub-object
        # is consulted, never the uplink section, which describes the line.
        **_extract_server_fields(status),
    }
    if any(result[key] is not None for key in ("down", "up", "lastrun")):
        return result

    # The gateway has no block, or none it has filled in yet. The uplink's
    # legacy fields are then the only record of a run there is, and they are
    # taken as one - a result of theirs is judged against the others by its
    # own timestamp, so a stale one simply loses.
    return {
        "down": uplink.get("xput_down"),
        "up": uplink.get("xput_up"),
        "ping": uplink.get("speedtest_ping"),
        "lastrun": uplink.get("speedtest_lastrun"),
        "status": uplink.get("speedtest_status"),
        "source_interface": None,
        **dict.fromkeys(SPEEDTEST_SERVER_FIELDS),
    }


def parse_speedtest_history(
    body: dict[str, Any] | None, wan: dict[int, dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    """Latest speedtest per WAN from the v2 API's records.

    Each record names its WAN directly (wan_networkgroup), which removes
    the guesswork of deciding which interface a global result belonged to.
    Records are applied oldest first so the newest wins per WAN.
    """
    if not isinstance(body, dict):
        return {}
    entries = body.get("data")
    if not isinstance(entries, list):
        return {}

    results: dict[int, dict[str, Any]] = {}
    for entry in sorted(
        (e for e in entries if isinstance(e, dict)),
        key=lambda e: speedtest_epoch(e.get("time")) or 0,
    ):
        wan_number = wan_group_to_number(
            entry.get("wan_networkgroup") or entry.get("wan_group")
        )
        if wan_number is None:
            wan_number = interface_to_wan_number(
                entry.get("interface_name")
                or entry.get("interface")
                or entry.get("ifname"),
                wan,
            )
        if wan_number is None:
            continue
        down = first_present(entry, "download_mbps", "xput_down", "download")
        up = first_present(entry, "upload_mbps", "xput_up", "upload")
        ping = first_present(entry, "latency_ms", "speedtest_ping", "latency")
        if down is None and up is None:
            continue
        if down is None or up is None:
            # A spelling we don't know would show up here as a permanently
            # missing figure, so say which record and what it contained.
            _LOGGER.debug(
                "Speedtest record for WAN%s has no %s value (record keys: %s)",
                wan_number,
                "download" if down is None else "upload",
                sorted(entry),
            )
        results[wan_number] = {
            "down": down,
            "up": up,
            "ping": ping,
            "lastrun": speedtest_epoch(entry.get("time")),
            "source": "speedtest_api",
            "requested_wan": None,
        }
    return results


def _log_raw_payload(gateway: dict[str, Any] | None, devices: list[dict]) -> None:
    """Emit a debug log that surfaces the gateway fields behind the IPv6,
    WAN identification and speedtest sensors so users can see what the
    controller is actually returning. Enable with:
        logger:
          default: warning
          logs:
            custom_components.unifi_wan: debug
    """
    if not _LOGGER.isEnabledFor(logging.DEBUG):
        return
    if not gateway:
        _LOGGER.debug(
            "UniFi raw payload: no gateway device found in %d devices", len(devices)
        )
        return
    uplink = gateway.get("uplink") or {}
    wan_keys = [
        k for k in gateway if k == "wan" or (k.startswith("wan") and k[3:].isdigit())
    ]
    wan_dump = {k: gateway.get(k) for k in wan_keys}
    port_table = gateway.get("port_table")
    ports = (
        [
            {k: p.get(k) for k in ("port_idx", "name", "ifname")}
            for p in port_table
            if isinstance(p, dict)
        ]
        if isinstance(port_table, list)
        else None
    )
    _LOGGER.debug(
        "UniFi raw gateway debug: uplink_keys=%s uplink.ip=%s uplink.ip6=%s "
        "uplink.name=%s uplink.physical_ports=%s speedtest-status=%s "
        "wan_blocks=%s last_wan_interfaces=%s port_table=%s",
        sorted(uplink.keys()),
        uplink.get("ip"),
        uplink.get("ip6"),
        uplink.get("name"),
        uplink.get("physical_ports"),
        gateway.get("speedtest-status"),
        wan_dump,
        gateway.get("last_wan_interfaces"),
        ports,
    )


def _looks_like_gateway(device: dict[str, Any]) -> bool:
    """Whether a device behaves like the site's internet gateway.

    Used only after the model list above has found nothing. A device that
    reports an uplink together with a WAN section or a WAN interface table
    is routing the site's traffic whatever Ubiquiti called it, which is
    what keeps a console that has just shipped a new gateway model working
    without waiting for a release here.
    """
    if "uplink" not in device:
        return False
    if isinstance(device.get("last_wan_interfaces"), dict):
        return True
    return any(
        isinstance(device.get(key), dict)
        for key in (
            "wan",
            "wan1",
            *(f"wan{i}" for i in range(2, MAX_WAN_INTERFACES + 1)),
        )
    )


def find_gateway(devices: list[dict]) -> dict[str, Any] | None:
    """The site's gateway, by model first and by shape second.

    An adopted device with an uplink wins over one without, so a console
    holding a record of a gateway it no longer manages does not displace
    the live one.
    """

    def best(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        return sorted(
            candidates, key=lambda d: (not d.get("adopted", True), "uplink" not in d)
        )[0]

    dicts = [d for d in devices if isinstance(d, dict)]
    for gateway_type in GATEWAY_DEVICES:
        candidates = [d for d in dicts if d.get("type") == gateway_type]
        if candidates:
            return best(candidates)

    shaped = [d for d in dicts if _looks_like_gateway(d)]
    if shaped:
        gateway = best(shaped)
        _LOGGER.debug(
            "No device matched a known gateway type; using %r (type %r), which "
            "reports an uplink and WAN interfaces",
            gateway.get("model"),
            gateway.get("type"),
        )
        return gateway
    return None


def _wan_numbers_from(
    gateway: dict[str, Any] | None, last_wan_interfaces: dict[str, Any]
) -> set[int]:
    """Which WAN numbers this gateway has."""
    numbers = {
        number
        for key in last_wan_interfaces
        if (number := wan_group_to_number(key)) is not None
    }
    if numbers or not gateway:
        return numbers
    # Fallback: last_wan_interfaces is absent, so read the WAN sections.
    if gateway.get("wan1") or gateway.get("wan"):
        numbers.add(1)
    for i in range(2, MAX_WAN_INTERFACES + 1):
        if gateway.get(f"wan{i}"):
            numbers.add(i)
    return numbers


def _normalise_ip6(section: dict[str, Any], description: str) -> None:
    """Put the section's routable IPv6, if it has one, in its "ip6" key.

    Whatever the controller left there is re-checked rather than taken as
    given: a gateway that reports a link-local address in "ip6" would
    otherwise have it shown as that line's IPv6, which is the one thing
    _is_routable_ipv6 exists to prevent. The raw payload is kept verbatim
    in diagnostics and dumps, so nothing is lost by dropping a value no
    sensor should display.
    """
    ip6 = get_ip6_from(section)
    if ip6:
        section["ip6"] = ip6
    elif stale := section.pop("ip6", None):
        # Popped either way, so the controller's "nothing here" empty string
        # does not reach a sensor; only a real address is worth a log line.
        _LOGGER.debug(
            "%s reported IPv6 address %s, which is not routable; leaving the "
            "IPv6 sensor unset",
            description,
            stale,
        )


def extract_wan_data(payload: dict[str, Any] | None) -> UniFiWanData:
    """Process raw JSON into a structured object once."""
    devices = []
    if isinstance(payload, dict):
        devices = payload.get("data", []) or []

    gateway = find_gateway(devices)
    _log_raw_payload(gateway, devices)

    uplink = dict((gateway.get("uplink") or {}) if gateway else {})
    last_wan_interfaces = (gateway.get("last_wan_interfaces") or {}) if gateway else {}
    last_wan_status_raw = (gateway.get("last_wan_status") or {}) if gateway else {}
    if not isinstance(last_wan_interfaces, dict):
        last_wan_interfaces = {}
    if not isinstance(last_wan_status_raw, dict):
        last_wan_status_raw = {}

    wan: dict[int, dict[str, Any]] = {}
    for wan_number in _wan_numbers_from(gateway, last_wan_interfaces):
        if wan_number == 1:
            raw = (gateway.get("wan1") or gateway.get("wan") or {}) if gateway else {}
        else:
            raw = (gateway.get(f"wan{wan_number}") or {}) if gateway else {}
        wan_entry = dict(raw) if isinstance(raw, dict) else {}
        _normalise_ip6(wan_entry, f"WAN{wan_number}")
        wan[wan_number] = wan_entry

    # The uplink's own IPv6, held to the same standard as the WAN sections
    # above, then supplemented from the WAN section describing this same
    # line and from the gateway's root-level fields.
    if not get_ip6_from(uplink):
        # A WAN with a different address is a different line, and its IPv6
        # is not the uplink's to report.
        active_ip = uplink.get("ip")
        for wan_data in wan.values():
            if not wan_data:
                continue
            if active_ip and wan_data.get("ip") != active_ip:
                continue
            if ip6 := wan_data.get("ip6"):
                uplink["ip6"] = ip6
                break
        else:
            if gateway and (ip6 := get_ip6_from(gateway)):
                uplink["ip6"] = ip6
    _normalise_ip6(uplink, "The uplink")

    wan_alive: dict[int, bool] = {}
    wan_status_map: dict[int, str] = {}
    for wan_key, iface_data in last_wan_interfaces.items():
        number = wan_group_to_number(wan_key)
        if number is None:
            continue
        # A null or unexpected value here used to fail the whole poll and
        # mark every entity unavailable.
        alive = (
            iface_data.get("alive", False) if isinstance(iface_data, dict) else False
        )
        wan_alive[number] = bool(alive)
        status = last_wan_status_raw.get(wan_key, "unknown")
        wan_status_map[number] = status if isinstance(status, str) else "unknown"

    return UniFiWanData(
        devices=devices,
        gateway=gateway,
        uplink=uplink,
        wan=wan,
        wan_alive=wan_alive,
        wan_status=wan_status_map,
        speedtest=_extract_speedtest(gateway, uplink),
        geo_info=_extract_geo_info(gateway),
    )


def extract_rates(payload: dict[str, Any] | None) -> UniFiWanData:
    """The gateway's uplink alone, for the fast rate poll.

    That poll runs as often as once a second and feeds two sensors, both
    reading the uplink's byte rates. Parsing the WAN sections, the ISP
    lookup and the speedtest block on every tick - and emitting the debug
    payload log with them - is work nothing consumes, so this stops at the
    uplink.
    """
    devices = []
    if isinstance(payload, dict):
        devices = payload.get("data", []) or []
    gateway = find_gateway(devices)
    return UniFiWanData(
        devices=devices,
        gateway=gateway,
        uplink=dict((gateway.get("uplink") or {}) if gateway else {}),
        wan={},
        wan_alive={},
        wan_status={},
        speedtest={},
    )


def resolve_active_wan(d: UniFiWanData) -> tuple[int | None, str]:
    """Resolve which WAN number is the active uplink, and how it was matched.

    This is the single source of truth used by both the Active WAN ID and
    Active WAN Name sensors, so the two can never point at different
    interfaces. Match order: the uplink's IP against each WAN section's IP,
    then the uplink's port name against each WAN section's ifname, and
    finally the only WAN that is up. In load-balanced dual-WAN setups the
    controller's uplink object can mix fields from both interfaces, which
    is why the IP match takes precedence over the port name.

    Reached through ``UniFiWanData.active_wan``, which caches it per poll.
    """
    u_ip = d.uplink.get("ip")
    if u_ip:
        for wan_number, wan_data in d.wan.items():
            if u_ip == wan_data.get("ip"):
                return wan_number, "uplink_ip"
    # Firmware differs over which key carries the uplink's interface and
    # which carries the WAN section's, so every combination is compared
    # rather than the one pairing this gateway happens to use.
    u_names = {
        str(d.uplink.get(key) or "").strip().lower() for key in ("name", "ifname")
    } - {""}
    if u_names:
        for wan_number, wan_data in d.wan.items():
            for key in ("ifname", "name"):
                if str(wan_data.get(key) or "").strip().lower() in u_names:
                    return wan_number, "uplink_ifname"
    up_numbers = [n for n, wan_data in d.wan.items() if wan_data.get("up")]
    if len(up_numbers) == 1:
        return up_numbers[0], "only_wan_up"
    # A dual-WAN gateway in failover reports both WANs up while only one is
    # carrying traffic, so the controller's own "alive" flag is the last
    # thing left that distinguishes them.
    alive_numbers = [n for n, alive in d.wan_alive.items() if alive and n in d.wan]
    if len(alive_numbers) == 1:
        return alive_numbers[0], "only_wan_alive"
    return None, "no_match"


def interface_to_wan_number(iface: Any, wan: dict[int, dict[str, Any]]) -> int | None:
    """Map a controller-reported interface to a WAN number.

    Matches against each WAN section's own interface fields, so PPPoE
    uplinks ("ppp0") resolve as readily as plain ethernet ones. The
    "wan"/"wan2" spellings used by the speedtest command are handled too,
    but only after the section match: a literal interface name is stronger
    evidence than a naming convention. The convention only names a WAN
    this gateway has - "wan3" on a two-WAN gateway identifies nothing, and
    a result recorded against it would sit on a WAN no sensor shows.
    """
    s = normalise_interface(iface)
    if not s:
        return None
    lowered = s.lower()
    for wan_number, wan_data in wan.items():
        for key in ("ifname", "name"):
            if lowered == str(wan_data.get(key) or "").strip().lower():
                return wan_number
    number = wan_group_to_number(s)
    return number if number in wan else None


def gateway_speedtest_wan(d: UniFiWanData) -> int | None:
    """The WAN the gateway's speedtest-status block belongs to, or None.

    The gateway keeps one such block and overwrites it on every run
    regardless of interface, so it is only claimed for a WAN on evidence:
    the interface the controller says the test ran on, or a gateway with a
    single WAN, where there is nothing else it could be.

    The active uplink is deliberately not a fallback here. It is good
    enough for throughput, which is replaced by that WAN's next run, but
    the speedtest server latches - a wrong guess would sit on a WAN
    indefinitely with no later run to correct it.
    """
    iface = d.speedtest.get("source_interface")
    if iface:
        return interface_to_wan_number(iface, d.wan)
    if len(d.wan) == 1:
        return next(iter(d.wan))
    return None


def attribution_source(d: UniFiWanData, wan_number: int) -> str:
    """How the gateway's block came to be recorded against a WAN, for the
    per-WAN sensors' "attributed_by" attribute.
    """
    iface = d.speedtest.get("source_interface")
    if iface and interface_to_wan_number(iface, d.wan) == wan_number:
        return "source_interface"
    if len(d.wan) == 1:
        return "only_wan"
    return "active_wan"


def gateway_result_wan(d: UniFiWanData, active: int | None) -> int | None:
    """Which WAN the gateway's last-run block counts as, or None.

    Looser than gateway_speedtest_wan, which answers the same question for
    the speedtest server alone: the server latches with no later run to
    correct a wrong guess, while throughput is replaced by that WAN's next
    run. Both the gateway-wide sensors and the per-WAN results this
    integration latches use *this* rule, so the two can never show
    different figures for the same WAN.

    The block is claimed for the interface it names, for the only WAN where
    there is one, and otherwise for the active uplink - which is what the
    controller tests when it is not told otherwise. The exception is a block
    a per-WAN record of the same moment identifies as another WAN's run:
    that is the one case where treating it as the active WAN's would put a
    non-active line's throughput on the active line's sensors.
    """
    iface = d.speedtest.get("source_interface")
    if iface:
        mapped = interface_to_wan_number(iface, d.wan)
        if mapped is not None:
            return mapped
        # An interface the controller named but that matches no WAN section
        # says nothing about which WAN ran, so it is treated as naming none.
    if len(d.wan) == 1:
        return next(iter(d.wan))
    if active is None:
        return None
    lastrun = speedtest_epoch(d.speedtest.get("lastrun"))
    if lastrun is None:
        return None
    for other, record in d.per_wan_speedtest.items():
        if other == active:
            continue
        other_run = speedtest_epoch(record.get("lastrun"))
        if (
            other_run is not None
            and abs(other_run - lastrun) <= GATEWAY_RESULT_MATCH_SECONDS
        ):
            return None
    return active
