"""Tests for the wellness water WRITE path (DRF-1402).

    POST   /customer/wellness/water              → add_water on Ayla
    DELETE /customer/wellness/water/{entry_id}   → undo_water on Ayla

The read half is covered by ``test_wellness_today.py``; this file is its
mirror image. Auth fixtures are duplicated from there deliberately —
the two suites must be able to fail independently.

No calendar constants: water is day-bound, so every timestamp here is
derived from ``timezone.now()``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

from django.test import Client
from django.urls import reverse
from django.utils import timezone

import pytest

from apps.identity.models import BotUser
from apps.integrations.ayla.nutrition_client import (
    NutritionAPIError,
    NutritionUnavailableError,
)
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-water"  # noqa: S105 — test fixture  # pragma: allowlist secret


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Анна"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(slug="water-test", name="Water Test", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "water-test"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="92001",
        display_name="Анна",
        client_name="Анна К.",
    )


@dataclass
class _FakeEntry:
    entry_id: str = "entry-abc"
    ml: int = 250
    water_ml: int = 250
    kcal: int = 0
    milestone_text: Any = None
    today_total_ml: int = 1250
    today_norm_ml: int = 2000
    alcohol_recovery_hint: bool = False
    raw: dict = field(default_factory=dict)


def _post_url() -> str:
    return reverse("miniapp_api:customer_wellness_water")


def _undo_url(entry_id: str) -> str:
    return reverse("miniapp_api:customer_wellness_water_undo", args=[entry_id])


def _patch_client(*, add=None, undo=None):
    """Patch get_nutrition_client with async stubs for add/undo_water."""
    client = AsyncMock()
    if isinstance(add, Exception):
        client.add_water = AsyncMock(side_effect=add)
    else:
        client.add_water = AsyncMock(return_value=add)
    if isinstance(undo, Exception):
        client.undo_water = AsyncMock(side_effect=undo)
    else:
        client.undo_water = AsyncMock(return_value=undo)
    return patch("apps.integrations.ayla.get_nutrition_client", return_value=client), client


def _post(client: Client, bot_user: BotUser, body: dict):
    return client.post(
        _post_url(),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
    )


class TestAddWaterHappyPath:
    def test_glass_reaches_ayla_and_response_uses_dashboard_units(
        self, client: Client, bot_user: BotUser
    ):
        patcher, fake = _patch_client(add=_FakeEntry())
        with patcher:
            resp = _post(client, bot_user, {"ml": 250})

        assert resp.status_code == 201
        fake.add_water.assert_awaited_once()
        kwargs = fake.add_water.await_args.kwargs
        assert kwargs["ml"] == 250
        assert kwargs["external_user_id"].endswith("92001")

        data = resp.json()
        assert data["entry_id"] == "entry-abc"
        assert data["today_total_ml"] == 1250
        # Same 250 ml glass + same default target as GET /wellness/today.
        assert data["water_glasses_eaten"] == 5
        assert data["water_glasses_target"] == 8

    def test_tap_time_and_idempotency_key_are_forwarded(self, client: Client, bot_user: BotUser):
        # A queued glass flushed later must keep its own timestamp.
        tap_ts = (timezone.now() - timezone.timedelta(hours=3)).isoformat()
        patcher, fake = _patch_client(add=_FakeEntry())
        with patcher:
            resp = _post(
                client,
                bot_user,
                {"ml": 250, "ts": tap_ts, "idempotency_key": "water-1-abc"},
            )

        assert resp.status_code == 201
        kwargs = fake.add_water.await_args.kwargs
        assert kwargs["ts"] == tap_ts
        assert kwargs["idempotency_key"] == "water-1-abc"

    def test_zero_norm_falls_back_to_the_default_target(self, client: Client, bot_user: BotUser):
        # Anketa skipped → Ayla reports norm 0; the read endpoint shows 8
        # glasses, so the write endpoint must not answer 0.
        patcher, _ = _patch_client(add=_FakeEntry(today_norm_ml=0))
        with patcher:
            resp = _post(client, bot_user, {"ml": 250})
        assert resp.json()["water_glasses_target"] == 8


class TestAddWaterValidation:
    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"ml": "250"},
            {"ml": True},
            {"ml": 0},
            {"ml": -250},
            {"ml": 50_000},
            {"ml": 250, "ts": "not-a-date"},
            {"ml": 250, "ts": 17},
            {"ml": 250, "beverage_slug": 5},
            {"ml": 250, "idempotency_key": "bad key\nX-Injected: 1"},
            {"ml": 250, "idempotency_key": "x" * 201},
        ],
    )
    def test_bad_bodies_are_rejected_before_ayla(
        self, client: Client, bot_user: BotUser, body: dict
    ):
        patcher, fake = _patch_client(add=_FakeEntry())
        with patcher:
            resp = _post(client, bot_user, body)
        assert resp.status_code == 400
        fake.add_water.assert_not_awaited()

    def test_non_json_body_rejected(self, client: Client, bot_user: BotUser):
        resp = client.post(
            _post_url(),
            data="ml=250",
            content_type="application/x-www-form-urlencoded",
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        assert resp.status_code == 400

    def test_unauthenticated_write_is_refused(self, client: Client, db):
        # No Authorization header -> require_init_data answers 400
        # ("malformed") before the view body, same as every other
        # customer endpoint. What matters is that nothing reaches Ayla.
        patcher, fake = _patch_client(add=_FakeEntry())
        with patcher:
            resp = client.post(
                _post_url(),
                data=json.dumps({"ml": 250}),
                content_type="application/json",
            )
        assert resp.status_code == 400
        fake.add_water.assert_not_awaited()

    def test_get_not_allowed(self, client: Client, bot_user: BotUser):
        resp = client.get(
            _post_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
        )
        assert resp.status_code == 405


class TestAddWaterFailureMapping:
    def test_outage_maps_to_502_so_the_queue_retries(self, client: Client, bot_user: BotUser):
        patcher, _ = _patch_client(add=NutritionUnavailableError("circuit_open"))
        with patcher:
            resp = _post(client, bot_user, {"ml": 250})
        assert resp.status_code == 502
        assert resp.json()["error"] == "ayla_unavailable"

    def test_ayla_rejection_maps_to_400_so_the_queue_drops_it(
        self, client: Client, bot_user: BotUser
    ):
        patcher, _ = _patch_client(add=NutritionAPIError("http_400_bad_slug"))
        with patcher:
            resp = _post(client, bot_user, {"ml": 250})
        assert resp.status_code == 400
        assert resp.json()["error"] == "ayla_bad_request"


class TestUndoWater:
    def test_successful_undo_returns_204(self, client: Client, bot_user: BotUser):
        patcher, fake = _patch_client(undo=True)
        with patcher:
            resp = client.delete(
                _undo_url("entry-abc"),
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
        assert resp.status_code == 204
        assert fake.undo_water.await_args.kwargs["entry_id"] == "entry-abc"

    def test_closed_window_returns_404_not_a_polite_204(self, client: Client, bot_user: BotUser):
        # The glass is still counted on Ayla — saying «removed» would be
        # the same lie the old flush stub told.
        patcher, _ = _patch_client(undo=False)
        with patcher:
            resp = client.delete(
                _undo_url("entry-abc"),
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_undoable"

    def test_outage_maps_to_502(self, client: Client, bot_user: BotUser):
        patcher, _ = _patch_client(undo=NutritionUnavailableError("http_503"))
        with patcher:
            resp = client.delete(
                _undo_url("entry-abc"),
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
        assert resp.status_code == 502

    def test_unauthenticated_undo_is_refused(self, client: Client, db):
        patcher, fake = _patch_client(undo=True)
        with patcher:
            resp = client.delete(_undo_url("entry-abc"))
        assert resp.status_code == 400
        fake.undo_water.assert_not_awaited()
