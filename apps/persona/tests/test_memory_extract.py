"""Explicit green-fact extractor tests (M-B2 / #1099)."""

from __future__ import annotations

import pytest

from apps.persona.memory_extract import extract_green_facts


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
        ["я вегетарианец", "я вегетарианка", "я — вегетарианка", "я не ем мясо"],
    )
    def test_vegetarian_self_statements(self, text):
        assert ("lifestyle", "diet", "vegetarian") in _keys(text)

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
