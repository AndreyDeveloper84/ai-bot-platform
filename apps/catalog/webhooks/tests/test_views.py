"""Catalog webhook receiver tests (Sprint 10 / C3 / DRF-879).

Coverage matrix — every leaf in `apps.catalog.webhooks.views`:

* Happy path → 200 + audit `received` + task enqueued
* Missing signature header → 200 + audit `bad_signature` + no enqueue
* Bad-prefix signature → 200 + audit `bad_signature` + no enqueue
* Wrong HMAC → 200 + audit `bad_signature` + no enqueue
* Empty secret in settings → fail-closed (200 + bad_signature)
* Malformed JSON → 200 + audit `malformed_json`
* Top-level non-dict (e.g. list) → 200 + audit `malformed_json`
* Missing required field → 200 + audit `missing_*`
* Unknown ``model`` → 200 + audit `unknown_model`
* Unknown ``event`` → 200 + audit `unknown_event`
* Non-int ``external_id`` → 200 + audit `external_id_not_int`

The receiver must **always** answer 200 — anything else triggers
retry storms from mysite's Celery delivery. This invariant is asserted
in every failure-path test.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Generator
from unittest.mock import patch

import pytest
from django.test import Client
from django.test.utils import override_settings

from apps.audit.models import AuditLog

_SECRET = "test-shared-secret"  # pragma: allowlist secret
_WEBHOOK_URL = "/api/v1/catalog/webhook/"


def _sign(body: bytes, secret: str = _SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _body(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "tenant_slug": "salon-a",
        "model": "service",
        "event": "updated",
        "external_id": 42,
        "external_updated_at": "2026-05-14T10:00:00+00:00",
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _hmac_secret() -> Generator[None, None, None]:
    with override_settings(MYSITE_WEBHOOK_HMAC_SECRET=_SECRET):
        yield


@pytest.fixture
def client(db) -> Client:
    return Client()


@pytest.fixture
def apply_delta_mock():
    """Patch the Celery `apply_delta` reference imported by the view.

    The view did ``from apps.catalog.webhooks.tasks import apply_delta``
    at module load — patch the binding the view sees.
    """
    with patch("apps.catalog.webhooks.views.apply_delta") as m:
        yield m


def _audit_actions() -> list[str]:
    return list(AuditLog.all_tenants.values_list("action", flat=True))


def _audit_payloads(action: str) -> list[dict[str, object]]:
    return list(AuditLog.all_tenants.filter(action=action).values_list("payload", flat=True))


class TestHappyPath:
    def test_valid_signature_returns_200_and_enqueues(
        self, client: Client, apply_delta_mock
    ) -> None:
        body = json.dumps(_body()).encode("utf-8")
        resp = client.post(
            _WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE=_sign(body),
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "received"}
        apply_delta_mock.delay.assert_called_once_with(
            tenant_slug="salon-a",
            model="service",
            event="updated",
            external_id=42,
            external_updated_at="2026-05-14T10:00:00+00:00",
        )
        assert "catalog.webhook.received" in _audit_actions()

    def test_int_coercion_for_string_external_id(self, client: Client, apply_delta_mock) -> None:
        body = json.dumps(_body(external_id="42")).encode("utf-8")
        resp = client.post(
            _WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE=_sign(body),
        )
        assert resp.status_code == 200
        apply_delta_mock.delay.assert_called_once()
        kwargs = apply_delta_mock.delay.call_args.kwargs
        assert kwargs["external_id"] == 42


class TestSignature:
    def test_missing_signature_header_rejected(self, client: Client, apply_delta_mock) -> None:
        body = json.dumps(_body()).encode("utf-8")
        resp = client.post(_WEBHOOK_URL, data=body, content_type="application/json")
        assert resp.status_code == 200
        apply_delta_mock.delay.assert_not_called()
        rejected = _audit_payloads("catalog.webhook.rejected")
        assert any(p.get("reason") == "bad_signature" for p in rejected)

    def test_wrong_prefix_rejected(self, client: Client, apply_delta_mock) -> None:
        body = json.dumps(_body()).encode("utf-8")
        # "sha512=" prefix instead of "sha256="
        resp = client.post(
            _WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE="sha512=" + hashlib.sha256(body).hexdigest(),
        )
        assert resp.status_code == 200
        apply_delta_mock.delay.assert_not_called()

    def test_wrong_hmac_rejected(self, client: Client, apply_delta_mock) -> None:
        body = json.dumps(_body()).encode("utf-8")
        wrong_sig = _sign(body, secret="WRONG")
        resp = client.post(
            _WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE=wrong_sig,
        )
        assert resp.status_code == 200
        apply_delta_mock.delay.assert_not_called()

    def test_empty_secret_fails_closed(self, client: Client, apply_delta_mock) -> None:
        body = json.dumps(_body()).encode("utf-8")
        # Signature is "correctly" computed against empty secret — but we
        # want to ensure the receiver still rejects, defending against
        # misconfigured deploy + matched-empty-string attack.
        sig = "sha256=" + hmac.new(b"", body, hashlib.sha256).hexdigest()
        with override_settings(MYSITE_WEBHOOK_HMAC_SECRET=""):
            resp = client.post(
                _WEBHOOK_URL,
                data=body,
                content_type="application/json",
                HTTP_X_SIGNATURE=sig,
            )
        assert resp.status_code == 200
        apply_delta_mock.delay.assert_not_called()


class TestPayloadValidation:
    def test_malformed_json_rejected(self, client: Client, apply_delta_mock) -> None:
        body = b"{this is not valid json"
        resp = client.post(
            _WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE=_sign(body),
        )
        assert resp.status_code == 200
        apply_delta_mock.delay.assert_not_called()
        rejected = _audit_payloads("catalog.webhook.rejected")
        assert any(p.get("reason") == "malformed_json" for p in rejected)

    def test_top_level_list_rejected(self, client: Client, apply_delta_mock) -> None:
        body = json.dumps([_body()]).encode("utf-8")  # list, not dict
        resp = client.post(
            _WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE=_sign(body),
        )
        assert resp.status_code == 200
        apply_delta_mock.delay.assert_not_called()

    @pytest.mark.parametrize(
        "missing_field",
        ["tenant_slug", "model", "event", "external_id", "external_updated_at"],
    )
    def test_missing_required_field_rejected(
        self, client: Client, apply_delta_mock, missing_field: str
    ) -> None:
        payload = _body()
        del payload[missing_field]
        body = json.dumps(payload).encode("utf-8")
        resp = client.post(
            _WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE=_sign(body),
        )
        assert resp.status_code == 200
        apply_delta_mock.delay.assert_not_called()
        rejected = _audit_payloads("catalog.webhook.rejected")
        assert any(p.get("reason") == f"missing_{missing_field}" for p in rejected)

    def test_unknown_model_rejected(self, client: Client, apply_delta_mock) -> None:
        body = json.dumps(_body(model="unknown_thing")).encode("utf-8")
        resp = client.post(
            _WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE=_sign(body),
        )
        assert resp.status_code == 200
        apply_delta_mock.delay.assert_not_called()
        rejected = _audit_payloads("catalog.webhook.rejected")
        assert any(p.get("reason") == "unknown_model" for p in rejected)

    def test_unknown_event_rejected(self, client: Client, apply_delta_mock) -> None:
        body = json.dumps(_body(event="patched")).encode("utf-8")
        resp = client.post(
            _WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE=_sign(body),
        )
        assert resp.status_code == 200
        apply_delta_mock.delay.assert_not_called()
        rejected = _audit_payloads("catalog.webhook.rejected")
        assert any(p.get("reason") == "unknown_event" for p in rejected)

    def test_non_int_external_id_rejected(self, client: Client, apply_delta_mock) -> None:
        body = json.dumps(_body(external_id="not-an-int")).encode("utf-8")
        resp = client.post(
            _WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE=_sign(body),
        )
        assert resp.status_code == 200
        apply_delta_mock.delay.assert_not_called()
        rejected = _audit_payloads("catalog.webhook.rejected")
        assert any(p.get("reason") == "external_id_not_int" for p in rejected)


class TestMethodNotAllowed:
    def test_get_returns_405(self, client: Client) -> None:
        # require_POST → Django emits 405 (this isn't a webhook-protocol
        # answer; method-not-allowed is a Django/HTTP concern, not a
        # mysite-delivery concern, so the always-200 rule doesn't apply).
        resp = client.get(_WEBHOOK_URL)
        assert resp.status_code == 405
