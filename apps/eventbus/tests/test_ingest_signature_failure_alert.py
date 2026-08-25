"""DRF-1291 — signature-failure escalation on the ingest endpoint.

Context: on 08.08 a legitimate Ayla ``booking.*`` delivery was rejected
with ``401 {"reason": "hmac_mismatch"}`` (the two services disagreed on
the shared secret). Ayla's publisher dead-lettered the event on the
first 4xx, and bot-platform's only record was a WARNING log line plus a
forensic audit row — nobody was paged, and the loss surfaced a fortnight
later as a mirror discrepancy. A signature failure that comes from our
OWN publisher means *every* event dies until the secret is fixed, so it
must be a visible, alerting failure — not a quiet 401.

Contract pinned here:

* ``hmac_mismatch`` / ``no_secret`` (service-to-service auth broken)
  escalate: ERROR log + a sampled ``system.module.health.degraded``
  domain event (``severity=error``) — the same ops-alert surface the
  outbox dispatcher uses for DLQ quarantine
  (:func:`apps.eventbus.dispatcher._emit_dlq_alert`).
* Prober-shaped reasons (missing / malformed / stale) keep the WARNING
  log and emit the alert at ``severity=warning``.
* The alert is sampled (at most one emit per reason per sampler window)
  so an unauthenticated flood cannot amplify into the DomainEvent
  outbox — same DoS reasoning as the Round-2 AS3 audit sampler.
* An alerting failure itself must never change the HTTP response.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

import pytest
from django.core.cache import cache
from django.test import Client

from apps.eventbus import vocabulary as V
from apps.eventbus.models import DomainEvent


pytestmark = pytest.mark.django_db


SECRET = "ingest-test-secret"  # pragma: allowlist secret
INGEST_URL = "/api/v1/internal/events/ingest"

BODY = b'{"event_id": "01J9HXKM8Z2T4V6R8Q1P3D5F7E"}'


def _post(client: Client, body: bytes = BODY, *, secret: str | None = SECRET) -> object:
    ts_ms = str(int(time.time() * 1000))
    headers: dict = {"HTTP_X_AYLA_EVENT_TIMESTAMP": ts_ms}
    if secret is not None:
        headers["HTTP_X_AYLA_EVENT_SIGNATURE"] = (
            "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        )
    return client.post(INGEST_URL, data=body, content_type="application/json", **headers)


@pytest.fixture(autouse=True)
def _settings_and_cache(settings):
    settings.EVENT_INGEST_HMAC_SECRET = SECRET
    # The view's rate-limit decorator would false-positive on the
    # repeated re-POSTs below; the rate-limit contract itself is pinned
    # in test_ingest_rate_limit.py.
    settings.RATELIMIT_ENABLE = False
    # The alert sampler is cache-backed — clear so tests don't suppress
    # each other's first (and only) alert emit within the window.
    cache.clear()
    try:
        yield
    finally:
        cache.clear()


@pytest.fixture
def client() -> Client:
    return Client()


def _health_events() -> list[DomainEvent]:
    return list(
        DomainEvent.objects.filter(event_name=V.SYSTEM_MODULE_HEALTH_DEGRADED).order_by("event_id")
    )


class TestSignatureFailureEscalation:
    def test_hmac_mismatch_emits_error_alert_and_error_log(
        self, client: Client, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="apps.eventbus.views"):
            response = _post(client, secret="wrong-secret")  # pragma: allowlist secret

        assert response.status_code == 401  # type: ignore[attr-defined]
        assert response.json()["reason"] == "hmac_mismatch"  # type: ignore[attr-defined]

        # Escalated log: a broken service-to-service secret is not
        # prober noise — it kills every delivery until fixed.
        error_records = [
            r
            for r in caplog.records
            if r.levelno == logging.ERROR and "eventbus.ingest.signature_failed" in r.getMessage()
        ]
        assert error_records, "hmac_mismatch must log at ERROR, not WARNING"

        events = _health_events()
        assert len(events) == 1
        assert events[0].data["module_name"] == "eventbus.ingest"
        assert events[0].data["severity"] == "error"
        assert "hmac_mismatch" in events[0].data["metric"]

    def test_no_secret_emits_error_alert(self, client: Client, settings) -> None:
        settings.EVENT_INGEST_HMAC_SECRET = ""
        response = _post(client)

        assert response.status_code == 401  # type: ignore[attr-defined]
        assert response.json()["reason"] == "no_secret"  # type: ignore[attr-defined]

        events = _health_events()
        assert len(events) == 1
        assert events[0].data["severity"] == "error"
        assert "no_secret" in events[0].data["metric"]

    def test_missing_signature_alerts_at_warning_severity(
        self, client: Client, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="apps.eventbus.views"):
            response = _post(client, secret=None)

        assert response.status_code == 401  # type: ignore[attr-defined]
        assert response.json()["reason"] == "missing_signature"  # type: ignore[attr-defined]

        # Prober-shaped failures stay WARNING in the log …
        assert not [
            r
            for r in caplog.records
            if r.levelno == logging.ERROR and "eventbus.ingest.signature_failed" in r.getMessage()
        ]
        # … but still leave a (sampled) health signal so a sustained
        # reject stream is visible in aggregate.
        events = _health_events()
        assert len(events) == 1
        assert events[0].data["severity"] == "warning"
        assert "missing_signature" in events[0].data["metric"]

    def test_repeated_failures_within_window_emit_single_alert(self, client: Client) -> None:
        for _ in range(3):
            response = _post(client, secret="wrong-secret")  # pragma: allowlist secret
            assert response.status_code == 401  # type: ignore[attr-defined]

        # Sampled: an unauthenticated flood must not amplify into the
        # DomainEvent outbox (same reasoning as the AS3 audit sampler).
        assert len(_health_events()) == 1

    def test_alert_emit_failure_never_changes_response(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from apps.eventbus import services

        def _boom(*args, **kwargs):  # noqa: ANN001, ANN202
            raise RuntimeError("outbox unavailable")

        monkeypatch.setattr(services, "emit", _boom)

        response = _post(client, secret="wrong-secret")  # pragma: allowlist secret
        assert response.status_code == 401  # type: ignore[attr-defined]
        assert response.json()["reason"] == "hmac_mismatch"  # type: ignore[attr-defined]
