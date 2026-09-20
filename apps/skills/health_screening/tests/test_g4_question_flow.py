"""[OD-BOT §164] G4 question flow — one persisted state, every surface.

What is proven here, on a REAL ``Conversation`` row (the state is re-read
from the database between steps, never carried in memory):

* the binding question ``health_screening.g4`` survives a new request and a
  fresh model instance; a foreign reply cannot close it (``close_question``);
  an ordinary open question cannot replace it; re-asking keeps one slot;
* per-tenant registry dispatch (MAX and Telegram share it): the ambiguous
  message asks, the next reply is bound to health_screening BEFORE booking —
  «не знаю», «нет», a booking intent all keep the restriction and never reach
  the booking skill; an explicit / recent-resolved sign → STOP G4; a G6 sign
  → STOP G6; the crisis gate runs before any of it;
* global concierge path: the same outcomes, decided BEFORE the model — the
  provider is never called on those turns;
* the B13 two-hour TTL: an expired question reads as «not asked», the
  restriction lapses with it (named, not hidden);
* the G1–G3 / G5–G7 and G6 slices are unchanged.

Technical checks only. Implementation of registered owner policy does not
constitute CLINICAL APPROVED, PHYSICIAN PASS, or SAFE FOR PILOT.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from django.utils import timezone

from apps.conversations.models import Conversation
from apps.conversations.services import (
    resolve_active_global_conversation,
    resolve_conversation_for_bot_user,
    write_skill_state,
)
from apps.identity.services import resolve_or_create_global_bot_user
from apps.orchestrator import concierge
from apps.orchestrator.concierge import generate_concierge_reply
from apps.orchestrator.open_question import (
    STATE_KEY,
    close_question,
    open_question,
    pending_question,
)
from apps.orchestrator.safety.gate import CRISIS_REPLY_TEXT, evaluate_inbound
from apps.orchestrator.safety.medical_emergency import MEDICAL_EMERGENCY_TEXT_V2
from apps.skills.base import SkillContext
from apps.skills.health_screening.classifier import PainSignal, classify, detect_g6
from apps.skills.health_screening.g4_question import (
    G4_QUESTION_ID,
    G4_ROUTING_QUESTION,
    g4_pending,
)
from apps.skills.health_screening.skill import HealthScreeningSkill
from apps.skills.registry import dispatch
from apps.tenancy.context import tenant_scope

pytestmark = pytest.mark.django_db(transaction=True)

AMBIGUOUS = "немеет рука иногда"
UNKNOWN = "не знаю"
PLAIN_NO = "нет"
BOOKING_INTENT = "запишите меня на массаж в пятницу"
POSITIVE = "да, внезапно онемела правая сторона"
RECENT_RESOLVED = "речь нарушилась, но уже прошло"
G6_ANSWER = "после крема опухли губы"
CRISIS = "я думаю о суициде"


def _fresh(conversation: Conversation) -> Conversation:
    """The row as the NEXT request would see it — never the cached object."""

    return Conversation.all_tenants.get(pk=conversation.pk)


def _bot_user_and_conversation(suffix: str):
    bot_user = resolve_or_create_global_bot_user(
        channel="max",
        channel_user_id=f"g4flow-{suffix}-uid",
        chat_id=f"g4flow-{suffix}-chat",
    )
    return bot_user, resolve_active_global_conversation(bot_user)


def _ctx(text: str, conversation: Conversation, bot_user=None) -> SkillContext:
    return SkillContext(conversation=conversation, bot_user=bot_user or Mock(), message_text=text)


def _dispatch(text: str, conversation: Conversation, bot_user):
    """Per-tenant registry dispatch — the seam MAX and Telegram share."""

    with tenant_scope(conversation.tenant):
        return dispatch(_ctx(text, conversation, bot_user))


# --------------------------------------------------------------------------- #
# Persistence of the state itself                                             #
# --------------------------------------------------------------------------- #


class TestPersistedState:
    def test_the_question_survives_a_fresh_read_of_the_conversation(self) -> None:
        bot_user, conversation = _bot_user_and_conversation("persist")
        HealthScreeningSkill().handle(_ctx(AMBIGUOUS, conversation))

        again = _fresh(conversation)
        assert g4_pending(again) is True
        pending = pending_question(again)
        assert pending is not None
        assert pending.question_id == G4_QUESTION_ID
        assert pending.binding is True
        assert pending.asked_text == G4_ROUTING_QUESTION

    def test_a_fresh_skill_on_a_fresh_row_keeps_the_restriction(self) -> None:
        bot_user, conversation = _bot_user_and_conversation("fresh-skill")
        HealthScreeningSkill().handle(_ctx(AMBIGUOUS, conversation))

        result = HealthScreeningSkill().handle(_ctx(UNKNOWN, _fresh(conversation)))
        assert result.meta["reply_kind"] == "health_restriction_persists"
        assert g4_pending(_fresh(conversation)) is True

    def test_a_foreign_reply_cannot_close_the_binding_question(self) -> None:
        bot_user, conversation = _bot_user_and_conversation("foreign")
        HealthScreeningSkill().handle(_ctx(AMBIGUOUS, conversation))

        assert close_question(_fresh(conversation), "а сколько стоит маникюр?") is None
        assert g4_pending(_fresh(conversation)) is True

    def test_an_ordinary_open_question_cannot_replace_it(self) -> None:
        bot_user, conversation = _bot_user_and_conversation("replace")
        HealthScreeningSkill().handle(_ctx(AMBIGUOUS, conversation))

        open_question(_fresh(conversation), "ask_clarification", asked_text="какой город?")
        pending = pending_question(_fresh(conversation))
        assert pending is not None and pending.question_id == G4_QUESTION_ID

    def test_re_asking_is_idempotent_one_slot(self) -> None:
        bot_user, conversation = _bot_user_and_conversation("idempotent")
        skill = HealthScreeningSkill()
        skill.handle(_ctx(AMBIGUOUS, conversation))
        first = pending_question(_fresh(conversation))
        skill.handle(_ctx(AMBIGUOUS, _fresh(conversation)))
        skill.handle(_ctx(UNKNOWN, _fresh(conversation)))

        state = _fresh(conversation).skill_state
        assert isinstance(state.get(STATE_KEY), dict)
        assert [k for k in state if k == STATE_KEY] == [STATE_KEY]
        second = pending_question(_fresh(conversation))
        assert first is not None and second is not None
        assert second.question_id == first.question_id == G4_QUESTION_ID
        assert second.asked_at >= first.asked_at

    def test_the_state_is_reachable_from_the_bot_user_alone(self) -> None:
        """The Mini App has no conversation in hand — it resolves the same row."""

        bot_user, conversation = _bot_user_and_conversation("by-bot-user")
        HealthScreeningSkill().handle(_ctx(AMBIGUOUS, conversation))

        resolved = resolve_conversation_for_bot_user(bot_user)
        assert resolved is not None and resolved.pk == conversation.pk
        assert g4_pending(resolved) is True

    def test_the_b13_ttl_ends_the_restriction_named_not_hidden(self) -> None:
        bot_user, conversation = _bot_user_and_conversation("ttl")
        HealthScreeningSkill().handle(_ctx(AMBIGUOUS, conversation))
        row = dict(_fresh(conversation).skill_state[STATE_KEY])
        row["at"] = (timezone.now() - timedelta(hours=2, minutes=1)).isoformat()
        with tenant_scope(conversation.tenant):
            write_skill_state(_fresh(conversation), STATE_KEY, row)

        assert g4_pending(_fresh(conversation)) is False
        # The next ambiguous message asks again — the flow restarts, nothing leaks.
        result = HealthScreeningSkill().handle(_ctx(AMBIGUOUS, _fresh(conversation)))
        assert result.meta["reply_kind"] == "health_clarify_g4"


# --------------------------------------------------------------------------- #
# Per-tenant registry (MAX + Telegram)                                        #
# --------------------------------------------------------------------------- #


class TestPerTenantRegistry:
    def test_ambiguous_asks_the_one_question(self) -> None:
        bot_user, conversation = _bot_user_and_conversation("pt-ask")
        result = _dispatch(AMBIGUOUS, conversation, bot_user)
        assert result is not None
        assert result.reply_text == G4_ROUTING_QUESTION
        assert result.meta == {"reply_kind": "health_clarify_g4", "s1_group": "G4"}
        assert g4_pending(_fresh(conversation)) is True

    @pytest.mark.parametrize("reply", (UNKNOWN, PLAIN_NO, BOOKING_INTENT, "хочу маникюр"))
    def test_unknown_no_or_new_intent_never_reaches_booking(self, reply: str) -> None:
        bot_user, conversation = _bot_user_and_conversation(f"pt-{abs(hash(reply)) % 9973}")
        _dispatch(AMBIGUOUS, conversation, bot_user)

        result = _dispatch(reply, _fresh(conversation), bot_user)
        assert result is not None
        assert result.meta["reply_kind"] == "health_restriction_persists"
        assert result.meta["s1_group"] == "G4"
        assert result.reply_text == G4_ROUTING_QUESTION
        assert result.reply_text != MEDICAL_EMERGENCY_TEXT_V2
        assert "запис" not in result.reply_text.lower()
        assert g4_pending(_fresh(conversation)) is True

    @pytest.mark.parametrize("reply", (POSITIVE, RECENT_RESOLVED))
    def test_positive_or_recent_resolved_answer_is_stop_g4(self, reply: str) -> None:
        bot_user, conversation = _bot_user_and_conversation(f"pt-pos-{abs(hash(reply)) % 9973}")
        _dispatch(AMBIGUOUS, conversation, bot_user)

        result = _dispatch(reply, _fresh(conversation), bot_user)
        assert result is not None
        assert result.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        assert result.meta == {"reply_kind": "health_red_flag", "s1_group": "G4"}
        assert g4_pending(_fresh(conversation)) is False

    def test_g6_answer_is_stop_g6_precedence_kept(self) -> None:
        bot_user, conversation = _bot_user_and_conversation("pt-g6")
        _dispatch(AMBIGUOUS, conversation, bot_user)

        assert detect_g6(G6_ANSWER)
        result = _dispatch(G6_ANSWER, _fresh(conversation), bot_user)
        assert result is not None
        assert result.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        assert result.meta == {"reply_kind": "health_red_flag", "s1_group": "G6"}
        assert g4_pending(_fresh(conversation)) is False

    def test_crisis_is_decided_by_the_gate_before_any_skill(self) -> None:
        """Both channel handlers run ``evaluate_inbound`` before dispatch; the
        pending question does not swallow it."""

        bot_user, conversation = _bot_user_and_conversation("pt-crisis")
        _dispatch(AMBIGUOUS, conversation, bot_user)

        inbound = evaluate_inbound(CRISIS)
        assert not inbound.allowed
        assert inbound.reply_text == CRISIS_REPLY_TEXT
        assert g4_pending(_fresh(conversation)) is True  # untouched by the gate

    def test_explicit_g4_is_stop_without_a_question(self) -> None:
        bot_user, conversation = _bot_user_and_conversation("pt-explicit")
        result = _dispatch("Внезапно перекосило лицо", conversation, bot_user)
        assert result is not None
        assert result.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        assert result.meta["s1_group"] == "G4"
        assert g4_pending(_fresh(conversation)) is False


# --------------------------------------------------------------------------- #
# Global concierge path                                                       #
# --------------------------------------------------------------------------- #


class TestGlobalConciergePath:
    def _provider(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        provider = AsyncMock()
        router = Mock()
        router.get_provider.return_value = provider
        monkeypatch.setattr(concierge, "get_router", lambda: router)
        return provider

    def test_ambiguous_asks_before_the_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = self._provider(monkeypatch)
        bot_user, conversation = _bot_user_and_conversation("gl-ask")

        reply = generate_concierge_reply(AMBIGUOUS, bot_user=bot_user, conversation=conversation)
        assert reply.text == G4_ROUTING_QUESTION
        assert reply.persisted is True
        provider.complete.assert_not_called()
        assert g4_pending(_fresh(conversation)) is True

    @pytest.mark.parametrize("reply_text", (UNKNOWN, PLAIN_NO, BOOKING_INTENT))
    def test_unknown_no_or_new_intent_is_answered_before_the_model(
        self, monkeypatch: pytest.MonkeyPatch, reply_text: str
    ) -> None:
        provider = self._provider(monkeypatch)
        bot_user, conversation = _bot_user_and_conversation(f"gl-{abs(hash(reply_text)) % 9973}")
        generate_concierge_reply(AMBIGUOUS, bot_user=bot_user, conversation=conversation)

        reply = generate_concierge_reply(
            reply_text, bot_user=bot_user, conversation=_fresh(conversation)
        )
        assert reply.text == G4_ROUTING_QUESTION
        provider.complete.assert_not_called()
        assert g4_pending(_fresh(conversation)) is True

    @pytest.mark.parametrize(
        ("reply_text", "group"), ((POSITIVE, "G4"), (RECENT_RESOLVED, "G4"), (G6_ANSWER, "G6"))
    )
    def test_positive_answers_stop_before_the_model(
        self, monkeypatch: pytest.MonkeyPatch, reply_text: str, group: str
    ) -> None:
        provider = self._provider(monkeypatch)
        bot_user, conversation = _bot_user_and_conversation(f"gl-stop-{group}-{len(reply_text)}")
        generate_concierge_reply(AMBIGUOUS, bot_user=bot_user, conversation=conversation)

        reply = generate_concierge_reply(
            reply_text, bot_user=bot_user, conversation=_fresh(conversation)
        )
        assert reply.text == MEDICAL_EMERGENCY_TEXT_V2
        provider.complete.assert_not_called()
        assert g4_pending(_fresh(conversation)) is False


# --------------------------------------------------------------------------- #
# Other groups unchanged                                                      #
# --------------------------------------------------------------------------- #


class TestOtherGroupsUnchanged:
    @pytest.mark.parametrize(
        "text",
        (
            "не могу вдохнуть",
            "потеряла сознание",
            "сильная боль в груди",
            "кровь не останавливается",
            "после лекарства внезапно опухли губы",
            "онемела рука и температура 38.5",
            "резко стало очень плохо",
            "Болит шея, отдаёт в руку",
        ),
    )
    def test_explicit_red_flags_are_still_stop_never_a_question(self, text: str) -> None:
        assert classify(text) is PainSignal.RED_FLAG
        bot_user, conversation = _bot_user_and_conversation(f"other-{abs(hash(text)) % 9973}")
        result = HealthScreeningSkill().handle(_ctx(text, conversation))
        assert result.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        assert g4_pending(_fresh(conversation)) is False

    def test_soft_pain_still_gets_the_pain_questions(self) -> None:
        bot_user, conversation = _bot_user_and_conversation("soft")
        result = HealthScreeningSkill().handle(_ctx("Болит спина", conversation))
        assert result.meta["reply_kind"] == "health_soft_pain"
        assert g4_pending(_fresh(conversation)) is False
