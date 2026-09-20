"""G4 detector — [OD-BOT §161] (recent-resolved) + [OD-BOT §164] (G4 boundary and
question contract), second runtime slice of S1.

Implemented here: the explicit / recent-resolved G4 detector (``detect_g4``) —
one-sided weakness / numbness / loss of movement, face droop, speech disturbance,
sudden vision loss, sudden loss of balance / coordination. Resolution wording
(«прошло», «стало лучше», «сейчас нормально», «отпустило», «восстановилось») does
NOT clear the flag ([§161]: для G4 исчезновение признаков не снимает срочность).

The §164 routing question for the ambiguous case («немеет рука иногда») is
implemented (``g4_question.py``, binding open question ``health_screening.g4``):
``TestQuestionContract`` runs it through the skill on an in-memory carrier; the
persisted / cross-surface half lives in ``test_g4_question_flow.py``.

Still pinned as gaps (strict xfail): negation is not modelled in the ambiguity
patterns («онемения и слабости нет» asks the question instead of nothing);
third-party, quoted and distant-history phrases are caught (fail-closed) —
attribution / history context is a gap of the whole S1 tract.

Technical checks only. Implementation of registered owner policy does not
constitute CLINICAL APPROVED, PHYSICIAN PASS, or SAFE FOR PILOT.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from apps.orchestrator import open_question as open_question_module
from apps.orchestrator.open_question import pending_question
from apps.orchestrator.safety.gate import CRISIS_REPLY_TEXT, evaluate_inbound
from apps.orchestrator.safety import s1_restriction as s1_restriction_module
from apps.orchestrator.safety.medical_emergency import MEDICAL_EMERGENCY_TEXT_V2
from apps.orchestrator.safety.s1_restriction import restriction
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
    "Речь заплетается",
    "Не могу выговорить слова",
    "Не вижу левым глазом",
    "внезапно опустился уголок рта",
    "отнялась рука",
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
    "Внезапно перекосило лицо, сейчас прошло",
    "Резко пропало зрение, затем восстановилось",
    "Внезапно потеряла равновесие, сейчас лучше",
)

#: Review 20.09 — four false-positive boundaries, fixed in the detector:
#: (1) side + weakness / numbness needs a SUDDEN marker; (2) cosmetic / lifelong
#: «асимметрия лица» is not facial droop; (3) bare «не могу говорить» is
#: availability / channel choice, not a speech sign; (4) «перестал видеть» /
#: «не вижу» count only with the eye / vision named. None of these is G4, none is
#: any red flag, none gets the emergency text.
G4_FALSE_POSITIVE_REGRESSION = (
    "Слабость в правой руке после тренировки",  # review #1875: NONE — kept (round 2)
    "Правая рука устала после работы",
    "Хочу исправить асимметрию лица",
    "Асимметрия лица с детства",
    "Не могу сейчас говорить, я на работе",
    "Не могу говорить по телефону, напишите",
    "Перестал видеть эффект от массажа",
    "Не вижу свободных окон",
    "Не вижу результата после процедуры",
    "Не вижу смысла менять мастера",
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

#: Not explicit G4 by the detector — the AMBIGUOUS G4 of [OD-BOT §164]: the
#: DRF-973 numbness forms and limb WEAKNESS without a sudden marker, routed to
#: the one question (``CLARIFY``), never STOP and never NONE.
G4_AMBIGUOUS = (
    "Онемения и слабости нет",  # negation gap: asks instead of nothing (TestKnownGaps)
    "Иногда немеет рука",
    "немеет рука иногда",
    "Немеет левая рука по утрам",
    "Онемела правая нога после долгого сидения",
    # UNEXPLAINED limb weakness — the §164 sign without the sudden marker
    "слабость в правой руке иногда",
    "иногда слабеет левая рука",
    "рука ослабла к вечеру",
    "слабость в левой ноге",
)

#: Limb weakness explained by a NAMED physical exertion in the same sentence —
#: the review-#1875 boundary (NONE): not the question, not a stop, no
#: restriction. Whether exertion should still ask is an owner question.
EXERTION_WEAKNESS_NOT_G4 = (
    "Слабость в правой руке после тренировки",
    "после зала слабость в руках",
    "ноги слабые после пробежки",
    "рука ослабла после тяжёлой сумки",
)

#: General fatigue / weakness with NO limb named — not the contract, never a
#: question, never a stop (review 20.09: «не расширяй правило до любой общей
#: усталости / слабости»).
GENERAL_FATIGUE_NOT_G4 = (
    "общая слабость после тренировки",
    "слабость после болезни, хочу расслабляющий массаж",
    "устала, слабость во всём теле",
    "Правая рука устала после работы",
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


def _bot_user() -> SimpleNamespace:
    """An identity with a ``context`` dict — the durable restriction's carrier."""

    return SimpleNamespace(pk=1, context={})


def _context(text: str, conversation: Any = None, bot_user: Any = None) -> SkillContext:
    return SkillContext(
        conversation=conversation if conversation is not None else Mock(id="conv-g4"),
        bot_user=bot_user if bot_user is not None else Mock(),
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

    @pytest.mark.parametrize("text", G4_AMBIGUOUS)
    def test_ambiguous_is_the_question_not_a_stop(self, text: str) -> None:
        """Not explicit G4, not a red flag, not silence — the one routing question."""
        assert detect_g4(text) is False
        assert classify(text) is PainSignal.CLARIFY

    @pytest.mark.parametrize("text", GENERAL_FATIGUE_NOT_G4 + EXERTION_WEAKNESS_NOT_G4)
    def test_general_fatigue_or_exertion_is_neither_question_nor_stop(self, text: str) -> None:
        assert detect_g4(text) is False
        assert classify(text) is not PainSignal.CLARIFY
        assert classify(text) is not PainSignal.RED_FLAG

    @pytest.mark.parametrize("text", EXERTION_WEAKNESS_NOT_G4)
    def test_exertion_weakness_is_none_and_opens_no_restriction(self, text: str) -> None:
        assert classify(text) is PainSignal.NONE
        bot_user = _bot_user()
        skill = HealthScreeningSkill()
        assert skill.matches(_context(text, _conversation(), bot_user)) is False
        assert restriction(bot_user) is None

    @pytest.mark.parametrize(
        "text", ("Резко ослабла левая рука", "Внезапно онемела правая сторона тела")
    )
    def test_explicit_sudden_unilateral_weakness_stays_stop(self, text: str) -> None:
        assert detect_g4(text) is True
        assert classify(text) is PainSignal.RED_FLAG


class TestFalsePositiveRegression:
    """Review 20.09 — the four boundaries; each phrase is neither G4 nor any red
    flag, so it never reaches the emergency text by any rule."""

    @pytest.mark.parametrize("text", G4_FALSE_POSITIVE_REGRESSION)
    def test_not_g4(self, text: str) -> None:
        assert detect_g4(text) is False

    @pytest.mark.parametrize("text", G4_FALSE_POSITIVE_REGRESSION)
    def test_not_a_red_flag_by_any_rule(self, text: str) -> None:
        assert classify(text) is PainSignal.NONE

    @pytest.mark.parametrize("text", G4_FALSE_POSITIVE_REGRESSION)
    def test_no_g4_label_and_no_emergency_text(self, text: str) -> None:
        skill = HealthScreeningSkill()
        context = _context(text)
        assert skill.matches(context) is False
        result = skill.handle(context)
        assert result.reply_text != MEDICAL_EMERGENCY_TEXT_V2
        assert result.meta.get("s1_group") != "G4"

    @pytest.mark.parametrize("text", G4_FALSE_POSITIVE_REGRESSION)
    def test_stays_negative_in_a_long_message(self, text: str) -> None:
        assert detect_g4(LONG_PREFIX + text) is False

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

    @pytest.mark.usefixtures("memory_carrier")
    def test_ambiguous_phrase_gets_the_question_not_the_emergency_text(self) -> None:
        result = HealthScreeningSkill().handle(
            _context("немеет рука иногда", _conversation(), _bot_user())
        )
        assert result.reply_text == G4_ROUTING_QUESTION
        assert result.meta == {
            "reply_kind": "health_clarify_g4",
            "s1_group": "G4",
            "s1_restriction": "open",
        }

    def test_ambiguous_phrase_without_a_carrier_still_asks_and_says_so(self) -> None:
        """No carrier (unit Mock): the question is still the reply — the turn ends
        here — and the meta names that the state did not persist."""
        result = HealthScreeningSkill().handle(_context("немеет рука иногда", _conversation()))
        assert result.reply_text == G4_ROUTING_QUESTION
        assert result.meta["s1_restriction"] == "not_persisted"


@pytest.fixture
def memory_carrier(monkeypatch: pytest.MonkeyPatch) -> None:
    """``open_question`` on a plain ``skill_state`` dict — the contract without a DB.

    The persisted half (a real Conversation row, re-read from the database)
    is proven in ``test_g4_question_flow.py``."""

    def _write(conversation: Any, subkey: str, value: Any | None) -> None:
        if value is None:
            conversation.skill_state.pop(subkey, None)
        else:
            conversation.skill_state[subkey] = value

    def _write_row(bot_user: Any, row: Any) -> None:
        if row is None:
            bot_user.context.pop(s1_restriction_module.RESTRICTION_KEY, None)
        else:
            bot_user.context[s1_restriction_module.RESTRICTION_KEY] = row

    monkeypatch.setattr(open_question_module, "_write", _write)
    monkeypatch.setattr(s1_restriction_module, "_write_row", _write_row)


@pytest.mark.usefixtures("memory_carrier")
class TestQuestionContract:
    """[OD-BOT §164] G4 question contract — implemented (``g4_question.py``).

    These five were strict xfail while the flow was an architectural blocker;
    they are the contract now. Outcomes here are the skill's; the same
    ``route_g4_reply`` serves every surface (``test_g4_question_flow.py``)."""

    def test_ambiguous_is_clarify_not_stop(self) -> None:
        assert classify("немеет рука иногда") is PainSignal.CLARIFY

    def test_ambiguous_gets_exactly_the_routing_question(self) -> None:
        conversation, bot_user = _conversation(), _bot_user()
        result = HealthScreeningSkill().handle(
            _context("немеет рука иногда", conversation, bot_user)
        )
        assert restriction(bot_user) is not None
        assert result.reply_text == G4_ROUTING_QUESTION
        pending = pending_question(conversation)
        assert pending is not None and pending.question_id == G4_QUESTION_ID

    def test_unknown_answer_keeps_the_restriction(self) -> None:
        conversation, bot_user = _conversation(), _bot_user()
        skill = HealthScreeningSkill()
        skill.handle(_context("немеет рука иногда", conversation, bot_user))
        follow_up = _context("не знаю", conversation, bot_user)
        assert skill.matches(follow_up) is True
        assert skill.handle(follow_up).meta.get("reply_kind") == "health_restriction_persists"
        assert pending_question(conversation) is not None  # still open, still binding

    def test_new_booking_intent_keeps_the_restriction(self) -> None:
        conversation, bot_user = _conversation(), _bot_user()
        skill = HealthScreeningSkill()
        skill.handle(_context("немеет рука иногда", conversation, bot_user))
        follow_up = _context("запишите меня на массаж в пятницу", conversation, bot_user)
        assert skill.matches(follow_up) is True
        assert skill.handle(follow_up).meta.get("reply_kind") == "health_restriction_persists"
        pending = pending_question(conversation)
        assert pending is not None and pending.binding

    def test_plain_no_is_not_clearance(self) -> None:
        conversation, bot_user = _conversation(), _bot_user()
        skill = HealthScreeningSkill()
        skill.handle(_context("немеет рука иногда", conversation, bot_user))
        follow_up = _context("нет", conversation, bot_user)
        assert skill.matches(follow_up) is True
        result = skill.handle(follow_up)
        assert result.meta.get("reply_kind") != "health_no_signal"
        assert result.meta.get("reply_kind") == "health_restriction_persists"
        assert result.reply_text == G4_ROUTING_QUESTION
        assert pending_question(conversation) is not None

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
        reason="negation is not modelled in the ambiguity patterns (DRF-973 forms): «онемения "
        "нет» asks the routing question instead of nothing (gap)",
    )
    def test_negated_numbness_is_no_signal_at_all(self) -> None:
        assert classify("Онемения и слабости нет") is PainSignal.NONE

    def test_non_sudden_side_numbness_is_the_question_not_a_stop(self) -> None:
        """Was a strict xfail while the flow was a blocker; §164 routes it now."""
        assert classify("Немеет левая рука по утрам") is PainSignal.CLARIFY

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
