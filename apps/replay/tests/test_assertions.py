"""evaluate + evaluate_voice tests (DRF-515 + DRF-516 / Sprint 5 / D2 + D3)."""

from __future__ import annotations

from apps.replay.assertions import evaluate, evaluate_voice


class TestEvaluateMustPass:
    def test_intent_match(self):
        trace = {"intent": "faq", "response_text": "x"}
        assert evaluate(trace, [{"intent": "faq"}], []) == []

    def test_intent_mismatch(self):
        trace = {"intent": "small_talk", "response_text": ""}
        failures = evaluate(trace, [{"intent": "faq"}], [])
        assert len(failures) == 1
        assert "intent" in failures[0]
        assert "faq" in failures[0]
        assert "small_talk" in failures[0]

    def test_skill_used_match(self):
        trace = {"skill_used": "faq", "response_text": ""}
        assert evaluate(trace, [{"skill_used": "faq"}], []) == []

    def test_safety_decision_match(self):
        trace = {"safety_decision": "allow", "response_text": ""}
        assert evaluate(trace, [{"safety_decision": "allow"}], []) == []

    def test_safety_decision_mismatch(self):
        trace = {"safety_decision": "block", "response_text": ""}
        failures = evaluate(trace, [{"safety_decision": "allow"}], [])
        assert len(failures) == 1

    def test_tool_called_hit(self):
        trace = {
            "tool_calls": [{"name": "search_kb", "args": {}}],
            "response_text": "",
        }
        assert evaluate(trace, [{"tool_called": "search_kb"}], []) == []

    def test_tool_called_miss(self):
        trace = {"tool_calls": [{"name": "other", "args": {}}], "response_text": ""}
        failures = evaluate(trace, [{"tool_called": "search_kb"}], [])
        assert len(failures) == 1
        assert "search_kb" in failures[0]

    def test_response_contains_any_hit(self):
        trace = {"response_text": "this is the price answer"}
        assert (
            evaluate(
                trace,
                [{"response_contains_any": ["price", "cost"]}],
                [],
            )
            == []
        )

    def test_response_contains_any_miss(self):
        trace = {"response_text": "I don't know"}
        failures = evaluate(
            trace,
            [{"response_contains_any": ["price", "cost"]}],
            [],
        )
        assert len(failures) == 1

    def test_response_contains_all_hit(self):
        trace = {"response_text": "price for the back massage is 2000"}
        assert (
            evaluate(
                trace,
                [{"response_contains_all": ["price", "massage"]}],
                [],
            )
            == []
        )

    def test_response_contains_all_partial_miss(self):
        trace = {"response_text": "price unknown"}
        failures = evaluate(
            trace,
            [{"response_contains_all": ["price", "massage"]}],
            [],
        )
        assert len(failures) == 1


class TestEvaluateForbidden:
    def test_forbidden_response_contains_any_blocks(self):
        trace = {"response_text": "I don't know, ask later"}
        failures = evaluate(
            trace,
            [],
            [{"response_contains_any": ["I don't know", "ask later"]}],
        )
        assert len(failures) == 1
        assert "forbidden" in failures[0]

    def test_forbidden_clean_passes(self):
        trace = {"response_text": "Here is the price: 2000"}
        assert (
            evaluate(
                trace,
                [],
                [{"response_contains_any": ["I don't know"]}],
            )
            == []
        )


class TestEvaluateMultipleFailures:
    def test_collects_all(self):
        trace = {
            "intent": "small_talk",
            "skill_used": "echo",
            "safety_decision": "block",
            "response_text": "wat",
        }
        failures = evaluate(
            trace,
            [
                {"intent": "faq"},
                {"skill_used": "faq"},
                {"safety_decision": "allow"},
            ],
            [{"response_contains_any": ["wat"]}],
        )
        # 3 must_pass misses + 1 forbidden hit = 4 failures.
        assert len(failures) == 4


class TestUnknownKey:
    def test_unknown_key_surfaced(self):
        failures = evaluate({"response_text": ""}, [{"bogus": "x"}], [])
        assert any("unknown_assertion_key" in f for f in failures)


class TestEvaluateVoice:
    def test_max_length_pass(self):
        assert evaluate_voice("short", {"max_length": 100}) == []

    def test_max_length_fail(self):
        text = "x" * 700
        failures = evaluate_voice(text, {"max_length": 600})
        assert len(failures) == 1
        assert "max_length" in failures[0]

    def test_caps_lock_pass(self):
        # 1/4 alpha chars caps → 0.25 ratio < 0.5 threshold
        assert evaluate_voice("Hello there", {"caps_lock": 0.5}) == []

    def test_caps_lock_fail(self):
        failures = evaluate_voice("SCREAMING TEXT HERE", {"caps_lock": 0.5})
        assert len(failures) == 1
        assert "caps_lock" in failures[0]

    def test_forbidden_phrase_match(self):
        failures = evaluate_voice(
            "we guarantee 100% success",
            {"forbidden_phrases": [r"\bguarantee\b"]},
        )
        assert len(failures) == 1
        assert "forbidden_phrase" in failures[0]

    def test_forbidden_phrase_clean(self):
        assert (
            evaluate_voice(
                "we promise to do our best",
                {"forbidden_phrases": [r"\bguarantee\b"]},
            )
            == []
        )

    def test_bad_regex_no_crash(self):
        # Bad regex → treated as no-match (not raised).
        assert (
            evaluate_voice(
                "anything",
                {"forbidden_phrases": ["[unclosed"]},
            )
            == []
        )

    def test_combined_max_length_and_phrase(self):
        text = "we guarantee 100% — " + ("x" * 700)
        failures = evaluate_voice(
            text,
            {
                "max_length": 600,
                "forbidden_phrases": [r"\bguarantee\b"],
            },
        )
        # Both failures collected.
        assert len(failures) == 2


class TestCaseFoldingDidNotDisableTheCheck:
    """The guard on the fix, on the same data the fix was made for.

    Making `response_contains_any` case-insensitive fixed two golden
    fixtures. It could also have been a way to make the whole rule stop
    catching things, and «the mismatches are gone» reads identically either
    way. These tests are the difference: they hold a real text mismatch —
    not a capital letter, a different word — against the same assertion and
    require it to fail.
    """

    def _trace(self, text: str) -> dict:
        return {
            "intent": "",
            "skill_used": "",
            "safety_decision": "allow",
            "tool_calls": [],
            "response_text": text,
        }

    def test_a_real_mismatch_still_fails(self):
        """Different word, not different case. Must still be caught."""

        failures = evaluate(
            self._trace("Возраст должно быть от 14 до 90 — проверь?"),
            [{"response_contains_any": ["рост"]}],
            [],
        )
        assert failures, "a genuinely absent phrase was accepted"
        assert "response_contains_any" in failures[0]

    def test_case_only_difference_now_passes(self):
        """And the thing it was changed FOR, stated so the change is visible."""

        assert (
            evaluate(
                self._trace("Возраст должно быть от 14 до 90 — проверь?"),
                [{"response_contains_any": ["возраст"]}],
                [],
            )
            == []
        )

    def test_partial_word_still_has_to_be_present(self):
        """Folding case does not fold anything else — no stemming, no fuzz."""

        failures = evaluate(
            self._trace("Поняла 🙂"),
            [{"response_contains_any": ["понял" + "и"]}],
            [],
        )
        assert failures

    def test_contains_all_still_requires_all(self):
        failures = evaluate(
            self._trace("Открой приложение, чтобы удалить данные."),
            [{"response_contains_all": ["удалить данные", "ничего не удалила"]}],
            [],
        )
        assert failures, "contains_all passed with one of two substrings missing"

    def test_forbidden_now_catches_the_capitalised_form_too(self):
        """The direction that matters: forbidden rules got STRICTER.

        `«не знаю»` used to sail past a reply that opened with `«Не знаю…»`,
        because a sentence starts with a capital and the rule was written in
        lowercase. Every adversarial and voice fixture is forbidden-only, so
        this is the half of the change that tightens the sets guarding the
        dangerous behaviour.
        """

        failures = evaluate(
            self._trace("Не знаю, что вам ответить."),
            [],
            [{"response_contains_any": ["не знаю"]}],
        )
        assert failures, "a forbidden phrase escaped by starting a sentence"

    def test_exact_is_available_when_the_wording_is_the_point(self):
        """`response_contains_exact` is the strict rule, and it is strict."""

        strict = [{"response_contains_exact": ["Поняла"]}]
        assert evaluate(self._trace("Поняла 🙂"), strict, []) == []
        assert evaluate(self._trace("поняла 🙂"), strict, []), (
            "response_contains_exact ignored a case difference — then it is "
            "not the strict rule and nothing in the engine is"
        )

    def test_exact_requires_every_substring(self):
        failures = evaluate(
            self._trace("Поняла 🙂"),
            [{"response_contains_exact": ["Поняла", "записала"]}],
            [],
        )
        assert failures

    def test_caps_lock_rule_is_untouched(self):
        """Case-sensitivity did not leave the engine; it lives here."""

        assert evaluate_voice("ТИХО", {"caps_lock": 0.5}) != []
        assert evaluate_voice("тихо", {"caps_lock": 0.5}) == []


class TestNonStringSubstringsDoNotCrashTheEngine:
    """Six golden fixtures carried bare YAML numbers.

    ``- 250`` loads as `int`, `x in text` raises `TypeError`, and the
    assertion engine came apart instead of reporting a mismatch — a
    traceback out of `evaluate` reads like a broken rig and gets ignored,
    while a mismatch reads like a defect and gets fixed.

    Worse, `«- +79»` — a rule meant to catch a leaked phone number — loaded
    as the integer `79`, so even a working comparison would have been
    looking for the wrong string. The fixtures are quoted now; this is the
    guard for the next author.
    """

    def _trace(self, text: str) -> dict:
        return {
            "intent": "",
            "skill_used": "",
            "safety_decision": "allow",
            "tool_calls": [],
            "response_text": text,
        }

    def test_int_substring_is_compared_not_raised(self):
        assert (
            evaluate(
                self._trace("Записала 250 мл."),
                [{"response_contains_any": [250]}],
                [],
            )
            == []
        )

    def test_int_substring_that_is_absent_reports_a_mismatch(self):
        failures = evaluate(
            self._trace("Записала 250 мл."),
            [{"response_contains_any": [500]}],
            [],
        )
        assert failures
        assert "response_contains_any" in failures[0]

    def test_forbidden_int_substring_is_compared_not_raised(self):
        failures = evaluate(
            self._trace("Звоните на 79001234567."),
            [],
            [{"response_contains_any": [79]}],
        )
        assert failures
