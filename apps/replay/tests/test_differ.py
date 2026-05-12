"""differ tests (DRF-517/518/519 / Sprint 5 / D4+D5+D6)."""

from __future__ import annotations

from apps.replay.differ import (
    metrics_delta,
    text_similarity,
    tool_calls_diff,
)


class TestTextSimilarity:
    def test_identical(self):
        assert text_similarity("hello world", "hello world") == 1.0

    def test_disjoint(self):
        # No characters in common → ratio is very low (not strictly 0
        # since difflib can find empty-string matches; we just assert
        # it's below 0.3 for clear "different").
        assert text_similarity("abcde", "fghij") < 0.3

    def test_empty_either(self):
        assert text_similarity("", "hello") == 0.0
        assert text_similarity("hello", "") == 0.0

    def test_partial_overlap_between(self):
        ratio = text_similarity("hello world", "hello there")
        assert 0.3 < ratio < 0.9


class TestToolCallsDiff:
    def test_no_diff_for_identical(self):
        calls = [{"name": "search_kb", "args": {"q": "price"}}]
        result = tool_calls_diff(calls, calls)
        assert result == {"added": [], "removed": [], "changed": []}

    def test_added(self):
        a = [{"name": "search_kb", "args": {"q": "x"}}]
        b = [
            {"name": "search_kb", "args": {"q": "x"}},
            {"name": "create_booking", "args": {}},
        ]
        result = tool_calls_diff(a, b)
        assert len(result["added"]) == 1
        assert result["added"][0][0] == "create_booking"
        assert result["removed"] == []
        assert result["changed"] == []

    def test_removed(self):
        a = [
            {"name": "search_kb", "args": {}},
            {"name": "create_booking", "args": {}},
        ]
        b = [{"name": "search_kb", "args": {}}]
        result = tool_calls_diff(a, b)
        assert len(result["removed"]) == 1
        assert result["removed"][0][0] == "create_booking"

    def test_changed_same_name_different_args(self):
        a = [{"name": "search_kb", "args": {"q": "price"}}]
        b = [{"name": "search_kb", "args": {"q": "schedule"}}]
        result = tool_calls_diff(a, b)
        assert result["added"] == []
        assert result["removed"] == []
        assert len(result["changed"]) == 1
        assert result["changed"][0]["name"] == "search_kb"
        assert result["changed"][0]["from"] != result["changed"][0]["to"]

    def test_args_order_invariant(self):
        """Same args dict regardless of key order → same hash."""

        a = [{"name": "f", "args": {"x": 1, "y": 2}}]
        b = [{"name": "f", "args": {"y": 2, "x": 1}}]
        result = tool_calls_diff(a, b)
        assert result == {"added": [], "removed": [], "changed": []}


class TestMetricsDelta:
    def test_no_change(self):
        a = {"latency_ms": 100, "tokens": 50, "voice_check_passed": True}
        result = metrics_delta(a, a)
        assert result["latency_delta_pct"] == 0
        assert result["tokens_delta_pct"] == 0
        assert result["latency_alarm"] is False
        assert result["tokens_alarm"] is False
        assert result["voice_check_changed"] is False

    def test_latency_alarm_triggered(self):
        result = metrics_delta(
            {"latency_ms": 100, "tokens": 50},
            {"latency_ms": 140, "tokens": 50},
        )
        # +40% > 30% threshold
        assert result["latency_alarm"] is True
        assert result["tokens_alarm"] is False

    def test_tokens_alarm_triggered(self):
        result = metrics_delta(
            {"latency_ms": 100, "tokens": 50},
            {"latency_ms": 100, "tokens": 80},
        )
        # +60% > 50% threshold
        assert result["tokens_alarm"] is True
        assert result["latency_alarm"] is False

    def test_voice_check_changed(self):
        result = metrics_delta(
            {"voice_check_passed": True},
            {"voice_check_passed": False},
        )
        assert result["voice_check_changed"] is True

    def test_zero_baseline_safe(self):
        # /0 safety — non-zero candidate → 100% increase
        result = metrics_delta(
            {"latency_ms": 0, "tokens": 0},
            {"latency_ms": 50, "tokens": 10},
        )
        assert result["latency_delta_pct"] == 1.0
        assert result["latency_alarm"] is True

    def test_zero_both(self):
        result = metrics_delta(
            {"latency_ms": 0, "tokens": 0},
            {"latency_ms": 0, "tokens": 0},
        )
        assert result["latency_delta_pct"] == 0
        assert result["latency_alarm"] is False
