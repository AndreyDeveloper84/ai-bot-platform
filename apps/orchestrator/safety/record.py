"""The Safety Engine's verdict gets written where the consumer reads it.

Lane A slice A-2, producer half. Slice A-1 (`assessment.py`) gave the verdict
its canonical shape; DRF-1629 gave `ConversationState` a `safety` field and a
codec that survives Redis. Between the two there was no writer: on a live
turn the engine's honest answer was still ``BLOCKED(SAFETY_UNKNOWN)``, because
nothing had ever put a verdict into the state. This module is that writer.

It is deliberately small, and the three things it does NOT do are the
substance of the slice:

**It does not advance the revision.** The verdict is computed for the
revision the state is at, and says so (``evaluated_at_revision``). Which
message is "the current revision" is the turn producer's decision, made
before safety runs; a recorder that bumped the number itself would let a
verdict about the previous message wear the next message's revision — the
exact staleness P3 exists to catch, made invisible by the writer. When the
state is expired or absent, a new epoch is opened the way ``open_epoch``
opens it: numbering continues above the last revision handed out.

**It does not swallow a failed write.** If Redis refuses, the exception
leaves. A recorder that logged and returned would leave the previous verdict
in place — or ``UNKNOWN`` — and the caller would carry on believing the
person's message had been judged. The consumer's own guard cannot see that:
``UNKNOWN`` with no revision is its *legal* pair, and a stale verdict at the
right revision is indistinguishable from a fresh one. The test file holds
this by reading the function's tree, not its docstring.

**It does not persist the wall clock.** ``SafetyAssessment.evaluated_at`` is
returned to the caller and not written: DRF-1629 keeps wall-clock time out
of the safety payload on purpose (``test_no_wall_clock_reaches_the_safety_payload``),
and §123's two-hour bound is already the state's own inactivity TTL —
``save()`` refreshes it, ``load()`` reports the expiry as an event. Where
A-3's «reset is an ACTION» guard stores *when* a verdict was made is A-3's
question, and answering it here would pre-empt it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from apps.orchestrator.decision_readiness import state as state_mod
from apps.orchestrator.decision_readiness.state import (
    ConversationState,
    LoadResult,
    StateExpiry,
    StateLifecycle,
)
from apps.orchestrator.safety.assessment import (
    SafetyAssessment,
    assess,
    to_readiness_input,
)


@dataclass(frozen=True, slots=True)
class Recorded:
    """What one call wrote, and what it found before writing.

    ``expiry`` is not ``None`` exactly when the previous state had timed out
    (§123 / DRF-1629: expiry is an event, not a disappearance). It is handed
    back rather than logged here so the caller — who knows the person and the
    channel — can put it in the right journal.
    """

    assessment: SafetyAssessment
    #: The state as handed to ``save()`` — before its activity stamp. What the
    #: consumer will read is ``load()``, and the tests read it that way.
    state: ConversationState
    found: StateLifecycle
    expiry: StateExpiry | None = None


def _state_for(conversation_id: str, found: LoadResult) -> ConversationState:
    if found.lifecycle is StateLifecycle.LIVE:
        assert found.state is not None  # LoadResult(LIVE) always carries one
        return found.state
    return state_mod.open_epoch(conversation_id, after=found)


def record_verdict(
    conversation_id: str,
    verdict_result,
    *,
    source: str = "pre_check",
    now: datetime | None = None,
) -> Recorded:
    """Assess ``verdict_result`` for the state's current revision and save it.

    ``verdict_result`` is :class:`apps.orchestrator.safety.pre_check.SafetyResult`
    — the same argument :func:`assess` takes. ``now`` is for tests; a caller
    on a live turn leaves it alone and the state's ``last_activity_at`` is the
    wall clock at write time.
    """
    found = state_mod.load(conversation_id, now=now)
    state = _state_for(conversation_id, found)
    assessment = assess(verdict_result, state_revision=state.revision, source=source, now=now)
    written = state.with_safety(to_readiness_input(assessment))
    state_mod.save(written, now=now)
    return Recorded(
        assessment=assessment,
        state=written,
        found=found.lifecycle,
        expiry=found.expiry,
    )


__all__ = ["Recorded", "record_verdict"]
