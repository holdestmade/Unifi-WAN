"""HTTP client for the UniFi Network endpoints this integration reads."""

from __future__ import annotations

import logging
from typing import Any, Final

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import (
    DEFAULT_SITE,
    HISTORY_UNSUPPORTED_STATUSES,
    REQUEST_TIMEOUT_SECONDS,
    UNSUPPORTED_STATUSES,
)

_LOGGER = logging.getLogger(__name__)

# Applied to every call to the console. Home Assistant's shared session has
# no timeout of its own, so this replaces aiohttp's five-minute default.
REQUEST_TIMEOUT: Final = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)


class UnifiWanClient:
    """Simple HTTP client for UniFi Network endpoints."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        api_key: str,
        site: str,
        verify_ssl: bool,
    ) -> None:
        self._hass = hass
        self.host = (host or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()
        # Stripped here too, for an entry whose site was stored padded
        # before the flows normalised it: the console has no " default ".
        self.site = (site or "").strip() or DEFAULT_SITE
        self.verify_ssl = bool(verify_ssl)
        self._session = async_get_clientsession(hass, self.verify_ssl)
        # Set once the controller has told us it has no per-WAN speedtest
        # API, so we stop asking on every poll.
        self._speedtest_history_unsupported = False
        # None until a targeted speedtest tells us whether this controller
        # accepts one; False stops us retrying endpoints it has rejected.
        self.targeted_speedtest_supported: bool | None = None

    @property
    def speedtest_history_supported(self) -> bool:
        """Whether the per-WAN speedtest API is still believed to exist.

        False only once the controller has answered that the endpoint is
        not there. It stays True while a fetch merely fails, which is what
        lets the caller tell "this console has no per-WAN records" apart
        from "that one request did not get through".
        """
        return not self._speedtest_history_unsupported

    def _url(self, path: str) -> str:
        return f"https://{self.host}/proxy/network/api/s/{self.site}/{path}"

    def _url_v2(self, path: str) -> str:
        return f"https://{self.host}/proxy/network/v2/api/site/{self.site}/{path}"

    async def get_json(self, path: str) -> dict:
        url = self._url(path)
        headers = {"X-API-Key": self.api_key}
        try:
            async with self._session.get(
                url, headers=headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status in (401, 403):
                    raise ConfigEntryAuthFailed(
                        f"Authentication failed (HTTP {resp.status})"
                    )
                text = await resp.text()
                if resp.status != 200:
                    raise UpdateFailed(f"HTTP {resp.status} for {url}: {text[:200]}")
                return await resp.json(content_type=None)
        except (ConfigEntryAuthFailed, UpdateFailed):
            raise
        except Exception as e:
            raise UpdateFailed(f"Connection error: {e}") from e

    @staticmethod
    def _api_error(body: Any) -> str | None:
        """The controller's own error message from a response body, if the
        body is one.

        The controller answers some rejected commands with HTTP 200 and the
        refusal in the envelope rather than in the status code, so a bare
        200 is not on its own proof that a command was accepted.
        """
        meta = body.get("meta") if isinstance(body, dict) else None
        if not isinstance(meta, dict):
            return None
        if str(meta.get("rc") or "").lower() != "error":
            return None
        return str(meta.get("msg") or "unknown error")

    async def _post(self, url: str, payload: dict) -> tuple[int, Any]:
        """POST and return (status, decoded body). Status 0 means the request
        itself failed. A 200 with an empty or non-JSON body still counts as
        accepted, so the body falls back to a plain ok marker.
        """
        headers = {"X-API-Key": self.api_key}
        try:
            async with self._session.post(
                url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT
            ) as resp:
                try:
                    body = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    body = {"ok": resp.status == 200}
                if resp.status != 200:
                    _LOGGER.debug("HTTP %s for %s", resp.status, url)
                return resp.status, body
        except Exception as e:
            _LOGGER.error("POST failed: %s", e)
            return 0, None

    async def post_json(self, path: str, payload: dict) -> bool:
        """Send a command and report whether the controller took it.

        The body itself carries nothing the caller needs - a speedtest is
        reported through the gateway's own records, not in the reply - so
        what comes back is the one thing worth acting on: whether there is
        any point waiting for a result.
        """
        status, body = await self._post(self._url(path), payload)
        if status != 200:
            _LOGGER.error("HTTP %s for %s", status, self._url(path))
            return False
        if (error := self._api_error(body)) is not None:
            # A 200 that says "no": worth surfacing, because the command
            # simply not happening is otherwise only visible as a result
            # that never arrives.
            _LOGGER.error("Controller refused %s: %s", path, error)
            return False
        return True

    async def get_speedtest_history(self) -> dict | None:
        """Per-WAN speedtest records from the v2 API, or None if this
        controller does not offer them.

        Unlike the gateway's single global result, this endpoint keeps a
        record per WAN, which is the only way to know a non-active WAN's
        own throughput. Older firmware answers 404/405 and is expected.
        """
        if self._speedtest_history_unsupported:
            return None
        url = self._url_v2("speedtest")
        headers = {"X-API-Key": self.api_key}
        try:
            async with self._session.get(
                url, headers=headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status in HISTORY_UNSUPPORTED_STATUSES:
                    self._speedtest_history_unsupported = True
                    _LOGGER.debug(
                        "Per-WAN speedtest API unavailable (HTTP %s for %s); "
                        "falling back to attributing the gateway's global result",
                        resp.status,
                        url,
                    )
                    return None
                if resp.status != 200:
                    # Not remembered: a 401/403 is as likely to be a key whose
                    # permissions changed as an endpoint that is not there, and
                    # a 5xx is the console having a bad moment. Either way the
                    # caller keeps the previous records rather than switching
                    # to the guessier attribution route for one poll.
                    _LOGGER.debug(
                        "Per-WAN speedtest fetch returned HTTP %s for %s; "
                        "keeping the records from the previous poll",
                        resp.status,
                        url,
                    )
                    return None
                body = await resp.json(content_type=None)
                return body if isinstance(body, dict) else None
        except Exception as e:
            _LOGGER.debug("Per-WAN speedtest fetch failed: %s", e)
            return None

    async def fetch_raw(self, path: str, *, v2: bool = False) -> dict[str, Any]:
        """GET an endpoint and report the whole outcome, without raising.

        For the unredacted dumps: unlike get_json this reports the status
        code and any error rather than turning them into an update failure,
        because "this endpoint answers 404 on your firmware" is itself the
        finding. The body is returned decoded where it is JSON and as text
        otherwise, so a controller answering HTML still shows what it said.
        """
        url = self._url_v2(path) if v2 else self._url(path)
        result: dict[str, Any] = {"url": url}
        headers = {"X-API-Key": self.api_key}
        try:
            async with self._session.get(
                url, headers=headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                result["status"] = resp.status
                result["content_type"] = resp.headers.get("Content-Type")
                try:
                    result["body"] = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    # Truncated: a controller that answers with a login page
                    # has already made its point in the first few hundred
                    # characters, and the rest is not worth the file size.
                    result["body_text"] = (await resp.text())[:2000]
        except Exception as e:  # noqa: BLE001 - the error is the finding here
            result["error"] = f"{type(e).__name__}: {e}"
        return result

    async def get_devices(self) -> dict:
        return await self.get_json("stat/device")

    async def get_device(self, mac: str) -> dict:
        return await self.get_json(f"stat/device/{mac}")

    async def run_speedtest(
        self,
        mac: str,
        wan_number: int | None = None,
        interface_name: str | None = None,
    ) -> bool:
        """Trigger a speedtest, optionally against a specific WAN.

        Returns whether the controller accepted the command, so a caller
        that would otherwise sit through a five-minute timeout waiting for
        a result can give up immediately when there was never going to be
        one.

        A targeted run identifies the interface with "interface_name" (the
        WAN's own ifname, e.g. "eth7"). Firmware differs over which endpoint
        accepts it, so the known forms are tried in turn until one is not
        rejected. Once a controller has rejected all of them it is not asked
        again, and every run uses the plain whole-gateway command.
        """
        plain = {"cmd": "speedtest", "mac": mac}
        if wan_number is None or self.targeted_speedtest_supported is False:
            return await self.post_json("cmd/devmgr", plain)

        # Fall back to the logical name when the WAN section has no ifname.
        iface = interface_name or ("wan" if wan_number == 1 else f"wan{wan_number}")
        attempts: list[tuple[str, dict]] = [
            (self._url("cmd/devmgr/speedtest"), {"interface_name": iface}),
            (
                self._url("cmd/devmgr"),
                {"cmd": "speedtest", "mac": mac, "interface_name": iface},
            ),
            (self._url_v2("speedtest"), {"interface_name": iface}),
        ]
        tried: list[str] = []
        unreachable: str | None = None
        for url, payload in attempts:
            status, body = await self._post(url, payload)
            # A 200 carrying an error in the envelope is a refusal, not an
            # acceptance: latching onto such an endpoint would leave every
            # later run silently unperformed.
            error = self._api_error(body)
            # Record the path from /network/ onwards; the host and site add
            # nothing and the site name is not worth putting in a log.
            tried.append(
                f"{url.split('/network/', 1)[-1]}={status}"
                + (f" ({error})" if error else "")
            )
            if status == 200 and error is None:
                self.targeted_speedtest_supported = True
                _LOGGER.debug("Speedtest for WAN%s accepted by %s", wan_number, url)
                return True
            if error is None and status not in UNSUPPORTED_STATUSES:
                # The request never got through, or the console erred. That
                # is not the console telling us this endpoint is the wrong
                # one, so it is not evidence to remember: disabling targeted
                # speedtests here would turn one unreachable moment into a
                # whole session of untargeted runs.
                unreachable = f"HTTP {status}" if status else "the request failed"
                break

        if unreachable is not None:
            _LOGGER.warning(
                "Could not ask this controller for a per-WAN speedtest on WAN%s "
                "(interface %r; %s; tried %s). Falling back to a whole-gateway "
                "speedtest for this run only - the targeted form will be tried "
                "again next time.",
                wan_number,
                iface,
                unreachable,
                ", ".join(tried),
            )
            return await self.post_json("cmd/devmgr", plain)

        self.targeted_speedtest_supported = False
        _LOGGER.warning(
            "This controller rejected every per-WAN speedtest request for WAN%s "
            "(interface %r; tried %s). Falling back to a whole-gateway speedtest "
            "and not asking again this session. Results are still recorded "
            "against whichever WAN the controller reports having tested.",
            wan_number,
            iface,
            ", ".join(tried),
        )
        return await self.post_json("cmd/devmgr", plain)
