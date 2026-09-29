"""The register's write contract is fail-closed, unlike `refusal_memo`'s.

`refusal_memo`'s contract is "never break a turn over a hint" — losing a write
there costs one repeated refusal. Losing one here costs a DRF-1542 loop, so
§13.4 changes the contract: a failed read or write becomes
`availability.ledger_readable = False` → `BLOCKED(READINESS_INPUT_UNAVAILABLE)`
→ the question is not asked.

These tests assert the exception is raised rather than swallowed. A returned
status could be ignored, and an ignored status *is* the best-effort behaviour
being replaced.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.orchestrator.decision_readiness import ledger_store
from apps.orchestrator.decision_readiness.ledger import (
    STATE_KEY,
    LedgerEntry,
    LedgerUnavailable,
    QuestionLedger,
)
from apps.orchestrator.decision_readiness.policy import ControlledPolicy
from apps.orchestrator.decision_readiness.questions import QuestionKind

POLICY = ControlledPolicy(policy_version=1)


class StubConversation:
    """Enough of `Conversation` for the store: the JSON field and an id."""

    def __init__(self, skill_state: Any = None) -> None:
        self.id = "conv-1"
        self.skill_state = skill_state if skill_state is not None else {}


class ExplodingConversation(StubConversation):
    @property  # type: ignore[override]
    def skill_state(self) -> Any:
        raise RuntimeError("column unavailable")

    @skill_state.setter
    def skill_state(self, value: Any) -> None:
        self._skill_state = value


def _ledger() -> QuestionLedger:
    return QuestionLedger().record_ask(
        LedgerEntry(
            qid="q1",
            kind=QuestionKind.REQUIRED_CONTEXT,
            slots=("body_area",),
            semantics_version=1,
            policy_version=1,
            asked_at="2026-09-10T12:00:00+00:00",
            asked_at_revision=2,
        ),
        policy=POLICY,
    )


def test_reading_an_absent_key_gives_an_empty_register() -> None:
    assert ledger_store.read_ledger(StubConversation()) == QuestionLedger()  # type: ignore[arg-type]


def test_reading_a_stored_register_round_trips() -> None:
    conversation = StubConversation({STATE_KEY: _ledger().to_state()})

    assert ledger_store.read_ledger(conversation) == _ledger()  # type: ignore[arg-type]


def test_an_unreachable_field_raises_rather_than_reading_as_empty() -> None:
    with pytest.raises(LedgerUnavailable, match="skill_state unreadable"):
        ledger_store.read_ledger(ExplodingConversation())  # type: ignore[arg-type]


def test_a_corrupt_payload_raises() -> None:
    conversation = StubConversation({STATE_KEY: {"v": 1, "asked": [{"qid": "q1"}]}})

    with pytest.raises(LedgerUnavailable):
        ledger_store.read_ledger(conversation)  # type: ignore[arg-type]


def test_a_failed_write_raises_and_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The single most important assertion in this module."""

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("row locked")

    monkeypatch.setattr(ledger_store, "write_skill_state", boom)

    with pytest.raises(LedgerUnavailable, match="ledger write failed"):
        ledger_store.write_ledger(StubConversation(), _ledger())  # type: ignore[arg-type]


def test_a_successful_write_passes_the_disjoint_subkey(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def capture(conversation: Any, subkey: str, value: Any) -> None:
        captured["subkey"] = subkey
        captured["value"] = value

    monkeypatch.setattr(ledger_store, "write_skill_state", capture)

    ledger_store.write_ledger(StubConversation(), _ledger())  # type: ignore[arg-type]

    assert captured["subkey"] == STATE_KEY
    assert captured["value"]["asked"][0]["qid"] == "q1"


def test_the_store_uses_the_shared_atomic_writer_not_its_own_read_modify_write() -> None:
    """Conversations retro B1: two skills writing different sub-keys, each
    reading the same pre-image, the second one's `update_fields=["skill_state"]`
    replacing the whole column with its single-key view. That bug does not need
    finding twice."""

    import inspect

    source = inspect.getsource(ledger_store)

    assert "write_skill_state" in source
    assert "save(update_fields" not in source
