"""DRF-2607: the exchange of the person's initData for their own salon token.

What the view tests cannot see — they replace the exchange whole: how a REAL
catalog answer is classified. «Whose to fix» decides whether a refusal wakes
someone up (ERROR) or is the person's (INFO), and a rule «below 500 is
theirs» would bury a missing route, a throttle and a key drift as the
person's fault.
"""

from __future__ import annotations

import json

import httpx
import pytest

from apps.integrations.ayla.person_token import PersonTokenRefused, obtain_person_token

RAW = "auth_date=1&user=%7B%22id%22%3A1%7D&hash=abc"  # the person's initData, verbatim
TENANT = "formula-tela"


def _exchange(handler):
    sink: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        sink.append(request)
        return handler(request)

    return sink, lambda: obtain_person_token(
        init_data=RAW,
        tenant_slug=TENANT,
        base_url="https://ayla.example",
        transport=httpx.MockTransport(recording),
    )


def test_the_exchange_carries_the_persons_initdata_and_no_credential_of_ours():
    sink, call = _exchange(
        lambda r: httpx.Response(200, json={"data": {"access_token": "t-1", "expires_in": 600}})
    )
    token = call()
    assert (token.access_token, token.expires_in) == ("t-1", 600)
    (request,) = sink
    assert request.url.path == "/api/v1/auth/max/salon-admin/token/"
    assert "authorization" not in {k.lower() for k in request.headers}
    assert request.headers["x-tenant"] == TENANT
    assert json.loads(request.content) == {"init_data": RAW}


@pytest.mark.parametrize(
    ("status", "body", "reason", "ours"),
    [
        # the person's: they may not / are not linked / reopen the app
        (403, {"error": {"code": "NO_SALON_ROLE"}}, "NO_SALON_ROLE", False),
        (403, {"error": {"code": "LINK_NOT_PROVEN"}}, "LINK_NOT_PROVEN", False),
        (401, {"error": {"code": "INIT_DATA_STALE"}}, "INIT_DATA_STALE", False),
        # ours: the bot verified this signature a moment ago; a missing route;
        # a throttle; a tenant the catalog does not know; the key unset
        (401, {"error": {"code": "INIT_DATA_BAD_SIGNATURE"}}, "INIT_DATA_BAD_SIGNATURE", True),
        (404, None, "HTTP_404", True),
        (429, {"detail": "Request was throttled."}, "HTTP_429", True),
        (400, {"error": {"code": "TENANT_REQUIRED"}}, "TENANT_REQUIRED", True),
        (503, {"error": {"code": "NOT_CONFIGURED"}}, "NOT_CONFIGURED", True),
    ],
    ids=lambda v: str(v) if not isinstance(v, dict) else "body",
)
def test_every_refusal_says_whose_it_is(status, body, reason, ours):
    _, call = _exchange(
        lambda r: (
            httpx.Response(status, json=body)
            if body is not None
            else httpx.Response(status, text="<html>")
        )
    )
    with pytest.raises(PersonTokenRefused) as caught:
        call()
    assert (caught.value.reason, caught.value.ours_to_fix) == (reason, ours)
    assert RAW not in str(caught.value) and "abc" not in repr(caught.value.args)


def test_an_unreachable_catalog_is_ours():
    def down(request):
        raise httpx.ConnectError("refused")

    _, call = _exchange(down)
    with pytest.raises(PersonTokenRefused) as caught:
        call()
    assert (caught.value.reason, caught.value.ours_to_fix) == ("EXCHANGE_UNREACHABLE", True)


def test_a_success_without_a_token_is_not_a_token():
    _, call = _exchange(lambda r: httpx.Response(200, json={"data": {}}))
    with pytest.raises(PersonTokenRefused) as caught:
        call()
    assert (caught.value.reason, caught.value.ours_to_fix) == ("EXCHANGE_BAD_ANSWER", True)


def test_a_code_that_is_not_a_code_does_not_reach_the_journal_verbatim():
    _, call = _exchange(lambda r: httpx.Response(403, json={"error": {"code": "x y\nforged=1"}}))
    with pytest.raises(PersonTokenRefused) as caught:
        call()
    assert caught.value.reason == "UNRECOGNISED_CODE"


def test_without_initdata_nothing_is_sent():
    sink: list[httpx.Request] = []
    with pytest.raises(PersonTokenRefused):
        obtain_person_token(
            init_data="",
            tenant_slug=TENANT,
            base_url="https://ayla.example",
            transport=httpx.MockTransport(lambda r: sink.append(r) or httpx.Response(200)),
        )
    assert sink == []
