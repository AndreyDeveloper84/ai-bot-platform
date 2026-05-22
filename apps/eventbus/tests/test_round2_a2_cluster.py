"""Round-2 adversarial-pass regression tests — A2 cluster (AS1 + AS2 + AS3).

Per memory ``feedback_h3_waiver_pattern`` N=10: «when implementing a
security control, write the negative test first — assert the bypass
attempt fails. If the test can't even formulate the bypass, the
defense wasn't designed adversarially.»

Each test in this module IS the bypass attempt. If any one of them
passes the bypass, the defense is broken.
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


def _post(
    client: Client,
    body: bytes,
    *,
    real_ip: str | None = None,
    forwarded_for: str | None = None,
):
    ts_ms = str(int(time.time() * 1000))
    sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    headers = {
        "HTTP_X_AYLA_EVENT_SIGNATURE": sig,
        "HTTP_X_AYLA_EVENT_TIMESTAMP": ts_ms,
    }
    if real_ip is not None:
        headers["HTTP_X_REAL_IP"] = real_ip
    if forwarded_for is not None:
        headers["HTTP_X_FORWARDED_FOR"] = forwarded_for
    return client.post(
        INGEST_URL,
        data=body,
        content_type="application/json",
        **headers,  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
def _settings_and_cache(settings):
    settings.EVENT_INGEST_HMAC_SECRET = SECRET
    settings.RATELIMIT_ENABLE = True
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def client() -> Client:
    return Client()


# ─── AS1 bypass — spoofed XFF MUST NOT get isolated bucket ──────────────


class TestAS1ProxyAwareKey:
    @override_settings(EVENT_INGEST_RATE_LIMIT="2/m", EVENT_INGEST_TRUSTED_PROXY_DEPTH=0)
    def test_xff_spoof_does_not_isolate_bucket_when_depth_zero(self, client: Client) -> None:
        """With proxy depth=0 (no trust), an attacker spoofing XFF
        MUST NOT get a per-XFF bucket — they share REMOTE_ADDR with
        every other client behind the (untrusted) layer.

        Bypass attempt: vary X-Forwarded-For per request to dodge the
        rate limit. Defense: depth=0 ignores XFF entirely; only
        REMOTE_ADDR drives the bucket. All 3 requests share one
        bucket; the 3rd is 429.
        """
        body = json.dumps(VALID_BODY).encode()

        r1 = _post(client, body, forwarded_for="1.1.1.1")
        r2 = _post(client, body, forwarded_for="2.2.2.2")
        r3 = _post(client, body, forwarded_for="3.3.3.3")

        assert r1.status_code != 429
        assert r2.status_code != 429
        # If the defense fails, this third request gets its own bucket
        # and returns 200/422/etc. With defense intact, it's 429.
        assert r3.status_code == 429, (
            "AS1 bypass succeeded: spoofed XFF created an isolated rate-limit bucket with depth=0"
        )

    @override_settings(EVENT_INGEST_RATE_LIMIT="2/m", EVENT_INGEST_TRUSTED_PROXY_DEPTH=1)
    def test_xff_canonicalised_at_depth_isolates_real_clients(self, client: Client) -> None:
        """With depth=1 (one trusted proxy), DIFFERENT real-client IPs
        get separate buckets. Same real-client over multiple proxy
        hops shares one bucket.

        Tests the positive complement to AS1: when proxy trust IS
        configured, the per-client isolation that the rate limit
        intends actually works.
        """
        body = json.dumps(VALID_BODY).encode()

        # Three different real clients through the same trusted proxy.
        # Each gets their own bucket; none should hit 429 with limit
        # 2/m.
        r1 = _post(client, body, forwarded_for="10.0.0.1, 1.1.1.1")
        r2 = _post(client, body, forwarded_for="10.0.0.2, 1.1.1.1")
        r3 = _post(client, body, forwarded_for="10.0.0.3, 1.1.1.1")

        assert all(r.status_code != 429 for r in (r1, r2, r3))


# ─── AS3 bypass — 429 audit MUST NOT be unbounded ───────────────────────


class TestAS3AuditSampling:
    @override_settings(EVENT_INGEST_RATE_LIMIT="1/m")
    def test_burst_of_429s_writes_at_most_one_audit_row_per_ip(self, client: Client) -> None:
        """A scanner without HMAC hitting at line speed MUST NOT
        generate unbounded AuditLog rows.

        Bypass attempt: hammer the endpoint to amplify AuditLog
        writes. Defense: per-IP cache sampler emits at most 1 audit
        per IP per 60s window.
        """
        from apps.audit.models import AuditLog

        body = json.dumps(VALID_BODY).encode()

        # Burn 1 successful request to consume the bucket.
        _post(client, body)

        # 10 over-limit requests from the same IP.
        for _ in range(10):
            response = _post(client, body)
            assert response.status_code == 429

        rate_rows = AuditLog.all_tenants.filter(
            action="eventbus.ingest.rate_limited",
        )
        # Defense intact: at most 1 audit row from the 10 over-limit
        # requests (plus any earlier from the burn-in — total ≤ 2).
        # Defense broken: 10+ audit rows.
        assert rate_rows.count() <= 2, (
            f"AS3 bypass succeeded: {rate_rows.count()} audit rows from "
            "10 over-limit requests; expected ≤2 (sampled to 1/min/IP)"
        )

    @override_settings(
        EVENT_INGEST_RATE_LIMIT="1/m",
        # Round-3 NEW-1 — X-Real-IP only honored when proxy trust is
        # declared. This test exercises the «trusted edge sets
        # X-Real-IP» production path, so the ack is set explicitly.
        EVENT_INGEST_EDGE_CONFIGURED_ACK=True,
    )
    def test_distinct_ips_each_get_one_audit(self, client: Client) -> None:
        """Sampling is per-IP — distinct attackers each get their
        first audit row through. Linear in attack diversity, not
        attack volume.
        """
        from apps.audit.models import AuditLog

        body = json.dumps(VALID_BODY).encode()

        # Each distinct IP fills its 1/m bucket with one request,
        # then hits 429 on the second.
        for ip in ("10.0.0.1", "10.0.0.2", "10.0.0.3"):
            _post(client, body, real_ip=ip)  # 1st through, no audit
            _post(client, body, real_ip=ip)  # 2nd over limit, audit

        rate_rows = AuditLog.all_tenants.filter(
            action="eventbus.ingest.rate_limited",
        )
        # Three distinct IPs → three audit rows (one per IP).
        assert rate_rows.count() == 3
