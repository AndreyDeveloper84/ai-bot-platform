"""Tests for the ingest endpoint rate limit (PR #507 adversarial A2).

Pins the 429 response shape + audit row. Uses the django-ratelimit
``override_settings(RATELIMIT_ENABLE=...)`` and a tight per-test rate
override so a 2-request loop suffices instead of looping the
production 100/min ceiling.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from django.core.cache import cache
from django.test import Client, override_settings


pytestmark = pytest.mark.django_db


SECRET = "ingest-test-secret"  # pragma: allowlist secret
INGEST_URL = "/api/v1/internal/events/ingest"


VALID_BODY: dict = {
    "event_id": "01J9HXKM8Z2T4V6R8Q1P3D5F7E",
    "event_name": "booking.created",
    "event_version": 1,
    "occurred_at": "2026-05-21T14:32:11.482Z",
    "tenant_id": "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c",
    "user_id": "f1a2b3c4-d5e6-4789-9abc-def012345678",
    "actor": "user",
    "correlation_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "causation_id": None,
    "data": {"appointment_id": "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"},
}


def _post(client: Client, body: bytes, *, secret: str = SECRET, ts: float | None = None):
    ts_ms = str(int((ts or time.time()) * 1000))
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        INGEST_URL,
        data=body,
        content_type="application/json",
        HTTP_X_AYLA_EVENT_SIGNATURE=sig,
        HTTP_X_AYLA_EVENT_TIMESTAMP=ts_ms,
    )


@pytest.fixture(autouse=True)
def _settings_and_cache(settings):
    settings.EVENT_INGEST_HMAC_SECRET = SECRET
    settings.RATELIMIT_ENABLE = True
    # django-ratelimit stores counters in Django's cache. Wipe between
    # tests so a prior test's bucket doesn't leak.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def client() -> Client:
    return Client()


class TestRateLimit:
    @override_settings(EVENT_INGEST_RATE_LIMIT="2/m")
    def test_third_request_within_minute_returns_429(self, client: Client) -> None:
        """Per-IP bucket exhaustion → 429 + JSON body + audit row.

        The first two requests pass the rate limit (they may still
        fail downstream on missing handler etc., but NOT on rate);
        the third hits the bucket and returns 429 before HMAC verify
        — saving CPU/DB on the captured-tuple replay scenario.
        """
        body = json.dumps(VALID_BODY).encode()

        r1 = _post(client, body)
        r2 = _post(client, body)
        r3 = _post(client, body)

        # First two: not rate-limited (they 422 on unknown handler
        # because no consumer is registered, but THAT'S NOT 429).
        assert r1.status_code != 429
        assert r2.status_code != 429
        # Third: rate-limited.
        assert r3.status_code == 429
        body_json = json.loads(r3.content)
        assert body_json["status"] == "rate_limited"
        assert body_json["reason"] == "rate_limit_exceeded"

    @override_settings(EVENT_INGEST_RATE_LIMIT="2/m")
    def test_rate_limited_request_writes_audit_row(self, client: Client) -> None:
        """Operator triage on a flood needs the audit trail."""
        from apps.audit.models import AuditLog

        body = json.dumps(VALID_BODY).encode()
        _post(client, body)
        _post(client, body)
        _post(client, body)  # exhausts

        rate_rows = AuditLog.all_tenants.filter(
            action="eventbus.ingest.rate_limited",
        )
        assert rate_rows.count() >= 1
        payload = rate_rows.first().payload
        assert "remote_ip" in payload
        assert "body_bytes" in payload

    def test_default_rate_allows_steady_traffic(self, client: Client) -> None:
        """Production default 100/m — 5 calls in a row pass.

        Pins that the default rate isn't accidentally so low it would
        false-positive on normal Ayla outbox dispatch (1-event-per-row
        roughly equals 1 request per state change).
        """
        body = json.dumps(VALID_BODY).encode()
        responses = [_post(client, body) for _ in range(5)]
        assert all(r.status_code != 429 for r in responses)


class TestRateLimitDisabledInTests:
    def test_disable_setting_bypasses_limit(self, client: Client, settings) -> None:
        """RATELIMIT_ENABLE=False (deploys with WAF-side limiting) bypasses
        the code-side gate entirely."""
        settings.RATELIMIT_ENABLE = False
        settings.EVENT_INGEST_RATE_LIMIT = "1/m"

        body = json.dumps(VALID_BODY).encode()
        # Even though rate is 1/min, the disable flag bypasses.
        r1 = _post(client, body)
        r2 = _post(client, body)
        r3 = _post(client, body)
        assert all(r.status_code != 429 for r in (r1, r2, r3))
