"""Tests for the YooKassa-retired ``410 Gone`` view — #732.

Pins the contract per ADR-0009 / event-contract.md / issue body:

* ``POST /api/v1/yookassa/webhook/`` returns HTTP 410 with a JSON
  body that tells the caller the endpoint is gone.
* Audit row written for forensic «who's still hitting us?» trail.
* Audit row contains NO body content — only byte count, source IP,
  truncated UA. YooKassa webhook bodies can carry card-tail digits
  in failure messages (PII rule §7).
* GET / PUT / etc. → ``405 Method Not Allowed`` (different signal
  from real YooKassa retries in monitoring).
"""

from __future__ import annotations

import json

import pytest

from apps.audit.models import AuditLog
from apps.integrations.yookassa_retired.views import AUDIT_ACTION


pytestmark = pytest.mark.django_db


# Canonical YooKassa-shaped retry payload — small JSON that mimics
# the wire shape without ever being parsed by our view.
_SAMPLE_BODY = {
    "event": "payment.succeeded",
    "object": {
        "id": "2f1f9b8a-0000-5000-a000-000000000000",
        "amount": {"value": "1500.00", "currency": "RUB"},
        # Intentional card-tail PII to prove we don't persist it.
        "payment_method": {
            "type": "bank_card",
            "card": {"last4": "4242"},
        },
    },
}


URL = "/api/v1/yookassa/webhook/"


class TestStatusCode:
    def test_post_returns_410(self, client) -> None:
        resp = client.post(URL, data=_SAMPLE_BODY, content_type="application/json")
        assert resp.status_code == 410

    def test_get_returns_405_method_not_allowed(self, client) -> None:
        resp = client.get(URL)
        assert resp.status_code == 405

    def test_put_returns_405(self, client) -> None:
        resp = client.put(URL, data="{}", content_type="application/json")
        assert resp.status_code == 405

    def test_body_signals_retirement(self, client) -> None:
        resp = client.post(URL, data=_SAMPLE_BODY, content_type="application/json")
        payload = resp.json()
        assert payload["status"] == "gone"
        assert payload["reason"] == "yookassa_webhook_retired"
        # Help message present (operators look here when triaging).
        assert "info" in payload


class TestAuditRow:
    def test_audit_row_written_on_each_post(self, client) -> None:
        client.post(URL, data=_SAMPLE_BODY, content_type="application/json")
        rows = AuditLog.all_tenants.filter(action=AUDIT_ACTION)
        assert rows.count() == 1

    def test_audit_row_per_call_no_dedupe(self, client) -> None:
        """Forensic capture wants ONE row per inbound hit — no dedupe.

        If two cabinets both leak retries, we want to count them
        separately. The 30-day soak window is too narrow for audit-log
        retention to be a concern.
        """
        for _ in range(3):
            client.post(URL, data=_SAMPLE_BODY, content_type="application/json")
        assert AuditLog.all_tenants.filter(action=AUDIT_ACTION).count() == 3

    def test_audit_payload_carries_byte_count_not_body(self, client) -> None:
        """PII §7 — body content (which can carry card-tail digits) is
        NEVER persisted. Only the byte count is captured for «is this a
        big retry / small probe?» triage."""
        client.post(URL, data=_SAMPLE_BODY, content_type="application/json")
        row = AuditLog.all_tenants.get(action=AUDIT_ACTION)
        payload = row.payload
        # Required fields present.
        assert "body_bytes" in payload
        assert payload["body_bytes"] > 0
        # The leaky card-tail MUST NOT be anywhere in the audit row.
        flattened = json.dumps(payload)
        assert "4242" not in flattened
        assert "payment.succeeded" not in flattened
        assert "RUB" not in flattened

    def test_audit_remote_ip_from_x_forwarded_for(self, client) -> None:
        """Proxy-aware: when behind a reverse proxy, REMOTE_ADDR is the
        proxy's IP. We want the originating client IP — the left-most
        non-proxy entry in X-Forwarded-For per RFC 7239."""
        client.post(
            URL,
            data=_SAMPLE_BODY,
            content_type="application/json",
            HTTP_X_FORWARDED_FOR="203.0.113.42, 10.0.0.1",
        )
        row = AuditLog.all_tenants.get(action=AUDIT_ACTION)
        assert row.payload["remote_ip"] == "203.0.113.42"

    def test_audit_user_agent_truncated(self, client) -> None:
        """UA strings can be arbitrarily long (forks, version stamps).
        Cap the persisted value so a single row doesn't bloat the audit
        table."""
        huge_ua = "X" * 500
        client.post(
            URL,
            data=_SAMPLE_BODY,
            content_type="application/json",
            HTTP_USER_AGENT=huge_ua,
        )
        row = AuditLog.all_tenants.get(action=AUDIT_ACTION)
        assert len(row.payload["user_agent_truncated"]) <= 120

    def test_audit_handles_empty_body(self, client) -> None:
        """Some retry implementations send an empty body on a probe.
        ``body_bytes == 0`` is a valid forensic signal, not an error."""
        resp = client.post(URL, data="", content_type="application/json")
        assert resp.status_code == 410
        row = AuditLog.all_tenants.get(action=AUDIT_ACTION)
        assert row.payload["body_bytes"] == 0


class TestForensicLog:
    def test_warning_log_emitted(self, client, caplog) -> None:
        """WARNING log fires so on-call paging rules trigger if any
        Cabinet still points here past the deploy window."""
        import logging

        caplog.set_level(logging.WARNING, logger="apps.integrations.yookassa_retired.views")
        client.post(URL, data=_SAMPLE_BODY, content_type="application/json")
        warnings = [
            rec
            for rec in caplog.records
            if rec.levelname == "WARNING"
            and "yookassa.webhook_received_after_retirement" in rec.getMessage()
        ]
        assert warnings, "expected WARNING log on each hit"

    def test_log_does_not_leak_body(self, client, caplog) -> None:
        """Log message MUST NOT include body content (mirrors audit PII
        discipline)."""
        import logging

        caplog.set_level(logging.WARNING, logger="apps.integrations.yookassa_retired.views")
        client.post(URL, data=_SAMPLE_BODY, content_type="application/json")
        for rec in caplog.records:
            msg = rec.getMessage()
            assert "4242" not in msg
            assert "RUB" not in msg


class TestUrlMounting:
    def test_url_is_reachable_at_expected_path(self, client) -> None:
        """Pin the exact path string. YooKassa Personal Cabinet pre-
        flip had ``/api/v1/yookassa/webhook/`` configured; the 410
        endpoint MUST match that path exactly — otherwise YooKassa
        retries hit the project's default 404 (silent loss)."""
        resp = client.post(URL, data="{}", content_type="application/json")
        # 410 not 404 — proof the route is mounted at the expected
        # location.
        assert resp.status_code == 410
