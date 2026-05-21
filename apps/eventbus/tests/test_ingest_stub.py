"""Tests for the /api/v1/internal/events/ingest/ stub (Phase 0 / #432).

Stub scope only — the endpoint exists at the canonical URL, responds
to POST with 501 Not Implemented, and has the HMAC middleware skeleton
in place. The full body + per-event dispatch waits on Beta #441
(``docs/architecture/event-contract.md``, Phase 0 Sync 4).

These three tests pin the stub's contract so the follow-up PR that
fills the handler keeps the URL, the verb policy, and the
501-pending shape stable.
"""

from __future__ import annotations

import json

from django.test import Client


def test_ingest_post_returns_501_with_pending_message() -> None:
    """POST returns 501 Not Implemented and a JSON body that names #441.

    501 (vs 200 or 400) signals to a publisher that the channel is
    reserved but the contract is unfinalised — publishers should NOT
    retry on 501; they should hold the event until the contract lands.
    The body advertises the blocker so operators reading logs / a
    publisher's error stream get a textual hint instead of a bare 501.
    """
    client = Client()
    response = client.post(
        "/api/v1/internal/events/ingest/",
        data="{}",
        content_type="application/json",
    )
    assert response.status_code == 501
    body = json.loads(response.content)
    assert body["status"] == "not_implemented"
    assert "441" in body["reason"]


def test_ingest_post_sets_long_retry_after() -> None:
    """501 response carries Retry-After to deter aggressive retry loops.

    Many HTTP retry middlewares (urllib3 Retry, httpx transport
    retries, cloud ingress) treat 5xx as transiently retryable by
    default. A long Retry-After (24h) tells a naive publisher to
    hold the event until #441 lands rather than DOS this endpoint
    while the contract is unfinalised.
    """
    client = Client()
    response = client.post(
        "/api/v1/internal/events/ingest/",
        data="{}",
        content_type="application/json",
    )
    assert response.status_code == 501
    assert response["Retry-After"] == "86400"


def test_ingest_get_returns_405() -> None:
    """The endpoint is POST-only — GET (or any other verb) returns 405.

    Pins the View.http_method_names allow-list. A publisher
    misconfigured for GET should fail loudly, not silently 200 OK.
    """
    client = Client()
    response = client.get("/api/v1/internal/events/ingest/")
    assert response.status_code == 405


def test_hmac_middleware_skeleton_is_callable() -> None:
    """The HMAC middleware skeleton is importable + callable.

    Stub: verify the middleware class exists and that ``__call__``
    passes the request through unchanged (no enforcement yet — that
    lands with #441 when the canonical header name + secret rotation
    rules are spec'd). Wiring the class shape early lets the
    follow-up PR fill in body verification without changing the view
    or settings.MIDDLEWARE call site.
    """
    from apps.eventbus.middleware import HMACSignatureMiddleware

    captured: dict[str, object] = {}

    def _downstream(request: object) -> str:
        captured["request"] = request
        return "downstream-ok"

    middleware = HMACSignatureMiddleware(_downstream)
    sentinel = object()
    result = middleware(sentinel)
    assert result == "downstream-ok"
    assert captured["request"] is sentinel
