"""Unredacted raw-data dumps for UniFi WAN.

The diagnostics download is meant to be attached to a public issue, so it
redacts addresses, identifiers and the ISP lookup. That is exactly wrong
when the person looking at the file is the one who owns the network: the
redacted fields are often the ones that explain the behaviour.

This module writes the same payloads with nothing removed, to a file on the
Home Assistant host, for the operator's own inspection. Nothing is uploaded
and nothing is offered for download - the file has to be fetched off the
host deliberately, which is the point.

Every controller response is captured verbatim, alongside what the
integration parsed out of it, so a wrong sensor can be traced to either the
data or the parsing without a second round trip.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from copy import deepcopy
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN, DUMP_DIR_NAME, DUMP_SIZE_WARN_BYTES
from .models import UniFiWanData

if TYPE_CHECKING:
    from .runtime import UniFiWanRuntimeData

_LOGGER = logging.getLogger(__name__)

# Stands in for the API key, the one field a dump holds back. Worded so it
# cannot be mistaken for the controller having sent nothing.
WITHHELD: str = "**WITHHELD (not controller data)**"

WARNING: str = (
    "UNREDACTED. This file contains the controller's payloads exactly as "
    "received: public IP addresses, MAC addresses, serial numbers, site and "
    "device identifiers, DNS servers, the ISP/geolocation lookup for each "
    "WAN, and the site's forwarding rules - which internal host and port "
    "each one opens from outside. It is for your own inspection - do not "
    "attach it to a GitHub issue or post it publicly. Use Download "
    "diagnostics for that, which redacts the rest and carries no forwarding "
    "rules at all."
)

# Endpoints captured, as (name in the dump, path, use the v2 API). The
# gateway-only path is added per entry, once its MAC is known.
BASE_ENDPOINTS: tuple[tuple[str, str, bool], ...] = (
    # Everything the site reports, every device - the source of almost every
    # sensor, and the only place a misidentified gateway shows up.
    ("stat_device", "stat/device", False),
    # The per-WAN speedtest history, where the controller offers it. Captured
    # here even when it 404s: the status code is the answer to "why are my
    # per-WAN speedtest sensors empty?".
    ("v2_speedtest", "speedtest", True),
    # The site's forwarding rules. Nothing in the integration reads these
    # yet; they are captured because the questions behind doing so are all
    # answered by one real response. Whether a local API key may read them
    # at all is the status code, and what a rule's "pfwd_interface" holds -
    # which WAN an inbound rule is bound to, and how that firmware spells
    # "either one" - is the body. Both differ by console, and neither can
    # be settled by reasoning about it.
    ("port_forwards", "rest/portforward", False),
)


def _safe(value: Any) -> str:
    """Filename-safe form of a site or host name."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "unknown"))[:40]


def _dump_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(DUMP_DIR_NAME))


def _prefix(site: Any, entry_id: str) -> str:
    """Filename prefix identifying one config entry's dumps.

    Carries the site for a human reading the directory and the entry id so
    two entries on the same site stay apart.
    """
    return f"unifi_wan_{_safe(site)}_{entry_id[:8]}_"


def _filename(prefix: str, moment: datetime) -> str:
    """A dump's filename. Fixed-width timestamp, so the directory listing
    sorts chronologically and pruning can rely on it.
    """
    return f"{prefix}{moment.strftime('%Y%m%d-%H%M%S')}.json"


def _write_and_prune(
    path: Path, payload: dict[str, Any], prefix: str, keep: int
) -> int:
    """Write the dump and drop the oldest files past ``keep``.

    Runs in the executor: this is blocking file I/O. Pruning matches on the
    entry's own prefix, so two configured gateways do not evict each other's
    dumps. Returns the size written, in bytes.
    """
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    # default=str so a value the controller sends in a shape json cannot
    # represent still lands in the file rather than failing the whole dump.
    text = json.dumps(payload, indent=2, default=str)
    path.write_text(text, encoding="utf-8")

    # The timestamp is fixed-width and in the filename, so a lexical sort is
    # a chronological one.
    existing = sorted(
        entry
        for entry in directory.iterdir()
        if entry.name.startswith(prefix) and entry.suffix == ".json"
    )
    for stale in existing[: max(0, len(existing) - keep)]:
        try:
            stale.unlink()
        except OSError as err:  # pragma: no cover - a stale file is not fatal
            _LOGGER.debug("Could not remove old dump %s: %s", stale.name, err)

    return len(text.encode("utf-8"))


def _snapshot(data: UniFiWanData) -> dict[str, Any]:
    """The parsed data as plain dicts, without the device list.

    Not dataclasses.asdict: that deep-copies every field first and the
    device list is the largest thing the integration holds - a site with
    fifty access points is megabytes of dicts, copied on the event loop and
    then thrown away, because the full list is already in the stat/device
    capture verbatim. The gateway stays: it is the one device every sensor
    reads, and having it isolated is worth the repetition.

    The remaining fields are copied rather than referenced. They are small,
    and the JSON is written in an executor thread while the event loop
    keeps running - serialising the live objects would race the next poll
    and the speedtest results the manager writes in place.
    """
    snapshot = {
        field.name: deepcopy(getattr(data, field.name))
        for field in fields(data)
        if field.name != "devices"
    }
    snapshot["device_count"] = len(data.devices)
    return snapshot


async def _capture(runtime: UniFiWanRuntimeData) -> dict[str, Any]:
    """Fetch every endpoint the integration reads, verbatim.

    Fetched fresh rather than taken from the coordinator so the file shows
    what the controller returns right now, including the endpoints the
    coordinator has stopped asking for.
    """
    client = runtime.client
    names = [name for name, _, _ in BASE_ENDPOINTS]
    requests = [client.fetch_raw(path, v2=v2) for _, path, v2 in BASE_ENDPOINTS]

    mac = runtime.dev_meta.get("mac")
    if mac:
        # The cheap gateway-only endpoint behind the live rate sensors.
        names.append("stat_device_gateway")
        requests.append(client.fetch_raw(f"stat/device/{mac}"))

    # Independent endpoints, so they are asked together rather than in
    # turn. fetch_raw reports its own failures instead of raising, so one
    # endpoint being unreachable still leaves the others captured.
    endpoints = dict(zip(names, await asyncio.gather(*requests), strict=True))

    if not mac:
        endpoints["stat_device_gateway"] = {
            "skipped": "the gateway's MAC is not known, so there is no per-device URL to call"
        }
    return endpoints


async def async_dump_entry(
    hass: HomeAssistant, entry_id: str, runtime: UniFiWanRuntimeData, keep: int
) -> dict[str, Any]:
    """Write one entry's dump and return a record of what was written."""
    now = dt_util.now()
    prefix = _prefix(runtime.site, entry_id)
    path = _dump_dir(hass) / _filename(prefix, now)

    endpoints = await _capture(runtime)

    data: UniFiWanData | None = runtime.device_coordinator.data
    parsed: dict[str, Any] = {}
    derived: dict[str, Any] = {}
    if data is None:
        parsed["error"] = "coordinator has no data"
    else:
        parsed = _snapshot(data)
        active_wan, match_reason = data.active_wan
        derived = {
            "wan_numbers": runtime.wan_numbers,
            "active_wan": active_wan,
            "match_reason": match_reason,
            "latched_speedtest_results": runtime.speedtest.results,
            "per_wan_api_available": data.speedtest_history_raw is not None,
            "targeted_speedtest_supported": runtime.client.targeted_speedtest_supported,
            "per_wan_speedtest_honoured": runtime.speedtest.per_wan_supported,
            "auto_speedtest_enabled": runtime.speedtest.auto_enabled,
            "speedtest_running": runtime.speedtest.running,
        }

    rates = runtime.rates_coordinator
    rates_parsed: Any = None
    if rates is not None and rates.data is not None:
        # The fast poll parses only as far as the rate sensors read, so this
        # carries the uplink and little else by design. The endpoint's full
        # response is captured verbatim under controller.stat_device_gateway.
        rates_parsed = _snapshot(rates.data)

    entry = hass.config_entries.async_get_entry(entry_id)
    entry_config: dict[str, Any] = {}
    if entry is not None:
        # The one thing held back, and it is not controller data: the API key
        # explains nothing about a WAN, and a dump is a file that gets copied
        # around. Everything the controller sent is here in full.
        entry_config = {
            "title": entry.title,
            "entry_id": entry.entry_id,
            "data": {
                key: (WITHHELD if key == "api_key" else value)
                for key, value in entry.data.items()
            },
            "options": {
                key: (WITHHELD if key == "api_key" else value)
                for key, value in entry.options.items()
            },
        }

    payload = {
        "warning": WARNING,
        "created": now.isoformat(),
        "integration": {
            "domain": DOMAIN,
            "host": runtime.host,
            "site": runtime.site,
            "gateway_model": runtime.dev_meta.get("model"),
            "gateway_firmware": runtime.dev_meta.get("sw_version"),
            "gateway_mac": runtime.dev_meta.get("mac"),
        },
        "entry": entry_config,
        # What the controller sent, exactly as it sent it.
        "controller": endpoints,
        # What the integration made of it, and what it concluded.
        "parsed": parsed,
        "parsed_rates": rates_parsed,
        "derived": derived,
    }

    size = await hass.async_add_executor_job(
        _write_and_prune, path, payload, prefix, keep
    )
    if size >= DUMP_SIZE_WARN_BYTES:
        # Worth saying out loud: the file is mostly the site's device list,
        # it is kept alongside `keep` others, and it lives in the config
        # directory that gets backed up.
        _LOGGER.warning(
            "The UniFi WAN dump at %s is %.1f MB. It holds every device on "
            "site %r verbatim, and %s of them are kept. Lower the 'keep' "
            "field, or delete the ones you have finished with.",
            path,
            size / (1024 * 1024),
            runtime.site,
            keep,
        )
    else:
        _LOGGER.info("Wrote unredacted UniFi WAN dump to %s (%s bytes)", path, size)
    return {
        "path": str(path),
        "bytes": size,
        "site": runtime.site,
        "entry_id": entry_id,
    }


async def async_dump_all(hass: HomeAssistant, keep: int) -> dict[str, Any]:
    """Dump every loaded config entry. One file each."""
    files: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        if runtime is None:
            continue
        try:
            files.append(await async_dump_entry(hass, entry.entry_id, runtime, keep))
        except Exception as err:  # noqa: BLE001 - one entry must not sink the rest
            _LOGGER.error(
                "Could not write UniFi WAN dump for %s: %s", entry.entry_id, err
            )
            errors.append({"entry_id": entry.entry_id, "error": str(err)})

    result: dict[str, Any] = {
        "directory": str(_dump_dir(hass)),
        "files": files,
        "warning": WARNING,
    }
    if errors:
        result["errors"] = errors
    return result
