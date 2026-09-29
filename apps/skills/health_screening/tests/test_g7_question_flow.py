"""[OD-BOT §170] G7 question flow — one router, every surface.

Proven on REAL ``Conversation`` / ``BotUser`` rows (re-read between steps):

* the classifier: G1–G6 always win over G7; explicit G7 is STOP without a
  question; the ambiguous forms ask; the «not G7» list and remote history do
  not; the historical 18.09 wording is not used by the runtime;
* the question contract: the exact text, three verbatim labels, callbacks bound
  to the slot's one-time token;
* persistence and isolation: one slot, same token on a repeat; ``close_question``
  and another question cannot touch it; the B13 TTL ends it (no restriction
  exists to outlive it — the named consequence); a new conversation has no
  question; another user's / a forged / a stale token is never an answer; a
  redelivered tap is idempotent;
* answer №2 with a restriction on record lifts nothing — ``clear_restriction``
  is never called and still refuses;
* the global concierge answers every G7 turn BEFORE the model; the Mini App
  gate never forwards while the question is open and takes only a live token.

Technical checks only. Implementation of the registered G7 owner contract does
not constitute CLINICAL APPROVED, PHYSICIAN PASS, or SAFE FOR PILOT.
"""

from __future__ import annotations

import inspect
from datetime import timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from django.utils import timezone

from apps.conversations.models import Conversation
from apps.conversations.services import (
    close_conversation,
    resolve_active_global_conversation,
    write_skill_state,
)
from apps.identity.models import BotUser
from apps.identity.services import resolve_or_create_global_bot_user
from apps.miniapp_api.health_gate import KIND_CLARIFY, KIND_RED_FLAG, screen_goal_body
from apps.orchestrator import concierge
from apps.orchestrator.concierge import generate_concierge_reply
from apps.orchestrator.open_question import STATE_KEY, close_question, open_question
from apps.orchestrator.safety.gate import evaluate_inbound
from apps.orchestrator.safety.medical_emergency import MEDICAL_EMERGENCY_TEXT_V2
from apps.orchestrator.safety.pre_check import SafetyVerdict
from apps.orchestrator.safety.s1_restriction import (
    RecheckNotRegistered,
    clear_restriction,
    mark_stop,
    restriction,
)
from apps.skills.base import SkillContext
from apps.skills.health_screening import g7_question
from apps.skills.health_screening.classifier import (
    PainSignal,
    clarify_group,
    classify,
    s1_group_of,
)
from apps.skills.health_screening.g7_question import (
    ANSWER_LABELS,
    ANSWERS,
    G7_QUESTION_ID,
    G7_QUESTION_TEXT,
    OUTSIDE_S1_G7_ACK,
    ask_g7,
    buttons_of,
    g7_callback,
    g7_pending,
    parse_g7_callback,
    route_g7_turn,
)
from apps.skills.health_screening.skill import HealthScreeningSkill
from apps.skills.registry import dispatch
from apps.tenancy.context import tenant_scope

pytestmark = pytest.mark.django_db(transaction=True)

AMBIGUOUS = "Мне резко стало очень плохо"
BOOKING_INTENT = "запишите меня на массаж в пятницу"
HISTORICAL_18_09 = (
    "Прямо сейчас тебе трудно дышать, стоять, говорить, ты теряешь сознание "
    "или состояние быстро ухудшается?"
)


def _fresh(conversation: Conversation) -> Conversation:
    return Conversation.all_tenants.get(pk=conversation.pk)


def _fresh_user(bot_user: BotUser) -> BotUser:
    return BotUser.all_tenants.get(pk=bot_user.pk)


def _token(conversation: Conversation) -> str:
    pending = g7_pending(_fresh(conversation))
    assert pending is not None
    return pending.token


def _pair(suffix: str, channel: str = "max"):
    bot_user = resolve_or_create_global_bot_user(
        channel=channel, channel_user_id=f"g7flow-{suffix}-uid", chat_id=f"g7flow-{suffix}-chat"
    )
    return bot_user, resolve_active_global_conversation(bot_user)


def _dispatch(text: str, conversation: Conversation, bot_user: BotUser):
    conversation, bot_user = _fresh(conversation), _fresh_user(bot_user)
    with tenant_scope(conversation.tenant):
        return dispatch(
            SkillContext(conversation=conversation, bot_user=bot_user, message_text=text)
        )


def _tap(answer: str, conversation: Conversation, bot_user: BotUser):
    pending = g7_pending(_fresh(conversation))
    assert pending is not None
    return _dispatch(g7_callback(answer, pending.token), conversation, bot_user)


# --------------------------------------------------------------------------- #
# Classifier: G7 is a fallback                                                #
# --------------------------------------------------------------------------- #


class TestPrecedence:
    @pytest.mark.parametrize(
        ("text", "group"),
        (
            ("Мне резко стало очень плохо, не могу вдохнуть", "G1"),
            ("Мне очень плохо, потеряла сознание", "G2"),
            ("Резко стало плохо, сильная боль в груди", "G3"),
            ("Внезапно стало совсем плохо, перекосило лицо", "G4"),
            ("Мне очень плохо, кровь не останавливается", "G5"),
            ("Мне резко стало очень плохо, опух язык", "G6"),
            # a speech sign is G4 before it is G7 ([OD-BOT §170] решение 1)
            ("не могу нормально говорить", "G4"),
        ),
    )
    def test_g1_to_g6_win_over_g7(self, text: str, group: str) -> None:
        assert classify(text) is PainSignal.RED_FLAG
        assert s1_group_of(text) == group
        assert clarify_group(text) is None

    @pytest.mark.parametrize(
        "text",
        (
            "мне очень плохо, сейчас упаду",
            "появилась спутанность",
            "не могу самостоятельно стоять",
            "состояние быстро ухудшается",
        ),
    )
    def test_explicit_g7_is_stop_without_a_question(self, text: str) -> None:
        assert classify(text) is PainSignal.RED_FLAG
        assert s1_group_of(text) == "G7"

    @pytest.mark.parametrize(
        "text",
        (
            "Мне резко стало очень плохо",
            "Мне внезапно совсем плохо",
            "Не понимаю, что со мной, становится хуже",
            "Было просто очень плохо, но не знаю как",
        ),
    )
    def test_ambiguous_g7_is_the_g7_question(self, text: str) -> None:
        assert classify(text) is PainSignal.CLARIFY
        assert clarify_group(text) == "G7"
        assert s1_group_of(text) is None

    def test_an_ambiguous_g4_sign_asks_g4_first(self) -> None:
        assert clarify_group("мне резко стало очень плохо и немеет рука") == "G4"

    def test_the_runtime_does_not_use_the_historical_wording(self) -> None:
        source = inspect.getsource(g7_question)
        # presence first: the registered wording IS in the module …
        assert "кажется, что вы вот-вот потеряете сознание" in source
        assert G7_QUESTION_TEXT != HISTORICAL_18_09
        # … and the historical 18.09 wording is not
        assert HISTORICAL_18_09 not in source


class TestQuestionContract:
    def test_exact_text_and_labels(self) -> None:
        assert G7_QUESTION_ID == "health_screening.g7"
        assert G7_QUESTION_TEXT == (
            "Сейчас есть хотя бы один из признаков: кажется, что вы вот-вот потеряете "
            "сознание; трудно самостоятельно стоять, говорить или дышать; появилась "
            "спутанность; состояние быстро ухудшается?"
        )
        assert ANSWERS == ("yes", "no", "unsure")
        assert ANSWER_LABELS == {
            "yes": "Да, есть хотя бы один признак",
            "no": "Нет — этих признаков не было и сейчас нет, состояние не ухудшается",
            "unsure": "Не уверен(а) или не могу ответить",
        }

    def test_callbacks_are_bound_to_a_token(self) -> None:
        assert g7_callback("no", "0123456789ab") == "cb:s1g7:no:0123456789ab"
        assert parse_g7_callback("cb:s1g7:yes:0123456789ab") == ("yes", "0123456789ab")
        for bad in (
            "cb:s1g7:no",
            "cb:s1g7:no:XYZ",
            "cb:s1g7:maybe:0123456789ab",
            ANSWER_LABELS["no"],
            "нет",
        ):
            assert parse_g7_callback(bad) is None, bad


# --------------------------------------------------------------------------- #
# Persistence and isolation                                                   #
# --------------------------------------------------------------------------- #


class TestPersistence:
    def test_one_slot_no_restriction_same_token_on_repeat(self) -> None:
        bot_user, conversation = _pair("slot")
        first = _dispatch(AMBIGUOUS, conversation, bot_user)
        token = _token(conversation)
        assert [b["callback"] for b in buttons_of(first.action_data)] == [
            g7_callback(a, token) for a in ANSWERS
        ]
        _dispatch("Мне внезапно совсем плохо", conversation, bot_user)
        assert _token(conversation) == token
        assert restriction(_fresh_user(bot_user)) is None

    def test_close_question_and_another_question_cannot_touch_it(self) -> None:
        bot_user, conversation = _pair("binding")
        _dispatch(AMBIGUOUS, conversation, bot_user)
        assert close_question(_fresh(conversation), "а сколько стоит маникюр?") is None
        open_question(
            _fresh(conversation), "concierge.ask_clarification", asked_text="Какой город?"
        )
        pending = g7_pending(_fresh(conversation))
        assert pending is not None and pending.question_id == G7_QUESTION_ID

    @pytest.mark.parametrize(
        "reply", ("нет", "неа", "прошло", "сейчас нормально", "всё хорошо", BOOKING_INTENT)
    )
    def test_free_text_is_not_an_answer(self, reply: str) -> None:
        bot_user, conversation = _pair(f"free-{abs(hash(reply)) % 99991}")
        _dispatch(AMBIGUOUS, conversation, bot_user)
        result = _dispatch(reply, conversation, bot_user)
        assert result.reply_text == G7_QUESTION_TEXT
        assert g7_pending(_fresh(conversation)) is not None
        assert restriction(_fresh_user(bot_user)) is None

    def test_the_label_typed_by_hand_is_not_an_answer(self) -> None:
        bot_user, conversation = _pair("typed")
        _dispatch(AMBIGUOUS, conversation, bot_user)
        result = _dispatch(ANSWER_LABELS["no"], conversation, bot_user)
        assert result.reply_text == G7_QUESTION_TEXT
        assert g7_pending(_fresh(conversation)) is not None

    def test_a_forged_or_foreign_token_is_not_an_answer(self) -> None:
        a_user, a_conv = _pair("iso-a")
        b_user, b_conv = _pair("iso-b", channel="telegram")
        _dispatch(AMBIGUOUS, a_conv, a_user)
        _dispatch(AMBIGUOUS, b_conv, b_user)
        a_token = _token(a_conv)
        # B taps A's button; and a forged token
        for text in (g7_callback("no", a_token), g7_callback("no", "0" * 12)):
            result = _dispatch(text, b_conv, b_user)
            assert result.reply_text == G7_QUESTION_TEXT
            assert g7_pending(_fresh(b_conv)) is not None
        assert g7_pending(_fresh(a_conv)) is not None  # A untouched

    def test_answer_yes_is_durable_and_idempotent(self) -> None:
        bot_user, conversation = _pair("yes")
        _dispatch(AMBIGUOUS, conversation, bot_user)
        token = _token(conversation)
        first = _tap("yes", conversation, bot_user)
        assert first.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        again = _dispatch(g7_callback("yes", token), conversation, bot_user)  # redelivery
        assert again.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        rec = restriction(_fresh_user(bot_user))
        assert rec is not None and rec.status == "stop" and rec.group == "G7"

    def test_answer_no_then_a_stale_redelivery_changes_nothing(self) -> None:
        bot_user, conversation = _pair("no-stale")
        _dispatch(AMBIGUOUS, conversation, bot_user)
        token = _token(conversation)
        assert _tap("no", conversation, bot_user).meta["reply_kind"] == "health_outside_s1_g7"
        stale = _dispatch(g7_callback("no", token), conversation, bot_user)
        assert stale.meta["reply_kind"] == "health_g7_stale_tap"
        # owner decisions on PR #1982: not an accepted action — no text at all,
        # and never the acknowledgement reserved for a real answer
        assert stale.should_send is False
        assert stale.reply_text == ""
        assert g7_pending(_fresh(conversation)) is None  # a tap never re-opens the question
        assert restriction(_fresh_user(bot_user)) is None

    def test_after_answer_no_the_flow_is_open(self) -> None:
        bot_user, conversation = _pair("no-open")
        _dispatch(AMBIGUOUS, conversation, bot_user)
        _tap("no", conversation, bot_user)
        ctx = SkillContext(
            conversation=_fresh(conversation),
            bot_user=_fresh_user(bot_user),
            message_text=BOOKING_INTENT,
        )
        assert HealthScreeningSkill().matches(ctx) is False

    def test_the_ttl_ends_the_question_and_nothing_outlives_it(self) -> None:
        """The named consequence of [OD-BOT §170] решение 4 + prompt §12: no
        durable restriction exists, so after the B13 TTL the flow is open."""

        bot_user, conversation = _pair("ttl")
        _dispatch(AMBIGUOUS, conversation, bot_user)
        row = dict(_fresh(conversation).skill_state[STATE_KEY])
        row["at"] = (timezone.now() - timedelta(hours=2, minutes=1)).isoformat()
        with tenant_scope(conversation.tenant):
            write_skill_state(_fresh(conversation), STATE_KEY, row)
        assert g7_pending(_fresh(conversation)) is None
        assert restriction(_fresh_user(bot_user)) is None

    def test_a_new_conversation_has_no_question(self) -> None:
        bot_user, conversation = _pair("session")
        _dispatch(AMBIGUOUS, conversation, bot_user)
        close_conversation(_fresh(conversation), outcome=Conversation.Outcome.values[0])
        fresh = resolve_active_global_conversation(_fresh_user(bot_user))
        assert fresh is not None and fresh.pk != conversation.pk
        assert g7_pending(fresh) is None
        assert restriction(_fresh_user(bot_user)) is None


class TestActiveRestriction:
    def test_answer_no_lifts_nothing_and_never_clears(self) -> None:
        bot_user, conversation = _pair("active")
        assert ask_g7(conversation)
        assert mark_stop(
            bot_user, group="G4", question_id="health_screening.g4", source="t", reason="x"
        )
        token = _token(conversation)
        with patch("apps.orchestrator.safety.s1_restriction.clear_restriction") as clear:
            outcome = route_g7_turn(
                _fresh(conversation), _fresh_user(bot_user), g7_callback("no", token)
            )
        clear.assert_not_called()
        assert outcome.kind == "restriction_persists"
        rec = restriction(_fresh_user(bot_user))
        assert rec is not None and rec.status == "stop" and rec.group == "G4"

    def test_clear_restriction_still_refuses(self) -> None:
        bot_user, _ = _pair("refuse")
        with pytest.raises(RecheckNotRegistered):
            clear_restriction(bot_user, provenance={"mechanism": "safety_recheck"})


class TestNoAcknowledgement:
    """Owner decisions on PR #1982 (22.09), point 1 — the exact string, and only
    for a live structured №2 on the first ambiguous turn with no restriction and
    no other S1 group."""

    def test_the_exact_owner_string(self) -> None:
        assert OUTSIDE_S1_G7_ACK == "Спасибо, что уточнили. Чем могу помочь дальше?"

    def test_shown_for_a_live_answer_no(self) -> None:
        bot_user, conversation = _pair("ack-yes")
        _dispatch(AMBIGUOUS, conversation, bot_user)
        result = _tap("no", conversation, bot_user)
        assert result.reply_text == OUTSIDE_S1_G7_ACK

    @pytest.mark.parametrize("reply", ("нет", ANSWER_LABELS["no"]))
    def test_never_for_an_unstructured_answer(self, reply: str) -> None:
        bot_user, conversation = _pair(f"ack-free-{abs(hash(reply)) % 99991}")
        _dispatch(AMBIGUOUS, conversation, bot_user)
        result = _dispatch(reply, conversation, bot_user)
        assert result.reply_text == G7_QUESTION_TEXT
        assert result.reply_text != OUTSIDE_S1_G7_ACK

    def test_never_with_an_active_restriction(self) -> None:
        bot_user, conversation = _pair("ack-active")
        _dispatch(AMBIGUOUS, conversation, bot_user)
        token = _token(conversation)
        assert mark_stop(
            _fresh_user(bot_user),
            group="G4",
            question_id="health_screening.g4",
            source="t",
            reason="x",
        )
        result = _dispatch(g7_callback("no", token), conversation, bot_user)
        assert result.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        assert result.reply_text != OUTSIDE_S1_G7_ACK

    def test_never_with_another_s1_group(self) -> None:
        bot_user, conversation = _pair("ack-other")
        _dispatch(AMBIGUOUS, conversation, bot_user)
        result = _dispatch("губы и язык опухли", conversation, bot_user)
        assert result.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        # the question is resolved by the STOP; a later «Нет» tap gets no ack
        assert g7_pending(_fresh(conversation)) is None
        later = _dispatch(g7_callback("no", "0" * 12), conversation, bot_user)
        assert later.reply_text != OUTSIDE_S1_G7_ACK

    def test_an_ambiguous_g4_sign_hands_over_to_the_g4_question(self) -> None:
        bot_user, conversation = _pair("ack-g4")
        _dispatch(AMBIGUOUS, conversation, bot_user)
        result = _dispatch("немеет рука иногда", conversation, bot_user)
        assert result.meta["reply_kind"] == "health_clarify_g4"
        assert g7_pending(_fresh(conversation)) is None
        rec = restriction(_fresh_user(bot_user))
        assert rec is not None and rec.status == "open" and rec.group == "G4"


class TestOtherRoutesUnchanged:
    def test_crisis_route_is_unchanged(self) -> None:
        outcome = evaluate_inbound("я думаю о суициде")
        assert not outcome.allowed and outcome.verdict == SafetyVerdict.HANDOFF.value

    def test_an_explicit_sign_while_the_question_is_open_is_stop_with_its_label(self) -> None:
        bot_user, conversation = _pair("explicit-during")
        _dispatch(AMBIGUOUS, conversation, bot_user)
        result = _dispatch("губы и язык опухли", conversation, bot_user)
        assert result.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        assert result.meta["s1_group"] == "G6"


# --------------------------------------------------------------------------- #
# Global concierge: before the model                                          #
# --------------------------------------------------------------------------- #


class TestGlobalConcierge:
    def _provider(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        provider = AsyncMock()
        router = Mock()
        router.get_provider.return_value = provider
        monkeypatch.setattr(concierge, "get_router", lambda: router)
        return provider

    def test_ambiguous_asks_before_the_model_with_buttons(self, monkeypatch) -> None:
        provider = self._provider(monkeypatch)
        bot_user, conversation = _pair("gl-ask")
        reply = generate_concierge_reply(AMBIGUOUS, bot_user=bot_user, conversation=conversation)
        assert reply.text == G7_QUESTION_TEXT
        assert reply.action_data is not None and len(buttons_of(reply.action_data)) == 3
        provider.complete.assert_not_called()

    @pytest.mark.parametrize("reply_text", ("нет", BOOKING_INTENT))
    def test_free_text_is_answered_before_the_model(self, monkeypatch, reply_text: str) -> None:
        provider = self._provider(monkeypatch)
        bot_user, conversation = _pair(f"gl-free-{abs(hash(reply_text)) % 99991}")
        generate_concierge_reply(AMBIGUOUS, bot_user=bot_user, conversation=conversation)
        reply = generate_concierge_reply(
            reply_text, bot_user=bot_user, conversation=_fresh(conversation)
        )
        assert reply.text == G7_QUESTION_TEXT
        provider.complete.assert_not_called()

    def test_a_tap_is_answered_before_the_model(self, monkeypatch) -> None:
        provider = self._provider(monkeypatch)
        bot_user, conversation = _pair("gl-tap")
        generate_concierge_reply(AMBIGUOUS, bot_user=bot_user, conversation=conversation)
        token = _token(conversation)
        reply = generate_concierge_reply(
            g7_callback("yes", token), bot_user=bot_user, conversation=_fresh(conversation)
        )
        assert reply.text == MEDICAL_EMERGENCY_TEXT_V2
        provider.complete.assert_not_called()


# --------------------------------------------------------------------------- #
# Mini App gate                                                               #
# --------------------------------------------------------------------------- #


class TestMiniAppGate:
    @pytest.mark.parametrize("field", ("goal_text", "answer.text"))
    def test_ambiguous_is_the_g7_frame_and_nothing_is_forwarded(self, field: str) -> None:
        bot_user, _ = _pair(f"ma-{field}")
        body = {"goal_text": AMBIGUOUS} if field == "goal_text" else {"answer": {"text": AMBIGUOUS}}
        stop, _ = screen_goal_body(bot_user, body)
        assert stop is not None and stop.kind == KIND_CLARIFY
        payload = stop.as_payload()
        assert payload["questions"] == [G7_QUESTION_TEXT]
        assert payload["question_id"] == G7_QUESTION_ID
        assert [o["label"] for o in payload["options"]] == [ANSWER_LABELS[a] for a in ANSWERS]
        assert restriction(_fresh_user(bot_user)) is None

    def test_free_safety_answer_keeps_the_frame(self) -> None:
        bot_user, _ = _pair("ma-free")
        screen_goal_body(bot_user, {"goal_text": AMBIGUOUS})
        stop, _ = screen_goal_body(bot_user, {"goal_text": AMBIGUOUS, "safety_answer": "нет"})
        assert stop is not None and stop.kind == KIND_CLARIFY
        assert stop.as_payload()["questions"] == [G7_QUESTION_TEXT]

    def test_structured_no_forwards_without_a_restriction(self) -> None:
        bot_user, _ = _pair("ma-no")
        first, _ = screen_goal_body(bot_user, {"goal_text": AMBIGUOUS})
        assert first is not None
        value = next(
            o["value"]
            for o in first.as_payload()["options"]
            if o["value"].startswith("cb:s1g7:no:")
        )
        stop, forward = screen_goal_body(bot_user, {"goal_text": AMBIGUOUS, "safety_answer": value})
        assert stop is None
        assert forward == {"goal_text": AMBIGUOUS}  # safety_answer stripped
        assert restriction(_fresh_user(bot_user)) is None

    def test_structured_yes_is_the_stop(self) -> None:
        bot_user, _ = _pair("ma-yes")
        first, _ = screen_goal_body(bot_user, {"goal_text": AMBIGUOUS})
        assert first is not None
        value = next(
            o["value"]
            for o in first.as_payload()["options"]
            if o["value"].startswith("cb:s1g7:yes:")
        )
        stop, _ = screen_goal_body(bot_user, {"goal_text": AMBIGUOUS, "safety_answer": value})
        assert stop is not None and stop.kind == KIND_RED_FLAG
        later, _ = screen_goal_body(bot_user, {"goal_text": "хочу массаж"})
        assert later is not None and later.kind == KIND_RED_FLAG
