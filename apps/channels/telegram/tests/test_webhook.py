"""Telegram webhook view tests (Phase 1 / CH1, DRF-848).

Behaviours 12-15, 21:
  12. Webhook secret mismatch → 403 + audit row
  13. Webhook unknown tenant slug → 404
  14. Webhook secret match → 200 + handler invoked
  15. Webhook empty body → 200 (no crash)
  21. Cross-tenant isolation — webhook for tenant A cannot touch tenant B
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.test import Client

from apps.audit.models import AuditLog
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


# Test fixtures — placeholder credentials, never deployed. Centralised
# constants so the detect-secrets pre-commit hook needs only one
# allowlist marker per test value.
_TOKEN_A = "bot-token-aaa"  # pragma: allowlist secret
_SECRET_A = "secret-aaa"  # pragma: allowlist secret
_TOKEN_B = "bot-token-bbb"  # pragma: allowlist secret
_SECRET_B = "secret-bbb"  # pragma: allowlist secret


@pytest.fixture
def tenant_a():
    return Tenant.objects.create(
        slug="tenant-a",
        name="Tenant A",
        telegram_bot_token=_TOKEN_A,
        telegram_webhook_secret=_SECRET_A,
    )


@pytest.fixture
def tenant_b():
    return Tenant.objects.create(
        slug="tenant-b",
        name="Tenant B",
        telegram_bot_token=_TOKEN_B,
        telegram_webhook_secret=_SECRET_B,
    )


@pytest.fixture
def tenant_unconfigured():
    """Tenant exists but has no Telegram credentials — must 404."""
    return Tenant.objects.create(slug="tenant-unconfigured", name="Unconfigured")


def _text_update(text="привет", user_id=12345, chat_id=12345, message_id=1):
    return {
        "update_id": 100,
        "message": {
            "message_id": message_id,
            "date": 1731320000,
            "from": {"id": user_id, "is_bot": False, "first_name": "Иван"},
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


def _post(client, slug, *, body=None, secret_header=None):
    """Send a webhook POST to /api/v1/channels/telegram/webhook/<slug>/."""
    headers = {}
    if secret_header is not None:
        # Django test client expects HTTP_ prefix + uppercase + underscores.
        headers["HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN"] = secret_header
    return client.post(
        f"/api/v1/channels/telegram/webhook/{slug}/",
        data=json.dumps(body) if body is not None else b"",
        content_type="application/json",
        **headers,
    )


class TestUnknownSlug:
    """Behaviour 13: unknown slug → 404."""

    def test_unknown_slug_404(self):
        client = Client()
        r = _post(client, "no-such-tenant", body=_text_update(), secret_header="x")
        assert r.status_code == 404

    def test_unconfigured_tenant_404(self, tenant_unconfigured):
        """Existing tenant without telegram credentials → 404 (no info leak)."""
        client = Client()
        r = _post(
            client,
            tenant_unconfigured.slug,
            body=_text_update(),
            secret_header="x",
        )
        assert r.status_code == 404

    def test_inactive_tenant_404(self, tenant_a):
        tenant_a.is_active = False
        tenant_a.save()
        client = Client()
        r = _post(client, tenant_a.slug, body=_text_update(), secret_header=_SECRET_A)
        assert r.status_code == 404


class TestSecretAuth:
    """Behaviour 12: mismatch → 403 + audit row."""

    def test_secret_mismatch_403(self, tenant_a):
        client = Client()
        r = _post(client, tenant_a.slug, body=_text_update(), secret_header="WRONG")
        assert r.status_code == 403

    def test_secret_mismatch_writes_audit_row(self, tenant_a):
        client = Client()
        _post(client, tenant_a.slug, body=_text_update(), secret_header="WRONG")
        rows = AuditLog.all_tenants.filter(action="telegram.webhook.bad_signature")
        assert rows.exists()
        row = rows.first()
        assert row.tenant_id == tenant_a.id

    def test_missing_secret_header_403(self, tenant_a):
        client = Client()
        r = _post(client, tenant_a.slug, body=_text_update())  # no secret_header
        assert r.status_code == 403

    def test_empty_secret_header_403(self, tenant_a):
        client = Client()
        r = _post(client, tenant_a.slug, body=_text_update(), secret_header="")
        assert r.status_code == 403


class TestSecretMatch:
    """Behaviour 14: matching secret → 200 + handler invoked."""

    def test_secret_match_invokes_handler(self, tenant_a):
        client = Client()
        with patch("apps.channels.telegram.webhook.handle_inbound") as mock_handle:
            r = _post(
                client,
                tenant_a.slug,
                body=_text_update(),
                secret_header=_SECRET_A,
            )
        assert r.status_code == 200
        assert mock_handle.called
        kwargs = mock_handle.call_args.kwargs
        # Tenant passed through to the handler matches the URL tenant.
        assert kwargs["tenant"].id == tenant_a.id


class TestEmptyBody:
    """Behaviour 15: empty body → 200, no crash."""

    def test_empty_body_no_handler_call(self, tenant_a):
        client = Client()
        with patch("apps.channels.telegram.webhook.handle_inbound") as mock_handle:
            r = client.post(
                f"/api/v1/channels/telegram/webhook/{tenant_a.slug}/",
                data=b"",
                content_type="application/json",
                HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=_SECRET_A,
            )
        assert r.status_code == 200
        assert not mock_handle.called

    def test_invalid_json_returns_200_no_crash(self, tenant_a):
        """Telegram doesn't retry on 4xx — bad JSON is operator's problem."""
        client = Client()
        with patch("apps.channels.telegram.webhook.handle_inbound") as mock_handle:
            r = client.post(
                f"/api/v1/channels/telegram/webhook/{tenant_a.slug}/",
                data=b"{not json",
                content_type="application/json",
                HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=_SECRET_A,
            )
        assert r.status_code == 200
        assert not mock_handle.called

    def test_handler_exception_returns_200(self, tenant_a):
        """Telegram retries 5xx forever — we MUST return 200 even on handler crash."""
        client = Client()
        with patch(
            "apps.channels.telegram.webhook.handle_inbound",
            side_effect=RuntimeError("boom"),
        ):
            r = _post(
                client,
                tenant_a.slug,
                body=_text_update(),
                secret_header=_SECRET_A,
            )
        assert r.status_code == 200


class TestCrossTenantIsolation:
    """Behaviour 21 — webhook for tenant A cannot touch tenant B's data."""

    def test_tenant_a_webhook_creates_bot_user_under_tenant_a_only(self, tenant_a, tenant_b):
        client = Client()
        # Use the REAL handler, no mocking — verify the integration end-to-end.
        with patch("apps.channels.telegram.outbound.requests.post") as mock_post:
            # Outbound must not actually call the network even with the
            # real handler. Mock to a 200 response.
            from types import SimpleNamespace

            mock_post.return_value = SimpleNamespace(ok=True, status_code=200, text='{"ok": true}')
            r = _post(
                client,
                tenant_a.slug,
                body=_text_update(user_id=999, chat_id=999),
                secret_header=_SECRET_A,
            )
        assert r.status_code == 200

        # BotUser created under tenant_a, NOT tenant_b.
        users_a = BotUser.all_tenants.filter(
            tenant=tenant_a, channel="telegram", channel_user_id="999"
        )
        users_b = BotUser.all_tenants.filter(
            tenant=tenant_b, channel="telegram", channel_user_id="999"
        )
        assert users_a.count() == 1
        assert users_b.count() == 0

    def test_tenant_b_secret_cannot_authenticate_tenant_a_url(self, tenant_a, tenant_b):
        """The secret-per-tenant invariant: B's secret won't open A's webhook."""
        client = Client()
        r = _post(
            client,
            tenant_a.slug,
            body=_text_update(),
            secret_header=_SECRET_B,
        )
        assert r.status_code == 403


class TestMethodGuard:
    """Reject non-POST methods cleanly."""

    def test_get_returns_405(self, tenant_a):
        client = Client()
        r = client.get(f"/api/v1/channels/telegram/webhook/{tenant_a.slug}/")
        assert r.status_code == 405


class TestIgnoredUpdate:
    """Parser returns None for channel_post — view still 200, handler ignores."""

    def test_channel_post_update_200(self, tenant_a):
        client = Client()
        with patch("apps.channels.telegram.outbound.requests.post") as mock_post:
            r = _post(
                client,
                tenant_a.slug,
                body={"update_id": 1, "channel_post": {"message_id": 1, "chat": {"id": 1}}},
                secret_header=_SECRET_A,
            )
        assert r.status_code == 200
        # Outbound never called for ignored updates.
        assert not mock_post.called
