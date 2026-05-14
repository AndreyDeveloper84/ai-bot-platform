"""Voice examples coverage + render tests (DRF-831 / Sprint 9 / D1)."""

from __future__ import annotations

import pytest
from ayla_ai_core import Example

from apps.promptreg.voice_examples import (
    ANKETA_EMPATHY_EXAMPLES,
    BOOKING_EXAMPLES,
    DIAGNOSTIC_FIRST_PAIN_EXAMPLES,
    EXAMPLES,
    FOOD_RECOGNITION_EXAMPLES,
    WATER_BEVERAGE_EXAMPLES,
    render_voice_examples,
)


class TestPoolCoverage:
    def test_booking_count(self) -> None:
        assert len(BOOKING_EXAMPLES) == 5

    def test_diagnostic_first_count(self) -> None:
        assert len(DIAGNOSTIC_FIRST_PAIN_EXAMPLES) == 5

    def test_food_recognition_count(self) -> None:
        assert len(FOOD_RECOGNITION_EXAMPLES) == 5

    def test_water_count(self) -> None:
        assert len(WATER_BEVERAGE_EXAMPLES) == 3

    def test_anketa_count(self) -> None:
        assert len(ANKETA_EMPATHY_EXAMPLES) == 3

    def test_default_pool_is_booking_plus_diagnostic(self) -> None:
        assert EXAMPLES == BOOKING_EXAMPLES + DIAGNOSTIC_FIRST_PAIN_EXAMPLES

    def test_all_are_ayla_examples(self) -> None:
        """Type guard — must remain compatible with ayla-ai-core's Example.

        If ayla-ai-core ever changes the Example shape, this surfaces it
        before the orchestrator silently misrenders.
        """
        for ex in (
            BOOKING_EXAMPLES
            + DIAGNOSTIC_FIRST_PAIN_EXAMPLES
            + FOOD_RECOGNITION_EXAMPLES
            + WATER_BEVERAGE_EXAMPLES
            + ANKETA_EMPATHY_EXAMPLES
        ):
            assert isinstance(ex, Example)
            assert ex.user.strip()
            assert ex.assistant.strip()


class TestDRF358Coverage:
    """The DRF-358 fix mandated specific failure modes have explicit examples."""

    def test_diagnostic_first_asks_clarifying_question(self) -> None:
        """At least one diagnostic-first example must ask a clarifying question,
        not jump to tool-call.
        """
        for ex in DIAGNOSTIC_FIRST_PAIN_EXAMPLES:
            if "?" in ex.assistant and "tool_call" not in ex.assistant:
                return
        pytest.fail("no diagnostic-first example asks a clarifying question")

    def test_red_flag_redirects_to_doctor(self) -> None:
        """At least one example must show the red-flag → doctor redirect."""
        redirects = [ex for ex in DIAGNOSTIC_FIRST_PAIN_EXAMPLES if "неврологу" in ex.assistant]
        assert redirects, "no red-flag → doctor redirect example"


class TestRenderVoiceExamples:
    def test_no_intent_returns_default_pool(self) -> None:
        block = render_voice_examples(None)
        assert "ЭТАЛОННЫЕ ДИАЛОГИ:" in block
        assert "Шея болит" in block  # diagnostic-first in default pool
        assert "Здравствуйте" in block  # booking in default pool

    def test_known_intent_filters_pool(self) -> None:
        block = render_voice_examples("water")
        assert "стакан воды" in block
        # Booking examples should not leak into water pool.
        assert "Здравствуйте" not in block

    def test_unknown_intent_returns_empty(self) -> None:
        """Unknown intent is a routing bug, not a fallback case."""
        assert render_voice_examples("definitely-not-a-real-intent") == ""

    def test_health_screening_uses_diagnostic_first(self) -> None:
        block = render_voice_examples("health_screening")
        assert "неврологу" in block

    def test_nutrition_anketa_uses_anketa_pool(self) -> None:
        block = render_voice_examples("nutrition_anketa")
        assert "анкете" in block or "норм" in block.lower()
        assert "Шея болит" not in block

    def test_cross_domain_merges_pools(self) -> None:
        block = render_voice_examples("cross_domain")
        assert "Здравствуйте" in block
        assert "омлет" in block.lower()
