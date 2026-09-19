"""F8, фото-половина: ``POST customer/food/scan`` и запись по ``scan_id`` (DRF-2098).

Решение владельца 18.09 (§48 п.4), дословно: «food-diary-v1 покрывает фото
из Mini App». Отдельного согласия на фото нет — ворота те же три, что у
текста (DRF-2091/2093), и это проверяется на этом маршруте, не выводится
из соседнего.

Бот — только пересылка: узел «ни один байт фото не задерживается» ищет
метку из снимка в кэше, в файлах, появившихся за время запроса, и в
INSERT/UPDATE, ушедших в БД, — а не только в логе.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import tempfile
import time as time_module
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.identity.models import BotUser
from apps.integrations.ayla.nutrition_client import (
    FoodNotRecognizedError,
    NutritionAPIError,
    NutritionUnavailableError,
)
from apps.miniapp_api import views
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-2098"  # noqa: S105 — test fixture  # pragma: allowlist secret
#: Уникальная метка внутри «фото»: по ней ищется любой след байтов.
MARKER = b"DRF2098-PHOTO-MARKER-7f3c9a"
PHOTO = b"\xff\xd8\xff\xe0" + b"jpegdata" * 64 + MARKER + b"\xff\xd9"


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _auth(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Клиент"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.NUTRITION_ENABLED = True
    # DRF-2109 — прокси стоит и за cross-border флагом фото (тем же
    # предикатом, что чат); этот файл — про прокси при ОТКРЫТЫХ воротах.
    settings.FOOD_PHOTO_SCAN_ENABLED = True
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def personal_consent():
    with patch(
        "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=True
    ) as m:
        yield m


@pytest.fixture(autouse=True)
def diary_consent():
    with patch("apps.consent.nutrition.diary_is_granted", return_value=True) as m:
        yield m


@pytest.fixture
def bot_user(db, settings) -> BotUser:
    tenant = Tenant.objects.create(slug="food-scan", name="Food Scan", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = tenant.slug
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="92098", display_name="Клиент"
    )


@dataclass
class _Scan:
    scan_id: str = "scan-2098-1"
    dish_name: str = "борщ"
    confidence: float = 0.83
    portion_g: float | None = 300.0
    nutrition: dict[str, Any] | None = field(
        default_factory=lambda: {"calories": 250, "protein_g": 12, "fat_g": 8, "carbs_g": 32}
    )
    provider: str = "test"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Log:
    log_id: str = "01J9FOODPHOTO0000000000AA"
    dish_name: str = "борщ"
    meal_type: str = "lunch"
    calories: float = 250.0


def _patch_client(*, scan=None, log=None):
    client = AsyncMock()
    client.scan_photo = AsyncMock(
        side_effect=scan if isinstance(scan, Exception) else None,
        return_value=None if isinstance(scan, Exception) else (scan or _Scan()),
    )
    client.log_meal = AsyncMock(
        side_effect=log if isinstance(log, Exception) else None,
        return_value=None if isinstance(log, Exception) else (log or _Log()),
    )
    return patch("apps.integrations.ayla.get_nutrition_client", return_value=client), client


def _scan(client: Client, bot_user: BotUser, *, image=PHOTO, mime="image/jpeg", field_name="image"):
    upload = SimpleUploadedFile("photo.jpg", image, content_type=mime)
    return client.post(
        reverse("miniapp_api:customer_food_scan"),
        data={field_name: upload},
        HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id),
    )


def _log(client: Client, bot_user: BotUser, body: dict):
    return client.post(
        reverse("miniapp_api:customer_food_log"),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id),
    )


SCAN_LOG = {
    "scan_id": "scan-2098-1",
    "portion_multiplier": 1.0,
    "meal_type": "lunch",
    "idempotency_key": "k-photo-1",
}


class TestRouteAndGates:
    def test_scan_is_routed(self) -> None:
        assert reverse("miniapp_api:customer_food_scan").endswith("/food/scan")

    def test_diary_off_is_404_before_any_catalog_call(self, client, bot_user, settings) -> None:
        settings.NUTRITION_ENABLED = False
        patcher, fake = _patch_client()
        with patcher:
            resp = _scan(client, bot_user)
        assert resp.status_code == 404 and resp.json()["error"] == "nutrition_disabled"
        fake.scan_photo.assert_not_called()

    def test_no_personal_data_consent_is_403(self, client, bot_user) -> None:
        with patch(
            "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=False
        ):
            patcher, fake = _patch_client()
            with patcher:
                resp = _scan(client, bot_user)
        assert resp.status_code == 403 and resp.json()["error"] == "consent_required"
        fake.scan_photo.assert_not_called()

    def test_no_diary_v1_is_403_by_its_own_name(self, client, bot_user) -> None:
        """D26 = v1 покрывает фото: без строки food-diary-v1 фото не сканируется."""
        with patch("apps.consent.nutrition.diary_is_granted", return_value=False):
            patcher, fake = _patch_client()
            with patcher:
                resp = _scan(client, bot_user)
        assert resp.status_code == 403 and resp.json()["error"] == "food_diary_consent_required"
        fake.scan_photo.assert_not_called()


class TestScanForwardsAndAnswers:
    def test_the_same_bytes_reach_the_catalog_under_the_subject(self, client, bot_user) -> None:
        patcher, fake = _patch_client()
        with patcher:
            resp = _scan(client, bot_user)
        assert resp.status_code == 200, resp.content
        fake.scan_photo.assert_awaited_once()
        kwargs = fake.scan_photo.await_args.kwargs
        assert kwargs["image_bytes"] == PHOTO
        assert kwargs["external_user_id"] == f"bot:max:{bot_user.channel_user_id}"
        assert kwargs["filename"] == "meal.jpg"
        assert resp.json() == {
            "scan_id": "scan-2098-1",
            "dish_name": "борщ",
            "confidence": 0.83,
            "portion_g": 300.0,
            "nutrition": {"calories": 250, "protein_g": 12, "fat_g": 8, "carbs_g": 32},
        }

    def test_missing_file_is_400_and_nothing_is_called(self, client, bot_user) -> None:
        patcher, fake = _patch_client()
        with patcher:
            resp = _scan(client, bot_user, field_name="photo")
        assert resp.status_code == 400 and resp.json()["error"] == "malformed"
        fake.scan_photo.assert_not_called()

    def test_non_image_is_400(self, client, bot_user) -> None:
        patcher, fake = _patch_client()
        with patcher:
            resp = _scan(client, bot_user, image=b"%PDF-1.4 not a photo", mime="application/pdf")
        assert resp.status_code == 400 and resp.json()["error"] == "unsupported_media_type"
        fake.scan_photo.assert_not_called()

    def test_over_the_chat_limit_is_413_and_nothing_is_called(self, client, bot_user) -> None:
        from apps.channels.max.photo import MAX_PHOTO_BYTES

        patcher, fake = _patch_client()
        with patcher:
            resp = _scan(client, bot_user, image=b"\xff" * (MAX_PHOTO_BYTES + 1))
        assert resp.status_code == 413 and resp.json()["error"] == "photo_too_large"
        fake.scan_photo.assert_not_called()

    def test_the_limit_is_the_chat_constant_not_a_copy(self) -> None:
        from apps.channels.max import photo

        assert views._food_scan_max_bytes() is photo.MAX_PHOTO_BYTES
        with patch.object(photo, "MAX_PHOTO_BYTES", 5):
            assert views._food_scan_max_bytes() == 5

    def test_not_recognized_is_400_by_its_own_name(self, client, bot_user) -> None:
        patcher, _ = _patch_client(scan=FoodNotRecognizedError("nope"))
        with patcher:
            resp = _scan(client, bot_user)
        assert resp.status_code == 400 and resp.json()["error"] == "food_not_recognized"

    def test_catalog_down_is_503(self, client, bot_user) -> None:
        patcher, _ = _patch_client(scan=NutritionUnavailableError("circuit_open"))
        with patcher:
            resp = _scan(client, bot_user)
        assert resp.status_code == 503 and resp.json()["error"] == "nutrition_unavailable"

    def test_other_catalog_refusal_is_400(self, client, bot_user) -> None:
        patcher, _ = _patch_client(scan=NutritionAPIError("bad"))
        with patcher:
            resp = _scan(client, bot_user)
        assert resp.status_code == 400 and resp.json()["error"] == "ayla_bad_request"


class TestNoByteOfThePhotoStays:
    def test_nothing_of_the_photo_is_kept_or_logged(
        self, client, bot_user, settings, tmp_path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Пересылка, не хранение: метка снимка не остаётся нигде в боте."""
        settings.MEDIA_ROOT = str(tmp_path / "media")
        settings.FILE_UPLOAD_TEMP_DIR = str(tmp_path / "uploads")
        os.makedirs(settings.FILE_UPLOAD_TEMP_DIR, exist_ok=True)
        caplog.set_level(logging.DEBUG)
        before = time_module.time()
        decoy = Path(settings.FILE_UPLOAD_TEMP_DIR) / "decoy-2098.bin"
        decoy.write_bytes(b"decoy " + MARKER)

        patcher, fake = _patch_client()
        with patcher, CaptureQueriesContext(connection) as queries:
            resp = _scan(client, bot_user)
        assert resp.status_code == 200
        fake.scan_photo.assert_awaited_once()

        # 1. Не в логе — ни байты, ни имя файла, ни метка.
        rendered = "\n".join(r.getMessage() for r in caplog.records)
        assert "food_scan_ma.scanned" in rendered  # след есть — есть чему не утечь рядом
        assert MARKER.decode() not in rendered
        assert "photo.jpg" not in rendered
        assert "jpegdata" not in rendered

        # 2. Не в БД — за запрос не ушло ни одного INSERT; UPDATE — только
        #    служебные по строке человека (last_seen), и без метки.
        writes = [
            q["sql"]
            for q in queries.captured_queries
            if q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE"))
        ]
        assert not [w for w in writes if w.lstrip().upper().startswith("INSERT")], writes
        assert not [w for w in writes if MARKER.decode() in w], writes

        # 3. Не в кэше — ни одно значение не несёт метку.
        raw_cache = getattr(cache, "_cache", None)
        if isinstance(raw_cache, dict):
            for value in raw_cache.values():
                assert MARKER not in (value if isinstance(value, bytes) else repr(value).encode())

        # 4. Не в файлах — ни в MEDIA_ROOT, ни во временном каталоге загрузок,
        #    ни среди файлов системного tmp, появившихся за время запроса.
        #    Сама проверка откалибрована подсадкой: файл-приманка с меткой
        #    положен ДО запроса, и обход обязан его увидеть — иначе «метки
        #    нигде нет» было бы правдой и про слепой обход.
        hits: list[Path] = []
        for root in (
            Path(settings.MEDIA_ROOT),
            Path(settings.FILE_UPLOAD_TEMP_DIR),
            Path(tempfile.gettempdir()),
        ):
            if not root.exists():
                continue
            for path in root.rglob("*"):
                try:
                    if path.is_file() and path.stat().st_mtime >= before - 1:
                        if MARKER in path.read_bytes():
                            hits.append(path)
                except (OSError, PermissionError):
                    continue
        # tmp_path лежит внутри системного tmp — один и тот же файл может
        # встретиться в двух обходах; считаем файлы, не пути обхода.
        assert {p.resolve() for p in hits} == {decoy.resolve()}, hits


class TestLogByScan:
    def test_scan_id_goes_to_log_meal_with_the_photo_key_and_no_dish_name(
        self, client, bot_user
    ) -> None:
        patcher, fake = _patch_client()
        with patcher:
            resp = _log(client, bot_user, SCAN_LOG)
        assert resp.status_code == 201, resp.content
        kwargs = fake.log_meal.await_args.kwargs
        assert kwargs["scan_id"] == "scan-2098-1"
        assert kwargs["portion_multiplier"] == 1.0
        assert kwargs["meal_type"] == "lunch"
        assert (
            kwargs["idempotency_key"]
            == f"food-photo-ma:bot:max:{bot_user.channel_user_id}:k-photo-1"
        )
        assert "dish_name" not in kwargs
        # DRF-2110 — §136: подтверждённая как есть фото-запись названа своим
        # кодом, а не NULL («до §136»).
        assert kwargs["entry_origin"] == "photo_estimated_confirmed"
        assert resp.json() == {
            "log_id": "01J9FOODPHOTO0000000000AA",
            "dish_name": "борщ",
            "meal_type": "lunch",
            "calories": 250.0,
            "entry_origin": "photo_estimated_confirmed",
        }

    def test_a_corrected_portion_is_photo_user_corrected(self, client, bot_user) -> None:
        patcher, fake = _patch_client()
        with patcher:
            resp = _log(client, bot_user, {**SCAN_LOG, "portion_multiplier": 1.5})
        assert resp.status_code == 201
        kwargs = fake.log_meal.await_args.kwargs
        assert kwargs["portion_multiplier"] == 1.5
        assert kwargs["entry_origin"] == "photo_user_corrected"
        assert resp.json()["entry_origin"] == "photo_user_corrected"

    def test_a_rename_keeps_the_scan_id_next_to_the_new_name(self, client, bot_user) -> None:
        """Провенанс фото (§136 photo_*) не теряется при переименовании."""
        patcher, fake = _patch_client()
        with patcher:
            resp = _log(client, bot_user, {**SCAN_LOG, "dish_name": "свекольник"})
        assert resp.status_code == 201
        kwargs = fake.log_meal.await_args.kwargs
        assert kwargs["scan_id"] == "scan-2098-1"
        assert kwargs["dish_name"] == "свекольник"
        assert kwargs["entry_origin"] == "photo_user_corrected"

    def test_no_meal_type_falls_back_to_the_unnamed_type(self, client, bot_user) -> None:
        from apps.skills.food_clarify.text_entry import MEAL_TYPE_UNNAMED

        patcher, fake = _patch_client()
        with patcher:
            body = {k: v for k, v in SCAN_LOG.items() if k != "meal_type"}
            assert _log(client, bot_user, body).status_code == 201
        assert fake.log_meal.await_args.kwargs["meal_type"] == MEAL_TYPE_UNNAMED

    @pytest.mark.parametrize(
        "bad",
        [
            {"portion_multiplier": 0.1},
            {"portion_multiplier": True},
            {"meal_type": "brunch"},
            {"dish_name": ""},
            {"idempotency_key": ""},
            {"scan_id": ""},
        ],
        ids=[
            "multiplier-low",
            "multiplier-bool",
            "meal-type",
            "empty-dish",
            "no-key",
            "empty-scan",
        ],
    )
    def test_malformed_scan_bodies_are_400_and_nothing_is_written(
        self, client, bot_user, bad
    ) -> None:
        patcher, fake = _patch_client()
        with patcher:
            resp = _log(client, bot_user, {**SCAN_LOG, **bad})
        assert resp.status_code == 400 and resp.json()["error"] == "malformed"
        fake.log_meal.assert_not_called()

    def test_the_text_branch_is_untouched(self, client, bot_user) -> None:
        """Регрессия DRF-2091: тело без scan_id идёт прежней тропой."""
        patcher, fake = _patch_client()
        with patcher:
            resp = _log(
                client,
                bot_user,
                {
                    "dish_name": "борщ",
                    "portion_g": 250,
                    "corrected": False,
                    "idempotency_key": "k-1",
                },
            )
        assert resp.status_code == 201
        kwargs = fake.log_meal.await_args.kwargs
        assert kwargs["dish_name"] == "борщ" and "scan_id" not in kwargs
        assert kwargs["idempotency_key"].startswith("food-text-ma:")
        assert kwargs["entry_origin"] == "text_estimated_confirmed"

    def test_scan_log_is_behind_the_same_three_gates(self, client, bot_user) -> None:
        with patch("apps.consent.nutrition.diary_is_granted", return_value=False):
            patcher, fake = _patch_client()
            with patcher:
                resp = _log(client, bot_user, SCAN_LOG)
        assert resp.status_code == 403 and resp.json()["error"] == "food_diary_consent_required"
        fake.log_meal.assert_not_called()
