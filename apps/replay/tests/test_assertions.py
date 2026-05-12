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
