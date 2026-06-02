"""Endpoint tests for food scanner Веха 2.

Covers the 6 endpoints from
``docs/architecture/food-scanner-api-contract.md``:

* ``POST/GET /customer/food/consent``
* ``POST /customer/food/scan``  (cross-border, photo-gate)
* ``POST /customer/food/log``
* ``GET /customer/food/diary``
* ``GET /customer/health-flags``

Each endpoint is pinned on the two-gate + consent enforcement contract
(``settings.NUTRITION_ENABLED`` / ``settings.FOOD_PHOTO_SCAN_ENABLED`` /
``BotUser.food_scanner_consent_at``), the ED-mode redaction contract,
and the failure-slug taxonomy.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from datetime import datetime, timezone as dt_timezone
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.integrations.ayla.nutrition_client import (
    FoodLogResponse,
    FoodNotRecognizedError,
    NutritionUnavailableError,
    ProfileResponse,
    ScanResponse,
    SummaryResponse,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-food"  # noqa: S105 — test fixture  # pragma: allowlist secret


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
    # Default to gates ON so the happy paths can run; refusal-path tests
    # flip them off explicitly.
    settings.NUTRITION_ENABLED = True
    settings.FOOD_PHOTO_SCAN_ENABLED = True


@pytest.fixture
def tenant(settings) -> Tenant:
    t = Tenant.objects.create(slug="food-test", name="Food Test", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "food-test"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="93001",
        display_name="Анна",
        food_scanner_consent_at=datetime.now(dt_timezone.utc),
    )


def _hdr(bot_user: BotUser) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": _init_data_header(bot_user.channel_user_id)}


# ─── helpers — Ayla response stubs ─────────────────────────────────────────


def _scan_resp() -> ScanResponse:
    return ScanResponse(
        scan_id="scan-1",
        dish_name="Борщ",
        confidence=0.86,
        portion_g=320,
        nutrition={"calories": 250, "protein_g": 12, "fat_g": 8, "carbs_g": 32},
        provider="openai-gpt-4o",
        raw={},
    )


def _log_resp() -> FoodLogResponse:
    return FoodLogResponse(
        log_id="log-1",
        dish_name="Борщ",
        meal_type="lunch",
        calories=250.0,
        raw={},
    )


def _summary_resp() -> SummaryResponse:
    return SummaryResponse(
        date="2026-06-02",
        calories_total=1240.0,
        calories_goal=2100,
        protein_g=65.4,
        fat_g=40.1,
        carbs_g=120.9,
        entries=[
            {
                "log_id": "log-1",
                "dish_name": "Овсянка",
                "meal_type": "breakfast",
                "logged_at": "2026-06-02T07:25:00Z",
                "nutrition": {"calories": 320, "protein_g": 9, "fat_g": 7, "carbs_g": 56},
            }
        ],
        raw={},
    )


def _profile(*, ed: bool = False, override: str | None = None) -> ProfileResponse:
    flags: dict = {}
    if ed and override is None:
        flags["eating_disorder"] = True
    return ProfileResponse(
        gender="female",
        age=30,
        height_cm=170,
        weight_kg=60,
        goal="maintain",
        daily_kcal=2000,
        protein_g=100,
        fat_g=70,
        carbs_g=250,
        water_ml=2000,
        bmr=1400,
        health_flags=flags,
        disclaimer_acked=None,
        goal_overridden_by=override,
    )


def _patch_nutrition(**methods) -> object:
    """Patch get_nutrition_client to return an AsyncMock with the given methods.

    Each kwarg value is either a return value or an Exception instance.
    """

    client = AsyncMock()
    for name, val in methods.items():
        if isinstance(val, Exception):
            setattr(client, name, AsyncMock(side_effect=val))
        else:
            setattr(client, name, AsyncMock(return_value=val))
    return patch("apps.integrations.ayla.get_nutrition_client", return_value=client)


# ─── /customer/food/consent ────────────────────────────────────────────────


class TestConsentEndpoint:
    def test_get_returns_existing_timestamp(self, client: Client, bot_user: BotUser):
        url = reverse("miniapp_api:customer_food_consent")
        resp = client.get(url, **_hdr(bot_user))
        assert resp.status_code == 200
        body = resp.json()
        assert body["accepted_at"] is not None

    def test_get_returns_null_when_not_consented(self, client: Client, bot_user: BotUser):
        bot_user.food_scanner_consent_at = None
        bot_user.save(update_fields=["food_scanner_consent_at"])
        resp = client.get(reverse("miniapp_api:customer_food_consent"), **_hdr(bot_user))
        assert resp.json() == {"accepted_at": None}

    def test_post_records_consent(self, client: Client, bot_user: BotUser):
        bot_user.food_scanner_consent_at = None
        bot_user.save(update_fields=["food_scanner_consent_at"])
        resp = client.post(
            reverse("miniapp_api:customer_food_consent"),
            data=json.dumps({"accepted": True}),
            content_type="application/json",
            **_hdr(bot_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["accepted_at"] is not None
        bot_user.refresh_from_db()
        assert bot_user.food_scanner_consent_at is not None

    def test_post_is_idempotent(self, client: Client, bot_user: BotUser):
        # bot_user already has a consent_at from fixture.
        first_ts = bot_user.food_scanner_consent_at.isoformat()
        resp = client.post(
            reverse("miniapp_api:customer_food_consent"),
            data=json.dumps({"accepted": True}),
            content_type="application/json",
            **_hdr(bot_user),
        )
        assert resp.status_code == 200
        # Original timestamp preserved.
        assert resp.json()["accepted_at"] == first_ts

    def test_post_refusal_returns_400(self, client: Client, bot_user: BotUser):
        resp = client.post(
            reverse("miniapp_api:customer_food_consent"),
            data=json.dumps({"accepted": False}),
            content_type="application/json",
            **_hdr(bot_user),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"

    def test_post_malformed_returns_400(self, client: Client, bot_user: BotUser):
        resp = client.post(
            reverse("miniapp_api:customer_food_consent"),
            data="not-json",
            content_type="application/json",
            **_hdr(bot_user),
        )
        assert resp.status_code == 400

    def test_consent_endpoint_bypasses_nutrition_gate(
        self, client: Client, bot_user: BotUser, settings
    ):
        # Both flags off — consent endpoint must still work so the F0
        # gate can register the user before anything else.
        settings.NUTRITION_ENABLED = False
        bot_user.food_scanner_consent_at = None
        bot_user.save(update_fields=["food_scanner_consent_at"])
        resp = client.post(
            reverse("miniapp_api:customer_food_consent"),
            data=json.dumps({"accepted": True}),
            content_type="application/json",
            **_hdr(bot_user),
        )
        assert resp.status_code == 200


# ─── /customer/food/scan ────────────────────────────────────────────────────


def _multipart_image(content: bytes = b"fakejpegdata", *, mime: str = "image/jpeg"):
    return SimpleUploadedFile("meal.jpg", content, content_type=mime)


class TestScanEndpoint:
    def test_nutrition_off_returns_503(self, client: Client, bot_user: BotUser, settings):
        settings.NUTRITION_ENABLED = False
        resp = client.post(
            reverse("miniapp_api:customer_food_scan"),
            data={"image": _multipart_image()},
            **_hdr(bot_user),
        )
        assert resp.status_code == 503
        assert resp.json()["error"] == "nutrition_disabled"

    def test_photo_scan_off_returns_503(self, client: Client, bot_user: BotUser, settings):
        settings.FOOD_PHOTO_SCAN_ENABLED = False
        resp = client.post(
            reverse("miniapp_api:customer_food_scan"),
            data={"image": _multipart_image()},
            **_hdr(bot_user),
        )
        assert resp.status_code == 503
        assert resp.json()["error"] == "photo_scan_disabled"

    def test_consent_missing_returns_428(self, client: Client, bot_user: BotUser):
        bot_user.food_scanner_consent_at = None
        bot_user.save(update_fields=["food_scanner_consent_at"])
        resp = client.post(
            reverse("miniapp_api:customer_food_scan"),
            data={"image": _multipart_image()},
            **_hdr(bot_user),
        )
        assert resp.status_code == 428
        assert resp.json()["error"] == "consent_required"

    def test_missing_image_returns_400(self, client: Client, bot_user: BotUser):
        resp = client.post(
            reverse("miniapp_api:customer_food_scan"),
            data={},
            **_hdr(bot_user),
        )
        assert resp.status_code == 400

    def test_wrong_mime_returns_415(self, client: Client, bot_user: BotUser):
        resp = client.post(
            reverse("miniapp_api:customer_food_scan"),
            data={"image": _multipart_image(mime="application/pdf")},
            **_hdr(bot_user),
        )
        assert resp.status_code == 415
        assert resp.json()["error"] == "unsupported_media_type"

    def test_happy_path_ed_off(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(scan_photo=_scan_resp(), get_profile=_profile(ed=False)):
            resp = client.post(
                reverse("miniapp_api:customer_food_scan"),
                data={"image": _multipart_image()},
                **_hdr(bot_user),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ed_mode"] is False
        assert body["nutrition"] == {
            "calories": 250,
            "protein_g": 12,
            "fat_g": 8,
            "carbs_g": 32,
        }
        assert body["dish_name"] == "Борщ"
        assert body["scan_id"] == "scan-1"

    def test_happy_path_ed_on(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(scan_photo=_scan_resp(), get_profile=_profile(ed=True)):
            resp = client.post(
                reverse("miniapp_api:customer_food_scan"),
                data={"image": _multipart_image()},
                **_hdr(bot_user),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ed_mode"] is True
        assert body["nutrition"] is None
        # Neutral fields preserved.
        assert body["dish_name"] == "Борщ"

    def test_food_not_recognized_returns_422(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(
            scan_photo=FoodNotRecognizedError("blurry"),
            get_profile=_profile(),
        ):
            resp = client.post(
                reverse("miniapp_api:customer_food_scan"),
                data={"image": _multipart_image()},
                **_hdr(bot_user),
            )
        assert resp.status_code == 422
        assert resp.json()["error"] == "food_not_recognized"

    def test_ayla_unavailable_returns_503(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(
            scan_photo=NutritionUnavailableError("circuit_open"),
            get_profile=_profile(),
        ):
            resp = client.post(
                reverse("miniapp_api:customer_food_scan"),
                data={"image": _multipart_image()},
                **_hdr(bot_user),
            )
        assert resp.status_code == 503
        assert resp.json()["error"] == "ayla_unavailable"

    def test_profile_failure_defaults_to_ed_mode(self, client: Client, bot_user: BotUser):
        # Fail-closed: profile read failure → render with ED hiding.
        with _patch_nutrition(
            scan_photo=_scan_resp(),
            get_profile=NutritionUnavailableError("profile_down"),
        ):
            resp = client.post(
                reverse("miniapp_api:customer_food_scan"),
                data={"image": _multipart_image()},
                **_hdr(bot_user),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ed_mode"] is True
        assert body["nutrition"] is None


# ─── /customer/food/log ────────────────────────────────────────────────────


class TestLogEndpoint:
    def test_scan_confirmation_happy(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(log_meal=_log_resp(), get_profile=_profile(ed=False)):
            resp = client.post(
                reverse("miniapp_api:customer_food_log"),
                data=json.dumps({"scan_id": "scan-1", "meal_type": "lunch"}),
                content_type="application/json",
                **_hdr(bot_user),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["log_id"] == "log-1"
        assert body["calories"] == 250.0
        assert body["ed_mode"] is False

    def test_manual_entry_happy(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(log_meal=_log_resp(), get_profile=_profile(ed=False)):
            resp = client.post(
                reverse("miniapp_api:customer_food_log"),
                data=json.dumps({"dish_name": "Овсянка", "meal_type": "breakfast"}),
                content_type="application/json",
                **_hdr(bot_user),
            )
        assert resp.status_code == 200
        assert resp.json()["ed_mode"] is False

    def test_ed_nulls_calories(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(log_meal=_log_resp(), get_profile=_profile(ed=True)):
            resp = client.post(
                reverse("miniapp_api:customer_food_log"),
                data=json.dumps({"scan_id": "scan-1", "meal_type": "lunch"}),
                content_type="application/json",
                **_hdr(bot_user),
            )
        body = resp.json()
        assert body["calories"] is None
        assert body["ed_mode"] is True

    def test_missing_both_returns_400(self, client: Client, bot_user: BotUser):
        resp = client.post(
            reverse("miniapp_api:customer_food_log"),
            data=json.dumps({"meal_type": "lunch"}),
            content_type="application/json",
            **_hdr(bot_user),
        )
        assert resp.status_code == 400

    def test_invalid_meal_type_returns_400(self, client: Client, bot_user: BotUser):
        resp = client.post(
            reverse("miniapp_api:customer_food_log"),
            data=json.dumps({"scan_id": "x", "meal_type": "fourth_meal"}),
            content_type="application/json",
            **_hdr(bot_user),
        )
        assert resp.status_code == 400

    def test_consent_missing_returns_428(self, client: Client, bot_user: BotUser):
        bot_user.food_scanner_consent_at = None
        bot_user.save(update_fields=["food_scanner_consent_at"])
        resp = client.post(
            reverse("miniapp_api:customer_food_log"),
            data=json.dumps({"scan_id": "x", "meal_type": "lunch"}),
            content_type="application/json",
            **_hdr(bot_user),
        )
        assert resp.status_code == 428


# ─── /customer/food/diary ──────────────────────────────────────────────────


class TestDiaryEndpoint:
    def test_default_date_happy(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(daily_summary=_summary_resp(), get_profile=_profile(ed=False)):
            resp = client.get(reverse("miniapp_api:customer_food_diary"), **_hdr(bot_user))
        assert resp.status_code == 200
        body = resp.json()
        assert body["calories_total"] == 1240
        assert body["calories_goal"] == 2100
        assert body["protein_g"] == 65
        assert len(body["entries"]) == 1
        assert body["entries"][0]["nutrition"] is not None
        assert body["ed_mode"] is False

    def test_explicit_date_happy(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(daily_summary=_summary_resp(), get_profile=_profile(ed=False)):
            resp = client.get(
                reverse("miniapp_api:customer_food_diary") + "?date=2026-06-02",
                **_hdr(bot_user),
            )
        assert resp.status_code == 200

    def test_invalid_date_returns_400(self, client: Client, bot_user: BotUser):
        resp = client.get(
            reverse("miniapp_api:customer_food_diary") + "?date=invalid",
            **_hdr(bot_user),
        )
        assert resp.status_code == 400

    def test_ed_nulls_numbers_preserves_entries(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(daily_summary=_summary_resp(), get_profile=_profile(ed=True)):
            resp = client.get(reverse("miniapp_api:customer_food_diary"), **_hdr(bot_user))
        body = resp.json()
        assert body["ed_mode"] is True
        assert body["calories_total"] is None
        assert body["calories_goal"] is None
        assert body["entries"][0]["nutrition"] is None
        # Dish + timestamp preserved.
        assert body["entries"][0]["dish_name"] == "Овсянка"
        assert body["entries"][0]["logged_at"] == "2026-06-02T07:25:00Z"

    def test_ayla_unavailable_returns_503(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(
            daily_summary=NutritionUnavailableError("down"),
            get_profile=_profile(),
        ):
            resp = client.get(reverse("miniapp_api:customer_food_diary"), **_hdr(bot_user))
        assert resp.status_code == 503


# ─── /customer/health-flags ────────────────────────────────────────────────


class TestHealthFlagsEndpoint:
    def test_no_flags(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(get_profile=_profile()):
            resp = client.get(reverse("miniapp_api:customer_health_flags"), **_hdr(bot_user))
        assert resp.status_code == 200
        assert resp.json() == {
            "eating_disorder": False,
            "pregnancy": False,
            "breastfeeding": False,
            "ed_mode": False,
        }

    def test_ed_via_health_flag(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(get_profile=_profile(ed=True)):
            resp = client.get(reverse("miniapp_api:customer_health_flags"), **_hdr(bot_user))
        body = resp.json()
        assert body["eating_disorder"] is True
        assert body["ed_mode"] is True

    def test_ed_via_override(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(get_profile=_profile(override="eating_disorder")):
            resp = client.get(reverse("miniapp_api:customer_health_flags"), **_hdr(bot_user))
        assert resp.json()["ed_mode"] is True

    def test_photo_gate_off_does_not_block(self, client: Client, bot_user: BotUser, settings):
        # health-flags does NOT require FOOD_PHOTO_SCAN_ENABLED.
        settings.FOOD_PHOTO_SCAN_ENABLED = False
        with _patch_nutrition(get_profile=_profile()):
            resp = client.get(reverse("miniapp_api:customer_health_flags"), **_hdr(bot_user))
        assert resp.status_code == 200

    def test_ayla_unavailable_returns_503(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(get_profile=NutritionUnavailableError("circuit")):
            resp = client.get(reverse("miniapp_api:customer_health_flags"), **_hdr(bot_user))
        assert resp.status_code == 503

    def test_nutrition_gate_blocks(self, client: Client, bot_user: BotUser, settings):
        settings.NUTRITION_ENABLED = False
        resp = client.get(reverse("miniapp_api:customer_health_flags"), **_hdr(bot_user))
        assert resp.status_code == 503
        assert resp.json()["error"] == "nutrition_disabled"
