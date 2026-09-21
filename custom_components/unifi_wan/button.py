"""Buttons that trigger a speedtest."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .runtime import UniFiWanConfigEntry
from .speedtest import SpeedtestManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UniFiWanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    entry_id = entry.entry_id
    device_info = runtime.device_info

    entities: list[ButtonEntity] = [
        RunSpeedtestButton(runtime.speedtest, entry_id, device_info)
    ]

    # With a single WAN the gateway-wide button already tests that WAN, so a
    # per-WAN button would just be a second control doing the same thing.
    if len(runtime.wan_numbers) > 1:
        entities.extend(
            RunSpeedtestWanButton(runtime.speedtest, entry_id, device_info, wan_number)
            for wan_number in runtime.wan_numbers
        )

    async_add_entities(entities)


class UniFiSpeedtestButtonBase(ButtonEntity):
    _attr_icon = "mdi:speedometer"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(
        self, speedtest: SpeedtestManager, entry_id: str, device_info: DeviceInfo
    ) -> None:
        self._speedtest = speedtest
        self._attr_device_info = device_info


class RunSpeedtestButton(UniFiSpeedtestButtonBase):
    _attr_name = "UniFi Run Speedtest"

    def __init__(self, speedtest, entry_id, device_info):
        super().__init__(speedtest, entry_id, device_info)
        self._attr_unique_id = f"{entry_id}_run_speedtest"

    async def async_press(self) -> None:
        self._speedtest.trigger()


class RunSpeedtestWanButton(UniFiSpeedtestButtonBase):
    def __init__(self, speedtest, entry_id, device_info, wan_number: int):
        super().__init__(speedtest, entry_id, device_info)
        self._wan_number = wan_number
        self._attr_name = f"UniFi Run Speedtest WAN{wan_number}"
        self._attr_unique_id = f"{entry_id}_run_speedtest_wan{wan_number}"

    async def async_press(self) -> None:
        self._speedtest.trigger(self._wan_number)
