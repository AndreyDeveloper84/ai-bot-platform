"""DRF-2267 (решение владельца CD §72, «все по рекомендациям») — бот без тупиков.

Правило: после каждого завершённого шага — 1–2 кнопки следующего шага и
«Меню». Основа — черновики переписи ``docs/BOT_DEAD_ENDS_CENSUS_2026-09-21.md``.
Этот файл — первый срез: топ по пути нового клиента.

Каждая кнопка ведёт в ветку, которая уже отвечает на глобальном пути
(правило DRF-1492: кнопка, которая где-то отвечает «не понял», хуже никакой):

* «Подобрать услугу» — ``DISCOVER_TAP_TEXT`` (та же фраза, что у пункта меню);
* «Найти салон» — ``cb:catalog:salons`` (``discovery.show_salons_button``);
* «Меню» — ``cb:menu:help`` («Что ты умеешь?» → меню, DRF-1491);
* «Дать согласие» / «Узнать что хранится» — ``cb:welcome:start_s2`` /
  ``cb:welcome:consent_details`` (экраны S2/S2a).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.skills.menu.marketplace import DISCOVER_TAP_TEXT
from apps.skills.menu.matching import CALLBACK_MENU_HELP

SALONS = "cb:catalog:salons"


def _callbacks(action_data: dict[str, Any] | None) -> list[str]:
    """Колбэки кнопок — из плоского списка или из готового конверта клавиатуры."""
    if not action_data:
        return []
    out = [b.get("callback", "") for b in action_data.get("buttons") or []]
    for att in action_data.get("attachments") or []:
        for row in (att.get("payload") or {}).get("buttons") or []:
            cells = row if isinstance(row, list) else [row]
            out.extend(c.get("callback") or c.get("payload") or "" for c in cells)
    return out


# ── B1 — ответ консьержа словами ──────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestConciergeProseCarriesNextSteps:
    def test_text_only_answer_offers_discover_salons_menu(self, monkeypatch) -> None:
        from apps.conversations.services import resolve_active_global_conversation
        from apps.identity.services import resolve_or_create_global_bot_user
        from apps.llm.protocol import CompletionResult
        from apps.orchestrator import concierge
        from apps.orchestrator.tests.test_concierge import _router_returning

        provider = AsyncMock()
        provider.complete.return_value = CompletionResult(
            text="Расскажите, что хочется: отдохнуть или привести в порядок кожу?"
        )
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user = resolve_or_create_global_bot_user(channel="max", channel_user_id="dead-2267")
        conversation = resolve_active_global_conversation(bot_user)

        reply = concierge.generate_concierge_reply(
            "хочу выглядеть свежее", bot_user=bot_user, conversation=conversation
        )

        assert reply.text.startswith("Расскажите")  # положительная пара: ответ модели на месте
        assert _callbacks(reply.action_data) == [DISCOVER_TAP_TEXT, SALONS, CALLBACK_MENU_HELP]


# ── B6 / B7 — канонические вопросы без критериев ──────────────────────────


class TestCanonQuestionsHaveAWayOn:
    def test_no_criteria_master_question(self) -> None:
        from apps.orchestrator.discovery import (
            NO_CRITERIA_QUESTION,
            render_no_criteria_clarification,
        )

        reply = render_no_criteria_clarification()
        assert reply.text == NO_CRITERIA_QUESTION  # текст канона не трогаем
        assert _callbacks(reply.action_data) == [SALONS, CALLBACK_MENU_HELP]

    def test_no_criteria_service_question(self) -> None:
        from apps.orchestrator.discovery import render_no_service_criteria_clarification

        reply = render_no_service_criteria_clarification()
        assert reply.text
        assert _callbacks(reply.action_data) == [SALONS, CALLBACK_MENU_HELP]


# ── B9 — «Не знаю», а текст предлагает «посмотрим доступные услуги?» ──────


class TestDontKnowOffersWhatItSays:
    def test_dont_know_answer_has_the_buttons_its_text_promises(self) -> None:
        from apps.orchestrator.discovery import (
            CLARIFY_DONT_KNOW_CALLBACK,
            CLARIFY_DONT_KNOW_TEXT,
            execute_clarify_callback,
        )

        outcome = execute_clarify_callback(CLARIFY_DONT_KNOW_CALLBACK, [])
        assert outcome is not None
        assert outcome.reply.text == CLARIFY_DONT_KNOW_TEXT
        assert _callbacks(outcome.reply.action_data) == [DISCOVER_TAP_TEXT, SALONS]


# ── F1 — подмена ответа стражем ───────────────────────────────────────────


class TestGuardReplacementOffersTheContinuationItNames:
    def test_replacement_keyboard(self) -> None:
        """Текст обещает «показать доступные услуги» — кнопка это и делает.

        «Уточнить запрос» из §128 кнопкой не становится: за ней нет ветки,
        которая бы ответила (DRF-1492) — его место в тексте.
        """
        from apps.orchestrator.safety.outbound import (
            REPLACEMENT_TEXT,
            replacement_action_data,
        )

        assert "доступные услуги" in REPLACEMENT_TEXT
        assert _callbacks(replacement_action_data()) == [DISCOVER_TAP_TEXT, CALLBACK_MENU_HELP]

    def test_the_handler_hangs_it_under_the_blocked_reply(self) -> None:
        """Обработчик кладёт клавиатуру под подменённый ответ, а не ``None``."""
        from pathlib import Path

        src = (
            Path(__file__)
            .resolve()
            .parents[2]
            .joinpath("channels/max/handler.py")
            .read_text(encoding="utf-8")
        )
        block = src.split("if guarded.blocked:", 1)[1].split("clarify_redraw = False", 1)[0]
        assert "replacement_action_data()" in block, block[:400]


# ── A1 — «Не сейчас» на согласии ──────────────────────────────────────────


class TestConsentRefusalLeavesADoorOpen:
    def test_not_now_offers_consent_and_details(self) -> None:
        from apps.skills.base import SkillContext
        from apps.skills.welcome.skill import S2_REFUSED_TEXT, WelcomeSkill

        ctx = SkillContext(
            conversation=MagicMock(), bot_user=MagicMock(), message_text="cb:welcome:consent_refuse"
        )
        result = WelcomeSkill().handle(ctx)
        assert result.reply_text == S2_REFUSED_TEXT
        assert _callbacks(result.action_data) == [
            "cb:welcome:start_s2",
            "cb:welcome:consent_details",
        ]
