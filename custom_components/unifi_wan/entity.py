"""Naming shared by every UniFi WAN entity."""

from __future__ import annotations

from homeassistant.util import slugify

# Entity names used to spell out a "UniFi " prefix themselves. They now use
# has_entity_name, so Home Assistant puts the device name in front instead,
# and the names are just what each entity measures ("Speedtest last run").
#
# Left alone, Home Assistant would build the entity id of a new install from
# the device name too, giving "sensor.unifi_wan_192_168_1_1_default_wan_download"
# where every earlier release created "sensor.unifi_wan_download". Suggesting
# the old id keeps new installs in line with existing ones, and with the
# automations and dashboards written against them. Entities already in the
# registry keep whatever id they have regardless.
#
# The gateway-wide WAN entities also dropped a leading "WAN" ("UniFi WAN
# Download" is now just "Download"), since the device is already the WAN,
# so they pass LEGACY_WAN_PREFIX to get their old ids back.
LEGACY_NAME_PREFIX = "UniFi"
LEGACY_WAN_PREFIX = "UniFi WAN"


def legacy_entity_id(domain: str, name: str, prefix: str = LEGACY_NAME_PREFIX) -> str:
    """The entity id the entity's old, prefixed name would have produced."""
    return f"{domain}.{slugify(f'{prefix} {name}')}"
