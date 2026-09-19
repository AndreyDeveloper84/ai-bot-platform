"""G4 detector — [OD-BOT §161] (recent-resolved) + [OD-BOT §164] (G4 boundary and
question contract), second runtime slice of S1.

Implemented here: the explicit / recent-resolved G4 detector (``detect_g4``) —
one-sided weakness / numbness / loss of movement, face droop, speech disturbance,
sudden vision loss, sudden loss of balance / coordination. Resolution wording
(«прошло», «стало лучше», «сейчас нормально», «отпустило», «восстановилось») does
NOT clear the flag ([§161]: для G4 исчезновение признаков не снимает срочность).

NOT implemented (architectural blocker, pinned as ``strict`` xfail so a change is
visible): the §164 routing question for the ambiguous case («немеет рука иногда»).
The runtime cannot today bind a reply to that specific question deterministically
(``open_question`` is closed by ANY next message and adjudicated by the model), has
no restriction state that survives a turn (DRF-2040), and on the Mini App path an
answer without a hard stop proceeds — so a «нет» would unlock. A keyword «да / нет»
flow would be a pseudo-implementation; none is added. The ambiguous phrase stays
fail-closed STOP by the older numbness rule (DRF-973), NOT attributed to G4.

Also pinned as gaps: the DRF-973 rule fires on «онемения и слабости нет» (negation
not modelled there); third-party, quoted and distant-history phrases are caught
(fail-closed) — attribution / history context is a gap of the whole S1 tract.

Technical checks only. Implementation of registered owner policy does not
constitute CLINICAL APPROVED, PHYSICIAN PASS, or SAFE FOR PILOT.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from apps.orchestrator.open_question import pending_question
from apps.orchestrator.safety.gate import CRISIS_REPLY_TEXT, evaluate_inbound
from apps.orchestrator.safety.medical_emergency import MEDICAL_EMERGENCY_TEXT_V2
from apps.skills.base import SkillContext
from apps.skills.health_screening.classifier import (
    PainSignal,
    classify,
    detect_g4,
    detect_g6,
)
from apps.skills.health_screening.skill import RED_FLAG_REPLY, HealthScreeningSkill
from apps.skills.health_screening.tests.s1_fixtures import LONG_PREFIX

#: Explicit current G4 — the brief's ten plus the matrix's own phrases.
G4_POSITIVE = (
    "Внезапно онемела правая сторона тела",
    "Резко ослабла левая рука",
    "Перекосило лицо",
    "Внезапно речь стала невнятной",
    "Внезапно перестал видеть одним глазом",
    "Резко пропало зрение",
    "Внезапно начало двоиться в глазах",
    "Внезапно потеряла равновесие",
    "Резко нарушилась координация, не могу нормально стоять",
    "Внезапно повело в сторону и не могу нормально идти",
    "не чувствую половину лица, речь заплетается",
    "внезапно отнялась рука и нога справа",
    "Лицо перекосило, но я не хочу обсуждать здоровье",
)

#: Post-procedure — the procedure next to the sign does not soften it.
G4_POST_PROCEDURE = (
    "после массажа шеи перекосило лицо",
    "после массажа шеи внезапно онемела левая рука",
    "после процедуры резко пропало зрение на один глаз",
)

#: [OD-BOT §161] — recent-resolved G4 stays STOP. The message carries both the
#: event and the resolution; no cross-turn state is involved.
G4_RECENT_RESOLVED = (
    "Внезапно перекосило лицо, но сейчас прошло",
    "Речь внезапно стала невнятной, сейчас нормально",
    "Резко пропало зрение, через несколько минут восстановилось",
    "Внезапно потеряла равновесие, сейчас стало лучше",
    "Правая рука внезапно онемела, но уже отпустило",
    "10 минут назад перекосило лицо и речь была невнятной, сейчас прошло",
    "речь нарушилась, но уже прошло",
)

#: Negation attached to the sign word, lexical false friends, hypothetical future.
#: None of these is G4; none is a red flag by any rule.
G4_NEGATIVE = (
    "Лицо не перекосило",
    "Речь не нарушена",
    "Вижу нормально",
    "Равновесие не нарушено",
    "Хочу проверить зрение",
    "Нужна гимнастика для координации",
    "Хочу найти баланс между работой и отдыхом",
    "Речь идёт о записи на пятницу",
    "У меня отнялась суббота, перенесите запись",
    "Половина лица после ботокса у другого мастера, можно коррекцию?",
    "Обе руки затекли после сна",
    "Что делать, если когда-нибудь перекосит лицо?",
    "Речь нормальная, лицо не перекошено",
    "хочу массаж лица",
    "правая рука устала после работы",
)

#: Not G4 by the detector, but still a red flag by the older numbness rule
#: (DRF-973) that runs before it — fail-closed, named.
G4_LEGACY_PREEMPTED = (
    "Онемения и слабости нет",
    "Иногда немеет рука",
    "немеет рука иногда",
)

#: [OD-BOT §164] — the one routing question, verbatim. Contract only: not asked
#: by the runtime (see the module docstring).
G4_ROUTING_QUESTION = (
    "Это началось внезапно, и есть ли сейчас слабость или онемение с одной стороны, "
    "перекос лица, нарушение речи, зрения или равновесия?"
)
G4_QUESTION_ID = "health_screening.g4"


def _conversation() -> SimpleNamespace:
    return SimpleNamespace(id="conv-g4", skill_state={})


def _context(text: str, conversation: Any = None) -> SkillContext:
    return SkillContext(
        conversation=conversation if conversation is not None else Mock(id="conv-g4"),
        bot_user=Mock(),
        message_text=text,
    )


class TestDetectG4:
    @pytest.mark.parametrize("text", G4_POSITIVE + G4_POST_PROCEDURE)
    def test_positive_is_g4_and_red_flag(self, text: str) -> None:
        assert detect_g4(text) is True
        assert classify(text) is PainSignal.RED_FLAG

    @pytest.mark.parametrize("text", G4_POSITIVE)
    def test_positive_survives_a_long_message(self, text: str) -> None:
        """No length threshold: the sign at the end of a long message still counts."""
        assert detect_g4(LONG_PREFIX + text) is True
        assert classify(LONG_PREFIX + text) is PainSignal.RED_FLAG

    @pytest.mark.parametrize("text", G4_RECENT_RESOLVED)
    def test_recent_resolved_stays_stop(self, text: str) -> None:
        """[OD-BOT §161]: «прошло / стало лучше / сейчас нормально» is not clearance."""
        assert detect_g4(text) is True
        assert classify(text) is PainSignal.RED_FLAG

    @pytest.mark.parametrize("text", G4_NEGATIVE)
    def test_negative_is_not_g4_and_not_a_red_flag(self, text: str) -> None:
        assert detect_g4(text) is False
        assert classify(text) is not PainSignal.RED_FLAG

    @pytest.mark.parametrize("text", G4_NEGATIVE)
    def test_negative_stays_negative_in_a_long_message(self, text: str) -> None:
        assert detect_g4(LONG_PREFIX + text) is False

    @pytest.mark.parametrize("text", G4_LEGACY_PREEMPTED)
    def test_legacy_preempted_phrases_are_not_attributed_to_g4(self, text: str) -> None:
        """The detector itself is right about these; the older rule is what fires."""
        assert detect_g4(text) is False

    def test_empty_and_non_string(self) -> None:
        assert detect_g4("") is False
        assert detect_g4("   ") is False
        assert detect_g4(None) is False  # type: ignore[arg-type]

    def test_other_groups_are_not_attributed_to_g4(self) -> None:
        """G1–G3, G5–G7 keep their red flag and are not labelled G4."""
        for text in (
            "не могу вдохнуть",
            "потеряла сознание",
            "сильная боль в груди",
            "кровь не останавливается",
            "после лекарства внезапно опухли губы",
            "онемела рука и температура 38.5",
            "резко стало очень плохо",
        ):
            assert classify(text) is PainSignal.RED_FLAG, text
            assert detect_g4(text) is False, text

    def test_g6_slice_from_1864_is_unchanged(self) -> None:
        for text, expected in (
            ("После крема сыпь и губы опухают", True),
            ("Опух язык", True),
            ("Губы не опухли", False),
            ("Нет отёка языка или горла", False),
            ("Что делать, если когда-нибудь опухнут губы?", False),
        ):
            assert detect_g6(text) is expected, text
            assert (classify(text) is PainSignal.RED_FLAG) is expected, text


class TestSkillRouting:
    def test_g4_reply_is_the_canonical_text_and_is_labelled(self) -> None:
        result = HealthScreeningSkill().handle(_context("Внезапно онемела правая сторона тела"))
        assert result.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        assert result.reply_text is RED_FLAG_REPLY
        assert result.meta == {"reply_kind": "health_red_flag", "s1_group": "G4"}

    @pytest.mark.parametrize("text", G4_RECENT_RESOLVED)
    def test_recent_resolved_gets_the_same_stop_reply(self, text: str) -> None:
        result = HealthScreeningSkill().handle(_context(text))
        assert result.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        assert result.meta["s1_group"] == "G4"

    def test_g4_reply_carries_emergency_numbers_and_no_booking(self) -> None:
        result = HealthScreeningSkill().handle(_context("Резко пропало зрение"))
        assert "103" in result.reply_text and "112" in result.reply_text
        assert "услуг" not in result.reply_text.lower()
        assert "записать" not in result.reply_text.lower()

    def test_g4_does_not_take_the_crisis_route(self) -> None:
        for text in G4_POSITIVE:
            assert evaluate_inbound(text).allowed, text
        result = HealthScreeningSkill().handle(_context("Перекосило лицо"))
        assert result.reply_text != CRISIS_REPLY_TEXT

    def test_crisis_text_still_takes_the_crisis_route(self) -> None:
        inbound = evaluate_inbound("я думаю о суициде")
        assert not inbound.allowed
        assert inbound.reply_text == CRISIS_REPLY_TEXT

    def test_g6_label_wins_when_both_signs_are_present(self) -> None:
        """One label per turn; the reply is the same text either way."""
        result = HealthScreeningSkill().handle(
            _context("после укола отекло горло и перекосило лицо")
        )
        assert result.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        assert result.meta["s1_group"] == "G6"

    @pytest.mark.parametrize("text", G4_NEGATIVE)
    def test_negative_does_not_reach_the_red_flag_reply(self, text: str) -> None:
        skill = HealthScreeningSkill()
        context = _context(text)
        if skill.matches(context):
            assert skill.handle(context).reply_text != MEDICAL_EMERGENCY_TEXT_V2

    def test_legacy_preempted_phrase_is_a_red_flag_without_a_group(self) -> None:
        """Fail-closed today: STOP text, but honestly NOT labelled G4."""
        result = HealthScreeningSkill().handle(_context("немеет рука иногда"))
        assert result.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        assert "s1_group" not in result.meta


class TestQuestionContract:
    """[OD-BOT §164] G4 question contract — pinned, not implemented.

    Every test here is ``strict`` xfail: the day the flow lands, the reviewer sees
    the boundary move. The reasons name the blocker, not a TODO."""

    BLOCKER = (
        "§164 G4 question flow not implemented: no deterministic binding of a reply to "
        "this question, no restriction state across turns (DRF-2040), Mini App answer "
        "path would treat «нет» as clearance"
    )

    @pytest.mark.xfail(strict=True, reason="§164: ambiguous G4 is CLARIFY, not STOP; " + BLOCKER)
    def test_ambiguous_is_clarify_not_stop(self) -> None:
        assert classify("немеет рука иногда") is not PainSignal.RED_FLAG

    @pytest.mark.xfail(strict=True, reason="§164: the one routing question is asked; " + BLOCKER)
    def test_ambiguous_gets_exactly_the_routing_question(self) -> None:
        conversation = _conversation()
        result = HealthScreeningSkill().handle(_context("немеет рука иногда", conversation))
        assert result.reply_text == G4_ROUTING_QUESTION
        pending = pending_question(conversation)
        assert pending is not None and pending.question_id == G4_QUESTION_ID

    @pytest.mark.xfail(strict=True, reason="§164: UNKNOWN keeps the restriction; " + BLOCKER)
    def test_unknown_answer_keeps_the_restriction(self) -> None:
        conversation = _conversation()
        skill = HealthScreeningSkill()
        skill.handle(_context("немеет рука иногда", conversation))
        follow_up = _context("не знаю", conversation)
        assert skill.matches(follow_up) is True
        assert skill.handle(follow_up).meta.get("reply_kind") == "health_restriction_persists"

    @pytest.mark.xfail(strict=True, reason="§164: a new intent keeps the restriction; " + BLOCKER)
    def test_new_booking_intent_keeps_the_restriction(self) -> None:
        conversation = _conversation()
        skill = HealthScreeningSkill()
        skill.handle(_context("немеет рука иногда", conversation))
        follow_up = _context("запишите меня на массаж в пятницу", conversation)
        assert skill.matches(follow_up) is True
        assert skill.handle(follow_up).meta.get("reply_kind") == "health_restriction_persists"

    @pytest.mark.xfail(strict=True, reason="§164: «нет» is not medical clearance; " + BLOCKER)
    def test_plain_no_is_not_clearance(self) -> None:
        conversation = _conversation()
        skill = HealthScreeningSkill()
        skill.handle(_context("немеет рука иногда", conversation))
        follow_up = _context("нет", conversation)
        assert skill.matches(follow_up) is True
        assert skill.handle(follow_up).meta.get("reply_kind") != "health_no_signal"

    # The positive branch of the contract is already covered by the detector: an
    # answer that names the sign is an explicit / recent-resolved G4 on its own.
    @pytest.mark.parametrize(
        "answer",
        (
            "да, внезапно онемела правая сторона",
            "да, перекосило лицо",
            "речь нарушилась, но уже прошло",
        ),
    )
    def test_positive_answer_is_stop_by_the_detector_itself(self, answer: str) -> None:
        assert detect_g4(answer) is True
        assert (
            HealthScreeningSkill().handle(_context(answer)).reply_text == MEDICAL_EMERGENCY_TEXT_V2
        )


class TestKnownGaps:
    """Fail-closed today; ``strict`` so a fix or a regression is visible."""

    @pytest.mark.xfail(
        strict=True,
        reason="DRF-973 numbness rule fires before detect_g4; negation not modelled there (gap)",
    )
    def test_negated_numbness_is_not_a_red_flag(self) -> None:
        assert classify("Онемения и слабости нет") is not PainSignal.RED_FLAG

    @pytest.mark.xfail(
        strict=True,
        reason="third-party context is not modelled in the S1 runtime (gap); caught fail-closed",
    )
    def test_third_party_is_not_the_persons_own_s1(self) -> None:
        assert detect_g4("У мамы внезапно перекосило лицо") is False

    @pytest.mark.xfail(
        strict=True,
        reason="quotation context is not modelled in the S1 runtime (gap); caught fail-closed",
    )
    def test_quoted_phrase_is_not_the_persons_own_s1(self) -> None:
        assert detect_g4("Подруга написала: «у меня внезапно пропало зрение»") is False

    @pytest.mark.xfail(
        strict=True,
        reason="distant history vs recent-resolved is not modelled (gap); caught fail-closed per §161",
    )
    def test_distant_history_is_not_current_s1(self) -> None:
        assert (
            detect_g4("Много лет назад было нарушение речи и перекос лица, давно прошло") is False
        )
