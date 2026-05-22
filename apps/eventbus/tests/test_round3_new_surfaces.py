"""Round-3 adversarial-pass regression tests — 5 NEW surfaces from round-2 fixes.

Each test IS the bypass attempt per memory ``feedback_h3_waiver_pattern``
N=13. Cluster mapping:

- NEW-1 — X-Real-IP blindly trusted (proxy-trust gap).
- NEW-2 — sampler fail-OPEN on cache outage (AS3 amplification regression).
- NEW-3 — SATURATED + rate-limited share cache key prefix.
- NEW-4 — enum regex accepts arbitrary lowercase free-text PII.
- NEW-5 — tenant fail-open flag invisible to ops (no startup warning, no audit row).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging
import time

import pytest
from django.core.cache import cache
from django.test import Client, override_settings


pytestmark = pytest.mark.django_db


SECRET = "ingest-test-secret"  # pragma: allowlist secret
INGEST_URL = "/api/v1/internal/events/ingest"
VALID_BODY: dict = {
    "event_id": "01J9HXKM8Z2T4V6R8Q1P3D5F7E",  # pragma: allowlist secret
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


def _post(client, body, *, real_ip=None, forwarded_for=None):
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
        **headers,
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


# ─── NEW-1 — X-Real-IP MUST be gated by proxy trust ─────────────────────


class TestNEW1XRealIPGate:
    @override_settings(EVENT_INGEST_RATE_LIMIT="2/m", EVENT_INGEST_TRUSTED_PROXY_DEPTH=0)
    def test_spoofed_x_real_ip_does_not_isolate_bucket_at_depth_zero(self, client) -> None:
        """Bypass: depth=0 means "no trusted proxy" yet X-Real-IP was
        accepted blindly. Attacker rotates the header per request →
        isolated buckets → rate limit defeated on the OTHER header
        the AS1 fix didn't cover.

        Defense: X-Real-IP only honored when depth>0 OR
        EVENT_INGEST_EDGE_CONFIGURED_ACK=True.
        """
        body = json.dumps(VALID_BODY).encode()

        r1 = _post(client, body, real_ip="1.1.1.1")
        r2 = _post(client, body, real_ip="2.2.2.2")
        r3 = _post(client, body, real_ip="3.3.3.3")

        assert r1.status_code != 429
        assert r2.status_code != 429
        # With NEW-1 defense intact, r3 hits the same REMOTE_ADDR
        # bucket as the first two.
        assert r3.status_code == 429, (
            "NEW-1 bypass succeeded: spoofed X-Real-IP at depth=0 "
            f"isolated bucket. Got {r3.status_code} instead of 429."
        )


# ─── NEW-2 — Sampler MUST NOT fail-OPEN on cache outage ─────────────────


class TestNEW2SamplerFailClosed:
    def test_cache_outage_does_not_revive_as3_amplification(self) -> None:
        """Bypass: AS3 sampler fail-OPENS when Django cache raises
        (Redis outage). On outage every over-limit hit writes audit →
        unauthenticated AuditLog DoS amplification returns.

        Defense: fail-CLOSED with locmem fallback. Per-process dict
        bounds amplification at 1/min/process/IP (vs cross-process
        1/min/IP under healthy cache).
        """
        from unittest.mock import patch

        from apps.eventbus.ingest_rate_audit_sampler import (
            should_audit_rate_limited,
        )

        # Simulate Redis outage — cache.add raises on every call.
        with patch(
            "apps.eventbus.ingest_rate_audit_sampler.cache.add",
            side_effect=RuntimeError("redis down"),
        ):
            results = [should_audit_rate_limited("10.0.0.1") for _ in range(20)]

        # Defense intact: locmem fallback bounds emission to ~1 per
        # IP per window. Even with all-cache-failures, at most 1
        # call returns True per (window, IP).
        true_count = sum(1 for r in results if r)
        assert true_count <= 1, (
            f"NEW-2 bypass succeeded: cache outage produced {true_count} "
            "audit emissions for one IP — AS3 amplifier returned."
        )


# ─── NEW-3 — Distinct slug cache prefixes ────────────────────────────────


class TestNEW3DistinctCachePrefixPerSlug:
    def test_saturated_and_rate_limited_dont_collide_for_same_ip(self) -> None:
        """Bypass: SATURATED audit + rate-limited audit both keyed by
        ``rate_audit:{ip}`` → one suppresses the other for 60s, half
        the forensic surface goes missing for distinct attack modes.

        Defense: distinct prefix per slug. ``should_audit_rate_limited``
        and ``should_audit_saturated`` use independent buckets.
        """
        from apps.eventbus.ingest_rate_audit_sampler import (
            should_audit_rate_limited,
            should_audit_saturated,
        )

        # Both should return True for the same IP — they don't share
        # a cache key.
        assert should_audit_rate_limited("10.0.0.42") is True
        assert should_audit_saturated("10.0.0.42") is True


# ─── NEW-4 — Enum regex MUST NOT accept free-text PII ───────────────────


class TestNEW4EnumAllowlist:
    def test_lowercase_name_is_redacted(self) -> None:
        """Bypass: ``customer_name="annaivanova"`` matches the
        ``[a-z0-9_.]{1,40}`` enum regex → passes redaction → free-text
        PII in 90d DLQ.

        Defense: drop the regex; only contract §3 enum values pass.
        """
        from apps.eventbus.ingest_redaction import (
            REDACTED_SENTINEL,
            redact_data_for_dlq,
        )

        data = {"customer_name": "annaivanova"}
        out = redact_data_for_dlq(data)
        assert out["customer_name"] == REDACTED_SENTINEL, (
            "NEW-4 bypass succeeded: lowercase free-text name passed "
            "redaction via the enum regex bypass."
        )

    def test_real_contract_enums_still_pass(self) -> None:
        """Positive complement — the round-2 fix MUST NOT drop the
        legitimate enum values from contract §3.
        """
        from apps.eventbus.ingest_redaction import redact_data_for_dlq

        data = {
            "status": "confirmed",
            "source": "mobile_app",
            "cancelled_by": "user",
            "reason": "card_declined",
            "provider": "yookassa",
            "change_type": "vacation_set",
        }
        out = redact_data_for_dlq(data)
        # All these are documented contract enums; they MUST survive.
        for k, v in data.items():
            assert out[k] == v, f"NEW-4 regression: contract enum {k}={v!r} got redacted"

    def test_unknown_enum_value_is_redacted(self) -> None:
        """An enum-shaped string not in the contract allowlist
        (e.g. attacker-controlled `status="payload_xss"`) MUST be
        redacted — operator sees `[REDACTED]` instead of a forged
        enum that might confuse triage.
        """
        from apps.eventbus.ingest_redaction import (
            REDACTED_SENTINEL,
            redact_data_for_dlq,
        )

        data = {"status": "payload_xss"}
        out = redact_data_for_dlq(data)
        assert out["status"] == REDACTED_SENTINEL


# ─── NEW-5 — Tenant fail-open flag MUST be loud ─────────────────────────


class TestNEW5TenantFailOpenVisibility:
    def test_startup_warning_when_fail_open_flag_set(self, settings, caplog) -> None:
        """Bypass: ops sets flag True in prod by mistake, but no
        startup signal surfaces — exposure window invisible.

        Defense: ``warn_if_tenant_verify_fail_open`` logs at app
        ready time when flag True.
        """
        from apps.eventbus.startup_checks import (
            warn_if_tenant_verify_fail_open,
        )

        settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True
        with caplog.at_level(logging.WARNING, logger="apps.eventbus.startup_checks"):
            warn_if_tenant_verify_fail_open()

        matching = [r for r in caplog.records if "tenant_verify_fail_open" in r.getMessage()]
        assert len(matching) >= 1, (
            "NEW-5 bypass: fail-open flag set but no startup warning surfaced."
        )

    def test_no_warning_when_fail_open_flag_unset(self, settings, caplog) -> None:
        from apps.eventbus.startup_checks import (
            warn_if_tenant_verify_fail_open,
        )

        # Default — flag absent / False.
        if hasattr(settings, "EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN"):
            del settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN
        with caplog.at_level(logging.WARNING, logger="apps.eventbus.startup_checks"):
            warn_if_tenant_verify_fail_open()

        matching = [r for r in caplog.records if "tenant_verify_fail_open" in r.getMessage()]
        assert matching == []

    def test_audit_row_written_on_fail_open_fall_through(self, settings) -> None:
        """Bypass: fail-open fall-through silently logged — ops loses
        forensic trail of the exposure window. Defense: audit row
        per fall-through (sampled per IP).
        """
        from apps.audit.models import AuditLog
        from apps.eventbus.ingest_envelope import IngestEnvelope
        from apps.eventbus.ingest_tenancy import (
            assert_envelope_tenant_authorized,
        )

        settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True

        env = IngestEnvelope(
            event_id="01J9HXKM8Z2T4V6R8Q1P3D5F7E",  # pragma: allowlist secret
            event_name="booking.created",
            event_version=1,
            occurred_at=dt.datetime(2026, 5, 21, 14, 32, 11, tzinfo=dt.timezone.utc),
            tenant_id="9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c",
            user_id="f1a2b3c4-d5e6-4789-9abc-def012345678",
            actor="user",
            correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            causation_id=None,
            data={},
        )

        assert_envelope_tenant_authorized(env)

        rows = AuditLog.all_tenants.filter(
            action="eventbus.ingest.tenant_verify_fail_open",
        )
        assert rows.count() >= 1, "NEW-5 bypass: fail-open fall-through left no audit row."
