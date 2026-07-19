"""Tests for :mod:`apps.eventbus.ingest_security` (Phase 0 / #432).

Pins the HMAC + timestamp anti-replay contract from
`event-contract.md` §6.2. Each REASON_* slug is exercised so the
view-layer 401 surface (§8.3) has a stable taxonomy.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from apps.eventbus.ingest_security import (
    REASON_HMAC_MISMATCH,
    REASON_INVALID_TIMESTAMP,
    REASON_MALFORMED_SIGNATURE,
    REASON_MISSING_SIGNATURE,
    REASON_MISSING_TIMESTAMP,
    REASON_NO_SECRET,
    REASON_OK,
    REASON_TIMESTAMP_STALE,
    TIMESTAMP_WINDOW_S,
    verify_signature,
)


SECRET = "shared-test-secret"  # pragma: allowlist secret


def _sig(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _ts(now: float | None = None) -> str:
    return str(int((now or time.time()) * 1000))


class TestHappyPath:
    def test_valid_signature_and_timestamp_returns_ok(self) -> None:
        body = b'{"event_name": "booking.created"}'
        now = 1_700_000_000.0
        result = verify_signature(
            body=body,
            signature_header=_sig(body),
            timestamp_header=_ts(now),
            secret=SECRET,
            now=now,
        )
        assert result.ok is True
        assert result.reason == REASON_OK

    def test_accepts_timestamp_within_window(self) -> None:
        body = b'{"x": 1}'
        now = 1_700_000_000.0
        # Stale by 290s — still inside the 300s window.
        old_ts = _ts(now - 290.0)
        result = verify_signature(
            body=body,
            signature_header=_sig(body),
            timestamp_header=old_ts,
            secret=SECRET,
            now=now,
        )
        assert result.ok is True


class TestFailureModes:
    def test_empty_secret_returns_no_secret(self) -> None:
        body = b"{}"
        result = verify_signature(
            body=body,
            signature_header=_sig(body),
            timestamp_header=_ts(),
            secret="",
        )
        assert result.ok is False
        assert result.reason == REASON_NO_SECRET

    def test_missing_signature_header(self) -> None:
        result = verify_signature(
            body=b"{}",
            signature_header="",
            timestamp_header=_ts(),
            secret=SECRET,
        )
        assert result.reason == REASON_MISSING_SIGNATURE

    def test_missing_timestamp_header(self) -> None:
        body = b"{}"
        result = verify_signature(
            body=body,
            signature_header=_sig(body),
            timestamp_header="",
            secret=SECRET,
        )
        assert result.reason == REASON_MISSING_TIMESTAMP

    def test_malformed_signature_no_sha256_prefix(self) -> None:
        result = verify_signature(
            body=b"{}",
            signature_header="md5=abc",
            timestamp_header=_ts(),
            secret=SECRET,
        )
        assert result.reason == REASON_MALFORMED_SIGNATURE

    def test_malformed_signature_empty_hex(self) -> None:
        result = verify_signature(
            body=b"{}",
            signature_header="sha256=",
            timestamp_header=_ts(),
            secret=SECRET,
        )
        assert result.reason == REASON_MALFORMED_SIGNATURE

    def test_invalid_timestamp_non_numeric(self) -> None:
        body = b"{}"
        result = verify_signature(
            body=body,
            signature_header=_sig(body),
            timestamp_header="not-a-number",
            secret=SECRET,
        )
        assert result.reason == REASON_INVALID_TIMESTAMP

    def test_timestamp_stale_beyond_window(self) -> None:
        body = b"{}"
        now = 1_700_000_000.0
        stale_ts = _ts(now - (TIMESTAMP_WINDOW_S + 1.0))
        result = verify_signature(
            body=body,
            signature_header=_sig(body),
            timestamp_header=stale_ts,
            secret=SECRET,
            now=now,
        )
        assert result.reason == REASON_TIMESTAMP_STALE

    def test_timestamp_future_beyond_window(self) -> None:
        body = b"{}"
        now = 1_700_000_000.0
        future_ts = _ts(now + (TIMESTAMP_WINDOW_S + 1.0))
        result = verify_signature(
            body=body,
            signature_header=_sig(body),
            timestamp_header=future_ts,
            secret=SECRET,
            now=now,
        )
        assert result.reason == REASON_TIMESTAMP_STALE

    def test_hmac_mismatch_wrong_secret(self) -> None:
        body = b'{"x": 1}'
        # Sign with wrong secret then verify with the real one.
        wrong_sig = _sig(body, secret="other")
        now = 1_700_000_000.0
        result = verify_signature(
            body=body,
            signature_header=wrong_sig,
            timestamp_header=_ts(now),
            secret=SECRET,
            now=now,
        )
        assert result.reason == REASON_HMAC_MISMATCH

    def test_hmac_mismatch_body_tampered(self) -> None:
        original = b'{"x": 1}'
        tampered = b'{"x": 2}'
        sig_for_original = _sig(original)
        now = 1_700_000_000.0
        result = verify_signature(
            body=tampered,
            signature_header=sig_for_original,
            timestamp_header=_ts(now),
            secret=SECRET,
            now=now,
        )
        assert result.reason == REASON_HMAC_MISMATCH


class TestConstantTimeCompare:
    def test_hmac_compare_uses_constant_time(self) -> None:
        """``hmac.compare_digest`` is used — a wrong-byte mismatch returns
        ``REASON_HMAC_MISMATCH``, not a timing-distinguishable outcome.

        This is a contract test: we assert the BEHAVIOUR (mismatch is
        a uniform failure mode), not the implementation. Timing
        analysis is left to a security audit.
        """
        body = b"{}"
        valid = _sig(body)
        # Flip the last hex char.
        last = valid[-1]
        new_last = "0" if last != "0" else "1"
        wrong = valid[:-1] + new_last
        now = 1_700_000_000.0
        result = verify_signature(
            body=body,
            signature_header=wrong,
            timestamp_header=_ts(now),
            secret=SECRET,
            now=now,
        )
        assert result.reason == REASON_HMAC_MISMATCH


@pytest.mark.django_db
class TestSettingsWiring:
    """O1/S4 — EVENT_INGEST_HMAC_SECRET is env-wired in settings (not
    getattr-fallback), so a deploy can actually provide it."""

    def test_setting_defined(self, settings) -> None:
        assert hasattr(settings, "EVENT_INGEST_HMAC_SECRET")

    def test_empty_secret_rejects_ingest(self, client, settings) -> None:
        """Empty secret → 401 no_secret on EVERY envelope (fail closed)."""
        import hashlib
        import hmac as hmac_mod
        import json
        import time

        settings.EVENT_INGEST_HMAC_SECRET = ""
        body = json.dumps(
            {
                "event_id": "01J9O1SMOKE0000000000001",
                "event_name": "booking.created",
                "event_version": 1,
                "occurred_at": "2026-07-19T12:00:00Z",
                "tenant_id": "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c",
                "user_id": "f1a2b3c4-d5e6-4789-9abc-def012345678",
                "actor": "user",
                "correlation_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "causation_id": None,
                "data": {"appointment_id": "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"},
            }
        ).encode()
        # A well-formed signature is still rejected — the failure mode is
        # the missing secret, not a bad signature.
        sig = "sha256=" + hmac_mod.new(b"whatever", body, hashlib.sha256).hexdigest()
        resp = client.post(
            "/api/v1/internal/events/ingest",
            data=body,
            content_type="application/json",
            HTTP_X_AYLA_EVENT_SIGNATURE=sig,
            HTTP_X_AYLA_EVENT_TIMESTAMP=str(int(time.time() * 1000)),
        )
        assert resp.status_code == 401

    def test_ingest_health_with_secret(self, client, settings, monkeypatch) -> None:
        """Secret set + valid signature → 200 (the staging smoke path)."""
        import hashlib
        import hmac as hmac_mod
        import json
        import time

        secret = "o1-smoke-secret"  # noqa: S105  # pragma: allowlist secret
        settings.EVENT_INGEST_HMAC_SECRET = secret
        settings.RATELIMIT_ENABLE = False
        from apps.eventbus import views as _views
        from apps.eventbus.ingest_dispatcher import dispatch_envelope as _direct

        monkeypatch.setattr(_views, "dispatch_with_timeout", _direct)

        # Health = the HMAC gate, not the consumer — stub the handler.
        import apps.eventbus.ingest_dispatcher as dispatcher_module

        dispatcher_module._REGISTRY[("booking.created", 1)] = lambda envelope: None

        body = json.dumps(
            {
                "event_id": "01J9O1SMOKE0000000000002",
                "event_name": "booking.created",
                "event_version": 1,
                "occurred_at": "2026-07-19T12:00:00Z",
                "tenant_id": "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c",
                "user_id": "f1a2b3c4-d5e6-4789-9abc-def012345678",
                "actor": "user",
                "correlation_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "causation_id": None,
                "data": {
                    "appointment_id": "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8",
                    "start_at": "2026-08-01T14:00:00+03:00",
                    "end_at": "2026-08-01T15:00:00+03:00",
                    "status": "confirmed",
                },
            }
        ).encode()
        sig = "sha256=" + hmac_mod.new(secret.encode(), body, hashlib.sha256).hexdigest()
        resp = client.post(
            "/api/v1/internal/events/ingest",
            data=body,
            content_type="application/json",
            HTTP_X_AYLA_EVENT_SIGNATURE=sig,
            HTTP_X_AYLA_EVENT_TIMESTAMP=str(int(time.time() * 1000)),
        )
        assert resp.status_code == 200
