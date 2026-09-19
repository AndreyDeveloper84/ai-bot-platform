"""Medical emergency text v2 — [OD-BOT §163] (18.09), on every live medical-S1 surface.

One canonical constant (:mod:`apps.orchestrator.safety.medical_emergency`), two
server surfaces that show a medical S1 verdict — the chat skill (MAX / Telegram,
per-tenant and global-through-tool paths) and the Mini App goal gate (``goal_text``
and the safety-stop frame, which renders the server's ``safety.text`` as is).
The Psychological Crisis Policy (``CRISIS_REPLY_TEXT``, ``HANDOFF``) is a separate
tract and must not move.

Technical checks only. Implementation of the registered owner policy does not
constitute CLINICAL APPROVED, PHYSICIAN PASS, or SAFE FOR PILOT.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from django.core.cache import cache

from apps.miniapp_api import health_gate
from apps.miniapp_api.health_gate import KIND_CRISIS, KIND_RED_FLAG, screen_goal_body
from apps.orchestrator.safety.gate import CRISIS_HOTLINE, CRISIS_REPLY_TEXT, evaluate_inbound
from apps.orchestrator.safety.medical_emergency import (
    EMERGENCY_NUMBERS,
    MEDICAL_EMERGENCY_TEXT_V2,
)
from apps.skills.base import SkillContext
from apps.skills.health_screening.skill import RED_FLAG_REPLY, HealthScreeningSkill

#: [OD-BOT §163] verbatim — a second, independent copy so a silent edit of the
#: module constant is caught here.
EXPECTED_V2 = (
    "По описанию это может требовать срочной медицинской помощи. "
    "Я не буду сейчас подбирать процедуру или оформлять запись. "
    "Если это происходит сейчас, произошло только что, повторяется, усиливается "
    "или тебе резко плохо — позвони 103 или 112. "
    "Не добирайся за рулём самостоятельно. "
    "Если можешь, попроси человека рядом помочь тебе вызвать помощь и остаться с тобой."
)

#: Retired wording of the medical S1 reply — must be unreachable on live paths.
RETIRED_PHRASES = ("сначала к врачу", "даст добро", "звучит серьёзно", "массаж может ухудшить")

#: The ruling: no diagnosis, no treatment, no medication, no home remedy.
FORBIDDEN_MARKERS = (
    "диагноз",
    "лечени",
    "прими",
    "принять",
    "выпей",
    "таблет",
    "антигистамин",
    "супрастин",
    "адреналин",
    "компресс",
    "анафилак",
    "квинке",
)

G6_TEXT = "После лекарства внезапно опухли губы"
CRISIS_TEXT = "я думаю о суициде"


def _context(text: str) -> SkillContext:
    return SkillContext(conversation=Mock(id="conv-v2"), bot_user=Mock(), message_text=text)


class TestCanonicalText:
    def test_verbatim(self) -> None:
        assert MEDICAL_EMERGENCY_TEXT_V2 == EXPECTED_V2

    def test_emergency_numbers_present(self) -> None:
        assert EMERGENCY_NUMBERS == ("103", "112")
        for number in EMERGENCY_NUMBERS:
            assert number in MEDICAL_EMERGENCY_TEXT_V2

    def test_no_diagnosis_or_treatment(self) -> None:
        lower = MEDICAL_EMERGENCY_TEXT_V2.lower()
        assert not [m for m in FORBIDDEN_MARKERS if m in lower]

    def test_retired_wording_is_gone(self) -> None:
        # Presence first: the strings under test are the v2 text, not something empty.
        assert "позвони 103 или 112" in MEDICAL_EMERGENCY_TEXT_V2.lower()
        assert "позвони 103 или 112" in RED_FLAG_REPLY.lower()
        for phrase in RETIRED_PHRASES:
            assert phrase not in MEDICAL_EMERGENCY_TEXT_V2.lower()
            assert phrase not in RED_FLAG_REPLY.lower()

    def test_skill_constant_is_the_same_object(self) -> None:
        """One source: the skill re-exports the module constant, not a copy."""
        assert RED_FLAG_REPLY is MEDICAL_EMERGENCY_TEXT_V2


class TestChatSurface:
    def test_red_flag_reply_is_v2_and_continues_nothing(self) -> None:
        result = HealthScreeningSkill().handle(_context(G6_TEXT))
        assert result.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        assert result.meta["reply_kind"] == "health_red_flag"
        # No recommendation / booking continuation in the reply.
        assert "услуг" not in result.reply_text.lower()
        assert "записать" not in result.reply_text.lower()


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


class TestMiniAppSurface:
    def test_goal_text_stop_carries_v2(self) -> None:
        stop, _ = screen_goal_body(Mock(pk=901), {"goal_text": G6_TEXT})
        assert stop is not None
        assert stop.kind == KIND_RED_FLAG
        assert stop.text == MEDICAL_EMERGENCY_TEXT_V2
        assert stop.as_payload() == {"kind": KIND_RED_FLAG, "text": MEDICAL_EMERGENCY_TEXT_V2}

    def test_safety_answer_stop_carries_v2(self) -> None:
        body = {"goal_text": "болит спина", health_gate.SAFETY_ANSWER_FIELD: G6_TEXT}
        stop, _ = screen_goal_body(Mock(pk=902), body)
        assert stop is not None
        assert stop.kind == KIND_RED_FLAG
        assert stop.text == MEDICAL_EMERGENCY_TEXT_V2

    def test_anketa_answer_text_stop_carries_v2(self) -> None:
        stop, _ = screen_goal_body(Mock(pk=903), {"answer": {"text": G6_TEXT}})
        assert stop is not None
        assert stop.kind == KIND_RED_FLAG
        assert stop.text == MEDICAL_EMERGENCY_TEXT_V2


class TestCrisisTractUntouched:
    def test_crisis_text_is_separate_and_keeps_the_hotline(self) -> None:
        assert CRISIS_REPLY_TEXT != MEDICAL_EMERGENCY_TEXT_V2
        assert CRISIS_HOTLINE in CRISIS_REPLY_TEXT
        assert "103" not in CRISIS_REPLY_TEXT

    def test_crisis_route_still_returns_the_crisis_text(self) -> None:
        inbound = evaluate_inbound(CRISIS_TEXT)
        assert not inbound.allowed
        assert inbound.reply_text == CRISIS_REPLY_TEXT

    def test_mini_app_crisis_stop_is_the_crisis_text(self) -> None:
        stop, _ = screen_goal_body(Mock(pk=904), {"goal_text": CRISIS_TEXT})
        assert stop is not None
        assert stop.kind == KIND_CRISIS
        assert stop.text == CRISIS_REPLY_TEXT

    def test_g6_does_not_take_the_crisis_route(self) -> None:
        assert evaluate_inbound(G6_TEXT).allowed
