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


# ---------------------------------------------------------------------------
# DRF-1404 — a stem is a WORD, not a substring.
#
# ``looks_like_food_drink`` matched stems with a bare ``stem in lower``.
# Measured on 2026-08-25, all 17 phrases below produced the food card.
#
# This module documents a deliberate bias — «False positives are fine»,
# the user taps «Опечатка» and the cost is one tap — and that bias is
# KEPT here: the fix closes the class of stems found INSIDE a longer
# word, and narrows only the handful of short stems that also collide at
# a word START. Nothing else is tightened.
#
# The load-bearing case is «кашель третий день» → food card («каш»).
# That one is not a cheap tap: a person reporting a symptom is asked
# whether to log it as a meal, so the health signal is spent on the
# wrong skill.
#
# ADD to these tuples — never trim them. FOOD_PHRASES is the half that
# keeps the fix honest: in THIS module the miss is the expensive
# direction, so a narrowed stem must be shown to still catch its food.
# ---------------------------------------------------------------------------

NOT_FOOD_PHRASES: tuple[str, ...] = (
    # ── the load-bearing one: a symptom, not a meal («каш») ──
    "кашель третий день",
    "кашель не проходит",
    # ── stem inside a longer word ──
    "правильное питание",  # «тан»
    "я стандартно опаздываю",  # «тан»
    "нет повода",  # «вод»
    "завод рядом",  # «вод»
    "провод оборвался",  # «вод»
    "высокий рост",  # «сок»
    "носок порвался",  # «сок»
    "вещи в шкафу",  # «щи»
    "муха летает",  # «уха»
    # ── stem at a word START, but a different word ──
    "мой водитель",  # «вод»
    "сокращаю сахар",  # «сок»
    "рисую по вечерам",  # «рис»
    "какой риск",  # «рис»
    "рискну",  # «рис»
    "пасть болит",  # «паст»
    "пастельные тона",  # «паст»
    "ухаживаю за мамой",  # «уха»
    "иду на танцы",  # «тан»
    "танго",  # «тан»
    "пловец",  # «плов»
    "кашне",  # «каш»
    "суперинтересно",  # «суп»
)


FOOD_PHRASES: tuple[str, ...] = (
    # Every stem narrowed above must be shown to still catch its food.
    "каша",
    "каши",
    "Каша с молоком",
    "суп",
    "супа",
    "суп 300г",
    "рис",
    "риса",
    "рис с курицей",
    "паста",
    "пасты",
    "уха",
    "плов",
    "плова",
    "щи",
    "сок",
    "Сок 0,5л",
    "соки",
    "вода",
    "воды",
    "водичка",
    "тан",
    # Untouched stems must be untouched.
    "борщ",
    "котлета",
    "котлеты",
    "пельмени",
    "гречка",
    "пирожки",
    "пирожное",
    "блины",
    "омлет с тостом",
)


class TestDrf1404NotFood:
    @pytest.mark.parametrize("text", NOT_FOOD_PHRASES)
    def test_everyday_phrases_are_not_a_food_log(self, text: str) -> None:
        assert not looks_like_food_drink(text), f"unexpected hit on {text!r}"


class TestDrf1404FoodSurvives:
    """The half that makes the fix honest — the miss is the costly way."""

    @pytest.mark.parametrize("text", FOOD_PHRASES)
    def test_real_food_still_hits(self, text: str) -> None:
        assert looks_like_food_drink(text), f"expected hit on {text!r}"
