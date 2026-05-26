"""Tests for the YooKassa-retired ``410 Gone`` view — #732.

Pins the contract per ADR-0009 / event-contract.md / issue body
PLUS Round-1 adversarial findings on PR #768
(agent ``ab115055e8ffcec2b``):

* M1 — strict-mode opt-out (otherwise 400 TENANT_REQUIRED beats the
  410 view to the response).
* P1 — rate-limit + audit sampling (DoS amplifier hardening).
* P2 — proxy-aware IP via ``apps.eventbus.ingest_ip.get_remote_ip``
  (trust-depth gated XFF; not raw left-most).
* P3 — IP validation + UA control-char stripping (no PII / log-
  injection via attacker-controlled headers).
* N1 + N6 (friendly) — REMOTE_ADDR fallback test; UA in WARNING log.
"""

from __future__ import annotations

import json
import logging

import pytest
from django.core.cache import cache

from apps.audit.models import AuditLog
from apps.eventbus.ingest_rate_audit_sampler import _locmem_seen
from apps.integrations.yookassa_retired.views import (
    AUDIT_ACTION,
    AUDIT_RATE_LIMITED_ACTION,
)


pytestmark = pytest.mark.django_db


# Canonical YooKassa-shaped retry payload — small JSON that mimics the
# wire shape without ever being parsed by our view. Card-tail digits
# are deliberate to anchor the «we don't persist body» PII assertion.
_SAMPLE_BODY = {
    "event": "payment.succeeded",
    "object": {
        "id": "2f1f9b8a-0000-5000-a000-000000000000",
        "amount": {"value": "1500.00", "currency": "RUB"},
        "payment_method": {
            "type": "bank_card",
            "card": {"last4": "4242"},
        },
    },
}


URL = "/api/v1/yookassa/webhook/"


@pytest.fixture(autouse=True)
def _reset_rate_limit_and_sampler_state():
    """Clear Django cache + locmem sampler state between tests.

    Both ``django_ratelimit`` and the rate-audit sampler key off
    Django's cache. Without a reset between tests, the first test's
    POSTs would consume the per-IP rate-limit and sampler quotas for
    every subsequent test in the same process.
    """
    cache.clear()
    _locmem_seen.clear()
    yield
    cache.clear()
    _locmem_seen.clear()


class TestStatusCode:
    def test_post_returns_410(self, client) -> None:
        resp = client.post(URL, data=_SAMPLE_BODY, content_type="application/json")
        assert resp.status_code == 410

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_non_post_returns_405(self, client, method) -> None:
        """``@require_POST`` rejects every non-POST verb — probes /
        scanners get a distinct monitoring signal from real YooKassa
        retries."""
        resp = getattr(client, method)(URL)
        assert resp.status_code == 405

    def test_body_signals_retirement(self, client) -> None:
        resp = client.post(URL, data=_SAMPLE_BODY, content_type="application/json")
        payload = resp.json()
        assert payload["status"] == "gone"
        assert payload["reason"] == "yookassa_webhook_retired"
        assert "info" in payload


class TestAuditRowSampling:
    """Round-1 adversarial P1 — audit-table DoS amplifier defense.

    A scanner posting unbounded times to this unauthenticated path
    would otherwise create one ``AuditLog`` row per request. Sampling
    via :func:`apps.eventbus.ingest_rate_audit_sampler.
    should_audit_yookassa_retired` caps writes at 1 per IP per 60s.
    """

    def test_first_post_writes_audit_row(self, client) -> None:
        client.post(URL, data=_SAMPLE_BODY, content_type="application/json")
        assert AuditLog.all_tenants.filter(action=AUDIT_ACTION).count() == 1

    def test_multiple_posts_same_ip_within_window_write_one_audit_row(self, client) -> None:
        for _ in range(5):
            client.post(URL, data=_SAMPLE_BODY, content_type="application/json")
        assert AuditLog.all_tenants.filter(action=AUDIT_ACTION).count() == 1

    def test_posts_from_distinct_ips_write_separate_audit_rows(self, client, settings) -> None:
        """Sampler keyed by (slug, remote_ip) — each new IP starts a
        fresh window. Forensic capture remains accurate across
        distinct sources. XFF supplies 2 hops so depth=1 can drop the
        trailing trusted proxy and resolve the client IP."""
        settings.EVENT_INGEST_TRUSTED_PROXY_DEPTH = 1
        for ip in ["203.0.113.1", "203.0.113.2", "203.0.113.3"]:
            client.post(
                URL,
                data=_SAMPLE_BODY,
                content_type="application/json",
                HTTP_X_FORWARDED_FOR=f"{ip}, 10.0.0.1",
            )
        assert AuditLog.all_tenants.filter(action=AUDIT_ACTION).count() == 3


class TestPIIDiscipline:
    """Round-1 adversarial P3 + friendly N3 — PII §7 enforcement on
    every attacker-controlled surface."""

    def test_audit_payload_carries_byte_count_not_body(self, client) -> None:
        client.post(URL, data=_SAMPLE_BODY, content_type="application/json")
        row = AuditLog.all_tenants.get(action=AUDIT_ACTION)
        payload = row.payload
        assert "body_bytes" in payload
        assert payload["body_bytes"] > 0
        flattened = json.dumps(payload)
        # Body content never persisted — including card-tail digits.
        assert "4242" not in flattened
        assert "payment.succeeded" not in flattened
        assert "RUB" not in flattened

    def test_invalid_xff_does_not_inject_pii_into_audit_remote_ip(self, client, settings) -> None:
        """Adversarial P3 — attacker stuffs ``X-Forwarded-For:
        4242-4242-4242-4242, <trusted-proxy>``. After depth-gated XFF
        extraction returns the leading attacker-controlled value,
        ``_sanitize_ip`` MUST reject it as not-parseable so the audit
        row's ``remote_ip`` slot doesn't carry the card-tail digits."""
        settings.EVENT_INGEST_TRUSTED_PROXY_DEPTH = 1
        client.post(
            URL,
            data=_SAMPLE_BODY,
            content_type="application/json",
            HTTP_X_FORWARDED_FOR="4242-4242-4242-4242, 10.0.0.1",
        )
        row = AuditLog.all_tenants.get(action=AUDIT_ACTION)
        # Not parseable as IP → sanitized to empty string.
        assert row.payload["remote_ip"] == ""
        assert "4242" not in json.dumps(row.payload)

    def test_ua_control_chars_stripped(self, client) -> None:
        """Log-injection vector: a UA with ``\\r\\n`` could split the
        WARNING log into two records. Strip ASCII control chars
        before persisting + logging."""
        leaky_ua = "Agent\r\nINJECTED-LINE\x00null"
        client.post(
            URL,
            data=_SAMPLE_BODY,
            content_type="application/json",
            HTTP_USER_AGENT=leaky_ua,
        )
        row = AuditLog.all_tenants.get(action=AUDIT_ACTION)
        ua = row.payload["user_agent_truncated"]
        assert "\r" not in ua
        assert "\n" not in ua
        assert "\x00" not in ua

    def test_ua_truncated_to_120(self, client) -> None:
        huge_ua = "X" * 500
        client.post(
            URL,
            data=_SAMPLE_BODY,
            content_type="application/json",
            HTTP_USER_AGENT=huge_ua,
        )
        row = AuditLog.all_tenants.get(action=AUDIT_ACTION)
        assert len(row.payload["user_agent_truncated"]) == 120


class TestRemoteIpResolution:
    """Round-1 adversarial P2 — proxy-aware IP via
    :func:`apps.eventbus.ingest_ip.get_remote_ip`, not raw left-most
    XFF (spoofable in one HTTP header)."""

    def test_remote_ip_from_xff_with_trusted_depth(self, client, settings) -> None:
        settings.EVENT_INGEST_TRUSTED_PROXY_DEPTH = 1
        client.post(
            URL,
            data=_SAMPLE_BODY,
            content_type="application/json",
            HTTP_X_FORWARDED_FOR="203.0.113.42, 10.0.0.1",
        )
        row = AuditLog.all_tenants.get(action=AUDIT_ACTION)
        # depth=1 drops 1 trailing hop (10.0.0.1) → 203.0.113.42 is
        # the rightmost remaining = client IP per RFC 7239 conventions
        # when using trust-depth gating.
        assert row.payload["remote_ip"] == "203.0.113.42"

    def test_remote_ip_falls_back_to_remote_addr_when_xff_untrusted(self, client) -> None:
        """Friendly N1 — default ``EVENT_INGEST_TRUSTED_PROXY_DEPTH=0``
        means XFF is NOT trusted; fall back to ``REMOTE_ADDR`` (set
        by Django test client)."""
        client.post(
            URL,
            data=_SAMPLE_BODY,
            content_type="application/json",
            REMOTE_ADDR="198.51.100.5",
        )
        row = AuditLog.all_tenants.get(action=AUDIT_ACTION)
        assert row.payload["remote_ip"] == "198.51.100.5"


class TestRateLimit:
    """Round-1 adversarial P1 — per-IP rate-limit ceiling mirrors
    sibling ``InternalEventsIngestView`` so a scanner can't compete
    with the audit-table writes from other code paths."""

    def test_over_limit_returns_429(self, client, settings) -> None:
        settings.EVENT_INGEST_RATE_LIMIT = "2/m"
        for _ in range(2):
            resp = client.post(URL, data=_SAMPLE_BODY, content_type="application/json")
            assert resp.status_code == 410
        resp = client.post(URL, data=_SAMPLE_BODY, content_type="application/json")
        assert resp.status_code == 429
        assert resp.json()["status"] == "rate_limited"
        assert resp.json()["reason"] == "rate_limit_exceeded"

    def test_over_limit_writes_distinct_audit_action_slug(self, client, settings) -> None:
        """429 path uses a distinct action slug
        (``...rate_limited``) so the 429 + 410 streams stay separable
        in dashboards."""
        settings.EVENT_INGEST_RATE_LIMIT = "1/m"
        client.post(URL, data=_SAMPLE_BODY, content_type="application/json")  # → 410
        client.post(URL, data=_SAMPLE_BODY, content_type="application/json")  # → 429
        # Sampler keyed by (slug, ip) so 410 + 429 are separate
        # buckets in the same IP — each gets its own row.
        assert AuditLog.all_tenants.filter(action=AUDIT_ACTION).count() == 1
        assert AuditLog.all_tenants.filter(action=AUDIT_RATE_LIMITED_ACTION).count() == 1


class TestStrictModeOptOut:
    """Round-1 adversarial M1 — YooKassa has no ``X-Tenant`` header.
    The strict-mode flip (2026-05-28 per memory
    ``project_strict_tenant_refuse_soak``) must NOT block this
    endpoint. ``/api/v1/yookassa/`` is in
    ``STRICT_OPT_OUT_PREFIXES``."""

    def test_strict_mode_does_not_block_yookassa_path(self, client, settings) -> None:
        settings.STRICT_TENANT_SCOPE = "strict"
        resp = client.post(URL, data=_SAMPLE_BODY, content_type="application/json")
        # 410, NOT 400 — opt-out works.
        assert resp.status_code == 410


class TestForensicLog:
    def test_warning_log_emitted(self, client, caplog) -> None:
        caplog.set_level(
            logging.WARNING,
            logger="apps.integrations.yookassa_retired.views",
        )
        client.post(URL, data=_SAMPLE_BODY, content_type="application/json")
        warnings = [
            rec
            for rec in caplog.records
            if rec.levelname == "WARNING"
            and "yookassa.webhook_received_after_retirement" in rec.getMessage()
        ]
        assert warnings, "expected WARNING log on each hit"

    def test_log_includes_user_agent(self, client, caplog) -> None:
        """Friendly N6 — UA in log helps operator triage «is this
        YooKassa or a scanner?» without leaking PII (UA is not PII)."""
        caplog.set_level(
            logging.WARNING,
            logger="apps.integrations.yookassa_retired.views",
        )
        client.post(
            URL,
            data=_SAMPLE_BODY,
            content_type="application/json",
            HTTP_USER_AGENT="YooKassa-Webhook/1.0",
        )
        ua_logs = [rec for rec in caplog.records if "YooKassa-Webhook/1.0" in rec.getMessage()]
        assert ua_logs

    def test_log_does_not_leak_body(self, client, caplog) -> None:
        caplog.set_level(
            logging.WARNING,
            logger="apps.integrations.yookassa_retired.views",
        )
        client.post(URL, data=_SAMPLE_BODY, content_type="application/json")
        for rec in caplog.records:
            msg = rec.getMessage()
            assert "4242" not in msg
            assert "RUB" not in msg


class TestUrlMounting:
    def test_url_reachable_at_expected_path(self, client) -> None:
        """Pin the exact path string. YooKassa Personal Cabinet pre-
        flip had ``/api/v1/yookassa/webhook/`` configured; the 410
        endpoint MUST match that path exactly — otherwise YooKassa
        retries hit the project's default 404 (silent loss)."""
        resp = client.post(URL, data="{}", content_type="application/json")
        assert resp.status_code == 410
