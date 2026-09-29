"""Coming back after a break — `ResumeSummary`, 24h, Redis. Slice E9, OD-DR-4.

### The dead end this exists to open

Reproduced before it was fixed, not reasoned about. A question asked at revision
7 and answered at 8; the person goes quiet; the state's two hours expire; they
come back at revision 9:

```
state    : blocked
blockers : readiness_input_unavailable — "state needs_required_context requires a
           question but the catalog offers none (unsatisfied slots: city)"
codes    : ASK_SUPPRESSED_ALREADY_ASKED · REQ_CONTEXT_UNSATISFIED:city
```

Three facts collide. The slot is empty, because slot values lived in
`ConversationState` and that expired. The register is intact, because it lives in
`Conversation.skill_state`, which has no TTL of its own. And the question is
suppressed as already asked, because it was.

So the slot can never be satisfied and the question can never be asked — for the
whole 24-hour window. `evaluate()` reported it honestly: the policy table and the
register disagree. It was right, and there was no way out.

That is not "a missing feature". It is a refusal wearing the shape of an answer:
`load()` says `EXPIRED` and hands back a revision number, and a caller cannot
tell "the state expired, here is what to resume from" apart from "the state
expired and there is nothing to resume from".

### What a summary is allowed to be

OD-DR-4, verbatim: the summary is **not** authoritative domain truth, **not**
mutable transaction truth, **not** UserMemory. So it does not carry slot values,
and there is no field for them — restoring a value from here would make a
24-hour-old note the source of truth for a price, a slot or a preference, and
would smuggle back evidence whose own lifetime had ended (P2).

It carries **pointers**: which questions had been asked and settled before the
gap. That is enough to open the dead end, and not enough to close a slot.

### The way out is one of the five reasons, not a sixth

On resume, a question whose answer expired with the state becomes eligible for
`REASK_ANSWER_EXPIRED` — one of §13.5's five, already frozen.

**This is a reading, and it is worth saying so.** §13.5 defines that reason as a
`VOLATILE` slot whose `ttl_seconds` elapsed. §13.4 says a `STABLE` slot's answer
lives "до конца сессии (канон §4.1, TTL бездействия 2 часа)". When those two
hours fire, a `STABLE` answer has reached the end of the life the spec gives it —
so the reason applies by the spec's own words rather than by extension. Nothing
new is invented, and no sixth reason is added.

The ceiling still holds: `MAX_ASKS_PER_QUESTION_ID = 2` is unchanged, so a
resumed conversation gets one controlled re-ask per question, not an open licence.
"""

from __future__ import annotations

import functools
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from django.conf import settings
import redis

from apps.orchestrator.decision_readiness.ledger import (
    ExpiryMechanism,
    QuestionLedger,
    ReaskConditions,
)
from apps.orchestrator.decision_readiness.state import (
    REVISION_HORIZON_SECONDS,
    ConversationState,
    LoadResult,
    StateLifecycle,
)

logger = logging.getLogger(__name__)

_KEY_PREFIX = "dre"

#: OD-DR-4: ResumeSummary TTL = 24h. The same horizon the revision counter keeps
#: (`state.REVISION_HORIZON_SECONDS`), and deliberately the same number: past it,
#: the revisions are gone too, so there would be nothing to resume *into*.
RESUME_TTL_SECONDS = REVISION_HORIZON_SECONDS


@dataclass(frozen=True, slots=True)
class SettledQuestion:
    """A question that had been asked and answered before the break.

    Slots by name, never by value. See the module docstring on why there is no
    field here for what the answer was.
    """

    qid: str
    slots: tuple[str, ...]
    resolved_at_revision: int | None = None


@dataclass(frozen=True, slots=True)
class ResumeSummary:
    """What survives the two hours, and nothing more.

    Not authoritative domain truth, not mutable transaction truth, not
    UserMemory (OD-DR-4). Structurally: no values, only references.
    """

    conversation_id: str
    last_revision: int
    settled: tuple[SettledQuestion, ...] = ()
    produced_at: datetime | None = None

    def settled_qids(self) -> frozenset[str]:
        return frozenset(item.qid for item in self.settled)

    def slots_that_had_answers(self) -> frozenset[str]:
        return frozenset(slot for item in self.settled for slot in item.slots)


def summarise(
    state: ConversationState,
    ledger: QuestionLedger,
    *,
    now: datetime | None = None,
) -> ResumeSummary:
    """Build the summary from a live state and its register.

    Called while the conversation is alive, so that something exists to resume
    from later. Reads the register for what was settled and the state only for
    its revision — not for its slot values, which are exactly what must not
    travel.
    """

    return ResumeSummary(
        conversation_id=state.conversation_id,
        last_revision=state.revision,
        settled=tuple(
            SettledQuestion(
                qid=entry.qid,
                slots=tuple(entry.slots),
                resolved_at_revision=entry.resolved_at_revision,
            )
            for entry in sorted(ledger.entries, key=lambda e: e.asked_at_revision)
            if entry.resolved
        ),
        produced_at=now or datetime.now(UTC),
    )


def resume_conditions(
    summary: ResumeSummary | None,
    *,
    after: LoadResult,
) -> dict[str, ReaskConditions]:
    """Per-question re-ask conditions for the turn that follows a break.

    Per-question, not global, and that is the point. A single
    `answer_expired=True` for the whole turn would also hand a re-ask to
    questions nobody ever answered — the imprecision out of which the DRF-1542
    class comes back. Only questions the summary records as **settled** get one,
    because only their answers are the thing that expired.

    Returns an empty mapping when the state is live (nothing expired) or when
    there is no summary (nothing to resume from — and the caller must not
    invent one).
    """

    if after.lifecycle is StateLifecycle.LIVE or summary is None:
        return {}

    return {
        item.qid: ReaskConditions(
            answer_expired=True,
            # Named, not implied: the audit has to be able to tell a session that
            # ended from a volatile slot whose own ttl_seconds ran out. Same code
            # outward, separate counters inward.
            expiry_mechanism=ExpiryMechanism.SESSION_ENDED,
        )
        for item in summary.settled
    }


@functools.lru_cache(maxsize=1)
def _redis_client() -> redis.Redis:
    """Same shape as `state._redis_client`; tests monkeypatch this attribute."""

    url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(url, decode_responses=True)


def _key(conversation_id: str) -> str:
    return f"{_KEY_PREFIX}:resume:{conversation_id}"


def save(summary: ResumeSummary) -> None:
    """Write the summary with its 24h TTL.

    Best-effort is **not** the contract here, but neither is fail-closed: losing
    a summary costs a resumed conversation one extra question, which the ask
    ceiling then bounds. It is written where the turn ends, and a failure is
    logged rather than raised — a person mid-conversation should not lose their
    turn because a resumption aid could not be stored.
    """

    payload = {
        "v": 1,
        "conversation_id": summary.conversation_id,
        "last_revision": summary.last_revision,
        "produced_at": summary.produced_at.isoformat() if summary.produced_at else None,
        "settled": [
            {
                "qid": item.qid,
                "slots": list(item.slots),
                "resolved_at_revision": item.resolved_at_revision,
            }
            for item in summary.settled
        ],
    }
    try:
        _redis_client().set(
            _key(summary.conversation_id),
            json.dumps(payload, ensure_ascii=False),
            ex=RESUME_TTL_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 — see the docstring on the contract
        logger.warning(
            "decision_readiness.resume.save_failed conversation=%s error=%s",
            summary.conversation_id,
            exc,
        )


def load(conversation_id: str) -> ResumeSummary | None:
    """Read the summary, or `None` past the 24 hours.

    `None` is not an error and not an empty summary: it means there is nothing
    to resume from, and the caller must open a clean epoch rather than granting
    re-asks it cannot justify.
    """

    try:
        raw = cast("str | None", _redis_client().get(_key(conversation_id)))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "decision_readiness.resume.load_failed conversation=%s error=%s",
            conversation_id,
            exc,
        )
        return None

    if raw is None:
        return None

    try:
        payload: dict[str, Any] = json.loads(raw)
        produced = payload.get("produced_at")
        return ResumeSummary(
            conversation_id=str(payload["conversation_id"]),
            last_revision=int(payload["last_revision"]),
            settled=tuple(
                SettledQuestion(
                    qid=str(item["qid"]),
                    slots=tuple(item.get("slots", ())),
                    resolved_at_revision=item.get("resolved_at_revision"),
                )
                for item in payload.get("settled", [])
            ),
            produced_at=datetime.fromisoformat(produced) if produced else None,
        )
    except Exception as exc:  # noqa: BLE001 — a corrupt summary is no summary
        logger.warning(
            "decision_readiness.resume.unreadable conversation=%s error=%s",
            conversation_id,
            exc,
        )
        return None
