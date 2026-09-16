"""DRF-1968 (M2+) — отказ по согласию несёт кнопку «Дать согласие» и возвращает в свой поток.

Решение владельца (раздел U): нет или отозван consent → объяснение → «Дать
согласие» → канонический поток согласия → возврат в исходный поток.

Сегодня человек в чате получает голый текст отказа. Отсюда три вещи:

* у каждого отказа по PERSONAL_DATA (фото-сканер, еда текстом, вода) есть
  кнопка «Дать согласие», и она несёт, откуда человек пришёл;
* тап открывает тот же экран согласия S2, что и приветствие (дословный текст
  152-ФЗ), а его «Да, продолжим» несёт тот же исходный вход;
* после выдачи согласия человек слышит не общий экран первого шага, а возврат
  в свой поток: «пришли фото ещё раз» / «напиши, что съела» / «сколько воды».

Кнопка живёт только на глобальном пути, и он ДВУСОСТОЯТЕЛЬНЫЙ: вне тенанта
(сам ход согласия — ``run_onboarding_turn`` бросает, если в него протёк scope,
инвариант атомарности #1074) ИЛИ под сентинелом ``get_global_bot_tenant()``
(три отказа исполняются внутри ``tenant_scope`` в ``nutrition_global._run_skill``).
Проверять «тенанта нет» недостаточно — сентинел не ``None`` никогда; различает
:func:`apps.skills.welcome.skill._is_global_bot_scope`. Здешние узлы зовут
навыки напрямую, то есть держат только половину «вне тенанта»; вторая половина
и сквозной ход в бою — в ``test_consent_recovery_wiring.py``.

На салонном пути ``WelcomeSkill._render_consent_granted`` ставит ``consent_at``,
но НЕ пишет ConsentRecord, а отказы читают журнал — кнопка там вернула бы
человека к тому же отказу. Решение главного окна 16.09: салонный — отдельный лист.

Работает при ``GLOBAL_BOT_ONBOARDING=true`` (замер пилота 15.09 22:40 UTC —
SET:true в web/worker/celery-worker). Флаг выключат — кнопка перестанет
доводить до экрана согласия; это названное условие, а не гарантия.
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from unittest.mock import Mock, patch

import pytest

from apps.consent.models import ConsentRecord
from apps.skills.base import SkillContext
from apps.skills.food_clarify.text_entry import CONSENT_TEXT

pytestmark = pytest.mark.django_db

OFFER_LABEL = "Дать согласие"
ORIGINS = ("photo", "text", "water")


@pytest.fixture(autouse=True)
def _global_path_on(settings):
    """Глобальный путь приветствия и питание включены — иначе отказы приходят
    по другой причине («функция готовится»), и красный был бы не о согласии."""
    settings.GLOBAL_BOT_ONBOARDING = True
    settings.NUTRITION_ENABLED = True
    settings.FOOD_PHOTO_SCAN_ENABLED = True


def _global_user_and_conv(channel_user_id: str = "990001"):
    """Глобальный человек и его разговор — теми же помощниками, что боевой путь."""
    from apps.conversations.services import resolve_active_global_conversation
    from apps.identity.services.resolver import resolve_or_create_global_bot_user

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=channel_user_id, chat_id="8888"
    )
    return bot_user, resolve_active_global_conversation(bot_user)


def _buttons(result) -> list[dict]:
    return list((getattr(result, "action_data", None) or {}).get("buttons") or [])


def _callbacks(result) -> list[str]:
    return [b.get("callback", "") for b in _buttons(result)]


# ── 1. Отказ несёт кнопку ────────────────────────────────────────────────────


class TestRefusalCarriesTheButton:
    def test_the_photo_scanner_refusal_offers_the_consent(self) -> None:
        from apps.skills.food_scanner.skill import FoodScannerSkill

        bot_user = Mock()
        bot_user.food_scanner_consent_at = datetime(2026, 9, 1, tzinfo=dt_timezone.utc)
        ctx = SkillContext(
            conversation=Mock(id=1, skill_state={}),
            bot_user=bot_user,
            message_text="",
            has_attachments=True,
        )
        with patch(
            "apps.orchestrator.personal_surface.personal_records_consent_open",
            return_value=False,
        ):
            result = FoodScannerSkill().handle(ctx)

        assert result.reply_text == CONSENT_TEXT
        assert result.meta.get("reply_kind") == "food_scanner_personal_data_required"
        assert [b["label"] for b in _buttons(result)] == [OFFER_LABEL]
        assert _callbacks(result) == ["cb:welcome:consent_offer_photo"]

    def test_the_food_text_refusal_offers_the_consent(self) -> None:
        from apps.skills.food_clarify import text_entry

        ctx = SkillContext(
            conversation=Mock(id=2, skill_state={}),
            bot_user=Mock(),
            message_text="омлет 150 г",
        )
        with patch.object(text_entry, "_consent_open", return_value=False):
            result = text_entry._gate(ctx)

        assert result is not None
        assert result.reply_text == CONSENT_TEXT
        assert result.meta.get("reply_kind") == "food_text_consent_required"
        assert _callbacks(result) == ["cb:welcome:consent_offer_text"]

    def test_the_water_refusal_offers_the_consent(self) -> None:
        from apps.skills.water import skill as water_skill

        bot_user = Mock()
        bot_user.channel = "max"
        bot_user.channel_user_id = "990001"
        ctx = SkillContext(
            conversation=Mock(id=3, skill_state={}),
            bot_user=bot_user,
            message_text="стакан воды",
        )
        with patch.object(water_skill, "_consent_open", return_value=False):
            result = water_skill.WaterSkill().handle(ctx)

        assert result.reply_text == CONSENT_TEXT
        assert result.meta.get("reply_kind") == "water_consent_required"
        assert _callbacks(result) == ["cb:welcome:consent_offer_water"]


# ── 2. Тап открывает канонический экран согласия ─────────────────────────────


class TestOfferOpensTheConsentScreen:
    @pytest.mark.parametrize("origin", ORIGINS)
    def test_the_offer_shows_the_s2_screen_carrying_the_origin(self, origin) -> None:
        from apps.channels.max.global_onboarding import needs_onboarding, run_onboarding_turn
        from apps.skills.welcome.skill import S2_CONSENT_TEXT

        bot_user, conversation = _global_user_and_conv()
        payload = f"cb:welcome:consent_offer_{origin}"
        assert needs_onboarding(bot_user, payload) is True

        reply = run_onboarding_turn(conversation, bot_user, payload)

        assert reply.text == S2_CONSENT_TEXT
        callbacks = [b.get("callback") for b in (reply.action_data or {}).get("buttons", [])]
        assert f"cb:welcome:consent_yes_{origin}" in callbacks
        # Согласие ещё не выдано: экран только предлагает.
        assert not ConsentRecord.all_tenants.filter(bot_user=bot_user, granted=True).exists()


# ── 3. Выдача согласия и возврат в исходный поток ────────────────────────────


class TestGrantWritesTheJournalAndReturns:
    def test_the_grant_writes_both_consent_types_with_the_document_version(self) -> None:
        from apps.channels.max.global_onboarding import (
            CONSENT_DOCUMENT_VERSION,
            run_onboarding_turn,
        )

        bot_user, conversation = _global_user_and_conv()
        run_onboarding_turn(conversation, bot_user, "cb:welcome:consent_yes_photo")

        rows = ConsentRecord.all_tenants.filter(bot_user=bot_user, granted=True, withdrawn_at=None)
        assert {r.consent_type for r in rows} == {
            ConsentRecord.ConsentType.PERSONAL_DATA.value,
            ConsentRecord.ConsentType.MEMORY_GREEN.value,
        }
        assert {r.document_version for r in rows} == {CONSENT_DOCUMENT_VERSION}

    @pytest.mark.parametrize("origin", ORIGINS)
    def test_the_grant_returns_the_person_to_their_own_flow(self, origin) -> None:
        from apps.channels.max.global_onboarding import run_onboarding_turn
        from apps.skills.welcome.skill import CONSENT_RECOVERY_RETURN_TEXTS

        bot_user, conversation = _global_user_and_conv()
        reply = run_onboarding_turn(conversation, bot_user, f"cb:welcome:consent_yes_{origin}")

        # Сверка константы с самой собой: доказывает ПРОВОДКУ origin → текст, и
        # это и есть предмет узла. Про сам текст он не говорит ничего — тексты
        # черновики W3; читать его как покрытие содержания нельзя.
        assert reply.text == CONSENT_RECOVERY_RETURN_TEXTS[origin]

    def test_every_new_tap_has_a_history_label(self) -> None:
        from apps.skills.welcome.skill import welcome_tap_labels

        labels = welcome_tap_labels()
        for origin in ORIGINS:
            assert labels.get(f"cb:welcome:consent_offer_{origin}") == OFFER_LABEL
            assert labels.get(f"cb:welcome:consent_yes_{origin}") == "Да, продолжим"


# ── Сторожа: зелёные и до правки, и после ────────────────────────────────────


class TestGuards:
    def test_on_the_tenant_path_the_refusal_has_no_button(self) -> None:
        """Салонный путь не пишет ConsentRecord — кнопка вернула бы к тому же отказу."""
        from apps.skills.water import skill as water_skill
        from apps.tenancy.context import tenant_scope
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug="consent-recovery", name="Consent Recovery")
        bot_user = Mock()
        bot_user.channel = "max"
        bot_user.channel_user_id = "990002"
        ctx = SkillContext(
            conversation=Mock(id=4, skill_state={}),
            bot_user=bot_user,
            message_text="стакан воды",
        )
        with patch.object(water_skill, "_consent_open", return_value=False), tenant_scope(tenant):
            result = water_skill.WaterSkill().handle(ctx)

        assert result.reply_text == CONSENT_TEXT
        assert _buttons(result) == []

    def test_a_repeat_grant_tap_does_not_duplicate_the_journal(self) -> None:
        from apps.channels.max.global_onboarding import run_onboarding_turn

        bot_user, conversation = _global_user_and_conv()
        for _ in range(2):
            run_onboarding_turn(conversation, bot_user, "cb:welcome:consent_yes_photo")

        rows = ConsentRecord.all_tenants.filter(bot_user=bot_user, granted=True, withdrawn_at=None)
        assert rows.count() == 2  # personal_data + memory_green, без дублей
