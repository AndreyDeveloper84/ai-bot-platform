"""Live run of scanner memory on the global path (DRF-1454).

This is the ticket's acceptance check, in code and in numbers: not «память
работает», but a five-turn conversation in which the **second visit does not
re-ask what the first one clarified**, with the rows counted on both sides.

The run goes through the real global dispatcher
(:func:`apps.orchestrator.nutrition_global.try_handle_structured_nutrition_turn`)
against a real ``Conversation`` and real ``MemoryEntry`` rows. Only the two
external edges are stubbed — Ayla's photo recogniser and the identity read-back
— because neither is what this ticket changed.

Turns::

    1. фото            → карточка «Узнала: Борщ … Примерно 300 г»   (память пуста)
    2. ✏️ Уточнить/вес → ЧИСТЫЙ вопрос «Сколько … в граммах?»
    3. «500 г»         → 1 зелёная запись, source=explicit
    4. фото того же    → карточка + строка «Помню с прошлого раза: 500 г»
    5. ✏️ Уточнить/вес → вопрос УЖЕ НЕ чистый: называет 500 и спрашивает
                          только об изменении
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import Mock

import pytest
from django.utils import timezone

from apps.consent.models import ConsentRecord
from apps.consent.services import record_global_consent
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.models import MemoryEntry
from apps.identity.services import resolve_or_create_global_bot_user
from apps.integrations.ayla import ScanResponse
from apps.integrations.ayla.identity_client import ResolvedIdentity
from apps.orchestrator.nutrition_global import try_handle_structured_nutrition_turn
from apps.skills.food_correction.skill import _PROMPTS

pytestmark = pytest.mark.django_db(transaction=True)

_PHOTO = [{"type": "image", "payload": {"url": "https://cdn.example/plate.jpg"}}]


@pytest.fixture
def ayla(monkeypatch: pytest.MonkeyPatch, settings):
    """Both external edges stubbed; everything between them is real."""

    settings.STRICT_TENANT_SCOPE = "strict"
    settings.NUTRITION_ENABLED = True
    settings.FOOD_PHOTO_SCAN_ENABLED = True

    ayla_user_id = uuid.uuid4()
    monkeypatch.setattr(
        "apps.integrations.ayla.identity_client.resolve_identity",
        lambda external_user_id: ResolvedIdentity(ayla_user_id=ayla_user_id, is_proxy=True),
        raising=True,
    )
    monkeypatch.setattr(
        "apps.channels.max.photo.extract_first_photo_url",
        lambda attachments: "https://cdn.example/plate.jpg",
        raising=True,
    )
    monkeypatch.setattr(
        "apps.channels.max.photo.download_photo", lambda url: b"jpeg-bytes", raising=True
    )

    scans = iter(("scan-1", "scan-2", "scan-3"))

    async def _scan(**_kwargs: Any) -> ScanResponse:
        return ScanResponse(
            scan_id=next(scans),
            dish_name="Борщ",
            confidence=0.9,
            portion_g=300,
            nutrition={"calories": 250, "protein_g": 12, "fat_g": 8, "carbs_g": 32},
            provider="test",
            raw={},
        )

    client = Mock()
    client.scan_photo = _scan
    monkeypatch.setattr(
        "apps.skills.food_scanner.skill.get_nutrition_client", lambda: client, raising=True
    )
    return ayla_user_id


@pytest.fixture
def person(ayla):
    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id="drf1454-flow", chat_id="drf1454-flow-chat"
    )
    # Веха 1 feature consent (152-ФЗ acknowledgement for the scanner surface).
    bot_user.food_scanner_consent_at = timezone.now()
    bot_user.save(update_fields=["food_scanner_consent_at"])
    # Memory's own two bases, exactly as the pilot onboarding grants them.
    for consent_type in (
        ConsentRecord.ConsentType.PERSONAL_DATA.value,
        ConsentRecord.ConsentType.MEMORY_GREEN.value,
    ):
        record_global_consent(bot_user, consent_type=consent_type, source="welcome")
    conversation = resolve_active_global_conversation(bot_user)
    return bot_user, conversation


def _turn(person, *, text: str = "", attachments: list[dict[str, Any]] | None = None):
    bot_user, conversation = person
    conversation.refresh_from_db()
    result = try_handle_structured_nutrition_turn(
        text=text,
        attachments=attachments,
        bot_user=bot_user,
        conversation=conversation,
        trace_id="drf1454",
    )
    conversation.refresh_from_db()
    return result


def _green_rows(user_id: uuid.UUID):
    return MemoryEntry.objects.filter(
        user_id=user_id,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        soft_deleted_at__isnull=True,
        status__in=(None, MemoryEntry.STATUS_ACTIVE),
    )


class TestSecondVisitDoesNotReAsk:
    def test_full_five_turn_run(self, person, ayla) -> None:
        bot_user, _conversation = person

        # ── turn 1: первое фото, память пуста ───────────────────────────
        first_card = _turn(person, attachments=_PHOTO)
        assert first_card is not None
        assert "Узнала: Борщ" in first_card.reply_text
        assert "Помню с прошлого раза" not in first_card.reply_text
        assert first_card.action_data["remembered"] is False
        # Ничего не создано: история еды — жёлтая зона, её не храним.
        assert MemoryEntry.objects.count() == 0

        # ── turn 2: «✏️ Уточнить» → вес. Вопрос ЧИСТЫЙ ─────────────────
        first_prompt = _turn(person, text="cb:food:correct:grams:scan-1")
        assert first_prompt is not None
        assert first_prompt.reply_text == _PROMPTS["grams"]
        assert first_prompt.action_data["remembered"] is False

        # ── turn 3: ответ человека → ровно одна зелёная запись ─────────
        stored = _turn(person, text="500 г")
        assert stored is not None
        assert stored.action_data == {"field": "grams", "value": 500, "stored": True}

        bot_user.refresh_from_db()
        rows = _green_rows(ayla)
        assert rows.count() == 1  # ← «сколько записей создано»
        entry = rows.get()
        assert entry.source == MemoryEntry.SOURCE_EXPLICIT
        assert entry.provenance == MemoryEntry.PROVENANCE_USER_STATED
        assert entry.content["key"] == "food_portion:борщ"
        assert entry.content["value"] == 500

        # ── turn 4: то же блюдо на следующем ходу — память прочитана ───
        second_card = _turn(person, attachments=_PHOTO)
        assert second_card is not None
        assert "Помню с прошлого раза: 500 г." in second_card.reply_text
        assert second_card.action_data["remembered"] is True  # ← «сколько прочитано»
        assert _green_rows(ayla).count() == 1  # чтение ничего не наплодило

        # ── turn 5: та же кнопка — и бот УЖЕ НЕ переспрашивает ─────────
        second_prompt = _turn(person, text="cb:food:correct:grams:scan-2")
        assert second_prompt is not None
        assert second_prompt.reply_text != _PROMPTS["grams"]
        assert "500" in second_prompt.reply_text
        assert "Оставляем?" in second_prompt.reply_text
        assert second_prompt.action_data["remembered"] is True

    def test_refusal_on_the_same_turn_is_answered_but_never_stored(self, person, ayla) -> None:
        """«Что не подошло» приходит ровно сюда — и остаётся за перимeтром."""
        _turn(person, attachments=_PHOTO)
        _turn(person, text="cb:food:correct:name:scan-1")

        answer = _turn(person, text="у меня непереносимость лактозы")

        assert answer is not None
        assert "не буду" in answer.reply_text.lower()
        assert MemoryEntry.objects.count() == 0

    def test_unrelated_text_is_not_claimed_and_reaches_the_concierge(self, person, ayla) -> None:
        """Ожидание ответа не должно съедать посторонний ход."""
        _turn(person, attachments=_PHOTO)
        _turn(person, text="cb:food:correct:grams:scan-1")

        assert _turn(person, text="а когда salon работает в воскресенье?") is None
        assert MemoryEntry.objects.count() == 0

    def test_correction_of_a_correction_replaces_it(self, person, ayla) -> None:
        _turn(person, attachments=_PHOTO)
        _turn(person, text="cb:food:correct:grams:scan-1")
        _turn(person, text="500")
        _turn(person, attachments=_PHOTO)
        _turn(person, text="cb:food:correct:grams:scan-2")
        _turn(person, text="250")

        third_card = _turn(person, attachments=_PHOTO)
        assert third_card is not None
        assert "Помню с прошлого раза: 250 г." in third_card.reply_text
        # История сохранена (две строки), но текущее значение одно.
        assert MemoryEntry.objects.count() == 2
        assert _green_rows(ayla).count() == 1


class TestAnketaAnswersAreNotCorrections:
    """Ревью DRF-1454, ось correctness, MUST_FIX_PRE_PILOT — вход из находки:
    карточка «Борщ» → ✏️ → «вес» (не отвечаем) → «Пройти анкету» → вопрос о
    росте → «170». До исправления записывалось «порция „борщ“ — 170 г», а
    ответ анкеты терялся — анкета стояла на том же шаге."""

    def test_a_numeric_anketa_answer_reaches_the_anketa(self, person, ayla) -> None:
        _turn(person, attachments=_PHOTO)
        _turn(person, text="cb:food:correct:grams:scan-1")  # правка веса осталась ждать

        started = _turn(person, text="/anketa")
        assert started is not None
        assert "пол" in started.reply_text.lower()

        _turn(person, text="cb:anketa:choice:gender:female")
        asked_height = _turn(person, text="30")  # возраст — тоже число в диапазоне порции
        assert asked_height is not None
        assert "рост" in asked_height.reply_text.lower()

        asked_weight = _turn(person, text="170")
        assert asked_weight is not None
        assert "вес" in asked_weight.reply_text.lower()  # анкета ПРОДВИНУЛАСЬ
        # …а не записала «порция „борщ“ — 170 г»:
        assert MemoryEntry.objects.count() == 0


class TestAPendingCorrectionDoesNotSwallowTheLadder:
    """Review DRF-1454 / A2: the predicate now calls a plain-text turn
    «structured» while a correction is open, but the correction skill claims
    only text shaped like its answer. Everything else must still reach the
    deterministic diary handler — a chip that leads to nothing is worse than no
    chip (DRF-1302)."""

    def test_the_diary_chip_still_executes_while_a_correction_is_open(
        self, person, ayla, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from apps.skills.base import SkillResult

        _turn(person, attachments=_PHOTO)
        _turn(person, text="cb:food:correct:name:scan-1")

        sentinel = SkillResult(reply_text="дневник", meta={"reply_kind": "diary"})
        monkeypatch.setattr(
            "apps.orchestrator.nutrition_global._try_handle_diary_request",
            lambda **_kw: sentinel,
            raising=True,
        )

        assert _turn(person, text="что я ел сегодня") is sentinel
        assert MemoryEntry.objects.count() == 0  # and nothing was stored as a dish


class TestRollbackSwitchEndToEnd:
    def test_flag_off_leaves_the_scanner_exactly_as_it_was(self, person, ayla, settings) -> None:
        bot_user, _conversation = person
        # Presence first: with the switch ON the five-turn loop stores and recalls.
        _turn(person, attachments=_PHOTO)
        _turn(person, text="cb:food:correct:grams:scan-1")
        _turn(person, text="500")
        assert _green_rows(ayla).count() == 1

        settings.FOOD_SCANNER_MEMORY_ENABLED = False

        card = _turn(person, attachments=_PHOTO)
        assert card is not None
        assert "Помню с прошлого раза" not in card.reply_text
        assert card.action_data["remembered"] is False

        prompt = _turn(person, text="cb:food:correct:grams:scan-2")
        assert prompt.reply_text == _PROMPTS["grams"]  # the plain question is back
        assert _turn(person, text="250") is None  # the answer falls through again
        assert _green_rows(ayla).count() == 1  # and nothing new was written
