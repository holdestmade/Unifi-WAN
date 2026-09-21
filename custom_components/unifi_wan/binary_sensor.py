from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from . import UniFiWanData, UniFiWanRuntimeData

@dataclass(frozen=True, kw_only=True)
class UniFiBinaryEntityDescription(BinarySensorEntityDescription):
    value_fn: Callable[[UniFiWanData], bool] = lambda x: False


def _wan_has_internet(d: UniFiWanData, wan_number: int) -> bool:
    """Whether one WAN both has a physical link and is reported alive.

    The controller's last_wan_interfaces "alive" flag can stay stale for a
    WAN whose cable is unplugged, so a WAN it reports as down is never
    treated as connected however alive it claims to be. Where the
    controller reports no link state for the WAN at all there is nothing
    to cross-check against, and its own flag - or failing that, whether it
    holds an address - is all the evidence there is.
    """
    section = d.wan.get(wan_number) or {}
    if "up" in section and not section.get("up"):
        return False
    alive = d.wan_alive.get(wan_number)
    if alive is not None:
        return bool(alive)
    return bool(section.get("ip"))


def _any_wan_has_internet(d: UniFiWanData) -> bool:
    """Gateway-wide connectivity: any WAN that is both linked and alive.

    Held to exactly the rule the per-WAN sensors use, so this can never
    report a connection while every one of them reads disconnected. The
    uplink's own flag is the fallback for a gateway that reports no WAN
    sections at all.
    """
    if d.wan:
        return any(_wan_has_internet(d, wan_number) for wan_number in d.wan)
    return bool(d.uplink.get("up"))


BINARY_SENSORS: tuple[UniFiBinaryEntityDescription, ...] = (
    UniFiBinaryEntityDescription(
        key="wan_internet",
        name="UniFi WAN Internet",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=_any_wan_has_internet,
    ),
    UniFiBinaryEntityDescription(
        key="active_wan_up",
        name="UniFi Active WAN Up",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda d: bool(d.uplink.get("up")),
    ),
)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    runtime: UniFiWanRuntimeData = hass.data[DOMAIN][entry.entry_id]
    device = runtime.device_coordinator

    entry_id = entry.entry_id
    device_info = runtime.device_info
    wan_numbers = runtime.wan_numbers

    entities = []
    for desc in BINARY_SENSORS:
        entities.append(UniFiGenericBinary(device, entry_id, device_info, desc))

    # Per-WAN binary sensors are only created on a multi-WAN gateway; with a
    # single WAN they restate the gateway-wide Internet / Active WAN Up
    # sensors above.
    if len(wan_numbers) > 1:
        for wan_number in wan_numbers:
            # Shares _wan_has_internet with the gateway-wide sensor above,
            # so the two cannot disagree about whether anything is connected.
            internet = UniFiBinaryEntityDescription(
                key=f"wan{wan_number}_internet",
                name=f"UniFi WAN{wan_number} Internet",
                device_class=BinarySensorDeviceClass.CONNECTIVITY,
                value_fn=lambda d, wn=wan_number: _wan_has_internet(d, wn),
            )
            entities.append(UniFiGenericBinary(device, entry_id, device_info, internet))
            link = UniFiBinaryEntityDescription(
                key=f"wan{wan_number}_link",
                name=f"UniFi WAN{wan_number} Link",
                device_class=BinarySensorDeviceClass.CONNECTIVITY,
                value_fn=lambda d, wn=wan_number: bool(d.wan.get(wn, {}).get("up")),
            )
            entities.append(UniFiGenericBinary(device, entry_id, device_info, link))

    entities.append(UniFiSpeedtestInProgress(runtime, entry_id, device_info))
    async_add_entities(entities)


class UniFiGenericBinary(CoordinatorEntity, BinarySensorEntity):
    entity_description: UniFiBinaryEntityDescription

    def __init__(self, coordinator, entry_id: str, device_info: dict[str, Any], description):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_device_info = device_info
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        return self.entity_description.value_fn(self.coordinator.data)


class UniFiSpeedtestInProgress(BinarySensorEntity):
    _attr_name = "UniFi Speedtest In Progress"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:progress-clock"
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime: UniFiWanRuntimeData, entry_id: str, device_info: dict[str, Any]):
        self._runtime = runtime
        self._attr_unique_id = f"{entry_id}_speedtest_in_progress"
        self._attr_device_info = device_info

    async def async_added_to_hass(self) -> None:
        signal = self._runtime.speedtest_running_signal
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal, self._signal_update)
        )

    @callback
    def _signal_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return bool(self._runtime.get_speedtest_running())
