"""Memory coordinator tests (DRF-540 / Sprint 6 / O6)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from apps.orchestrator.memory.coordinator import MemorySnapshot, load_snapshot


def _make_conversation(*, history=None, profile=None, slot_state=None):
    """Build a duck-typed Conversation-like object for the coordinator."""

    bot_user = SimpleNamespace()
    if profile is not None:
        bot_user.client_profile = profile

    return SimpleNamespace(
        id=uuid4(),
        bot_user=bot_user,
        slot_state=slot_state or {},
    )


def _profile(**overrides):
    base = {
        "rfm_segment": "loyal",
        "lifecycle_stage": "active",
        "loyalty_tier": "silver",
        "recency_days": 10,
        "frequency_visits": 5,
        "churn_risk": 0.2,
        "favorite_service_id": "svc_relax",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestShape:
    def test_returns_memory_snapshot(self):
        conv = _make_conversation()
        snap = load_snapshot(conv)
        assert isinstance(snap, MemorySnapshot)

    def test_dataclass_is_frozen(self):
        from dataclasses import FrozenInstanceError

        snap = MemorySnapshot()
        try:
            snap.history.append({"role": "user", "content": "x"})  # mutating list IS allowed
        except Exception:
            pass
        # The frozen check applies to the dataclass slots, not internal list state.
        # Attempting to rebind a field should raise:
        try:
            snap.history = [{"role": "user", "content": "y"}]  # type: ignore[misc]
        except FrozenInstanceError:
            pass
        else:
            raise AssertionError("MemorySnapshot fields must be frozen")


class TestShortTermIntegration:
    def test_history_pulled_from_short_term(self):
        conv = _make_conversation()
        recalled = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        with patch(
            "apps.orchestrator.memory.coordinator.short_term.recall",
            return_value=recalled,
        ):
            snap = load_snapshot(conv)
        assert snap.history == recalled

    def test_history_capped_by_settings(self, settings):
        settings.MEMORY_HISTORY_LIMIT = 3
        conv = _make_conversation()
        with patch("apps.orchestrator.memory.coordinator.short_term.recall") as mock_recall:
            mock_recall.return_value = []
            load_snapshot(conv)
        # Coordinator must request only the configured limit.
        args, kwargs = mock_recall.call_args
        assert kwargs.get("n") == 3

    def test_redis_failure_returns_empty_history(self):
        conv = _make_conversation()
        with patch(
            "apps.orchestrator.memory.coordinator.short_term.recall",
            side_effect=ConnectionError("redis down"),
        ):
            snap = load_snapshot(conv)
        assert snap.history == []


class TestLongTermIntegration:
    def test_profile_fields_propagate(self):
        conv = _make_conversation(profile=_profile())
        with patch("apps.orchestrator.memory.coordinator.short_term.recall", return_value=[]):
            snap = load_snapshot(conv)
        assert snap.long_term["rfm_segment"] == "loyal"
        assert snap.long_term["loyalty_tier"] == "silver"
        assert snap.long_term["lifecycle_stage"] == "active"
        assert snap.long_term["recency_days"] == 10
        assert snap.long_term["churn_risk"] == 0.2
        assert snap.long_term["favorite_service_id"] == "svc_relax"

    def test_missing_profile_returns_empty_long_term(self):
        # No client_profile attribute on bot_user → empty dict
        conv = _make_conversation()  # bot_user has no profile attr
        with patch("apps.orchestrator.memory.coordinator.short_term.recall", return_value=[]):
            snap = load_snapshot(conv)
        assert snap.long_term == {}

    def test_blank_profile_fields_become_empty_strings(self):
        profile = _profile(rfm_segment="", lifecycle_stage="", favorite_service_id="")
        conv = _make_conversation(profile=profile)
        with patch("apps.orchestrator.memory.coordinator.short_term.recall", return_value=[]):
            snap = load_snapshot(conv)
        assert snap.long_term["rfm_segment"] == ""
        assert snap.long_term["favorite_service_id"] == ""


class TestSlotState:
    def test_slot_state_copied(self):
        conv = _make_conversation(slot_state={"step": "name", "draft": {"x": 1}})
        with patch("apps.orchestrator.memory.coordinator.short_term.recall", return_value=[]):
            snap = load_snapshot(conv)
        assert snap.slot_state == {"step": "name", "draft": {"x": 1}}

    def test_slot_state_mutation_does_not_leak(self):
        original = {"step": "name"}
        conv = _make_conversation(slot_state=original)
        with patch("apps.orchestrator.memory.coordinator.short_term.recall", return_value=[]):
            snap = load_snapshot(conv)
        snap.slot_state["evil"] = "side_effect"
        # The coordinator returned a copy, so the original is untouched.
        assert "evil" not in original

    def test_none_slot_state_handled(self):
        conv = _make_conversation(slot_state=None)
        with patch("apps.orchestrator.memory.coordinator.short_term.recall", return_value=[]):
            snap = load_snapshot(conv)
        assert snap.slot_state == {}
