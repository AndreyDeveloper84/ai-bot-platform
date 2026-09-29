"""Один предикат согласия дневника на всех писателях (DRF-2093).

Красное листа: согласие дневника ``food-diary-v1`` ОТОЗВАНО в Mini App —
а стакан воды из того же Mini App всё равно записывается (ворота воды
спрашивали флаг и PERSONAL_DATA, реестр ``food_diary_processing`` — нет).
То же у правки/возврата еды в Mini App и у воды и текста в чате.

Реестр здесь НАСТОЯЩИЙ (``grant_diary`` → ``withdraw_diary``,
``record_person_consent`` для PERSONAL_DATA) — предикаты не подменяются:
подменённый предикат доказал бы только, что ворота его зовут, а не что
отозванное согласие закрывает запись.

Узлы:

* r1/p1 — Mini App вода: отозвано → 403 ``food_diary_consent_required``,
  ``add_water`` не вызван; выдано → 201, вызван (положительная пара);
* r2/p2 — Mini App правка и возврат еды: то же (``update_meal`` /
  ``restore_meal``);
* r3/p3 — чат, вода: отозвано → текст «открыть Mini App», без кнопки
  «Дать согласие» (та выдаёт PERSONAL_DATA), ``add_water`` не вызван; выдано
  → записан;
* r4 — чат, текст: отозвано → тот же текст, ``log_meal``/``estimate`` не
  вызваны;
* s1 — сканер фото — прежнее поведение через тот же предикат;
* g1 — перепись писателей ПО КЛАССУ: модули с вызовами
  ``log_meal``/``add_water``/``update_meal``/``restore_meal`` — ровно четыре,
  вызовов ≥ 7, и каждый модуль зовёт ``diary_write_refusal``;
* g2 — предикат fail-closed на исключении реестра.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import json
import time as time_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.consent.models import ConsentRecord
from apps.consent.nutrition import (
    FOOD_DIARY_CONSENT_DOCUMENT_VERSION,
    diary_is_granted,
    grant_diary,
    withdraw_diary,
)
from apps.consent.services import record_person_consent
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-2093"  # noqa: S105 — test fixture  # pragma: allowlist secret
WRITE_METHODS = {"log_meal", "add_water", "update_meal", "restore_meal"}
#: Слаг отказа реестра — литералом, чтобы файл собирался и на базе без модуля
#: предиката: красное листа должно быть видно по узлам, а не ошибкой сбора.
FOOD_DIARY_CONSENT_REQUIRED = "food_diary_consent_required"


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest})


def _auth(bot_user: BotUser) -> str:
    params = {
        "user": json.dumps({"id": int(bot_user.channel_user_id), "first_name": "Клиент"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.NUTRITION_ENABLED = True
    settings.MAX_BOT_TENANT_SLUG = "diary-2093"


@pytest.fixture
def bot_user(db) -> BotUser:
    tenant = Tenant.objects.create(slug="diary-2093", name="Diary 2093", timezone="Europe/Moscow")
    user = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="92093", display_name="Клиент"
    )
    # PERSONAL_DATA выдано по-настоящему: отказ ниже — только про реестр дневника.
    record_person_consent(
        user, consent_type=ConsentRecord.ConsentType.PERSONAL_DATA.value, source="test:2093"
    )
    return user


def _grant(bot_user: BotUser) -> None:
    grant_diary(bot_user, document_version=FOOD_DIARY_CONSENT_DOCUMENT_VERSION)
    assert diary_is_granted(bot_user) is True


def _withdraw(bot_user: BotUser) -> None:
    assert withdraw_diary(bot_user) >= 1
    assert diary_is_granted(bot_user) is False


def _client_stub() -> AsyncMock:
    client = AsyncMock()
    client.add_water = AsyncMock(
        return_value=SimpleNamespace(
            entry_id="e-1",
            ml=250,
            water_ml=250,
            kcal=0,
            milestone_text=None,
            today_total_ml=250,
            today_norm_ml=None,
            alcohol_recovery_hint=False,
            raw={},
        )
    )
    client.update_meal = AsyncMock(
        return_value=SimpleNamespace(
            log_id="L1",
            dish_name="борщ",
            meal_type="other",
            calories=300.0,
            protein_g=None,
            fat_g=None,
            carbs_g=None,
            portion_g=None,
            raw={},
        )
    )
    client.restore_meal = AsyncMock(
        return_value=SimpleNamespace(
            log_id="L1",
            dish_name="борщ",
            meal_type="other",
            calories=300.0,
            protein_g=None,
            fat_g=None,
            carbs_g=None,
            portion_g=None,
            raw={},
        )
    )
    return client


def _post(client: Client, bot_user: BotUser, url: str, body: dict, method: str = "post"):
    return getattr(client, method)(
        url,
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=_auth(bot_user),
    )


# ─── Mini App ───────────────────────────────────────────────────────────────


class TestMiniAppWriters:
    def test_r1_water_after_withdrawal_is_403_and_nothing_is_written(self, client, bot_user):
        _grant(bot_user)
        _withdraw(bot_user)
        stub = _client_stub()
        with patch("apps.integrations.ayla.get_nutrition_client", return_value=stub):
            resp = _post(
                client, bot_user, reverse("miniapp_api:customer_wellness_water"), {"ml": 250}
            )

        assert resp.status_code == 403, resp.content
        assert resp.json()["error"] == FOOD_DIARY_CONSENT_REQUIRED
        stub.add_water.assert_not_called()

    def test_p1_water_with_the_diary_consent_is_written_as_before(self, client, bot_user):
        _grant(bot_user)
        stub = _client_stub()
        with patch("apps.integrations.ayla.get_nutrition_client", return_value=stub):
            resp = _post(
                client, bot_user, reverse("miniapp_api:customer_wellness_water"), {"ml": 250}
            )

        assert resp.status_code == 201, resp.content
        stub.add_water.assert_awaited_once()

    def test_r2_edit_and_restore_after_withdrawal_are_403_and_untouched(self, client, bot_user):
        _grant(bot_user)
        _withdraw(bot_user)
        stub = _client_stub()
        entry = reverse("miniapp_api:customer_wellness_food_entry", args=["L1"])
        restore = reverse("miniapp_api:customer_wellness_food_entry_restore", args=["L1"])
        with patch("apps.integrations.ayla.get_nutrition_client", return_value=stub):
            edit = _post(client, bot_user, entry, {"grams": 200}, method="patch")
            back = _post(client, bot_user, restore, {})

        assert (edit.status_code, back.status_code) == (403, 403), (edit.content, back.content)
        assert edit.json()["error"] == back.json()["error"] == FOOD_DIARY_CONSENT_REQUIRED
        stub.update_meal.assert_not_called()
        stub.restore_meal.assert_not_called()

    def test_p2_edit_and_restore_with_the_consent_reach_the_catalog(self, client, bot_user):
        _grant(bot_user)
        stub = _client_stub()
        entry = reverse("miniapp_api:customer_wellness_food_entry", args=["L1"])
        restore = reverse("miniapp_api:customer_wellness_food_entry_restore", args=["L1"])
        with patch("apps.integrations.ayla.get_nutrition_client", return_value=stub):
            edit = _post(client, bot_user, entry, {"grams": 200}, method="patch")
            back = _post(client, bot_user, restore, {})

        assert edit.status_code < 400 and back.status_code < 400, (edit.content, back.content)
        stub.update_meal.assert_awaited_once()
        stub.restore_meal.assert_awaited_once()

    def test_d1_delete_needs_no_diary_consent(self, client, bot_user):
        """Убрать своё человек вправе всегда — удаление вне предиката записи."""
        _grant(bot_user)
        _withdraw(bot_user)
        stub = _client_stub()
        stub.delete_meal = AsyncMock(
            return_value=SimpleNamespace(
                log_id="L1", restore_window_expires_at="2026-09-18T10:00:00Z"
            )
        )
        with patch("apps.integrations.ayla.get_nutrition_client", return_value=stub):
            resp = client.delete(
                reverse("miniapp_api:customer_wellness_food_entry", args=["L1"]),
                HTTP_AUTHORIZATION=_auth(bot_user),
            )
        assert resp.status_code == 200, resp.content
        stub.delete_meal.assert_awaited_once()


# ─── чат ────────────────────────────────────────────────────────────────────


def _chat_context(bot_user: BotUser, text: str):
    from apps.skills.base import SkillContext

    return SkillContext(
        conversation=SimpleNamespace(id="conv-2093", skill_state={}),  # type: ignore[arg-type]
        bot_user=bot_user,
        message_text=text,
    )


class TestChatWriters:
    def test_r3_chat_water_after_withdrawal_refuses_without_the_personal_data_button(
        self, bot_user
    ):
        from apps.skills.food_clarify.text_entry import CONSENT_TEXT, DIARY_CONSENT_REQUIRED_TEXT
        from apps.skills.water.skill import WaterSkill

        _grant(bot_user)
        _withdraw(bot_user)
        writes: list[dict] = []

        async def _add_water(**kwargs):
            writes.append(kwargs)

        stub = Mock()
        stub.add_water = _add_water
        with patch("apps.skills.water.skill.get_nutrition_client", return_value=stub):
            result = WaterSkill().handle(_chat_context(bot_user, "стакан воды"))

        assert writes == []
        assert (
            result.reply_text == DIARY_CONSENT_REQUIRED_TEXT and result.reply_text != CONSENT_TEXT
        )
        assert result.meta == {"reply_kind": "water_diary_consent_required"}
        assert not result.action_data  # кнопка «Дать согласие» выдаёт PERSONAL_DATA — здесь её нет

    def test_p3_chat_water_with_the_consent_is_written(self, bot_user):
        from apps.skills.water.skill import WaterSkill

        _grant(bot_user)
        writes: list[dict] = []

        async def _add_water(**kwargs):
            writes.append(kwargs)
            return SimpleNamespace(
                entry_id="e-1",
                ml=250,
                water_ml=250,
                kcal=0,
                milestone_text=None,
                today_total_ml=250,
                today_norm_ml=None,
                alcohol_recovery_hint=False,
                raw={},
            )

        stub = Mock()
        stub.add_water = _add_water
        with patch("apps.skills.water.skill.get_nutrition_client", return_value=stub):
            result = WaterSkill().handle(_chat_context(bot_user, "стакан воды"))

        assert len(writes) == 1 and result.meta.get("reply_kind") != "water_diary_consent_required"

    def test_r4_chat_text_after_withdrawal_refuses_before_any_catalog_call(self, bot_user):
        from apps.skills.food_clarify import text_entry

        _grant(bot_user)
        _withdraw(bot_user)
        stub = AsyncMock()
        with patch("apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=stub):
            result = text_entry.show_estimate(
                _chat_context(bot_user, "борщ 300г"), "борщ", 300, corrected=False
            )

        assert result is not None and result.reply_text == text_entry.DIARY_CONSENT_REQUIRED_TEXT
        assert result.meta == {"reply_kind": "food_text_diary_consent_required"}
        stub.estimate_dish.assert_not_called()
        stub.log_meal.assert_not_called()

    def test_s1_the_photo_scanner_keeps_its_words_through_the_same_predicate(self, bot_user):
        from apps.skills.food_scanner import skill as scanner

        _grant(bot_user)
        _withdraw(bot_user)
        result = scanner._check_gates(
            _chat_context(bot_user, ""), kind="photo", require_photo_scan=False
        )

        assert result is not None
        assert result.reply_text == scanner.CONSENT_REQUIRED_FALLBACK
        assert result.meta == {"reply_kind": "food_scanner_consent_required"}


# ─── предикат и перепись ───────────────────────────────────────────────────


class TestThePredicate:
    def test_g2_registry_failure_reads_as_no_consent(self, bot_user):
        from apps.consent.diary_gate import diary_write_refusal

        _grant(bot_user)
        with patch("apps.consent.nutrition.diary_is_granted", side_effect=RuntimeError("db down")):
            assert diary_write_refusal(bot_user) == FOOD_DIARY_CONSENT_REQUIRED
        assert diary_write_refusal(bot_user) is None  # положительная стража: без сбоя — открыто

    def test_g1_every_writer_module_asks_the_one_predicate(self):
        """Перепись ПО КЛАССУ — вызовы методов записи клиента, не имена файлов."""
        root = Path(__file__).resolve().parents[3]
        writers: dict[str, int] = {}
        askers: set[str] = set()
        for path in (root / "apps").rglob("*.py"):
            rel = path.relative_to(root).as_posix()
            if "/tests/" in rel or rel.endswith("nutrition_client.py"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name in WRITE_METHODS:
                    writers[rel] = writers.get(rel, 0) + 1
                if name == "diary_write_refusal":
                    askers.add(rel)
        assert set(writers) == {
            "apps/miniapp_api/views.py",
            "apps/skills/food_clarify/text_entry.py",
            "apps/skills/food_scanner/skill.py",
            "apps/skills/water/skill.py",
        }, writers
        assert sum(writers.values()) >= 7, writers  # нижняя граница: скан не пуст
        missing = set(writers) - askers
        assert not missing, f"писатели без единого предиката: {sorted(missing)}"
