"""The automatic-speedtest switch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_AUTO_SPEEDTEST
from .runtime import UniFiWanConfigEntry
from .speedtest import SpeedtestManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UniFiWanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    async_add_entities(
        [
            UniFiAutoSpeedtestSwitch(
                entry, runtime.speedtest, entry.entry_id, runtime.device_info
            )
        ]
    )


class UniFiAutoSpeedtestSwitch(SwitchEntity):
    """Enable/disable the automatic speedtest schedule.

    Toggling applies the new state live through the speedtest manager and
    persists it to the config entry options for restart survival. The
    update listener skips the otherwise-automatic reload for this option,
    so toggling does not briefly mark every entity unavailable.
    """

    _attr_name = "UniFi WAN Auto Speedtest"
    _attr_icon = "mdi:speedometer-slow"
    _attr_should_poll = False

    def __init__(
        self,
        entry: UniFiWanConfigEntry,
        speedtest: SpeedtestManager,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        self._entry = entry
        self._speedtest = speedtest
        self._attr_unique_id = f"{entry_id}_auto_speedtest_enabled"
        self._attr_device_info = device_info

    async def async_added_to_hass(self) -> None:
        # Reflect changes made through the options dialog, which are applied
        # live rather than via a reload.
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._speedtest.auto_changed_signal, self._signal_update
            )
        )

    @callback
    def _signal_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._speedtest.auto_enabled

    def _set_enabled(self, enabled: bool) -> None:
        self._speedtest.set_auto_enabled(enabled)
        self.async_write_ha_state()
        options = dict(self._entry.options)
        if options.get(CONF_AUTO_SPEEDTEST) != enabled:
            options[CONF_AUTO_SPEEDTEST] = enabled
            self.hass.config_entries.async_update_entry(self._entry, options=options)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._set_enabled(False)
