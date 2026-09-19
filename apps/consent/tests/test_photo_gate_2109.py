"""Ворота фото — один предикат на чат и прокси Mini App (DRF-2109).

Замер модуля 8 (19.09): прокси ``customer_food_scan`` (DRF-2098) стоял за
тремя воротами дневника и не спрашивал ``FOOD_PHOTO_SCAN_ENABLED`` — при
выключенном флаге фото из чата отказывало, из Mini App уходило в
распознаватель. Здесь: (1) прокси отказывает тем же флагом, 404
``photo_scan_disabled``, до чтения файла и без вызова каталога; (2) чат
отвечает как раньше (текст и ``reply_kind`` прежние) — через предикат;
(3) перепись по роли: каждый модуль, зовущий ``scan_photo`` клиента
питания, спрашивает ``photo_scan_refusal``; (4) стража на сторож —
подсаженный модуль-строка с вызовом без предиката — красный.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import json
import time as time_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.consent.photo_gate import PHOTO_SCAN_DISABLED, photo_scan_refusal
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

REPO_ROOT = Path(__file__).resolve().parents[3]
BOT_TOKEN = "test-bot-token-2109"  # noqa: S105 — test fixture  # pragma: allowlist secret


def _auth(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Клиент"}),
        "auth_date": str(int(time_module.time())),
    }
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return f"MaxInitData {urlencode({**params, 'hash': digest})}"


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.NUTRITION_ENABLED = True
    settings.FOOD_PHOTO_SCAN_ENABLED = True


@pytest.fixture(autouse=True)
def _consents_open():
    with (
        patch(
            "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=True
        ),
        patch("apps.consent.nutrition.diary_is_granted", return_value=True),
    ):
        yield


@pytest.fixture
def bot_user(db, settings) -> BotUser:
    tenant = Tenant.objects.create(slug="photo-2109", name="Photo 2109", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = tenant.slug
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="92109", display_name="Клиент"
    )


def _scan(client: Client, bot_user: BotUser):
    upload = SimpleUploadedFile("photo.jpg", b"\xff\xd8jpeg", content_type="image/jpeg")
    return client.post(
        reverse("miniapp_api:customer_food_scan"),
        data={"image": upload},
        HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id),
    )


class TestThePredicate:
    def test_off_is_a_named_refusal_and_on_is_none(self, settings) -> None:
        settings.FOOD_PHOTO_SCAN_ENABLED = False
        assert photo_scan_refusal() == PHOTO_SCAN_DISABLED
        settings.FOOD_PHOTO_SCAN_ENABLED = True
        assert photo_scan_refusal() is None


class TestTheMiniAppProxy:
    def test_flag_off_refuses_before_the_catalog_is_called(
        self, client, bot_user, settings
    ) -> None:
        settings.FOOD_PHOTO_SCAN_ENABLED = False
        fake = AsyncMock()
        with patch("apps.integrations.ayla.get_nutrition_client", return_value=fake):
            resp = _scan(client, bot_user)
        assert resp.status_code == 404
        assert resp.json()["error"] == PHOTO_SCAN_DISABLED
        fake.scan_photo.assert_not_called()

    def test_flag_on_lets_the_photo_through(self, client, bot_user) -> None:
        """Положительная пара на тех же данных: ворота — не стена."""
        fake = AsyncMock()
        fake.scan_photo = AsyncMock(
            return_value=SimpleNamespace(
                scan_id="s-1", dish_name="борщ", confidence=0.9, portion_g=300.0, nutrition=None
            )
        )
        with patch("apps.integrations.ayla.get_nutrition_client", return_value=fake):
            resp = _scan(client, bot_user)
        assert resp.status_code == 200, resp.content
        fake.scan_photo.assert_awaited_once()

    def test_the_diary_gates_still_answer_first(self, client, bot_user, settings) -> None:
        """Порядок как в чате: питание → согласия → фото; выключенный дневник — 404 своим слагом."""
        settings.NUTRITION_ENABLED = False
        settings.FOOD_PHOTO_SCAN_ENABLED = False
        resp = _scan(client, bot_user)
        assert resp.status_code == 404 and resp.json()["error"] == "nutrition_disabled"


class TestTheChatIsUnchanged:
    def test_flag_off_keeps_the_old_text_and_reply_kind(self, bot_user, settings) -> None:
        from apps.skills.base import SkillContext
        from apps.skills.food_scanner import skill as scanner

        settings.FOOD_PHOTO_SCAN_ENABLED = False
        context = SkillContext(
            conversation=SimpleNamespace(id="conv-2109", skill_state={}, last_photo_bytes=b"jpeg"),  # type: ignore[arg-type]
            bot_user=bot_user,
            message_text="",
        )
        result = scanner._check_gates(context, kind="photo", require_photo_scan=True)
        assert result is not None
        assert result.reply_text == scanner.PHOTO_SCAN_OFF_FALLBACK
        assert result.meta == {"reply_kind": "food_scanner_photo_scan_off"}
        # Кнопки карточки (callback) флага не спрашивают — как раньше.
        assert scanner._check_gates(context, kind="callback", require_photo_scan=False) is None


# --- перепись по роли: кто зовёт scan_photo, тот спрашивает предикат ----------


def _calls_scan_photo(tree: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "scan_photo"
        for n in ast.walk(tree)
    )


def _asks_photo_gate(tree: ast.AST) -> bool:
    return any(
        (isinstance(n, ast.Name) and n.id == "photo_scan_refusal")
        or (isinstance(n, ast.Attribute) and n.attr == "photo_scan_refusal")
        for n in ast.walk(tree)
    )


def census(source: str) -> tuple[bool, bool]:
    tree = ast.parse(source)
    return _calls_scan_photo(tree), _asks_photo_gate(tree)


class TestCensusOfScanCallers:
    def test_every_scan_photo_caller_asks_the_photo_gate(self) -> None:
        callers: list[str] = []
        offences: list[str] = []
        for path in sorted((REPO_ROOT / "apps").rglob("*.py")):
            if "tests" in path.parts or path.name == "nutrition_client.py":
                continue
            calls, asks = census(path.read_text(encoding="utf-8"))
            if not calls:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            callers.append(rel)
            if not asks:
                offences.append(rel)
        assert len(callers) >= 2, callers  # положительно: чат и прокси найдены
        assert offences == [], "scan_photo without the photo gate: " + ", ".join(offences)

    def test_a_planted_caller_without_the_gate_is_caught(self) -> None:
        planted = "async def go(client):\n    return await client.scan_photo(external_user_id='x', image_bytes=b'')\n"
        assert census(planted) == (True, False)
        guarded = (
            "from apps.consent.photo_gate import photo_scan_refusal\n"
            "async def go(client):\n"
            "    if photo_scan_refusal(): return None\n"
            "    return await client.scan_photo(external_user_id='x', image_bytes=b'')\n"
        )
        assert census(guarded) == (True, True)
