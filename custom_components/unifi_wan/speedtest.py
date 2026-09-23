"""The speedtest state machine.

Triggering a speedtest on a UniFi gateway is not a request/response: the
command is accepted, the run happens somewhere in the next few minutes,
and the result appears in one or more records that firmware disagrees
about. This class owns waiting for that, deciding which WAN a finished run
belongs to, and the schedule that cycles runs across the WANs.

It was a stack of closures over nonlocal variables inside async_setup_entry;
as a class the state is named and the decisions can be tested directly.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .api import UnifiWanClient
from .const import (
    SIGNAL_AUTO_SPEEDTEST_CHANGED,
    SIGNAL_SPEEDTEST_RESULT,
    SIGNAL_SPEEDTEST_RUNNING,
    SPEEDTEST_POLL_SECONDS,
    SPEEDTEST_SERVER_FIELDS,
    SPEEDTEST_TIMEOUT_SECONDS,
)
from .coordinator import UniFiWanCoordinator, UniFiWanRatesCoordinator
from .models import (
    UniFiWanData,
    attributed_server,
    attribution_source,
    gateway_result_wan,
    gateway_speedtest_wan,
    interface_to_wan_number,
    is_newer,
    speedtest_epoch,
)

if TYPE_CHECKING:
    from .runtime import UniFiWanConfigEntry

_LOGGER = logging.getLogger(__name__)

# The figures a run's record carries. The controller fills them in over
# several seconds under one timestamp, so a record already held is still
# news when any of these has moved.
_FIGURES = ("down", "up", "ping")


class SpeedtestManager:
    """Runs speedtests, attributes their results, and keeps the schedule."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: UniFiWanConfigEntry,
        client: UnifiWanClient,
        coordinator: UniFiWanCoordinator,
        rates_coordinator: UniFiWanRatesCoordinator | None,
        wan_numbers: list[int],
        auto_enabled: bool,
        auto_minutes: int,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.coordinator = coordinator
        self.rates_coordinator = rates_coordinator
        self.wan_numbers = wan_numbers
        self.auto_enabled = auto_enabled
        self.auto_minutes = max(1, auto_minutes)

        # Shared by reference with the coordinator, which hands the same
        # dict to every UniFiWanData it builds.
        self.results = coordinator.speedtest_results

        self.running = False
        self.running_signal = f"{SIGNAL_SPEEDTEST_RUNNING}_{entry.entry_id}"
        self.auto_changed_signal = f"{SIGNAL_AUTO_SPEEDTEST_CHANGED}_{entry.entry_id}"
        self.result_signal = f"{SIGNAL_SPEEDTEST_RESULT}_{entry.entry_id}"

        # WAN number requested for the speedtest currently in flight, if
        # any. Used only to report on the controller's behaviour - never to
        # attribute a result. See _process_result.
        self._pending_wan: int | None = None
        # None until a targeted run tells us whether this controller honours
        # the requested interface; False disables the auto rotation.
        self._per_wan_supported: bool | None = None
        # Seed with the result already on the controller so a stale run is
        # not re-attributed after every restart; per-WAN sensors restore
        # their own previous state instead.
        data = coordinator.data
        self._last_attributed_run = data.speedtest.get("lastrun") if data else None
        # The WAN that run was recorded against, so the rest of its figures
        # can be filled in when they arrive. None for the seeded run above
        # and for one judged stale: neither is to be recorded at all.
        self._last_attributed_wan: int | None = None
        self._auto_wan_index = 0
        self._unsub_auto: CALLBACK_TYPE | None = None

    @property
    def per_wan_supported(self) -> bool | None:
        """Whether this controller honours a requested WAN.

        None until a targeted run has told us either way; False once one
        has been seen to be ignored, which stops the auto rotation. Exposed
        for diagnostics, where "why is my rotation not cycling?" is
        otherwise invisible.
        """
        return self._per_wan_supported

    # ---------------------------------------------------------------- setup

    @callback
    def async_setup(self) -> None:
        """Start listening for results and arm the schedule."""
        self.entry.async_on_unload(
            self.coordinator.async_add_listener(self._process_result)
        )
        self.entry.async_on_unload(self.async_shutdown)
        # The first refresh ran before this listener existed. Applied now,
        # before the platforms are set up, so the per-WAN sensors start
        # with the records it fetched rather than a scan interval of
        # nothing. The gateway's own block is not re-attributed: it was
        # seeded above as already seen.
        self._process_result()
        self.schedule_auto(self.auto_enabled)

    @callback
    def async_shutdown(self) -> None:
        """Cancel the schedule. Runs in flight, scheduled ones included, are
        the entry's background tasks, which Home Assistant cancels with it.
        """
        self.schedule_auto(False)

    # ------------------------------------------------------------- reporting

    @callback
    def _set_running(self, is_running: bool) -> None:
        if self.running == is_running:
            return
        self.running = is_running
        async_dispatcher_send(self.hass, self.running_signal)

    @callback
    def set_auto_enabled(self, enabled: bool) -> None:
        """Apply the auto-speedtest setting live and tell the switch.

        The one path for the change, whether it came from the switch entity
        or the options dialog, so the schedule and the switch can never
        disagree about whether it is on.
        """
        if enabled == self.auto_enabled:
            return
        self.auto_enabled = enabled
        self.schedule_auto(enabled)
        async_dispatcher_send(self.hass, self.auto_changed_signal)

    @callback
    def schedule_auto(self, enabled: bool) -> None:
        """Arm or disarm the automatic speedtest schedule."""
        if self._unsub_auto:
            self._unsub_auto()
        self._unsub_auto = None
        if enabled:
            self._unsub_auto = async_track_time_interval(
                self.hass,
                self._auto_speedtest,
                timedelta(minutes=self.auto_minutes),
            )
            _LOGGER.debug("Auto speedtest scheduled every %s min", self.auto_minutes)

    @callback
    def trigger(self, wan_number: int | None = None) -> None:
        """Start a run in the background.

        Fire and forget: a run waits for the result, up to several minutes,
        so it must not block the button press or the service call. The task
        is tied to the config entry, so unloading or reloading the entry
        cancels it rather than leaving it refreshing an orphaned
        coordinator. Progress is exposed via the In Progress sensor.
        """
        self.entry.async_create_background_task(
            self.hass,
            self.async_run(wan_number),
            name=f"{self.entry.entry_id} unifi_wan speedtest",
        )

    # ----------------------------------------------------------- attribution

    @callback
    def _process_result(self) -> None:
        """Publish per-WAN speedtest results on every coordinator refresh.

        When the controller offers a per-WAN speedtest API its records are
        authoritative and are used as-is. Otherwise the gateway's single
        global result is attributed to a WAN on evidence: the interface the
        controller says the test ran on, or failing that the active uplink.
        The requested interface is deliberately never used as a fallback -
        firmware that ignores it always tests the active uplink, so trusting
        the request labels one WAN's throughput as another's.
        """
        data: UniFiWanData | None = self.coordinator.data
        if not data:
            return

        # The speedtest server is recorded once, in the gateway's own block,
        # so it is folded into whichever WAN that block can be shown to
        # belong to. On the per-WAN route below the records themselves name
        # no server at all, and this is the only way those sensors are ever
        # populated.
        server_wan = gateway_speedtest_wan(data)
        server = {name: data.speedtest.get(name) for name in SPEEDTEST_SERVER_FIELDS}

        def _server_for(wan_number: int) -> dict[str, Any]:
            return attributed_server(
                server, wan_number == server_wan, self.results.get(wan_number)
            )

        if data.per_wan_speedtest:
            self._process_per_wan(data, _server_for)
            return
        self._process_gateway_result(data, _server_for)

    def _process_per_wan(self, data: UniFiWanData, server_for) -> None:
        """The per-WAN API's records, which name their own WAN."""
        changed = False
        for wan_number, record in data.per_wan_speedtest.items():
            # The records name no server, so it is folded in here.
            # Unconditionally, including when there is nothing to fold: the
            # stored record is compared against below, and one missing
            # these keys would differ on every refresh.
            result = {**record, **server_for(wan_number)}
            # Compare the whole record, not just its timestamp: the
            # controller fills a run's figures in over several seconds and
            # keeps the same timestamp while doing so, so a poll that
            # catches a half-written record must still accept the completed
            # one rather than treating it as already seen.
            stored = self.results.get(wan_number)
            if stored == result:
                continue
            # A record older than what this WAN already holds describes a
            # run the gateway's own block reported first (see below).
            # Without this the two sources would overwrite each other on
            # every poll. An equal timestamp is not older, so the
            # half-written record above is still completed.
            if stored is not None and is_newer(
                stored.get("lastrun"), result.get("lastrun")
            ):
                continue
            self.results[wan_number] = dict(result)
            changed = True

        # A per-WAN record can be older than the gateway's own block: not
        # every firmware adds a run started outside its own schedule to that
        # history, and a WAN whose record then never moves would leave these
        # sensors reporting a days-old figure while tests keep completing.
        # The gateway's result is folded in too, under the same rule the
        # gateway-wide sensors display it by, so the two families of sensor
        # cannot disagree about one WAN.
        gateway_wan = gateway_result_wan(data, data.active_wan[0])
        gateway_result = data.speedtest
        has_figures = (
            gateway_result.get("down") is not None
            or gateway_result.get("up") is not None
        )
        stored = (self.results.get(gateway_wan) or {}) if gateway_wan else {}
        newer = is_newer(gateway_result.get("lastrun"), stored.get("lastrun"))
        # The block recorded here on an earlier poll, caught before all of
        # its figures were in. A per-WAN record of the same run is left to
        # stand: it is the controller's own account of that WAN.
        completes = (
            bool(stored)
            and stored.get("source") != "speedtest_api"
            and speedtest_epoch(stored.get("lastrun"))
            == speedtest_epoch(gateway_result.get("lastrun"))
            and any(stored.get(k) != gateway_result.get(k) for k in _FIGURES)
        )
        if gateway_wan is not None and has_figures and (newer or completes):
            self.results[gateway_wan] = {
                "down": gateway_result.get("down"),
                "up": gateway_result.get("up"),
                "ping": gateway_result.get("ping"),
                "lastrun": gateway_result.get("lastrun"),
                "source": attribution_source(data, gateway_wan),
                "requested_wan": (
                    self._pending_wan if newer else stored.get("requested_wan")
                ),
                **server_for(gateway_wan),
            }
            changed = True

        if changed:
            _LOGGER.debug(
                "Per-WAN speedtest results updated: %s",
                {
                    f"WAN{n}": {k: r.get(k) for k in ("down", "up", "ping", "lastrun")}
                    for n, r in sorted(self.results.items())
                },
            )
            async_dispatcher_send(self.hass, self.result_signal)

    def _process_gateway_result(self, data: UniFiWanData, server_for) -> None:
        """The gateway's single global result, attributed on evidence."""
        result = data.speedtest
        lastrun = result.get("lastrun")
        if not lastrun:
            return
        if result.get("down") is None and result.get("up") is None:
            return
        # A run already recorded is still news if its figures have moved:
        # the controller fills them in over several seconds under the one
        # timestamp, and a poll can catch the block half-written. The run
        # seeded at startup, and one judged stale, have no WAN and stay
        # ignored.
        same_run = lastrun == self._last_attributed_run
        if same_run and self._last_attributed_wan is None:
            return

        requested = self._pending_wan
        wan_number = interface_to_wan_number(result.get("source_interface"), data.wan)
        source = "source_interface"
        if wan_number is None:
            wan_number = data.active_wan[0]
            source = "active_wan"
        if wan_number is None:
            # Not marked as seen, so a later poll that can resolve the
            # active uplink still attributes this run.
            _LOGGER.debug(
                "Speedtest result could not be attributed to a WAN "
                "(source_interface=%r)",
                result.get("source_interface"),
            )
            return

        stored = self.results.get(wan_number)
        if same_run:
            if (
                wan_number != self._last_attributed_wan
                or stored is None
                or stored.get("lastrun") != lastrun
                or all(stored.get(k) == result.get(k) for k in _FIGURES)
            ):
                return
            # The rest of a run already attributed, so what was asked for
            # and what was concluded from it stand.
            requested = stored.get("requested_wan")
            source = stored.get("source") or source
        else:
            self._last_attributed_run = lastrun
            self._last_attributed_wan = None
            # A WAN's result never moves backwards in time. The controller
            # can report an older run than the one already recorded - a
            # block caught mid-rewrite falls back to the uplink's legacy
            # fields, which on some firmware describe a run months earlier -
            # and that must not replace a newer result with a stale one for
            # as long as it takes the next poll to correct it.
            if stored is not None and not is_newer(lastrun, stored.get("lastrun")):
                _LOGGER.debug(
                    "Ignored a speedtest result for WAN%s older than the one "
                    "held (reported %s, holding %s)",
                    wan_number,
                    lastrun,
                    stored.get("lastrun"),
                )
                return

            if requested is not None and requested != wan_number:
                self._note_wrong_wan(requested, wan_number, source)
            elif requested is not None and source == "source_interface":
                self._per_wan_supported = True
            self._last_attributed_wan = wan_number

        self.results[wan_number] = {
            "down": result.get("down"),
            "up": result.get("up"),
            "ping": result.get("ping"),
            "lastrun": lastrun,
            "source": source,
            "requested_wan": requested,
            # Recorded against the WAN the block itself names, which is
            # stricter than the throughput above: that may fall back to the
            # active uplink, but the server latches with no later run to
            # correct a wrong guess.
            **server_for(wan_number),
        }
        _LOGGER.debug(
            "%s speedtest result for WAN%s (matched by %s, requested %s)",
            "Completed the" if same_run else "Attributed a",
            wan_number,
            source,
            requested,
        )
        async_dispatcher_send(self.hass, self.result_signal)

    def _note_wrong_wan(self, requested: int, recorded: int, source: str) -> None:
        """A result recorded against a WAN other than the one asked for.

        Only the controller naming the interface is evidence that it
        ignored the request. Where the WAN was inferred from the active
        uplink instead, nothing was observed about which line actually ran,
        and concluding that this gateway cannot target a WAN would disable
        the rotation on a guess.
        """
        if source != "source_interface":
            _LOGGER.debug(
                "Speedtest was requested on WAN%s and the result recorded "
                "against WAN%s, which was inferred from the active uplink "
                "rather than named by the controller. That is not evidence "
                "the request was ignored, so per-WAN requests stay enabled.",
                requested,
                recorded,
            )
            return
        if self._per_wan_supported is False:
            return
        self._per_wan_supported = False
        _LOGGER.warning(
            "Speedtest was requested on WAN%s but the controller reports having "
            "run it on WAN%s. This gateway appears to always test the active "
            "uplink, so per-WAN speedtest requests are not supported; the "
            "result has been recorded against WAN%s and the automatic "
            "speedtest will stop cycling interfaces.",
            requested,
            recorded,
            recorded,
        )

    # -------------------------------------------------------------- running

    def _stamps(self, data: UniFiWanData | None) -> dict[Any, Any]:
        """Every timestamp a finished run could move, keyed by the WAN
        number it belongs to and "gateway" for the global block.

        Firmware differs over what a completed run updates: the gateway's
        own block, that WAN's record in the per-WAN speedtest API, or both
        - and a targeted run on a controller that ignores the requested
        interface moves a different WAN's record than the one asked for.
        Watching only the timestamp this request asked for therefore
        reports a test that did run as having timed out, so all of them are
        watched and the run is judged on any of them moving.
        """
        if data is None:
            return {}
        stamps: dict[Any, Any] = {"gateway": data.speedtest.get("lastrun")}
        for number, record in (data.per_wan_speedtest or {}).items():
            stamps[number] = record.get("lastrun")
        return stamps

    async def _wait_for_result(self, before: dict[Any, Any]) -> list[Any]:
        """Poll until a watched timestamp moves, and report which ones did.

        Empty means the controller recorded nothing. The refresh is the
        undebounced one: this loop already paces itself, and the debounced
        form returns without fetching inside its cooldown, which would burn
        an iteration of the wait.
        """
        deadline = self.hass.loop.time() + SPEEDTEST_TIMEOUT_SECONDS
        while self.hass.loop.time() < deadline:
            await asyncio.sleep(SPEEDTEST_POLL_SECONDS)
            await self.coordinator.async_refresh()
            after = self._stamps(self.coordinator.data)
            if moved := [k for k, v in after.items() if v and before.get(k) != v]:
                return moved
        return []

    @staticmethod
    def _describe(keys: list[Any]) -> str:
        return (
            ", ".join(
                "the gateway's own result" if k == "gateway" else f"WAN{k}"
                for k in keys
            )
            or "nothing"
        )

    async def async_run(self, wan_number: int | None = None) -> None:
        """Trigger a speedtest, optionally on a specific WAN interface, and
        wait (with a timeout) for the controller to report a fresh result.
        """
        if wan_number is not None and wan_number not in self.wan_numbers:
            # Asking for an interface the gateway does not have is refused
            # by the console, and would be read as the console refusing
            # targeted runs altogether. The service rejects such a number
            # before it gets here; this keeps any other caller honest.
            _LOGGER.warning(
                "Not running a speedtest on WAN%s: this gateway has %s",
                wan_number,
                ", ".join(f"WAN{n}" for n in self.wan_numbers) or "no WANs",
            )
            return
        if self.running:
            _LOGGER.debug("Speedtest already in progress; ignoring trigger")
            return

        self._set_running(True)
        self._pending_wan = wan_number
        try:
            mac = await self._gateway_mac()
            if not mac:
                _LOGGER.warning("Cannot run speedtest: No gateway found.")
                return

            data = self.coordinator.data
            # With a single WAN the whole-gateway speedtest already is that
            # WAN's speedtest, so don't ask the controller to target it -
            # firmware that rejects targeted requests would otherwise turn
            # every press of the per-WAN button into three failed calls.
            target = wan_number if len(self.wan_numbers) > 1 else None
            iface = None
            if target is not None and data is not None:
                iface = (data.wan.get(target) or {}).get("ifname")

            before = self._stamps(data)
            if not await self.client.run_speedtest(mac, target, iface):
                # Nothing was started, so there is no result coming. Waiting
                # the full timeout here would report a command the console
                # refused as one that simply never finished.
                _LOGGER.warning(
                    "The controller did not accept a speedtest for %s, so no "
                    "result is expected; the error it gave is logged above.",
                    f"WAN{target}" if target is not None else "the active WAN",
                )
                return
            moved = await self._wait_for_result(before)

            if (
                not moved
                and target is not None
                and self.client.targeted_speedtest_supported
            ):
                # The controller took the targeted request and then recorded
                # nothing at all, so the interface it was given is one it
                # will not test. Rather than leaving the press unmeasured,
                # stop using the targeted form and repeat the run as a plain
                # whole-gateway test, which is what every other controller
                # falls back to.
                self.client.targeted_speedtest_supported = False
                _LOGGER.warning(
                    "The controller accepted a speedtest for WAN%s (interface "
                    "%r) but recorded no result within %s seconds. Repeating it "
                    "as a whole-gateway speedtest and not targeting an "
                    "interface again this session.",
                    target,
                    iface,
                    SPEEDTEST_TIMEOUT_SECONDS,
                )
                before = self._stamps(self.coordinator.data)
                if not await self.client.run_speedtest(mac):
                    _LOGGER.warning(
                        "The controller did not accept the whole-gateway "
                        "speedtest either, so no result is expected; the error "
                        "it gave is logged above."
                    )
                    return
                moved = await self._wait_for_result(before)

            self._report_outcome(wan_number, target, moved, before)
        except asyncio.CancelledError:
            _LOGGER.debug("Speedtest cancelled")
            raise
        except Exception as e:  # noqa: BLE001 - a failed run must not escape
            _LOGGER.error("Speedtest trigger failed: %s", e)
        finally:
            self._pending_wan = None
            if self.rates_coordinator:
                await self.rates_coordinator.async_request_refresh()
            self._set_running(False)

    async def _gateway_mac(self) -> str | None:
        """The gateway's MAC, refreshing once if the poll has not seen it."""
        data = self.coordinator.data
        mac = data.gateway.get("mac") if data and data.gateway else None
        if mac:
            return mac
        await self.coordinator.async_refresh()
        data = self.coordinator.data
        return data.gateway.get("mac") if data and data.gateway else None

    def _report_outcome(
        self,
        wan_number: int | None,
        target: int | None,
        moved: list[Any],
        before: dict[Any, Any],
    ) -> None:
        if not moved:
            _LOGGER.warning(
                "Speedtest did not report a result within %s seconds "
                "(requested %s; watched %s). The controller accepted the "
                "request but never recorded a result - check whether a "
                "speedtest run from the UniFi UI updates the gateway.",
                SPEEDTEST_TIMEOUT_SECONDS,
                f"WAN{wan_number}" if wan_number is not None else "the active WAN",
                self._describe(sorted(before, key=str)),
            )
            return
        if target is None or target in moved:
            return
        other_wans = [k for k in moved if isinstance(k, int)]
        if other_wans and self._per_wan_supported is not False:
            # The run happened, just not where it was asked for. A per-WAN
            # record moving is the controller naming the line itself, which
            # is evidence rather than inference.
            self._per_wan_supported = False
            _LOGGER.warning(
                "Speedtest was requested on WAN%s but the controller recorded "
                "the result against %s, so this gateway does not honour "
                "per-WAN speedtest requests. Results are still recorded "
                "against the WAN the controller names, and the automatic "
                "speedtest will stop cycling interfaces.",
                target,
                self._describe(other_wans),
            )

    # ----------------------------------------------------------------- auto

    @callback
    def _auto_speedtest(self, _now) -> None:
        """Start the scheduled speedtest, cycling through the WAN interfaces
        that currently have link so each WAN accumulates its own results.

        Started through trigger, as a button press is, so the run is one of
        the entry's background tasks and is cancelled with the entry. Run
        from here directly it belonged to the time tracker instead, and
        outlived an unload or reload - still sending the console commands.

        The rotation stops as soon as the controller is seen to ignore a
        requested interface: on those gateways every run tests the active
        uplink anyway, so cycling would only spend extra tests to measure
        the same WAN.
        """
        data = self.coordinator.data
        # Cycling is only worth the extra tests where the controller both
        # accepts a targeted request and acts on it. Once it has been seen
        # to test a WAN other than the one asked for, every run measures the
        # same line whichever API records the result - and on a controller
        # that records per WAN it would also spend a full timeout waiting on
        # a record that is never written.
        pointless = (
            self.client.targeted_speedtest_supported is False
            or self._per_wan_supported is False
        )
        if pointless:
            self.trigger()
            return
        candidates = (
            [n for n in self.wan_numbers if (data.wan.get(n) or {}).get("up")]
            if data
            else []
        )
        if len(candidates) > 1:
            wan_number = candidates[self._auto_wan_index % len(candidates)]
            self._auto_wan_index += 1
            self.trigger(wan_number)
        else:
            self.trigger()
