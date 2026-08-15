"""Multi-bot initData verification (DRF-1061).

### Why this file exists

One Mini App address per bot does **not** let the server tell the bots
apart: the address is chosen by us, but the request carries no bot
identifier. The signature does — MAX signs initData with the token of the
bot the app was opened from. So the verifier must try every configured
bot's key, and report back which one matched.

Without this, the salon bot's Mini App answers 401 ``bad_signature`` on
every screen, and no amount of role configuration helps: the request dies
before ``resolve_role`` is ever reached.

### The negative test is the point

A verifier that tries several keys is one refactor away from a verifier
that accepts anything. ``test_signature_from_unregistered_bot_is_rejected``
is the guard: a payload signed by a key that is *not* in the registry must
still fail. If that test ever passes vacuously, the auth layer is open.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from django.test import override_settings

from apps.channels.bot_registry import BotEntry
from apps.miniapp_api.auth import (
    InitDataBadSignature,
    InitDataNotConfigured,
    verify_init_data,
)

CLIENT_TOKEN = "client-bot-token"  # pragma: allowlist secret
SALON_TOKEN = "salon-bot-token"  # pragma: allowlist secret
STRANGER_TOKEN = "stranger-bot-token"  # pragma: allowlist secret

REGISTRY = (
    BotEntry(
        slug="client",
        webhook_secret="wh-client",  # pragma: allowlist secret
        api_token=CLIENT_TOKEN,
        stream="max_global",
    ),
    BotEntry(
        slug="salon",
        webhook_secret="wh-salon",  # pragma: allowlist secret
        api_token=SALON_TOKEN,
        tenant_slug="formula-tela",
        stream="max_salon",
    ),
)


def make_init_data(token: str, *, user_id: int = 4242, auth_date: int | None = None) -> str:
    """Build a genuinely-signed initData payload for ``token``.

    Mirrors what the MAX client does, so the test exercises the real HMAC
    rather than a stub: two-stage HMAC over the sorted ``key=value`` lines.
    """

    params = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "user": json.dumps({"id": user_id, "first_name": "Тест"}, ensure_ascii=False),
    }
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(params)


@pytest.fixture
def two_bots(settings):
    """Registry with both bots declared and no legacy token."""
    settings.MAX_BOT_REGISTRY = REGISTRY
    settings.MAX_BOT_TOKEN = ""
    return settings


@pytest.mark.usefixtures("two_bots")
class TestMultiBotVerification:
    def test_client_bot_signature_verifies_and_is_attributed(self):
        verified = verify_init_data(make_init_data(CLIENT_TOKEN))

        assert verified.user_id == "4242"
        assert verified.bot_slug == "client"

    def test_salon_bot_signature_verifies_and_is_attributed(self):
        # The whole point of DRF-1061: this is the payload that used to 401.
        verified = verify_init_data(make_init_data(SALON_TOKEN))

        assert verified.user_id == "4242"
        assert verified.bot_slug == "salon"

    def test_signature_from_unregistered_bot_is_rejected(self):
        # THE load-bearing negative test. A third bot's token is a valid
        # HMAC key — it just isn't ours. Accepting it would mean anyone who
        # registers a MAX bot can forge our Mini App sessions.
        with pytest.raises(InitDataBadSignature):
            verify_init_data(make_init_data(STRANGER_TOKEN))

    def test_tampered_payload_is_rejected(self):
        raw = make_init_data(SALON_TOKEN)
        tampered = raw.replace("4242", "9999")

        with pytest.raises(InitDataBadSignature):
            verify_init_data(tampered)

    def test_explicit_bot_token_override_still_pins_a_single_key(self):
        # The test-only override must not silently gain registry fallback:
        # a caller that names a key is asserting "this key and no other".
        salon_data = make_init_data(SALON_TOKEN)

        assert verify_init_data(salon_data, bot_token=SALON_TOKEN).bot_slug == ""
        with pytest.raises(InitDataBadSignature):
            verify_init_data(salon_data, bot_token=CLIENT_TOKEN)


class TestBackwardCompatibility:
    """A deployment that never declared MAX_BOTS must be unaffected."""

    @override_settings(MAX_BOT_REGISTRY=(), MAX_BOT_TOKEN=CLIENT_TOKEN)
    def test_legacy_single_token_still_verifies(self):
        verified = verify_init_data(make_init_data(CLIENT_TOKEN))

        assert verified.user_id == "4242"
        # No registry entry matched, so there is no slug to report.
        assert verified.bot_slug == ""

    @override_settings(MAX_BOT_REGISTRY=(), MAX_BOT_TOKEN=CLIENT_TOKEN)
    def test_legacy_path_still_rejects_a_foreign_key(self):
        with pytest.raises(InitDataBadSignature):
            verify_init_data(make_init_data(STRANGER_TOKEN))

    @override_settings(MAX_BOT_REGISTRY=REGISTRY, MAX_BOT_TOKEN=STRANGER_TOKEN)
    def test_legacy_token_is_honoured_alongside_the_registry(self):
        # Transitional state: registry declared, legacy setting still set.
        # Both must work, so a deploy can roll forward without a flag day.
        assert verify_init_data(make_init_data(SALON_TOKEN)).bot_slug == "salon"
        assert verify_init_data(make_init_data(STRANGER_TOKEN)).bot_slug == ""

    @override_settings(MAX_BOT_REGISTRY=(), MAX_BOT_TOKEN="")
    def test_nothing_configured_raises_not_configured(self):
        with pytest.raises(InitDataNotConfigured):
            verify_init_data(make_init_data(CLIENT_TOKEN))

    @override_settings(MAX_BOT_REGISTRY=REGISTRY, MAX_BOT_TOKEN="")
    def test_empty_explicit_override_raises_not_configured(self):
        # An empty override must not silently fall back to the registry —
        # that would turn a misconfiguration into a wider trust set.
        with pytest.raises(InitDataNotConfigured):
            verify_init_data(make_init_data(CLIENT_TOKEN), bot_token="")


@pytest.mark.usefixtures("two_bots")
class TestNonSignatureChecksStillApply:
    """Multi-key verification must not weaken the other guarantees."""

    def test_stale_auth_date_still_rejected(self):
        from apps.miniapp_api.auth import InitDataStale

        stale = make_init_data(SALON_TOKEN, auth_date=int(time.time()) - 7200)

        with pytest.raises(InitDataStale):
            verify_init_data(stale)

    def test_duplicate_key_still_rejected(self):
        from apps.miniapp_api.auth import InitDataMalformed

        raw = make_init_data(SALON_TOKEN) + "&hash=deadbeef"

        with pytest.raises(InitDataMalformed):
            verify_init_data(raw)
