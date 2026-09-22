"""Цель словами человека в переписке, целиком через обработчик (DRF-2283, CD §73).

Модульные узлы распознавания лежат в
``apps/orchestrator/tests/test_goal_capture_2283.py``. Здесь — ход целиком:
где эта ветка стоит в лестнице и чего она НЕ забирает.

Главный отрицательный узел — **фраза внутри другого шага**. Пока жива
нутриционная анкета, ЛЮБОЙ текст принадлежит ей (``is_structured_nutrition_turn``),
и «хочу похудеть», сказанное в ответ на её вопрос, — ответ анкете, а не
новая цель. Тот же довод уже принят рядом для «помощи» и «что я ел».
"""

from __future__ import annotations

import json
import uuid

import pytest

from apps.channels.handlers import GlobalMaxHandler
from apps.channels.max import handler as max_handler
from apps.consent.services import record_global_consent
from apps.identity.models import UserPersonalContext
from apps.identity.services import resolve_or_create_global_bot_user
from apps.orchestrator.memory import short_term

pytestmark = pytest.mark.django_db(transaction=True)

GOAL = "хочу −5 кг к лету"


def _payload(*, text: str, mid: str, user_id: int, chat_id: int) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Иван"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _entry(payload: dict) -> dict:
    return {"data": json.dumps(payload), "trace_id": str(uuid.uuid4()), "resolved_tenant_id": ""}


@pytest.fixture
def mock_send(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        max_handler,
        "send_message",
        lambda *, chat_id, text, attachments=None, timeout=10.0: (
            calls.append({"text": text}) or {"ok": True}
        ),
    )
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)


@pytest.fixture
def mock_discovery(monkeypatch):
    from apps.orchestrator.discovery import DiscoveryReply

    monkeypatch.setattr(
        "apps.orchestrator.concierge.generate_concierge_reply",
        lambda *a, **k: DiscoveryReply(text="__DISCOVERY__"),
    )


@pytest.fixture
def written(monkeypatch) -> list[dict]:
    """Перехват единственного пути записи — ручки целей Ayla."""

    calls: list[dict] = []

    def _fake_post(*, external_user_id, payload):
        calls.append({"external_user_id": external_user_id, "payload": payload})
        return {}

    monkeypatch.setattr("apps.integrations.ayla.goals_client.post_goal_select", _fake_post)
    monkeypatch.setattr(
        "apps.orchestrator.goal_capture._screen", lambda bot_user, body: (None, body)
    )
    return calls


def _person(user_id: int, *, consent: bool = True):
    ayla_uid = uuid.uuid4()
    bu = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), ayla_user_id=ayla_uid
    )
    if consent:
        record_global_consent(bu, source="welcome")
    UserPersonalContext.objects.create(user_id=ayla_uid)
    return bu


class TestTheGoalIsWrittenFromChat:
    def test_the_words_are_written_and_answered(
        self, settings, mock_send, fake_redis, mock_discovery, written
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        _person(7301)

        GlobalMaxHandler()(_entry(_payload(text=GOAL, mid="g1", user_id=7301, chat_id=8301)))

        assert [c["payload"] for c in written] == [{"goal_text": GOAL, "source_channel": "bot"}]
        # Ответ — о цели, а не разговор консьержа.
        assert mock_send[-1]["text"] != "__DISCOVERY__"
        assert GOAL in mock_send[-1]["text"]


class TestWhatItMustNotClaim:
    def test_an_everyday_wish_goes_to_the_concierge(
        self, settings, mock_send, fake_redis, mock_discovery, written
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        _person(7302)

        GlobalMaxHandler()(
            _entry(_payload(text="хочу пиццу", mid="g2", user_id=7302, chat_id=8302))
        )

        assert written == []  # empty-assert-ok: запись доказана в тесте выше
        assert mock_send[-1]["text"] == "__DISCOVERY__"

    def test_without_consent_nothing_is_written(
        self, settings, mock_send, fake_redis, mock_discovery, written
    ):
        """Цель — личные данные: без согласия PERSONAL_DATA её никуда не пишут."""

        settings.STRICT_TENANT_SCOPE = "strict"
        _person(7303, consent=False)

        GlobalMaxHandler()(_entry(_payload(text=GOAL, mid="g3", user_id=7303, chat_id=8303)))

        assert written == []  # empty-assert-ok: запись доказана в тесте выше
        assert mock_send[-1]["text"] == "__DISCOVERY__"

    def test_a_goal_phrase_inside_the_anketa_stays_the_anketa_s(
        self, settings, mock_send, fake_redis, mock_discovery, written, monkeypatch
    ):
        """Незаконченный шаг старше: анкета забирает ЛЮБОЙ текст, пока жива."""

        settings.STRICT_TENANT_SCOPE = "strict"
        _person(7304)

        from apps.skills.base import SkillResult

        monkeypatch.setattr(
            max_handler,
            "try_handle_structured_nutrition_turn",
            lambda **kwargs: SkillResult(
                reply_text="__ANKETA__", action_data=None, action_type="nutrition_anketa"
            ),
        )

        GlobalMaxHandler()(_entry(_payload(text=GOAL, mid="g4", user_id=7304, chat_id=8304)))

        assert written == []  # empty-assert-ok: запись доказана в тесте выше
        assert mock_send[-1]["text"] == "__ANKETA__"
