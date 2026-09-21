"""Payload builders and stand-ins used across the tests."""

from __future__ import annotations

from typing import Any


def gateway_payload(
    *,
    wan: dict[str, Any] | None = None,
    wan2: dict[str, Any] | None = None,
    uplink: dict[str, Any] | None = None,
    speedtest_status: dict[str, Any] | None = None,
    last_wan_interfaces: dict[str, Any] | None = None,
    device_type: str = "udm",
    **extra: Any,
) -> dict[str, Any]:
    """A stat/device response carrying one gateway."""
    device: dict[str, Any] = {
        "type": device_type,
        "mac": "aa:bb:cc:dd:ee:ff",
        "model": "UDMPRO",
        "version": "4.0.6",
        "uplink": uplink if uplink is not None else {"up": True, "ip": "203.0.113.1"},
    }
    if wan is not None:
        device["wan"] = wan
    if wan2 is not None:
        device["wan2"] = wan2
    if speedtest_status is not None:
        device["speedtest-status"] = speedtest_status
    if last_wan_interfaces is not None:
        device["last_wan_interfaces"] = last_wan_interfaces
    device.update(extra)
    return {"data": [device]}


def history(*records: dict[str, Any]) -> dict[str, Any]:
    """A v2 speedtest-history response."""
    return {"data": list(records)}


def record(
    wan_group: str, when_ms: int, down: float = 100.0, up: float = 20.0
) -> dict[str, Any]:
    return {
        "wan_networkgroup": wan_group,
        "time": when_ms,
        "download_mbps": down,
        "upload_mbps": up,
        "latency_ms": 9,
    }


class FakeResponse:
    def __init__(self, status: int, body: Any = None, text: str = "") -> None:
        self.status = status
        self._body = body
        self._text = text
        self.headers = {"Content-Type": "application/json"}

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def json(self, content_type: Any = None) -> Any:
        if self._body is None:
            raise ValueError("not json")
        return self._body

    async def text(self) -> str:
        return self._text


class FakeSession:
    """Replays a scripted sequence of responses and records the calls.

    An entry may be an exception, which is raised as a connection failure
    would be. Running past the end of the script yields a plain success, so
    a test only scripts the responses it is actually about.
    """

    def __init__(self, script: list[Any] | None = None) -> None:
        self.script = list(script or [])
        self.calls: list[tuple[str, str, Any]] = []
        self.timeouts: list[Any] = []

    def _next(self, method: str, url: str, payload: Any, timeout: Any) -> FakeResponse:
        self.calls.append((method, url, payload))
        self.timeouts.append(timeout)
        item = self.script.pop(0) if self.script else (200, {"meta": {"rc": "ok"}})
        if isinstance(item, Exception):
            raise item
        status, body = item
        return FakeResponse(status, body)

    def get(self, url: str, headers: Any = None, timeout: Any = None) -> FakeResponse:
        return self._next("GET", url, None, timeout)

    def post(
        self, url: str, headers: Any = None, json: Any = None, timeout: Any = None
    ) -> FakeResponse:
        return self._next("POST", url, json, timeout)


def make_client(script: list[Any] | None = None):
    """A UnifiWanClient wired to a FakeSession, with no Home Assistant."""
    from unifi_wan.api import UnifiWanClient

    client = UnifiWanClient.__new__(UnifiWanClient)
    client._hass = None
    client.host = "10.0.0.1"
    client.api_key = "key"
    client.site = "default"
    client.verify_ssl = False
    client._session = FakeSession(script)
    client._speedtest_history_unsupported = False
    client.targeted_speedtest_supported = None
    return client


class FakeCoordinator:
    """Enough of DataUpdateCoordinator for the speedtest manager."""

    def __init__(self, data: Any = None) -> None:
        self.data = data
        self.speedtest_results: dict[int, dict[str, Any]] = {}
        self.listeners: list[Any] = []
        self.refreshes = 0

    def async_add_listener(self, callback: Any, context: Any = None) -> Any:
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback)

    async def async_refresh(self) -> None:
        self.refreshes += 1

    async def async_request_refresh(self) -> None:
        self.refreshes += 1

    def notify(self) -> None:
        for listener in list(self.listeners):
            listener()


class FakeEntry:
    """Enough of ConfigEntry for the speedtest manager."""

    def __init__(self, entry_id: str = "entry123") -> None:
        self.entry_id = entry_id
        self.unloads: list[Any] = []
        self.tasks: list[Any] = []

    def async_on_unload(self, callback: Any) -> None:
        self.unloads.append(callback)

    def async_create_background_task(self, hass: Any, target: Any, name: str) -> Any:
        self.tasks.append((name, target))
        target.close()  # nothing awaits it in a unit test
        return None
