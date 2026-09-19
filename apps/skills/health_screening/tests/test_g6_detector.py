"""G6 detector — [OD-BOT §159] (18.09), first runtime slice of S1.

The ruling: sudden swelling of lips / mouth / tongue / throat after a possible
contact with an allergen is an explicit G6 → STOP; breathing symptoms are not a
precondition. The boundary: negation attached to the swelling word, hypothetical
future, and isolated local rash / itch must NOT fire on keywords alone.

Named gaps are pinned as ``strict`` xfail so a fix (or a regression) is visible:
third-party («у мамы …») and a quoted phrase are still caught — the fail-closed
direction; attribution / quotation context is not modelled anywhere in the S1
runtime. The local-rash question contract ([OD-BOT §164]) is not implemented on
this path.

Technical checks only. Implementation of the registered owner policy does not
constitute CLINICAL APPROVED, PHYSICIAN PASS, or SAFE FOR PILOT.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from apps.orchestrator.safety.gate import CRISIS_REPLY_TEXT
from apps.skills.base import SkillContext
from apps.skills.health_screening.classifier import PainSignal, classify, detect_g6
from apps.skills.health_screening.skill import RED_FLAG_REPLY, HealthScreeningSkill
from apps.skills.health_screening.tests.s1_fixtures import LONG_PREFIX

#: The brief's positives — explicit current swelling / signs after exposure.
G6_POSITIVE = (
    "После крема сыпь и губы опухают",
    "После лекарства внезапно опухли губы",
    "Опух язык",
    "Начало отекать горло",
    "После укуса трудно дышать и глотать",
    "после укола отекло горло, тяжело дышать",
    "горло сдавило после орехов",
    "после укуса пчелы трудно глотать",
    "анафилактический шок",
    "отёк Квинке",
    # the negation particle is NOT attached to the swelling word
    "губы опухают, не знаю, что делать",
)

#: The brief's negatives — negation on the swelling word, hypothetical future,
#: isolated local rash / itch, look-alike words.
G6_NEGATIVE = (
    "После крема появилась только сыпь и зуд",
    "Губы не опухли",
    "Нет отёка языка или горла",
    "Сыпь и зуд, но дышу нормально и отёка нет",
    "Что делать, если когда-нибудь опухнут губы?",
    "сыпь и зуд после крема, но губы, язык и горло не отекали, дышу нормально",
    "после крема губы не опухли, но чешутся",
    "не сильно опухли губы",
    "аллергии нет",
    "сыпь после депиляции — это нормально?",
    "отечественный крем для губ хочу",
    "губка для макияжа",
    "хочу увеличить губы",
    "опухоль на губе была давно удалена",
)


def _context(text: str) -> SkillContext:
    return SkillContext(conversation=Mock(id="conv-g6"), bot_user=Mock(), message_text=text)


class TestDetectG6:
    @pytest.mark.parametrize("text", G6_POSITIVE)
    def test_positive_is_g6_and_red_flag(self, text: str) -> None:
        assert detect_g6(text) is True
        assert classify(text) is PainSignal.RED_FLAG

    @pytest.mark.parametrize("text", G6_POSITIVE)
    def test_positive_survives_a_long_message(self, text: str) -> None:
        """No length threshold: the sign at the end of a long message still counts."""
        assert detect_g6(LONG_PREFIX + text) is True
        assert classify(LONG_PREFIX + text) is PainSignal.RED_FLAG

    @pytest.mark.parametrize("text", G6_NEGATIVE)
    def test_negative_is_not_g6(self, text: str) -> None:
        assert detect_g6(text) is False
        assert classify(text) is not PainSignal.RED_FLAG

    @pytest.mark.parametrize("text", G6_NEGATIVE)
    def test_negative_stays_negative_in_a_long_message(self, text: str) -> None:
        assert detect_g6(LONG_PREFIX + text) is False

    def test_empty_and_non_string(self) -> None:
        assert detect_g6("") is False
        assert detect_g6("   ") is False
        assert detect_g6(None) is False  # type: ignore[arg-type]

    def test_other_groups_are_not_attributed_to_g6(self) -> None:
        """G1–G5, G7 keep their red flag and are not labelled G6."""
        for text in (
            "не могу вдохнуть",
            "потеряла сознание",
            "сильная боль в груди",
            "перекосило лицо",
            "кровь не останавливается",
            "онемела рука и температура 38.5",
        ):
            assert classify(text) is PainSignal.RED_FLAG, text
            assert detect_g6(text) is False, text


class TestKnownGaps:
    """Fail-closed today. ``strict``: the day a fix lands, the xfail turns into
    an unexpected pass and the reviewer sees the boundary moved."""

    @pytest.mark.xfail(
        strict=True,
        reason="third-party context is not modelled in the S1 runtime (gap); caught fail-closed",
    )
    def test_third_party_is_not_the_persons_own_s1(self) -> None:
        assert detect_g6("У мамы после лекарства опухли губы") is False

    @pytest.mark.xfail(
        strict=True,
        reason="quotation context is not modelled in the S1 runtime (gap); caught fail-closed",
    )
    def test_quoted_phrase_is_not_the_persons_own_s1(self) -> None:
        assert detect_g6("Подруга написала: «губы опухли, что делать»") is False

    @pytest.mark.xfail(
        strict=True,
        reason="[OD-BOT §164] local-rash question contract is not implemented on this path",
    )
    def test_local_rash_gets_the_routing_question(self) -> None:
        skill = HealthScreeningSkill()
        assert skill.matches(_context("сыпь и зуд после крема")) is True


class TestSkillRouting:
    def test_g6_reply_is_the_red_flag_reply_and_is_labelled(self) -> None:
        """The reply constant itself is pinned to [OD-BOT §163] in
        ``apps/orchestrator/safety/tests/test_medical_emergency_text_v2.py``."""
        result = HealthScreeningSkill().handle(_context("После лекарства внезапно опухли губы"))
        assert result.reply_text == RED_FLAG_REPLY
        assert result.meta == {"reply_kind": "health_red_flag", "s1_group": "G6"}

    def test_non_g6_red_flag_keeps_the_same_text_without_the_label(self) -> None:
        result = HealthScreeningSkill().handle(_context("онемела рука и температура 38.5"))
        assert result.reply_text == RED_FLAG_REPLY
        assert result.meta == {"reply_kind": "health_red_flag"}

    @pytest.mark.parametrize("text", G6_NEGATIVE)
    def test_negative_does_not_reach_the_red_flag_reply(self, text: str) -> None:
        skill = HealthScreeningSkill()
        context = _context(text)
        if skill.matches(context):
            assert skill.handle(context).reply_text != RED_FLAG_REPLY

    def test_g6_reply_is_not_the_crisis_text(self) -> None:
        result = HealthScreeningSkill().handle(_context("Опух язык"))
        assert result.reply_text != CRISIS_REPLY_TEXT
