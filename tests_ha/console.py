"""A scripted UniFi OS console behind Home Assistant's own HTTP session.

The integration talks to the console through the aiohttp session Home
Assistant hands out, so the console is served by the test harness's
AiohttpClientMocker: every request the integration makes lands in
MockConsole, which answers from state a test can change between polls.

Nothing here reimplements integration logic. The payloads are the shapes
the unit tests already use, extended to a two-WAN gateway.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from copy import deepcopy
from http import HTTPStatus
from typing import Any

from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

HOST = "192.0.2.10"
SITE = "default"
API_KEY = "test-key"
MAC = "aa:bb:cc:dd:ee:ff"

# Epoch seconds for the speedtest the console reports at setup.
T0 = 1_760_000_000


def two_wan_gateway() -> dict[str, Any]:
    """A dual-WAN UDM Pro with WAN1 as the active uplink."""
    return {
        "type": "udm",
        "model": "UDMPRO",
        "mac": MAC,
        "version": "4.0.6",
        "adopted": True,
        "uplink": {
            "up": True,
            "ip": "203.0.113.1",
            "name": "eth8",
            "rx_bytes-r": 1_250_000,
            "tx_bytes-r": 250_000,
        },
        "wan1": {"up": True, "ip": "203.0.113.1", "ifname": "eth8", "name": "wan"},
        "wan2": {"up": True, "ip": "198.51.100.7", "ifname": "eth9", "name": "wan2"},
        "last_wan_interfaces": {"WAN": {"alive": True}, "WAN2": {"alive": True}},
        "last_wan_status": {"WAN": "online", "WAN2": "online"},
        "speedtest-status": {
            "rundate": T0,
            "xput_download": 500.0,
            "xput_upload": 50.0,
            "latency": 9,
            "source_interface": "if!eth8",
            "status_summary": 2,
            "server": {"provider": "ExampleNet", "city": "London", "country": "GB"},
        },
        "geo_info": {
            "WAN": {"isp_name": "Line One ISP", "country_name": "United Kingdom"},
            "WAN2": {"isp_name": "Line Two ISP", "country_name": "United Kingdom"},
        },
    }


def one_wan_gateway() -> dict[str, Any]:
    """The same gateway with only WAN1."""
    device = two_wan_gateway()
    del device["wan2"]
    device["last_wan_interfaces"] = {"WAN": {"alive": True}}
    device["last_wan_status"] = {"WAN": "online"}
    device["geo_info"] = {"WAN": device["geo_info"]["WAN"]}
    return device


def history_record(group: str, when: int, down: float, up: float) -> dict[str, Any]:
    """One per-WAN record from the v2 speedtest API (timestamps in ms)."""
    return {
        "wan_networkgroup": group,
        "time": when * 1000,
        "download_mbps": down,
        "upload_mbps": up,
        "latency_ms": 10,
    }


PostHandler = Callable[["MockConsole", str, dict[str, Any] | None], tuple[int, Any]]


class MockConsole:
    """State for one console, served on every request the integration makes.

    ``gateway`` is the gateway device in stat/device; ``history`` is the
    v2 per-WAN body, or None to answer that endpoint 404 as older firmware
    does. ``status`` overrides the answer for a path ("stat/device",
    "v2:speedtest", ...) with a bare status code, and ``raises`` makes a
    path fail the way a transport error does. ``on_post`` decides what a
    command does; the default accepts it and records nothing. A request
    whose X-API-Key is not ``api_key`` is answered 401, as the console does.
    """

    def __init__(
        self,
        host: str = HOST,
        site: str = SITE,
        api_key: str = API_KEY,
        gateway: dict[str, Any] | None = None,
    ) -> None:
        self.host = host
        self.site = site
        self.api_key = api_key
        self.gateway: dict[str, Any] | None = (
            gateway if gateway is not None else two_wan_gateway()
        )
        self.other_devices: list[dict[str, Any]] = [
            {"type": "usw", "model": "USW-24", "mac": "11:22:33:44:55:66"}
        ]
        self.history: dict[str, Any] | None = {
            "data": [history_record("WAN", T0, 500.0, 50.0)]
        }
        self.status: dict[str, int] = {}
        self.raises: dict[str, BaseException] = {}
        self.posts: list[tuple[str, dict[str, Any] | None]] = []
        self.gets: list[str] = []
        self.on_post: PostHandler | None = None
        self._mocker: AiohttpClientMocker | None = None

    # ----------------------------------------------------------- serving

    def register(self, aioclient_mock: AiohttpClientMocker) -> None:
        self._mocker = aioclient_mock
        pattern = re.compile(rf"^https://{re.escape(self.host)}/")
        aioclient_mock.get(pattern, side_effect=self._handle)
        aioclient_mock.post(pattern, side_effect=self._handle)

    def _path(self, url: Any) -> str | None:
        """The endpoint a URL names, or None when the site is wrong."""
        path = url.path
        v1 = "/proxy/network/api/s/"
        v2 = "/proxy/network/v2/api/site/"
        if path.startswith(v1):
            site, _, rest = path[len(v1) :].partition("/")
            return rest if site == self.site else None
        if path.startswith(v2):
            site, _, rest = path[len(v2) :].partition("/")
            return f"v2:{rest}" if site == self.site else None
        return None

    async def _handle(
        self, method: str, url: Any, data: Any
    ) -> AiohttpClientMockResponse:
        path = self._path(url)
        if path is None:
            return self._respond(method, url, HTTPStatus.NOT_FOUND, text="Not Found")
        if path in self.raises:
            response = self._respond(method, url, 200, text="")
            response.exc = self.raises[path]
            return response
        # The mocker records the request, headers included, before asking
        # this handler for the answer.
        assert self._mocker is not None
        headers = self._mocker.mock_calls[-1][3] or {}
        if headers.get("X-API-Key") != self.api_key:
            return self._respond(method, url, 401, text="Unauthorized")
        if method.lower() == "post":
            self.posts.append((path, data))
            if path in self.status:
                return self._respond(method, url, self.status[path], text="")
            handler = self.on_post or accept_and_record_nothing
            status, body = handler(self, path, data)
            return self._respond(method, url, status, json=body)

        self.gets.append(path)
        if path in self.status:
            return self._respond(method, url, self.status[path], text="error")
        if path == "stat/device":
            return self._respond(method, url, 200, json={"data": self.devices()})
        if path.startswith("stat/device/"):
            mac = path.rsplit("/", 1)[-1]
            match = [d for d in self.devices() if d.get("mac") == mac]
            return self._respond(method, url, 200, json={"data": match})
        if path == "v2:speedtest":
            if self.history is None:
                return self._respond(method, url, 404, text="Not Found")
            return self._respond(method, url, 200, json=deepcopy(self.history))
        if path == "rest/portforward":
            return self._respond(method, url, 200, json={"data": []})
        return self._respond(method, url, 404, text="Not Found")

    @staticmethod
    def _respond(
        method: str, url: Any, status: int, *, json: Any = None, text: str | None = None
    ) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse(
            method,
            url,
            status=status,
            json=json,
            text=text,
            headers={"Content-Type": "application/json"},
        )

    # ------------------------------------------------------------ state

    def devices(self) -> list[dict[str, Any]]:
        gateway = [deepcopy(self.gateway)] if self.gateway is not None else []
        return gateway + deepcopy(self.other_devices)

    def finish_run(
        self,
        *,
        rundate: int,
        down: float,
        up: float | None,
        iface: str | None,
        history_group: str | None = None,
    ) -> None:
        """Record a completed run the way firmware reports one."""
        assert self.gateway is not None
        status = self.gateway["speedtest-status"]
        status.update(
            {
                "rundate": rundate,
                "xput_download": down,
                "xput_upload": up,
                "source_interface": f"if!{iface}" if iface else "",
            }
        )
        if history_group is not None and self.history is not None:
            self.history["data"].append(
                history_record(history_group, rundate, down, up or 0.0)
            )

    def speedtest_posts(self) -> list[tuple[str, dict[str, Any] | None]]:
        return [p for p in self.posts if "speedtest" in p[0] or "devmgr" in p[0]]


def accept_and_record_nothing(
    console: MockConsole, path: str, data: dict[str, Any] | None
) -> tuple[int, Any]:
    """Accept every command and never produce a result."""
    return 200, {"meta": {"rc": "ok"}, "data": []}


def run_on(
    wan_for_iface: dict[str, tuple[str, str]],
    *,
    down: float,
    up: float,
    active_iface: str = "eth8",
    record_history: bool = True,
) -> PostHandler:
    """A console whose speedtest command runs and records a result at once.

    ``wan_for_iface`` maps the interface_name a targeted request carries to
    the (ifname, WAN group) the run is recorded against, which lets a test
    model firmware that honours the request and firmware that ignores it.
    An untargeted request tests ``active_iface``.
    """
    groups = {"eth8": "WAN", "eth9": "WAN2"}
    counter = {"n": 0}

    def handler(
        console: MockConsole, path: str, data: dict[str, Any] | None
    ) -> tuple[int, Any]:
        counter["n"] += 1
        requested = (data or {}).get("interface_name")
        if requested is not None and requested not in wan_for_iface:
            return 400, {"meta": {"rc": "error", "msg": "api.err.InvalidInterface"}}
        ifname, group = (
            wan_for_iface[requested]
            if requested is not None
            else (active_iface, groups[active_iface])
        )
        console.finish_run(
            rundate=T0 + 600 * counter["n"],
            down=down,
            up=up,
            iface=ifname,
            history_group=group if record_history else None,
        )
        return 200, {"meta": {"rc": "ok"}, "data": []}

    return handler
