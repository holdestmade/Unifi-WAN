"""The polling coordinators.

Two of them: a full site poll that every sensor reads, and an optional
fast poll of the gateway alone behind the live rate sensors.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import UnifiWanClient
from .const import DOMAIN
from .models import (
    UniFiWanData,
    extract_rates,
    extract_wan_data,
    parse_speedtest_history,
)

if TYPE_CHECKING:
    from .runtime import UniFiWanConfigEntry

_LOGGER = logging.getLogger(__name__)


class UniFiWanCoordinator(DataUpdateCoordinator[UniFiWanData]):
    """Full site poll: the gateway's own payload and the per-WAN speedtest
    records, parsed into the shape every sensor reads.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: UniFiWanConfigEntry,
        client: UnifiWanClient,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_device",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        # Per-WAN speedtest results latched by the speedtest manager. The
        # controller only stores the latest result, so each completed run is
        # attributed to a WAN and kept here. Owned by the coordinator
        # because every UniFiWanData it builds carries a reference to it,
        # which is how the gateway-wide sensors read the same figures the
        # per-WAN sensors show.
        self.speedtest_results: dict[int, dict[str, Any]] = {}
        # The last per-WAN speedtest history this controller returned. A
        # fetch that merely failed must not be mistaken for a controller
        # that keeps no per-WAN records: that other route attributes the
        # gateway's block to the active uplink without the cross-check this
        # one applies, and the rule that a result never moves backwards
        # would then defend the wrong record against the next correct one.
        self._last_history: dict[str, Any] | None = None

    async def _async_update_data(self) -> UniFiWanData:
        """Fetch and process data.

        The two endpoints are independent, so they are fetched together:
        the speedtest history is the slower of the pair on some consoles
        and there is no reason for the device poll to wait on it.
        """
        devices, history = await asyncio.gather(
            self.client.get_devices(),
            self.client.get_speedtest_history(),
            return_exceptions=True,
        )
        if isinstance(devices, BaseException):
            # The site payload is the poll; without it there is nothing to
            # publish. get_speedtest_history never raises, so a failure
            # there arrives as None and is handled below.
            raise devices
        if isinstance(history, BaseException):  # pragma: no cover - defensive
            _LOGGER.debug("Per-WAN speedtest fetch raised: %s", history)
            history = None

        data = extract_wan_data(devices)
        data.speedtest_latched = self.speedtest_results

        if history is None and self.client.speedtest_history_supported:
            # This fetch did not get through, but the endpoint is still
            # there. Reuse what it last said rather than falling back to
            # the guessier route for a single poll.
            history = self._last_history
        else:
            self._last_history = history
        if history is not None:
            data.speedtest_history_raw = history
            data.per_wan_speedtest = parse_speedtest_history(history, data.wan)
        return data


class UniFiWanRatesCoordinator(DataUpdateCoordinator[UniFiWanData]):
    """Fast poll of the gateway alone, behind the live WAN rate sensors.

    Runs as often as once a second, so it parses only as far as those
    sensors read - see models.extract_rates.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: UniFiWanConfigEntry,
        client: UnifiWanClient,
        mac: str,
        rate_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_rates",
            update_interval=timedelta(seconds=rate_interval),
        )
        self.client = client
        self.mac = mac

    async def _async_update_data(self) -> UniFiWanData:
        return extract_rates(await self.client.get_device(self.mac))
