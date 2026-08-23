"""Explicit green-fact extractor tests (M-B2 / #1099)."""

from __future__ import annotations

import pytest

from apps.persona.memory_extract import extract_green_facts, extract_user_facts


def _keys(text):
    return {c.dedup_key for c in extract_green_facts(text)}


class TestExtractGreenFacts:
    @pytest.mark.parametrize(
        "text",
        ["я веган", "Я — веган", "кстати я веганка", "я веган, кстати"],
    )
    def test_vegan_self_statements(self, text):
        assert ("lifestyle", "diet", "vegan") in _keys(text)

    @pytest.mark.parametrize(
        "text",
        ["я вегетарианец", "я вегетарианка", "я — вегетарианка"],
    )
    def test_vegetarian_self_statements(self, text):
        assert ("lifestyle", "diet", "vegetarian") in _keys(text)

    @pytest.mark.parametrize(
        "text",
        [
            "я не ем мясо",  # explicit exclusion, NOT a named diet (no fabrication)
            "я не ем свинину",  # NOT halal — no religious inference
            "я не ем глютен",  # NOT celiac — no diagnosis inference
            "я не пью молоко",  # NOT lactose intolerance
        ],
    )
    def test_food_exclusions_are_dropped_not_fabricated(self, text):
        """Owner ruling 2026-08-23 (Ответ 3): the receiver has no
        excluded_foods field, and mapping an exclusion to a diet_type is the
        forbidden fabrication. Detected → dropped loudly → NOT stored."""
        result = extract_user_facts(text)
        assert result.candidates == []
        assert [d.reason for d in result.drops] == ["diet_exclusion"]

    @pytest.mark.parametrize(
        "text",
        [
            "я не веган",  # negation between «я» and keyword
            "не хочу быть веганом",  # negated intent, no «я»→keyword adjacency
            "перестал быть веганом",
            "жена веганка",  # someone else, not the user
            "посоветуй ресторан для веганов",  # request, not a self-statement
            "уже не вегетарианка",
        ],
    )
    def test_false_positives_suppressed(self, text):
        # High precision: none of these store a diet fact about the user.
        assert extract_green_facts(text) == []

    def test_mixed_negation_and_affirmation(self):
        # «я вегетарианец, но не веган» → vegetarian yes, vegan suppressed.
        keys = _keys("я вегетарианец, но не веган")
        assert ("lifestyle", "diet", "vegetarian") in keys
        assert ("lifestyle", "diet", "vegan") not in keys

    def test_dedup_within_turn(self):
        out = extract_green_facts("я веган, чистый я веган")
        assert len([c for c in out if c.content["value"] == "vegan"]) == 1

    def test_no_match(self):
        assert extract_green_facts("хочу маникюр в Пензе") == []

    def test_non_string_safe(self):
        assert extract_green_facts("") == []
        assert extract_green_facts(None) == []  # type: ignore[arg-type]


def _by_key(text, key):
    return [c for c in extract_green_facts(text) if c.content.get("key") == key]


class TestNamedDietTypes:
    """DRF-1261: diet is a domain — named types only, never fabricated."""

    def test_keto(self):
        assert ("lifestyle", "diet", "keto") in _keys("я на кето")

    def test_halal_only_when_named(self):
        assert ("lifestyle", "diet", "halal") in _keys("я ем только халяль")

    def test_kosher_only_when_named(self):
        assert ("lifestyle", "diet", "kosher") in _keys("я соблюдаю кошер")

    def test_no_excluded_foods_fabrication_for_named_diet(self):
        """«я веган» → diet_type=vegan, БЕЗ развёрнутого списка ограничений."""
        (c,) = _by_key("я веган", "diet")
        assert c.content["diet_type"] == "vegan"
        assert "excluded_foods" not in c.content


class TestDietRetraction:
    """«исправляю» — the correction candidate drives supersession."""

    @pytest.mark.parametrize(
        "text",
        [
            "я теперь снова ем мясо",
            "я снова ем мясо",
            "я больше не веган",
            "я больше не вегетарианка",
        ],
    )
    def test_retraction_candidate(self, text):
        (c,) = _by_key(text, "diet")
        assert c.content["value"] == "none"
        assert c.content["diet_type"] is None

    def test_retraction_then_new_named_diet_keeps_the_last(self):
        """Single-cardinality: one turn keeps ONE diet candidate — the last."""
        out = _by_key("я больше не веган, я теперь на кето", "diet")
        assert [c.content["value"] for c in out] == ["keto"]


class TestAllergyPerimeter:
    """DRF-1290: allergy formulations are never stored, never silent."""

    @pytest.mark.parametrize(
        "text",
        [
            "у меня аллергия на орехи",
            "у меня аллергия на орехи, и я не ем мясо",
            "у меня непереносимость лактозы",
        ],
    )
    def test_allergy_dropped_loudly(self, text):
        result = extract_user_facts(text)
        assert result.candidates == []
        assert "allergy" in {d.reason for d in result.drops}

    def test_allergy_clause_does_not_poison_clean_clauses(self):
        """«я веган, и у меня аллергия на орехи» — the vegan part is clean."""
        result = extract_user_facts("я веган, и у меня аллергия на орехи")
        assert ("lifestyle", "diet", "vegan") in {c.dedup_key for c in result.candidates}
        assert "allergy" in {d.reason for d in result.drops}

    def test_dislike_is_not_allergy_and_not_stored(self):
        """«не люблю орехи» — no allergy marker, no consumption exclusion:
        nothing to store under the five pilot keys (and nothing dropped as
        sensitive)."""
        result = extract_user_facts("не люблю орехи")
        assert result.candidates == []
        assert result.drops == []


class TestSessionContextIsNotMemory:
    """Owner ruling §4: «сегодня хочу массаж в центре после шести до 3000»
    is the current search, not a durable preference."""

    @pytest.mark.parametrize(
        "text",
        [
            "сегодня хочу массаж в центре после шести до 3000",
            "хочу маникюр в Пензе",  # «хочу» — one-off desire, no durable anchor
            "мне сейчас удобно вечером",
            "завтра мне удобно после 18:00",
        ],
    )
    def test_session_phrases_store_nothing(self, text):
        assert extract_green_facts(text) == []


class TestPreferredTimeSlots:
    def test_named_slots(self):
        values = {
            c.content["value"]
            for c in _by_key("мне удобно утром и вечером", "preferred_time_slots")
        }
        assert values == {"morning", "evening"}

    def test_after_hour(self):
        values = {
            c.content["value"] for c in _by_key("мне удобно после 18:00", "preferred_time_slots")
        }
        assert values == {"evening"}

    def test_after_word_numeral(self):
        values = {
            c.content["value"] for c in _by_key("обычно могу после шести", "preferred_time_slots")
        }
        assert values == {"evening"}

    def test_late_evening_not_doubled(self):
        values = {
            c.content["value"]
            for c in _by_key("мне удобно поздним вечером", "preferred_time_slots")
        }
        assert values == {"late_evening"}

    def test_no_anchor_no_fact(self):
        """A bare time word without a durable anchor is not a preference."""
        assert _by_key("запишите меня на вечер", "preferred_time_slots") == []


class TestPreferredDistricts:
    def test_single_district(self):
        values = {
            c.content["value"] for c in _by_key("мне удобнее в Центре", "preferred_districts")
        }
        assert values == {"Центре"}  # verbatim as stated (inflection kept)

    def test_two_districts(self):
        values = {
            c.content["value"]
            for c in _by_key("мне удобно в Центре и в Арбекове", "preferred_districts")
        }
        assert values == {"Центре", "Арбекове"}

    def test_no_anchor_no_fact(self):
        assert _by_key("ищу массаж в Центре", "preferred_districts") == []


class TestPriceRange:
    def test_max_only(self):
        (c,) = _by_key("мне комфортно до 3000", "price_range")
        assert c.content["max"] == "3000.00"
        assert "min" not in c.content

    def test_range(self):
        (c,) = _by_key("ориентируюсь на бюджет от 1500 до 3000", "price_range")
        assert c.content["min"] == "1500.00"
        assert c.content["max"] == "3000.00"

    def test_not_ready_to_pay_more(self):
        (c,) = _by_key("я не готова платить больше 2500", "price_range")
        assert c.content["max"] == "2500.00"

    def test_foreign_currency_skipped(self):
        """The contract is rubles — a dollar phrase is not guessed."""
        assert _by_key("мне комфортно до 50 $", "price_range") == []

    def test_no_anchor_no_fact(self):
        assert _by_key("сколько стоит стрижка до 3000", "price_range") == []


class TestFavoriteMasters:
    @pytest.mark.parametrize(
        "text,name",
        [
            ("мой мастер — Анна", "Анна"),
            ("моя любимая мастер Мария", "Мария"),
            ("предпочитаю мастера Ирину", "Ирину"),  # verbatim inflection
            ("я записываюсь только к Анне", "Анне"),
        ],
    )
    def test_explicit_master_preference(self, text, name):
        values = {c.content["value"] for c in _by_key(text, "favorite_masters")}
        assert values == {name}

    def test_habit_without_preference_word_is_not_a_fact(self):
        """Owner ruling: «часто ходит к Анне» ≠ favorite. Even user-stated,
        «хожу к Анне» describes a habit, not a preference — «только/всегда»
        or an explicit «любимый/мой мастер» is required."""
        assert _by_key("я хожу к Анне", "favorite_masters") == []
