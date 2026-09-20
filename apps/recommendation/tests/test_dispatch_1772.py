"""Триггер и отправка карточки C04 в DM (DRF-1772, К-3).

Что заперто:

1. триггер — только серверный факт `next.id == return_to_chat` при пустом
   `missing`; любой другой документ — молчание;
2. одна карточка на собранный контекст: второй вызов с тем же документом —
   ни отправки, ни второй записи; другой контекст (другое направление) —
   новая карточка;
3. C04.1 — DM по `user_id` человека с четырьмя кнопками, строка ассистента
   в глобальной переписке несёт `recommendation.id`;
4. C04.4 — нет направления → текст владельца 12.09 + два действия, запись
   `kind=absence`, тоже один раз;
5. отказ MAX не поднимается наружу и не запирает ключ идемпотентности;
6. пользователь салонного бота карточки от глобального бота не получает.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from apps.channels.max.outbound import MaxAPIError
from apps.conversations.models import Message
from apps.identity.models import BotUser
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.recommendation import card as c
from apps.recommendation.dispatch import context_collected, maybe_send_card
from apps.recommendation.models import Recommendation
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _goal(**over):
    goal = {
        "id": "goal-1",
        "goal_key": "self_care",
        "goal_text": None,
        "label": "Привести себя в порядок",
        "direction": {
            "what": "Уменьшить утреннюю отёчность",
            "subline": "Сфокусируемся на этом.",
            "area_key": "face",
        },
        "answers": [
            {"step": "area", "label": "Лицо и кожа", "option_key": "face", "unknown": False}
        ],
    }
    goal.update(over)
    return goal


def _collected(goal=None):
    return {
        "version": 2,
        "known": {"goal": _goal() if goal is None else goal, "anketa": []},
        "missing": [],
        "suggestions": [],
        "intents": [],
        "next": {"id": "return_to_chat", "label": "Вернуться в чат"},
    }


@pytest.fixture
def global_user(db, settings):
    settings.MAX_BOT_WEB_APP = "aylabot"
    return resolve_or_create_global_bot_user(
        channel="max", channel_user_id="777001", chat_id="777001"
    )


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id=None, user_id=None, text, attachments=None, timeout=10.0, bot=None):
        calls.append({"user_id": user_id, "chat_id": chat_id, "text": text, "att": attachments})
        return {"ok": True}

    monkeypatch.setattr("apps.channels.max.outbound.send_message", fake_send)
    return calls


def _labels(call: dict) -> list[str]:
    return [b["text"] for row in call["att"][0]["payload"]["buttons"] for b in row]


class TestTrigger:
    def test_only_return_to_chat_with_no_questions_counts(self):
        assert context_collected(_collected()) is True
        assert context_collected({**_collected(), "next": None}) is False
        assert (
            context_collected(
                {**_collected(), "next": {"id": "browse_catalog", "label": "Найти услугу"}}
            )
            is False
        )
        assert (
            context_collected(
                {**_collected(), "missing": [{"kind": "goal_anketa", "step": "area"}]}
            )
            is False
        )
        assert context_collected("not a doc") is False

    def test_non_collected_document_sends_nothing(self, global_user, sent):
        assert maybe_send_card(global_user, {**_collected(), "next": None}) is None
        assert sent == []
        assert Recommendation.objects.count() == 0


class TestCard:
    def test_card_goes_to_the_person_once_with_four_buttons(self, global_user, sent):
        record = maybe_send_card(global_user, _collected())
        assert record is not None
        assert record.kind == Recommendation.Kind.DIRECTION
        assert record.what == "Уменьшить утреннюю отёчность"
        assert record.why == [
            "Ты сказала, что хочешь привести себя в порядок",
            "Ты выбрала: лицо и кожа",
        ]
        assert record.facts == {"goal": "Привести себя в порядок", "area": "Лицо и кожа"}

        assert len(sent) == 1
        assert sent[0]["user_id"] == "777001" and sent[0]["chat_id"] is None  # DRF-1558
        assert sent[0]["text"].startswith(c.CARD_HEAD)
        assert _labels(sent[0]) == [c.BUTTON_PICK, c.BUTTON_WHY, c.BUTTON_ALT, c.BUTTON_SKIP]

        row = Message.all_tenants.filter(role="assistant").latest("created_at")
        assert row.content == sent[0]["text"]
        assert row.action_type == "recommendation_card"
        assert row.action_data["recommendation"] == {"id": str(record.id), "kind": "direction"}

        # Тот же документ ещё раз — молчание, не вторая карточка.
        assert maybe_send_card(global_user, _collected()) is None
        assert len(sent) == 1
        assert Recommendation.objects.filter(bot_user=global_user).count() == 1

    def test_a_different_direction_is_a_new_card(self, global_user, sent):
        maybe_send_card(global_user, _collected())
        other = _collected(
            _goal(direction={"what": "Вернуть лёгкость", "subline": "", "area_key": None})
        )
        assert maybe_send_card(global_user, other) is not None
        assert len(sent) == 2
        assert Recommendation.objects.filter(bot_user=global_user).count() == 2

    def test_card_never_names_a_service_or_recommends(self, global_user, sent):
        maybe_send_card(global_user, _collected())
        text = sent[0]["text"]
        assert text.startswith(c.CARD_HEAD)  # присутствие: карточка на месте
        assert "рекоменду" not in text.lower()
        assert c.boundary_violation(text) is None


class TestAbsence:
    def test_no_direction_is_the_owner_absence_frame_once(self, global_user, sent):
        doc = _collected(_goal(direction=None))
        record = maybe_send_card(global_user, doc)
        assert record is not None
        assert record.kind == Recommendation.Kind.ABSENCE
        assert sent[0]["text"] == c.NO_VERIFIED_EVIDENCE_TEXT
        assert _labels(sent[0]) == [c.ACTION_SHOW_SERVICES, c.ACTION_CLARIFY_REQUEST]
        row = Message.all_tenants.filter(role="assistant").latest("created_at")
        assert row.action_type == "recommendation_absence"

        assert maybe_send_card(global_user, doc) is None
        assert len(sent) == 1

    def test_no_grounded_why_is_absence_too(self, global_user, sent):
        doc = _collected(_goal(label="", answers=[]))
        record = maybe_send_card(global_user, doc)
        assert record is not None and record.kind == Recommendation.Kind.ABSENCE
        assert sent[0]["text"] == c.NO_VERIFIED_EVIDENCE_TEXT


class TestFailureAndScope:
    def test_max_failure_does_not_raise_and_frees_the_key(self, global_user, monkeypatch):
        def boom(**kwargs):
            raise MaxAPIError("down")

        monkeypatch.setattr("apps.channels.max.outbound.send_message", boom)
        assert maybe_send_card(global_user, _collected()) is None
        # Ключ идемпотентности не заперт: следующий триггер попробует снова.
        assert Recommendation.objects.filter(bot_user=global_user).count() == 0

    def test_salon_bot_user_gets_no_card_from_the_global_bot(self, sent, settings):
        tenant = Tenant.objects.create(
            slug="salon-reco-dispatch", name="Салон", timezone="Europe/Moscow"
        )
        salon_user = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="555001", display_name="Ольга"
        )
        with patch("apps.recommendation.dispatch._send_once") as inner:
            assert maybe_send_card(salon_user, _collected()) is None
            inner.assert_not_called()
        assert sent == []
