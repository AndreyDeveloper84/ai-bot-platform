"""Event vocabulary tests (DRF-463 / Sprint 3 / B6)."""

from __future__ import annotations

from apps.events import vocabulary
from apps.events.vocabulary import CANONICAL_EVENTS, is_canonical


class TestVocabulary:
    def test_all_13_constants_defined(self):
        """Phase 0 §F0.7 enumerates 13 canonical events. All must be exported."""

        expected = {
            "CONVERSATION_STARTED",
            "MESSAGE_SENT",
            "MESSAGE_RECEIVED",
            "INTENT_CLASSIFIED",
            "SKILL_DISPATCHED",
            "TOOL_CALLED",
            "SAFETY_TRIGGERED",
            "HANDOFF_INITIATED",
            "PIPELINE_ERROR",
            "CONSENT_GRANTED",
            "CONSENT_WITHDRAWN",
            "CLIENT_PROFILE_RECOMPUTED",
            "REPLAY_CAPTURED",
        }
        for name in expected:
            assert hasattr(vocabulary, name), f"missing canonical constant: {name}"

    def test_canonical_events_frozenset_membership(self):
        assert len(CANONICAL_EVENTS) == 13
        assert "consent_granted" in CANONICAL_EVENTS
        assert "consent_withdrawn" in CANONICAL_EVENTS
        assert "safety_triggered" in CANONICAL_EVENTS
        assert "conversation_started" in CANONICAL_EVENTS

    def test_canonical_events_is_immutable(self):
        """frozenset, not set — vocabulary must not drift at runtime."""

        assert isinstance(CANONICAL_EVENTS, frozenset)

    def test_is_canonical_true_for_vocab(self):
        assert is_canonical("consent_granted") is True
        assert is_canonical("handoff_initiated") is True

    def test_is_canonical_false_for_legacy_dotted_names(self):
        """Internal infra events (worker.*, ingress.*) are intentionally out-of-vocab."""

        assert is_canonical("worker.handler_started") is False
        assert is_canonical("ingress.webhook_received") is False
        assert is_canonical("identity.bot_user.created") is False

    def test_is_canonical_false_for_unknown(self):
        assert is_canonical("totally_made_up_event") is False
        assert is_canonical("") is False

    def test_constants_match_string_values(self):
        """Constants are stringified canonical names — no accidental rename."""

        assert vocabulary.CONSENT_GRANTED == "consent_granted"
        assert vocabulary.CONSENT_WITHDRAWN == "consent_withdrawn"
        assert vocabulary.SAFETY_TRIGGERED == "safety_triggered"
        assert vocabulary.CONVERSATION_STARTED == "conversation_started"
        assert vocabulary.HANDOFF_INITIATED == "handoff_initiated"
