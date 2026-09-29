"""The 25 documentary ``T-S1-G7Q-*`` fixtures, executed ([OD-BOT §170]).

Every fixture of ``g7q_fixtures.py`` runs through the real per-tenant registry
dispatch (the seam MAX and Telegram share) on a REAL ``Conversation`` /
``BotUser`` — state is re-read from the database between turns. A structured
answer is sent exactly as a tap arrives: the callback of the live slot token.

Technical checks only. Implementation of the registered G7 owner contract does
not constitute CLINICAL APPROVED, PHYSICIAN PASS, or SAFE FOR PILOT.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from apps.conversations.models import Conversation
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.models import BotUser
from apps.identity.services import resolve_or_create_global_bot_user
from apps.orchestrator.safety.medical_emergency import MEDICAL_EMERGENCY_TEXT_V2
from apps.orchestrator.safety.s1_restriction import mark_stop, restriction
from apps.skills.base import SkillContext
from apps.skills.health_screening.classifier import (
    PainSignal,
    clarify_group,
    classify,
    s1_group_of,
)
from apps.skills.health_screening.g7_question import (
    ANSWER_LABELS,
    G7_QUESTION_ID,
    G7_QUESTION_TEXT,
    buttons_of,
    g7_callback,
    g7_pending,
)
from apps.skills.health_screening.tests.g7q_fixtures import (
    G7Q_FIXTURES,
    G7Q_FIXTURES_COUNT,
    GAP_TEMPERATURE,
    G7QFixture,
)
from apps.skills.registry import dispatch
from apps.tenancy.context import tenant_scope

pytestmark = pytest.mark.django_db(transaction=True)

BOOKING_INTENT = "запишите меня на массаж в пятницу"


def _fresh(conversation: Conversation) -> Conversation:
    return Conversation.all_tenants.get(pk=conversation.pk)


def _fresh_user(bot_user: BotUser) -> BotUser:
    return BotUser.all_tenants.get(pk=bot_user.pk)


def _token(conversation: Conversation) -> str:
    pending = g7_pending(_fresh(conversation))
    assert pending is not None
    return pending.token


def _pair(suffix: str):
    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=f"g7q-{suffix}-uid", chat_id=f"g7q-{suffix}-chat"
    )
    return bot_user, resolve_active_global_conversation(bot_user)


def _send(text: str, conversation: Conversation, bot_user: BotUser):
    """One turn through the per-tenant registry — MAX and Telegram share it."""

    conversation, bot_user = _fresh(conversation), _fresh_user(bot_user)
    if text in ANSWER_LABELS:  # a structured answer: the tap of the live slot
        pending = g7_pending(conversation)
        assert pending is not None and pending.token
        text = g7_callback(text, pending.token)
    with tenant_scope(conversation.tenant):
        return dispatch(
            SkillContext(conversation=conversation, bot_user=bot_user, message_text=text)
        )


def _run(fixture: G7QFixture):
    bot_user, conversation = _pair(fixture.id.lower().replace("t-s1-", ""))
    results = [_send(turn, conversation, bot_user) for turn in fixture.turns]
    return bot_user, conversation, results


def test_every_documentary_fixture_is_here_once() -> None:
    ids = [f.id for f in G7Q_FIXTURES]
    assert len(ids) == G7Q_FIXTURES_COUNT == 25
    assert len(set(ids)) == 25
    assert all(re.fullmatch(r"T-S1-G7Q-[A-Z]+-\d\d", i) for i in ids)


@pytest.mark.parametrize(
    "fixture",
    [
        pytest.param(f, marks=pytest.mark.xfail(strict=True, reason=f.gap)) if f.gap else f
        for f in G7Q_FIXTURES
    ],
    ids=lambda f: f.id,
)
def test_g7q_fixture(fixture: G7QFixture) -> None:
    check = fixture.check
    if check == "stop_group":
        (text,) = fixture.turns
        assert classify(text) is PainSignal.RED_FLAG
        assert s1_group_of(text) == fixture.group
        _, conversation, (result,) = _run(fixture)
        assert result.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        assert result.meta.get("s1_group") == fixture.group
        assert g7_pending(_fresh(conversation)) is None  # no question on an explicit sign
    elif check == "asks_once":
        bot_user, conversation, (result,) = _run(fixture)
        assert result.reply_text == G7_QUESTION_TEXT
        assert len(buttons_of(result.action_data)) == 3
        token = _token(conversation)
        again = _send(fixture.turns[0], conversation, bot_user)  # the same ambiguity again
        assert again.reply_text == G7_QUESTION_TEXT
        assert _token(conversation) == token  # one slot, same token
        assert restriction(_fresh_user(bot_user)) is None  # no durable restriction
    elif check == "answer_yes":
        bot_user, conversation, (_, result) = _run(fixture)
        assert result.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        assert result.meta["s1_group"] == "G7"
        rec = restriction(_fresh_user(bot_user))
        assert rec is not None and rec.status == "stop" and rec.group == "G7"
        later = _send(BOOKING_INTENT, conversation, bot_user)
        assert later.reply_text == MEDICAL_EMERGENCY_TEXT_V2  # booking not reached
    elif check == "answer_no_first_turn":
        bot_user, conversation, (_, result) = _run(fixture)
        assert result.meta["reply_kind"] == "health_outside_s1_g7"
        assert restriction(_fresh_user(bot_user)) is None
        assert g7_pending(_fresh(conversation)) is None
    elif check == "answer_unsure":
        bot_user, conversation, (_, result) = _run(fixture)
        assert result.reply_text == G7_QUESTION_TEXT
        assert g7_pending(_fresh(conversation)) is not None
        assert restriction(_fresh_user(bot_user)) is None
    elif check == "free_text":
        bot_user, conversation, (_, result) = _run(fixture)
        assert result.reply_text == G7_QUESTION_TEXT  # re-asked, not an answer
        assert g7_pending(_fresh(conversation)) is not None
        assert restriction(_fresh_user(bot_user)) is None
    elif check == "not_g7":
        for text in fixture.turns:
            assert s1_group_of(text) != "G7", text
            assert clarify_group(text) is None, text
            bot_user, conversation = _pair(f"not-{abs(hash(text)) % 99991}")
            _send(text, conversation, bot_user)
            assert g7_pending(_fresh(conversation)) is None, text
    elif check in ("active_ordinary_turn", "active_answer_no"):
        bot_user, conversation = _pair(fixture.id.lower())
        _send("Мне резко стало очень плохо", conversation, bot_user)
        assert mark_stop(
            _fresh_user(bot_user),
            group="G4",
            question_id="health_screening.g4",
            source="test",
            reason="positive_sign",
        )
        if check == "active_ordinary_turn":
            # the label typed as an ordinary turn — not a structured action
            result = _send(ANSWER_LABELS["no"], conversation, bot_user)
        else:
            with patch("apps.orchestrator.safety.s1_restriction.clear_restriction") as clear:
                result = _send("no", conversation, bot_user)
            clear.assert_not_called()
        assert result.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        rec = restriction(_fresh_user(bot_user))
        assert rec is not None and rec.status == "stop" and rec.group == "G4"  # nothing lifted
    elif check == "other_group_after_no":
        bot_user, conversation, (_, after_no, other) = _run(fixture)
        assert after_no.meta["reply_kind"] == "health_outside_s1_g7"
        assert other.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        assert other.meta["s1_group"] == fixture.group
    elif check == "no_diagnosis_no_cta":
        _, _, (_, result) = _run(fixture)
        assert result.action_data is None  # no buttons, no booking / wellness CTA
        lowered = result.reply_text.lower()
        for word in ("сепсис", "инсульт", "инфаркт", "диагноз"):
            assert word not in lowered
    elif check == "emergency_text":
        _, _, (_, result) = _run(fixture)
        assert result.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        assert "103" in result.reply_text and "112" in result.reply_text
        lowered = result.reply_text.lower()
        assert "сначала к врачу" not in lowered and "даст добро" not in lowered
        assert result.meta.get("reply_kind") == "health_red_flag"  # medical, not crisis
    elif check == "third_party":
        (text,) = fixture.turns
        bot_user, conversation, (result,) = _run(fixture)
        # contract: no personal restriction / question, an emergency recommendation
        assert g7_pending(_fresh(conversation)) is None
        assert restriction(_fresh_user(bot_user)) is None
        assert "103" in result.reply_text and "112" in result.reply_text
    else:  # pragma: no cover — a new check name must be wired here
        pytest.fail(f"unknown check {check}")


@pytest.mark.xfail(strict=True, reason=GAP_TEMPERATURE)
def test_isolated_temperature_is_no_s1_at_all() -> None:
    """T-S1-G7Q-NOT-03, the half this package cannot honour: «no S1»."""

    assert classify("У меня температура 37,8") is PainSignal.NONE


def test_the_question_id_is_the_registered_one() -> None:
    assert G7_QUESTION_ID == "health_screening.g7"
