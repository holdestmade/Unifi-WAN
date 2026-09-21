"""The client's handling of what the console answers.

The distinction that matters here is between a console saying "I do not
have that endpoint", which is worth remembering for the session, and a
request that simply did not get through, which is not.
"""

from __future__ import annotations

import pytest
from helpers import make_client

OK = {"meta": {"rc": "ok"}}
REFUSED = {"meta": {"rc": "error", "msg": "api.err.InvalidPayload"}}


# ----------------------------------------------------------------- timeouts


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.get_json("stat/device"),
        lambda c: c.post_json("cmd/devmgr", {}),
        lambda c: c.get_speedtest_history(),
        lambda c: c.fetch_raw("stat/device"),
    ],
)
async def test_every_request_carries_a_timeout(call):
    """Home Assistant's shared session sets none, so aiohttp's five-minute
    default would otherwise apply to every call.
    """
    client = make_client([(200, {"data": []})])
    await call(client)
    assert client._session.timeouts[0].total == 30


# ------------------------------------------------------------- command sent


async def test_a_plain_speedtest_reports_acceptance():
    assert await make_client([(200, OK)]).run_speedtest("aa") is True


@pytest.mark.parametrize(
    "response",
    [(200, REFUSED), (503, None), (0, None)],
    ids=["refused in the envelope", "http error", "unreachable"],
)
async def test_a_command_that_was_not_taken_reports_false(response):
    status, body = response
    script = [Exception("connection reset")] if status == 0 else [response]
    assert await make_client(script).run_speedtest("aa") is False


async def test_a_200_carrying_an_error_is_not_acceptance():
    """The console refuses some commands with HTTP 200 and a message."""
    client = make_client([(200, REFUSED)] * 3 + [(200, OK)])
    await client.run_speedtest("aa", 2, "eth6")
    assert client.targeted_speedtest_supported is False


# ------------------------------------------------- remembering what is true


async def test_rejection_of_every_targeted_form_is_remembered():
    client = make_client([(404, None), (404, None), (404, None), (200, OK)])
    assert await client.run_speedtest("aa", 2, "eth6") is True
    assert client.targeted_speedtest_supported is False
    # A later run goes straight to the whole-gateway command.
    later = make_client([(200, OK)])
    later.targeted_speedtest_supported = False
    await later.run_speedtest("aa", 2, "eth6")
    assert later._session.calls[0][1].endswith("cmd/devmgr")


async def test_acceptance_is_remembered():
    client = make_client([(200, OK)])
    assert await client.run_speedtest("aa", 2, "eth6") is True
    assert client.targeted_speedtest_supported is True


@pytest.mark.parametrize("failure", [(500, None), (0, None)])
async def test_a_failure_that_is_not_a_refusal_is_not_remembered(failure):
    """One unreachable moment must not cost per-WAN speedtests for the session."""
    script = [Exception("boom")] if failure[0] == 0 else [failure]
    client = make_client([*script, (200, OK)])
    # Falls back to a whole-gateway run for this press, which is accepted.
    assert await client.run_speedtest("aa", 2, "eth6") is True
    assert client.targeted_speedtest_supported is None
    # And it stopped rather than working through the other targeted forms.
    assert len(client._session.calls) == 2
    assert client._session.calls[1][1].endswith("cmd/devmgr")


async def test_nothing_accepted_at_all_reports_false():
    client = make_client([Exception("down"), Exception("down")])
    assert await client.run_speedtest("aa", 2, "eth6") is False
    assert client.targeted_speedtest_supported is None


async def test_a_single_wan_request_never_tries_the_targeted_forms():
    client = make_client([(200, OK)])
    await client.run_speedtest("aa")
    assert len(client._session.calls) == 1


# ------------------------------------------------------ speedtest history


@pytest.mark.parametrize("status", [404, 405])
async def test_a_missing_history_endpoint_is_remembered(status):
    client = make_client([(status, None)])
    assert await client.get_speedtest_history() is None
    assert client.speedtest_history_supported is False
    # And it is not asked again.
    assert await client.get_speedtest_history() is None
    assert len(client._session.calls) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 502])
async def test_other_history_failures_are_not_remembered(status):
    """A 401 is as likely to be a key whose permissions changed."""
    client = make_client([(status, None)])
    assert await client.get_speedtest_history() is None
    assert client.speedtest_history_supported is True


async def test_history_returns_the_body():
    client = make_client([(200, {"data": [{"time": 1}]})])
    assert await client.get_speedtest_history() == {"data": [{"time": 1}]}


# ------------------------------------------------------------------ raw GET


async def test_fetch_raw_reports_a_failure_rather_than_raising():
    """A 404 here is itself the answer to a support question."""
    result = await make_client([(404, None)]).fetch_raw("speedtest", v2=True)
    assert result["status"] == 404
    assert "v2/api/site/default/speedtest" in result["url"]
