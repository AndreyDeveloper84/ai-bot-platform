"""Ayla ``429 THROTTLED`` handling + page width (DRF-1595).

### The incident

``formula-tela`` (the pilot's head salon) and ``fevralskiy-svet`` went three
days without a catalog refresh while the other eight salons synced every
fifteen minutes. The worker log said::

    catalog.sync.beat_completed run=8 skipped=0 failed=2
    CatalogClientError: Ayla catalog 4xx: HTTP 429 ... body={"error":
      {"code":"THROTTLED","message":"Expected available in 54 seconds",
       "details":{"wait_seconds":54}}}

Ayla was telling us exactly when to come back and
:meth:`CatalogHttpClient._get_with_retry` was throwing that number away: 429
is a 4xx, and the generic 4xx branch raises terminally. Meanwhile the bot
answered clients that services the salon actively sells do not exist.

### What these tests pin

1. A 429 is waited out for the time Ayla asked for, then retried.
2. Every OTHER 4xx still fails on the first response, with no sleep. This
   test is the fence around (1): the natural way to break it is to widen the
   retry branch to ``4xx`` and turn every operator bug into a slow one.
3. The waiting is bounded by a per-run budget, and running out of budget is
   reported as its own condition (:class:`CatalogThrottledError` with
   ``budget_exhausted``) rather than as a failure of the salon.
4. All three catalog walks ask for ``page_size=100`` on the wire — the
   parameter halves our own request count against a limiter that turns out
   to be the anonymous 30/min one, so it is part of this fix, not a tidy-up.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from apps.catalog.services.http_client import (
    CatalogClientError,
    CatalogHttpClient,
    CatalogThrottledError,
)
from apps.catalog.services.throttle import ThrottleWaitBudget

_BASE = "https://ayla.test"
_TOKEN = "internal-token-abc"  # noqa: S105
_TID = "b0a1c2d3-0000-4000-8000-000000000001"
_SALON_URL = f"{_BASE}/api/v1/internal/catalog/salon-services/"
_SPECIALISTS_URL = f"{_BASE}/api/v1/internal/specialists/"
_EDGES_URL = f"{_BASE}/api/v1/internal/catalog/specialist-services/"

# The salon-services URL as the client actually forms it since DRF-1595.
_SALON_WIRE = f"{_SALON_URL}?tenant={_TID}&page_size=100"

# Verbatim shape of Ayla's throttle envelope — djangoProject/
# exception_handler.py maps DRF's `Throttled` into it. Copied rather than
# paraphrased: if upstream ever moves `wait_seconds`, this literal is what
# should fail.
_THROTTLE_BODY: dict[str, Any] = {
    "error": {
        "code": "THROTTLED",
        "message": "Expected available in 54 seconds",
        "details": {"wait_seconds": 54},
    }
}


def _client(**kwargs: Any) -> CatalogHttpClient:
    return CatalogHttpClient(base_url=_BASE, token=_TOKEN, retries=3, timeout=5, **kwargs)


def _empty_page() -> dict[str, Any]:
    return {"count": 0, "next": None, "previous": None, "results": []}


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record sleep durations instead of taking them.

    The durations are the assertion, not an implementation detail: "we
    honoured ``wait_seconds``" and "we retried at all" are different claims
    and this ticket is about the first one.
    """
    calls: list[float] = []

    def _record(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr("time.sleep", _record)
    return calls


class TestThrottleIsRetried:
    def test_429_waits_the_time_ayla_asked_then_succeeds(
        self, httpx_mock: HTTPXMock, slept: list[float]
    ) -> None:
        httpx_mock.add_response(url=_SALON_WIRE, status_code=429, json=_THROTTLE_BODY)
        httpx_mock.add_response(url=_SALON_WIRE, json=_empty_page())

        with _client(wait_budget=ThrottleWaitBudget(240)) as c:
            rows = c.fetch_salon_services(tenant_id=_TID)

        assert rows == []
        # 54, not 0.5 — the backoff ladder would have been the wrong answer
        # even if it had been applied, because the limiter's window is
        # upstream's to know.
        assert slept == [54.0]

    def test_wait_is_taken_from_the_run_budget(
        self, httpx_mock: HTTPXMock, slept: list[float]
    ) -> None:
        httpx_mock.add_response(url=_SALON_WIRE, status_code=429, json=_THROTTLE_BODY)
        httpx_mock.add_response(url=_SALON_WIRE, json=_empty_page())
        budget = ThrottleWaitBudget(240)

        with _client(wait_budget=budget) as c:
            c.fetch_salon_services(tenant_id=_TID)

        assert budget.spent_seconds == 54.0
        assert budget.remaining_seconds == 186.0
        assert budget.exhausted is False

    def test_still_throttled_after_every_attempt_is_not_a_client_error(
        self, httpx_mock: HTTPXMock, slept: list[float]
    ) -> None:
        for _ in range(3):
            httpx_mock.add_response(url=_SALON_WIRE, status_code=429, json=_THROTTLE_BODY)

        with _client(wait_budget=ThrottleWaitBudget(240)) as c:
            with pytest.raises(CatalogThrottledError) as exc_info:
                c.fetch_salon_services(tenant_id=_TID)

        # Two sleeps for three attempts: the last attempt has nothing left to
        # wait for.
        assert slept == [54.0, 54.0]
        # Not budget_exhausted — we had room, Ayla simply stayed closed. The
        # beat reads this flag to decide whether to stand down for the whole
        # cycle or only lose this salon.
        assert exc_info.value.budget_exhausted is False

    def test_missing_wait_seconds_falls_back_to_the_backoff_ladder(
        self, httpx_mock: HTTPXMock, slept: list[float]
    ) -> None:
        # DRF omits `details` entirely when `Throttled.wait` is falsy, so this
        # is a shape Ayla really emits — and it means "unknown", not "zero".
        httpx_mock.add_response(
            url=_SALON_WIRE,
            status_code=429,
            json={"error": {"code": "THROTTLED", "message": "slow down"}},
        )
        httpx_mock.add_response(url=_SALON_WIRE, json=_empty_page())

        with _client(wait_budget=ThrottleWaitBudget(240)) as c:
            c.fetch_salon_services(tenant_id=_TID)

        assert slept == [0.5]

    def test_retry_after_header_is_honoured_when_the_body_is_silent(
        self, httpx_mock: HTTPXMock, slept: list[float]
    ) -> None:
        httpx_mock.add_response(
            url=_SALON_WIRE,
            status_code=429,
            headers={"Retry-After": "12"},
            text="rate limited",
        )
        httpx_mock.add_response(url=_SALON_WIRE, json=_empty_page())

        with _client(wait_budget=ThrottleWaitBudget(240)) as c:
            c.fetch_salon_services(tenant_id=_TID)

        assert slept == [12.0]


class TestOtherFourXxStillFailImmediately:
    """The fence around the fix.

    Widening the retry branch from ``== 429`` to ``4xx`` would make every
    misshapen request, every retired endpoint and every operator typo cost a
    sleep before failing — quietly, since the end state is the same
    exception. Without this test that regression is invisible.
    """

    @pytest.mark.parametrize("status", [400, 404, 409, 422])
    def test_client_errors_raise_on_the_first_response_without_sleeping(
        self, httpx_mock: HTTPXMock, slept: list[float], status: int
    ) -> None:
        # Exactly ONE response queued: a retry would blow up on an unmatched
        # request, so "no retry" is asserted twice over.
        httpx_mock.add_response(
            url=_SALON_WIRE,
            status_code=status,
            json={"error": {"code": "BAD_REQUEST", "details": {"wait_seconds": 54}}},
        )

        with _client(wait_budget=ThrottleWaitBudget(240)) as c:
            with pytest.raises(CatalogClientError):
                c.fetch_salon_services(tenant_id=_TID)

        # A `wait_seconds` in the body of a NON-429 must not tempt us either.
        assert slept == []

    def test_throttled_error_is_not_a_client_error(self) -> None:
        # Sub-classing would have been the tidy-looking choice (429 IS a 4xx)
        # and would have silently re-merged the two facts this ticket exists
        # to separate: every `except CatalogClientError` upstream would catch
        # a throttle again.
        assert not issubclass(CatalogThrottledError, CatalogClientError)


class TestWaitBudget:
    def test_wait_longer_than_the_budget_is_refused_without_sleeping(
        self, httpx_mock: HTTPXMock, slept: list[float]
    ) -> None:
        httpx_mock.add_response(url=_SALON_WIRE, status_code=429, json=_THROTTLE_BODY)
        budget = ThrottleWaitBudget(30)  # less than the 54 Ayla asks for

        with _client(wait_budget=budget) as c:
            with pytest.raises(CatalogThrottledError) as exc_info:
                c.fetch_salon_services(tenant_id=_TID)

        assert slept == []
        assert exc_info.value.budget_exhausted is True
        assert exc_info.value.wait_seconds == 54.0
        # Latched, so the fan-out can read it between tenants.
        assert budget.exhausted is True
        # Nothing was spent — a partial wait returns into a window that is
        # still closed.
        assert budget.spent_seconds == 0.0

    def test_budget_is_shared_across_calls_on_the_same_client(
        self, httpx_mock: HTTPXMock, slept: list[float]
    ) -> None:
        # First fetch waits, second is refused: 54 + 54 > 100. This is the
        # multi-tenant case in miniature — the budget is per RUN, so the
        # second salon inherits what the first one spent.
        httpx_mock.add_response(url=_SALON_WIRE, status_code=429, json=_THROTTLE_BODY)
        httpx_mock.add_response(url=_SALON_WIRE, json=_empty_page())
        httpx_mock.add_response(url=_SALON_WIRE, status_code=429, json=_THROTTLE_BODY)
        budget = ThrottleWaitBudget(100)

        with _client(wait_budget=budget) as c:
            c.fetch_salon_services(tenant_id=_TID)
            with pytest.raises(CatalogThrottledError):
                c.fetch_salon_services(tenant_id=_TID)

        assert slept == [54.0]
        assert budget.exhausted is True

    def test_unusable_wait_values_are_not_slept_on(self) -> None:
        budget = ThrottleWaitBudget(10)
        # Zero/absent is "nothing to reserve", not a refusal — the caller may
        # retry at once, and the budget must not latch on it.
        assert budget.consume(0) is True
        assert budget.exhausted is False
        assert budget.spent_seconds == 0.0

    def test_exhausted_when_everything_is_spent(self) -> None:
        budget = ThrottleWaitBudget(54)
        assert budget.consume(54) is True
        assert budget.remaining_seconds == 0.0
        assert budget.exhausted is True


class TestPageSizeOnTheWire:
    """``page_size=100`` on all three walks, asserted on the request itself.

    Two of the three walks shipped without it until DRF-1595 and therefore
    ran at Ayla's ``PAGE_SIZE = 20``: five requests per hundred rows instead
    of one, against the ``anon`` 30/min throttle (the internal viewsets set
    ``authentication_classes = []``, so our Bearer requests are anonymous as
    far as DRF's throttle is concerned). Asserted on the wire, not on the
    ``params`` dict, because "we passed it" and "upstream received it" are
    different claims and only the second one lowers the request count.
    """

    def test_salon_services_asks_for_a_hundred(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=_SALON_WIRE, json=_empty_page())
        with _client() as c:
            c.fetch_salon_services(tenant_id=_TID)
        request = httpx_mock.get_request()
        assert request is not None
        assert request.url.params["page_size"] == "100"

    def test_specialists_asks_for_a_hundred(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url=f"{_SPECIALISTS_URL}?tenant={_TID}&page_size=100", json=_empty_page()
        )
        with _client() as c:
            c.fetch_specialists(tenant_id=_TID)
        request = httpx_mock.get_request()
        assert request is not None
        assert request.url.params["page_size"] == "100"

    def test_specialist_services_asks_for_a_hundred(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=f"{_EDGES_URL}?tenant={_TID}&page_size=100", json=_empty_page())
        with _client() as c:
            c.fetch_specialist_services(tenant_id=_TID)
        request = httpx_mock.get_request()
        assert request is not None
        assert request.url.params["page_size"] == "100"

    def test_page_size_survives_the_next_link(self, httpx_mock: HTTPXMock) -> None:
        # Ayla builds `next` from the incoming request, so the width carries.
        # If it ever stops carrying, page two silently drops back to 20 and
        # the walk costs five times what this test says it costs.
        next_url = f"{_SALON_URL}?tenant={_TID}&page_size=100&page=2"
        httpx_mock.add_response(
            url=_SALON_WIRE,
            json={"count": 0, "next": next_url, "previous": None, "results": []},
        )
        httpx_mock.add_response(url=next_url, json=_empty_page())

        with _client() as c:
            c.fetch_salon_services(tenant_id=_TID)

        second = httpx_mock.get_requests()[1]
        assert second.url.params["page_size"] == "100"
