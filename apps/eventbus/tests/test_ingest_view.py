"""End-to-end tests for the cross-service ingest view (Phase 0 / #432).

Drive the view through Django's test client with valid + invalid
HMAC signatures, valid + invalid envelopes, and registered + missing
handlers. Pins every cell of the `event-contract.md` §8 status table.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from django.test import Client

from apps.eventbus.ingest_dispatcher import (
    register,
    registered_handlers,
    unregister,
)
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.models import IngestDedupe, IngestDLQ


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
def _settings_and_registry(settings, monkeypatch):
    settings.EVENT_INGEST_HMAC_SECRET = SECRET
    # PR #507 A2 — the rate-limit decorator on the view would
    # false-positive in non-rate-limit tests (we re-POST the same IP
    # repeatedly to exercise dedupe, malformed body, etc.). Disable
    # here; the rate-limit contract is pinned in test_ingest_rate_limit.py.
    settings.RATELIMIT_ENABLE = False

    # PR #507 A12 — the timeout wrapper submits dispatch_envelope to
    # a ThreadPoolExecutor. Under the SQLite test backend the worker
    # thread's IngestDedupe write deadlocks against the test
    # transaction with "database table is locked". Monkey-patch the
    # view's dispatch_with_timeout to call dispatch_envelope
    # directly — sidesteps the SQLite race while keeping §8 status
    # table coverage on the view. The actual timeout contract is
    # pinned by test_ingest_timeout.py (which mocks dispatch_envelope
    # entirely, no DB involvement).
    from apps.eventbus import views as _views
    from apps.eventbus.ingest_dispatcher import dispatch_envelope as _direct

    monkeypatch.setattr(_views, "dispatch_with_timeout", _direct)

    yield
    for key in list(registered_handlers().keys()):
        unregister(*key)


@pytest.fixture
def client() -> Client:
    return Client()


class TestHappyPath:
    def test_valid_signed_event_with_handler_returns_200(self, client: Client) -> None:
        calls: list[IngestEnvelope] = []
        register("booking.created", 1, calls.append)

        response = _post(client, json.dumps(VALID_BODY).encode())

        assert response.status_code == 200
        body = json.loads(response.content)
        assert body["status"] == "ok"
        assert len(calls) == 1
        assert IngestDedupe.objects.filter(event_id=VALID_BODY["event_id"]).count() == 1

    def test_duplicate_returns_200_with_duplicate_flag(self, client: Client) -> None:
        calls: list[IngestEnvelope] = []
        register("booking.created", 1, calls.append)

        body = json.dumps(VALID_BODY).encode()
        _post(client, body)
        response = _post(client, body)

        assert response.status_code == 200
        payload = json.loads(response.content)
        assert payload.get("duplicate") is True
        assert len(calls) == 1


class TestSignatureRejection:
    def test_missing_signature_returns_401(self, client: Client) -> None:
        body = json.dumps(VALID_BODY).encode()
        response = client.post(
            INGEST_URL,
            data=body,
            content_type="application/json",
            HTTP_X_AYLA_EVENT_TIMESTAMP=str(int(time.time() * 1000)),
        )
        assert response.status_code == 401

    def test_wrong_secret_returns_401(self, client: Client) -> None:
        body = json.dumps(VALID_BODY).encode()
        response = _post(client, body, secret="wrong-secret")
        assert response.status_code == 401

    def test_stale_timestamp_returns_401(self, client: Client) -> None:
        body = json.dumps(VALID_BODY).encode()
        # 10 minutes in the past — well past the 300s window.
        response = _post(client, body, ts=time.time() - 600.0)
        assert response.status_code == 401


class TestMalformedBody:
    def test_invalid_json_returns_400(self, client: Client) -> None:
        response = _post(client, b"not-json")
        assert response.status_code == 400

    def test_missing_field_returns_400(self, client: Client) -> None:
        bad = dict(VALID_BODY)
        del bad["event_name"]
        response = _post(client, json.dumps(bad).encode())
        assert response.status_code == 400

    def test_invalid_actor_returns_400(self, client: Client) -> None:
        bad = dict(VALID_BODY)
        bad["actor"] = "robot"
        response = _post(client, json.dumps(bad).encode())
        assert response.status_code == 400


class TestUnknownEvent:
    def test_known_name_unregistered_version_returns_422_and_dlq(self, client: Client) -> None:
        """§8.4 — publisher emitted a version the consumer hasn't shipped yet."""
        register("booking.created", 1, lambda e: None)

        bad = dict(VALID_BODY)
        bad["event_version"] = 2  # only v1 is registered
        response = _post(client, json.dumps(bad).encode())

        assert response.status_code == 422
        body = json.loads(response.content)
        assert body["reason"] == "unknown_event_version"
        # DLQ row written.
        assert IngestDLQ.objects.filter(event_id=VALID_BODY["event_id"]).count() == 1


class TestHandlerException:
    def test_handler_raises_returns_500_no_dedupe(self, client: Client) -> None:
        def _boom(env: IngestEnvelope) -> None:
            raise RuntimeError("downstream timeout")

        register("booking.created", 1, _boom)

        body = json.dumps(VALID_BODY).encode()
        response = _post(client, body)

        assert response.status_code == 500
        # §5.1 — dedupe row rolled back on handler exception so retry re-processes.
        assert IngestDedupe.objects.filter(event_id=VALID_BODY["event_id"]).count() == 0

    def test_handler_exception_audit_row_persists(self, client: Client) -> None:
        """PR #507 adversarial A5 — audit row survives handler exception.

        The dedupe rolls back (§5.1 — Ayla retry needs to re-process),
        but the audit row MUST persist for ops triage. View-layer
        write_audit() runs AFTER dispatch_envelope returns + outside
        the inner atomic block, so the audit row commits independently
        of the inner rollback.
        """
        from apps.audit.models import AuditLog

        def _boom(env: IngestEnvelope) -> None:
            raise RuntimeError("downstream timeout")

        register("booking.created", 1, _boom)

        body = json.dumps(VALID_BODY).encode()
        response = _post(client, body)

        assert response.status_code == 500
        # Audit row exists despite dedupe rollback.
        audit_rows = AuditLog.all_tenants.filter(
            action="eventbus.ingest.handler_exception",
        )
        assert audit_rows.count() == 1
        # PII rule §7 — exception TYPE only, not message.
        payload = audit_rows.first().payload
        assert payload.get("exception_type") == "RuntimeError"
        assert "downstream timeout" not in str(payload)


class TestVerbPolicy:
    def test_get_returns_405(self, client: Client) -> None:
        response = client.get(INGEST_URL)
        assert response.status_code == 405


class TestNoTrailingSlashContract:
    def test_trailing_slash_does_not_resolve_to_ingest(self, client: Client) -> None:
        """Contract §6.1 — the canonical URL has no trailing slash.

        Posting with a trailing slash MUST NOT silently 301 + retry
        (which would drop the body and break HMAC verification).
        """
        body = json.dumps(VALID_BODY).encode()
        ts_ms = str(int(time.time() * 1000))
        sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        response = client.post(
            INGEST_URL + "/",
            data=body,
            content_type="application/json",
            HTTP_X_AYLA_EVENT_SIGNATURE=sig,
            HTTP_X_AYLA_EVENT_TIMESTAMP=ts_ms,
        )
        # The contract requires no trailing slash; either a 301 (Django
        # APPEND_SLASH) or a 404 is acceptable here as long as it does
        # NOT 200. The publisher MUST hit the canonical URL.
        assert response.status_code != 200
