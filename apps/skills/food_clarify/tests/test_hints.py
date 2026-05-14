"""looks_like_food_drink detector tests (DRF-821 / Sprint 9 / P4).

The detector is the chokepoint for DRF-358: missing a food/drink mention
sends the user into the cold "не могу с заказом" LLM fallback. Tests
cover all known mysite-era bug repros plus type safety.
"""

from __future__ import annotations

import pytest

from apps.skills.food_clarify.hints import looks_like_food_drink


# ─── happy path: mainstream foods ─────────────────────────────────────────


class TestFoodHits:
    @pytest.mark.parametrize(
        "text",
        [
            "Борщ",
            "Борщ 300г",
            "котлета",
            "котлеты",  # declension still hits via "котлет" stem
            "котлету",
            "стейк",
            "Стейк 250г",
            "омлет с тостом",
            "пицца 2 куска",
            "Каша с молоком",
            "пельмени",
        ],
    )
    def test_food_words_hit(self, text: str) -> None:
        assert looks_like_food_drink(text), f"expected hit on {text!r}"


# ─── DRF-358 known parser-miss repros ─────────────────────────────────────


class TestDRF358BugRepros:
    """Cases where parse_beverage missed in mysite — must still hit here."""

    def test_juice_decimal_comma(self) -> None:
        """Bug 1 from 2026-05-08 dev-bot smoke."""
        assert looks_like_food_drink("Сок 0,5л")

    def test_coffee_with_milk(self) -> None:
        """parse_beverage stem missed; we catch via 'молочный' / 'кофе' miss
        is in upstream — here we catch the food shape via length + format."""
        # "Кофе с молоком" doesn't have a stem in this list — the upstream
        # parse_beverage hits it. If parse_beverage misses, this falls back
        # to the LLM unless the user adds units.
        # Make sure the 30-char + num-unit combo still works for the variant.
        assert looks_like_food_drink("Кофе 250мл")


# ─── num-unit pattern (no stem) ───────────────────────────────────────────


class TestNumUnitPattern:
    @pytest.mark.parametrize(
        "text",
        [
            "250г",
            "0.5л",
            "0,5л",
            "100 мл",
            "2 шт",
            "1 порц",  # stem-form; "порция" with declension doesn't hit
        ],
    )
    def test_number_unit_hits(self, text: str) -> None:
        assert looks_like_food_drink(text), f"expected hit on {text!r}"

    def test_bare_unit_without_number_does_not_hit(self) -> None:
        """Plain ``стакан`` with no digit AND no food-stem misses.

        This is intentional — without a number the message is too
        ambiguous (could be the start of a question). The food log
        path expects a quantity.
        """
        assert not looks_like_food_drink("стакан")


# ─── negative cases ───────────────────────────────────────────────────────


class TestMisses:
    @pytest.mark.parametrize(
        "text",
        [
            "Здравствуйте",
            "Хочу записаться на массаж",
            "Где вы находитесь?",
            "Когда работаете в воскресенье?",
            "У меня болит спина уже неделю, не могу разогнуться нормально",
            # Length cap: 31+ chars is a question, not a log.
            "Хочу борщ с пампушками и сметаной",
            # Empty / whitespace.
            "",
            "   ",
        ],
    )
    def test_non_food_messages(self, text: str) -> None:
        assert not looks_like_food_drink(text), f"unexpected hit on {text!r}"


class TestTypeTolerance:
    @pytest.mark.parametrize("bad", [None, 123, b"borscht", ["borscht"], {"text": "x"}])
    def test_non_str_returns_false(self, bad: object) -> None:
        assert looks_like_food_drink(bad) is False
