"""Тапы по карточке C04 в DM — `cb:reco:*` (DRF-1772, К-3).

Что заперто:

1. «Почему» → кадр C04.2: заголовок макета + те же причины; реакция
   `why_requested` в записи;
2. «Другой вариант» → честная заглушка (второго направления нет) — реакция
   `alternative_requested`;
3. «Не сейчас» → закрыть, реакция `rejected`;
4. сырой `cb:reco:…` в историю с ролью user не пишется (молчание, как у
   `cb:visit:*`), ответ бота записывается;
5. чужой/протухший id — ответ без записи и без чужой реакции.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from django.utils import timezone

from apps.channels.max import handler as max_handler
from apps.conversations.models import Message
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.orchestrator.memory import short_term
from apps.recommendation import card as c
from apps.recommendation import taps
from apps.recommendation.models import Recommendation

pytestmark = pytest.mark.django_db(transaction=True)

_CHAT = 8866


@pytest.fixture(autouse=True)
def _harness(settings, monkeypatch):
    settings.GLOBAL_BOT_ONBOARDING = True
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.STRICT_TENANT_REFUSE = True
    monkeypatch.setattr(
        "apps.channels.max.outbound.send_chat_action", lambda **kwargs: {"ok": True}
    )
    monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock())
    monkeypatch.setattr(max_handler, "maybe_weave_question", lambda _c, _b, reply: reply)
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    monkeypatch.setattr(short_term, "_redis_client", lambda: _FakeRedis())


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id=None, user_id=None, text, attachments=None, timeout=10.0, bot=None):
        calls.append({"text": text, "att": attachments})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


_UID = iter(range(79501, 79599))


def _welcomed_user():
    from apps.consent.services import record_global_consent

    user_id = next(_UID)
    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), chat_id=str(_CHAT)
    )
    bot_user.welcomed_at = timezone.now()
    bot_user.save(update_fields=["welcomed_at"])
    record_global_consent(
        bot_user,
        consent_type="personal_data",
        source="test:drf1772",
        document_version="welcome-s2-v1",
    )
    bot_user.refresh_from_db()
    return user_id, bot_user, resolve_active_global_conversation(bot_user)


def _card_for(bot_user) -> Recommendation:
    return Recommendation.objects.create(
        bot_user=bot_user,
        goal_id="goal-1",
        kind=Recommendation.Kind.DIRECTION,
        what="Уменьшить утреннюю отёчность",
        subline="Сфокусируемся на этом.",
        why=["Ты сказала, что хочешь привести себя в порядок", "Ты выбрала: лицо и кожа"],
        facts={"goal": "Привести себя в порядок", "area": "Лицо и кожа"},
        fingerprint="fp-1",
    )


def _tap(user_id: int, payload: str) -> dict:
    return {
        "update_type": "message_callback",
        "timestamp": 1731320000000,
        "callback": {
            "timestamp": 1731320000500,
            "callback_id": f"cb-{uuid.uuid4()}",
            "payload": payload,
            "user": {"user_id": user_id, "name": "Анна", "lang": "ru"},
        },
        "message": {
            "recipient": {"chat_id": _CHAT, "chat_type": "dialog"},
            "body": {"mid": "m-card", "seq": 1, "text": "карточка", "attachments": []},
        },
    }


def _run(payload: dict) -> None:
    max_handler.handle_global_max_event(payload, trace_id=str(uuid.uuid4()))


class TestTaps:
    def test_why_opens_c04_2_and_records_the_reaction(self, sent):
        user_id, bot_user, conversation = _welcomed_user()
        card = _card_for(bot_user)

        _run(_tap(user_id, f"{c.RECO_WHY_PREFIX}{card.id}"))

        assert sent[-1]["text"].startswith(c.WHY_MORE_HEAD)
        assert sent[-1]["text"].count("✓") == 2
        card.refresh_from_db()
        assert card.reaction == Recommendation.Reaction.WHY_REQUESTED
        assert card.reacted_at is not None
        # Ответ бота — в переписке; сырой payload как реплика человека — нет.
        rows = list(Message.all_tenants.filter(conversation=conversation).order_by("created_at"))
        assert [r.role for r in rows] == ["assistant"]
        assert rows[0].action_type == "recommendation_reaction"

    def test_alternative_is_an_honest_stub(self, sent):
        user_id, bot_user, _ = _welcomed_user()
        card = _card_for(bot_user)
        _run(_tap(user_id, f"{c.RECO_ALT_PREFIX}{card.id}"))
        assert sent[-1]["text"] == taps.ALT_STUB_TEXT
        card.refresh_from_db()
        assert card.reaction == Recommendation.Reaction.ALTERNATIVE_REQUESTED

    def test_not_now_closes_with_rejected(self, sent):
        user_id, bot_user, _ = _welcomed_user()
        card = _card_for(bot_user)
        _run(_tap(user_id, f"{c.RECO_SKIP_PREFIX}{card.id}"))
        assert sent[-1]["text"] == taps.SKIP_TEXT
        card.refresh_from_db()
        assert card.reaction == Recommendation.Reaction.REJECTED

    def test_someone_elses_card_is_stale_and_untouched(self, sent):
        user_id, _bot_user, _ = _welcomed_user()
        _other_id, other_user, _ = _welcomed_user()
        card = _card_for(other_user)
        _run(_tap(user_id, f"{c.RECO_WHY_PREFIX}{card.id}"))
        assert sent[-1]["text"] == taps.STALE_TEXT
        card.refresh_from_db()
        assert card.reaction == Recommendation.Reaction.NONE

    def test_malformed_id_is_stale(self, sent):
        user_id, _bot_user, _ = _welcomed_user()
        _run(_tap(user_id, f"{c.RECO_WHY_PREFIX}not-a-uuid"))
        assert sent[-1]["text"] == taps.STALE_TEXT
