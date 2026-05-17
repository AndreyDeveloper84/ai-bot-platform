"""Tenant Telegram-field tests (Phase 1 / CH1, DRF-848).

Behaviour 16: Tenant.telegram_bot_token must NOT appear in repr() —
the full token is masked to last 4 chars.
"""

from __future__ import annotations

import pytest

from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


class TestTokenMasking:
    def test_repr_does_not_contain_full_token(self):
        secret = "SUPER-SECRET-BOT-TOKEN-NOT-IN-REPR"  # pragma: allowlist secret
        t = Tenant.objects.create(
            slug="repr-test",
            name="Repr Test",
            telegram_bot_token=secret,
            telegram_webhook_secret="x",
        )
        rep = repr(t)
        assert secret not in rep, f"full token leaked into repr: {rep!r}"
        # The last 4 chars survive (with the masking prefix) — provides
        # operator-friendly identification without exposing the token.
        # Last 4 of "SUPER-SECRET-BOT-TOKEN-NOT-IN-REPR" → "REPR".
        assert "REPR" in rep
        assert "…REPR" in rep

    def test_repr_empty_token_safe(self):
        t = Tenant.objects.create(slug="empty-tok", name="Empty Tok")
        rep = repr(t)
        # No crash and no garbage masking when token absent.
        assert "Tenant" in rep
        # The masked field appears as empty quotes.
        assert "telegram_bot_token=''" in rep

    def test_mask_helper_short_tokens_starred(self):
        """Tokens < 4 chars should be fully obscured, not partially leaked."""
        t = Tenant(telegram_bot_token="abc")
        masked = t._mask_telegram_token()
        # Don't reveal the actual chars — only their length.
        assert "abc" not in masked
        assert masked == "…***"


class TestFieldDefaults:
    def test_blank_defaults(self):
        """Existing tenants must continue to validate with empty Telegram fields."""
        t = Tenant.objects.create(slug="def", name="Def")
        assert t.telegram_bot_token == ""
        assert t.telegram_webhook_secret == ""

    def test_fields_persist(self):
        t = Tenant.objects.create(
            slug="persist",
            name="P",
            telegram_bot_token="tok",  # pragma: allowlist secret
            telegram_webhook_secret="sec",  # pragma: allowlist secret
        )
        t.refresh_from_db()
        assert t.telegram_bot_token == "tok"  # pragma: allowlist secret
        assert t.telegram_webhook_secret == "sec"  # pragma: allowlist secret
