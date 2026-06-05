"""Tests for /auth/verify pending_booking_intent cache extension.

Per memory `project_booking_flow_implementation_cut` founder cut #6 +
tech-lead handoff 2026-05-26 (P0 PRE_PILOT).

Covers:
- Helper module: validate / store / get / clear / TTL bounds
- /auth/verify endpoint: body parsing, validation, cache round-trip,
  response shape, malformed-body 400, invalid-intent 400, no-body OK
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from urllib.parse import urlencode

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.miniapp_api.pending_intent import (
    PENDING_INTENT_TTL_SECONDS,
    PendingIntentInvalid,
    _cache_key,
    clear_intent,
    get_intent,
    store_intent,
    validate_intent,
)
from apps.tenancy.models import Tenant


BOT_TOKEN = "test-bot-token-pending-intent"  # noqa: S105 — test fixture  # pragma: allowlist secret


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Ольга"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(
        slug="pi-test",
        name="Pending Intent Test",
        timezone="Europe/Moscow",
    )
    settings.MAX_BOT_TENANT_SLUG = "pi-test"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="77777",
        display_name="Ольга",
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    """Per-test cache reset to keep TTL state isolated."""
    cache.clear()
    yield
    cache.clear()


# ─── TestValidator ──────────────────────────────────────────────────────


class TestValidator:
    def test_none_returns_empty_dict(self):
        assert validate_intent(None) == {}

    def test_empty_dict_returns_empty_dict(self):
        assert validate_intent({}) == {}

    def test_full_payload_round_trip(self):
        payload = {
            "master_id": "11111111-2222-3333-4444-555555555555",
            "service_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "slot_iso": "2026-07-15T14:00:00+03:00",
            "price_quoted": 1800,
            "note": "массаж лица",
            "loyalty_apply": True,
        }
        out = validate_intent(payload)
        assert out == payload

    def test_unknown_fields_dropped(self):
        out = validate_intent({"master_id": "x", "evil_field": "ignored"})
        assert "master_id" in out
        assert "evil_field" not in out

    def test_non_dict_raises(self):
        with pytest.raises(PendingIntentInvalid, match="must be an object"):
            validate_intent("just a string")
        with pytest.raises(PendingIntentInvalid):
            validate_intent([1, 2, 3])

    def test_wrong_type_raises(self):
        with pytest.raises(PendingIntentInvalid, match="master_id"):
            validate_intent({"master_id": 12345})  # int instead of str
        with pytest.raises(PendingIntentInvalid, match="loyalty_apply"):
            validate_intent({"loyalty_apply": "yes"})  # str instead of bool

    def test_price_out_of_range_raises(self):
        with pytest.raises(PendingIntentInvalid, match="price_quoted"):
            validate_intent({"price_quoted": -1})
        with pytest.raises(PendingIntentInvalid, match="price_quoted"):
            validate_intent({"price_quoted": 99_999_999})

    def test_note_truncated(self):
        long_note = "x" * 600
        out = validate_intent({"note": long_note})
        assert len(out["note"]) == 500

    def test_explicit_null_field_dropped(self):
        """`master_id: null` is the caller saying «clear this» — don't store."""
        out = validate_intent({"master_id": None, "service_id": "x"})
        assert "master_id" not in out
        assert out["service_id"] == "x"

    def test_price_quoted_accepts_float(self):
        out = validate_intent({"price_quoted": 1850.5})
        assert out["price_quoted"] == 1850.5


# ─── TestCacheRoundTrip ─────────────────────────────────────────────────


class TestCacheRoundTrip:
    def test_store_and_get(self):
        bu_id = "abc-1"
        store_intent(bu_id, {"master_id": "m1", "service_id": "s1"})
        assert get_intent(bu_id) == {"master_id": "m1", "service_id": "s1"}

    def test_get_missing_returns_none(self):
        assert get_intent("never-stored") is None

    def test_store_empty_clears(self):
        bu_id = "abc-2"
        store_intent(bu_id, {"master_id": "m1"})
        assert get_intent(bu_id) is not None
        store_intent(bu_id, {})
        assert get_intent(bu_id) is None

    def test_clear_intent(self):
        bu_id = "abc-3"
        store_intent(bu_id, {"master_id": "m1"})
        clear_intent(bu_id)
        assert get_intent(bu_id) is None

    def test_overwrite(self):
        bu_id = "abc-4"
        store_intent(bu_id, {"master_id": "m1"})
        store_intent(bu_id, {"master_id": "m2", "service_id": "s2"})
        assert get_intent(bu_id) == {"master_id": "m2", "service_id": "s2"}

    def test_cache_key_format(self):
        assert _cache_key("xyz") == "pending_booking_intent:xyz"

    def test_per_user_isolation(self):
        store_intent("user-a", {"master_id": "for-a"})
        store_intent("user-b", {"master_id": "for-b"})
        assert get_intent("user-a")["master_id"] == "for-a"
        assert get_intent("user-b")["master_id"] == "for-b"

    def test_ttl_constant_is_ten_minutes(self):
        """Locks the contract — 10 min per founder cut #6 docstring."""
        assert PENDING_INTENT_TTL_SECONDS == 600


# ─── TestAuthVerifyIntegration ──────────────────────────────────────────


class TestAuthVerifyIntegration:
    def _url(self) -> str:
        return reverse("miniapp_api:auth_verify")

    def test_no_body_response_includes_null_intent(self, client: Client, bot_user: BotUser):
        """First launch: empty body, no cached intent → field is null."""
        resp = client.post(
            self._url(),
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "pending_booking_intent" in data
        assert data["pending_booking_intent"] is None

    def test_post_intent_round_trips_in_response(self, client: Client, bot_user: BotUser):
        """Send intent → backend caches → response includes the intent."""
        payload = {
            "pending_booking_intent": {
                "master_id": "11111111-2222-3333-4444-555555555555",
                "service_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "slot_iso": "2026-07-15T14:00:00+03:00",
                "price_quoted": 1800,
                "note": "массаж лица",
                "loyalty_apply": True,
            }
        }
        resp = client.post(
            self._url(),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["pending_booking_intent"] == payload["pending_booking_intent"]

    def test_subsequent_call_no_body_returns_cached(self, client: Client, bot_user: BotUser):
        """Re-mount path: Mini App posts intent first, then re-mount calls
        with no body — backend still returns cached intent."""
        first_payload = {
            "pending_booking_intent": {
                "master_id": "m-first",
                "loyalty_apply": False,
            }
        }
        client.post(
            self._url(),
            data=json.dumps(first_payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )

        # No body — but the cache should still have the intent
        resp = client.post(
            self._url(),
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        assert resp.status_code == 200
        assert resp.json()["pending_booking_intent"] == first_payload["pending_booking_intent"]

    def test_overwrite_via_second_post(self, client: Client, bot_user: BotUser):
        """Second POST with new intent replaces the cached one."""
        client.post(
            self._url(),
            data=json.dumps({"pending_booking_intent": {"master_id": "old"}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        resp = client.post(
            self._url(),
            data=json.dumps({"pending_booking_intent": {"master_id": "new"}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        assert resp.json()["pending_booking_intent"] == {"master_id": "new"}

    def test_explicit_empty_intent_clears_cache(self, client: Client, bot_user: BotUser):
        """Sending `pending_booking_intent: {}` clears any prior cache."""
        client.post(
            self._url(),
            data=json.dumps({"pending_booking_intent": {"master_id": "draft"}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        resp = client.post(
            self._url(),
            data=json.dumps({"pending_booking_intent": {}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        assert resp.json()["pending_booking_intent"] is None

    def test_per_user_isolation_in_endpoint(self, client: Client, tenant: Tenant):
        """User A's cached intent invisible to User B."""
        bu_a = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="55501",
        )
        bu_b = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="55502",
        )

        client.post(
            self._url(),
            data=json.dumps({"pending_booking_intent": {"master_id": "for-a"}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header(bu_a.channel_user_id),
        )
        resp_b = client.post(
            self._url(),
            HTTP_AUTHORIZATION=_init_data_header(bu_b.channel_user_id),
        )
        assert resp_b.json()["pending_booking_intent"] is None

    def test_malformed_json_returns_400(self, client: Client, bot_user: BotUser):
        resp = client.post(
            self._url(),
            data="not valid json {",
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "malformed"

    def test_non_object_body_returns_400(self, client: Client, bot_user: BotUser):
        resp = client.post(
            self._url(),
            data=json.dumps([1, 2, 3]),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "malformed"

    def test_invalid_intent_returns_400(self, client: Client, bot_user: BotUser):
        resp = client.post(
            self._url(),
            data=json.dumps({"pending_booking_intent": {"price_quoted": -5}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_intent"

    def test_unknown_fields_silently_dropped_via_endpoint(self, client: Client, bot_user: BotUser):
        """Frontend may send extra keys; backend tolerates + drops them."""
        resp = client.post(
            self._url(),
            data=json.dumps(
                {
                    "pending_booking_intent": {
                        "master_id": "m1",
                        "evil_field": "ignored",
                    }
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        assert resp.status_code == 200
        intent = resp.json()["pending_booking_intent"]
        assert intent == {"master_id": "m1"}
