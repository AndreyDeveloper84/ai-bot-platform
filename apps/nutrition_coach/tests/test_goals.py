"""Active-goal reader — every degradation branch fails closed (DRF-1464, T2).

For the proactive surface «no goal» means «stay silent», so the right
side of every error is ``None``: an outage is indistinguishable from a
person without a goal, and an archived goal reads as no goal at all
(owner decision Q-NUTRITION-08: архив = тишина).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from apps.integrations.ayla.goals_client import (
    GoalsBadRequest,
    GoalsConfigError,
    GoalsUnavailable,
)
from apps.nutrition_coach.goals import Goal, active_goal

USER = SimpleNamespace(channel="telegram", channel_user_id="123")


def fetch_returning(document: Any):
    def fetch(*, external_user_id: str) -> Any:
        return document

    return fetch


def fetch_raising(exc: Exception):
    def fetch(*, external_user_id: str) -> Any:
        raise exc

    return fetch


class TestFetchFailures:
    """The read itself failed — all four shapes land on None."""

    def test_config_error_is_silence(self) -> None:
        result = active_goal(USER, fetch=fetch_raising(GoalsConfigError("no token")))
        assert result is None

    def test_unavailable_is_silence(self) -> None:
        result = active_goal(USER, fetch=fetch_raising(GoalsUnavailable("circuit_open")))
        assert result is None

    def test_bad_request_is_silence(self) -> None:
        result = active_goal(USER, fetch=fetch_raising(GoalsBadRequest(400, {"detail": "x"})))
        assert result is None

    def test_unexpected_error_is_silence(self) -> None:
        result = active_goal(USER, fetch=fetch_raising(RuntimeError("boom")))
        assert result is None


class TestDocumentShapes:
    """Ayla answered, but the answer holds no active goal."""

    def test_document_not_a_dict(self) -> None:
        result = active_goal(USER, fetch=fetch_returning([]))
        assert result is None

    def test_empty_document(self) -> None:
        result = active_goal(USER, fetch=fetch_returning({}))
        assert result is None

    def test_known_not_a_dict(self) -> None:
        result = active_goal(USER, fetch=fetch_returning({"known": "nope"}))
        assert result is None

    def test_no_goal(self) -> None:
        result = active_goal(USER, fetch=fetch_returning({"known": {"goal": None}}))
        assert result is None

    def test_archived_goal_is_silence(self) -> None:
        """Q-NUTRITION-08. Ayla filters archived goals out of the document
        (``is_active`` filter), so the live shape is ``goal: None`` above;
        an explicit ``is_active: False`` fails closed the same way."""
        document = {"known": {"goal": {"goal_key": "relax", "is_active": False}}}
        result = active_goal(USER, fetch=fetch_returning(document))
        assert result is None

    def test_goal_not_a_dict(self) -> None:
        result = active_goal(USER, fetch=fetch_returning({"known": {"goal": "relax"}}))
        assert result is None

    def test_goal_without_key_and_text(self) -> None:
        document = {"known": {"goal": {"selected_at": "2026-09-01T10:00:00+00:00"}}}
        result = active_goal(USER, fetch=fetch_returning(document))
        assert result is None

    def test_goal_with_malformed_key_and_no_text(self) -> None:
        document = {"known": {"goal": {"goal_key": 42}}}
        result = active_goal(USER, fetch=fetch_returning(document))
        assert result is None


class TestActiveGoal:
    def test_curated_goal_by_key(self) -> None:
        document = {"known": {"goal": {"goal_key": "more_energy", "goal_text": None}}}
        result = active_goal(USER, fetch=fetch_returning(document))
        assert result == Goal(key="more_energy", text=None)

    def test_free_text_goal(self) -> None:
        document = {"known": {"goal": {"goal_key": None, "goal_text": "Больше энергии днём"}}}
        result = active_goal(USER, fetch=fetch_returning(document))
        assert result == Goal(key="", text="Больше энергии днём")

    def test_key_and_text_together(self) -> None:
        document = {"known": {"goal": {"goal_key": "more_energy", "goal_text": "к вечеру не валиться"}}}
        result = active_goal(USER, fetch=fetch_returning(document))
        assert result == Goal(key="more_energy", text="к вечеру не валиться")

    def test_non_string_text_is_treated_as_absent(self) -> None:
        document = {"known": {"goal": {"goal_key": "relax", "goal_text": 42}}}
        result = active_goal(USER, fetch=fetch_returning(document))
        assert result == Goal(key="relax", text=None)


class TestGoalTextSanitization:
    """``goal_text`` is untrusted Ayla-side free text headed for an LLM
    prompt (through ``build_safe_inputs`` later); it is cut down here
    already — control chars out, length capped."""

    def test_control_chars_stripped(self) -> None:
        document = {"known": {"goal": {"goal_text": "хочу\x00 энергии\n\nбольше"}}}
        result = active_goal(USER, fetch=fetch_returning(document))
        assert result is not None
        assert result.text == "хочу энергии\n\nбольше".replace("\x00", "")

    def test_text_capped_at_max_length(self) -> None:
        document = {"known": {"goal": {"goal_text": "я" * 500}}}
        result = active_goal(USER, fetch=fetch_returning(document))
        assert result is not None
        assert result.text is not None
        assert len(result.text) == 200

    def test_whitespace_only_text_is_no_text(self) -> None:
        document = {"known": {"goal": {"goal_key": "relax", "goal_text": "   "}}}
        result = active_goal(USER, fetch=fetch_returning(document))
        assert result == Goal(key="relax", text=None)


class TestIdentityBridging:
    def test_external_user_id_is_derived_from_bot_user(self) -> None:
        seen: list[str] = []

        def fetch(*, external_user_id: str) -> dict:
            seen.append(external_user_id)
            return {"known": {"goal": {"goal_key": "relax"}}}

        result = active_goal(USER, fetch=fetch)
        assert result is not None
        assert seen == ["bot:telegram:123"]
