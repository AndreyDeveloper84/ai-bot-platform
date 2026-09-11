"""The conversation's decision state: a monotone revision and three-valued slots.

### Where this lives, and why that is not a deviation

Spec §6 places the computation of `readiness_state` in `ayla-ai-core`. This
package is in `ai-bot-platform`. That is not the spec being overridden by
convenience: **OD-DR-4 (CLOSED) names `ai-bot-platform` the P0 owner and Redis
the storage** — `docs/MEASUREMENT_DECISION_READINESS_CURRENT.md:552-556` in the
`Ayla` docs repository, the same ruling that fixes the two TTLs below. §6
describes the target placement; OD-DR-4 describes where it lives in P0. Moving
it there later is its own piece of work, not a debt hidden inside this one.

### `state_revision` — gap G1, precondition P1

Nothing in either repository had a monotone revision before this module
(spec §1.6, re-verified on `883b7539`: `git grep state_revision -- apps/` is
empty). Without it two things in the spec are unimplementable: the idempotency
key (§17.3) and telling a stale callback from a current one (canon §13.4).

Monotonicity is Redis `INCR` on a key of its own, not a field inside the state
blob. A counter inside the blob would be a read-modify-write, and two turns
arriving together would hand out the same number — the same class of race
`apps.conversations.services.write_skill_state` was written to close
(Conversations retro B1). `INCR` is atomic and needs no lock.

### The two hours are an event, not a disappearance

OD-DR-4: `ConversationState` inactivity TTL = 2h, `ResumeSummary` TTL = 24h.

A plain Redis `EXPIRE` on the state would satisfy the number and lose the
point. After it fires a read returns "nothing" — and "this conversation went
quiet and its state aged out" becomes indistinguishable from "this conversation
never had state". Those two require opposite behaviour: the first resumes from
what was known, the second starts clean. Collapsing them is the defect class
this lane exists to remove.

So expiry is observable:

* the state blob carries the 2h inactivity TTL;
* the revision counter carries a **24h** horizon — the ResumeSummary window
  from the same ruling;
* between those two, a read returns `StateLifecycle.EXPIRED` **with the last
  revision**, plus a log line. It is a fact with a value, not an absence.

The 24h horizon also keeps revisions monotone across the gap: a new epoch
continues from the last number rather than restarting at 1, so a callback
issued before the quiet period cannot come back looking newer than the state
that replaced it. Past 24h both keys are gone and the state is `ABSENT` — and a
callback older than that is outside ResumeSummary's own validity, so there is
nothing left for it to be compared against.

### Clocks

`evaluate()` reads no clock (spec §17.1); expiry is computed by the input
adapter (P2). This module is that adapter's storage half, so the clock lives
here and nowhere downstream.
"""

from __future__ import annotations

import functools
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, cast

from django.conf import settings
import redis

from .safety_input import KNOWN_SAFETY_STATES, Handoff, SafetyResult, SafetyState

logger = logging.getLogger(__name__)


# Key prefix for everything this package keeps in Redis. Disjoint from
# `conv:{uuid}:msgs` (`apps.orchestrator.memory.short_term`) and from every
# `skill_state` sub-key in use on `883b7539`.
_KEY_PREFIX = "dre"

# OD-DR-4 (CLOSED): ConversationState inactivity TTL = 2h.
STATE_TTL_SECONDS = 2 * 3600

# OD-DR-4 (CLOSED): ResumeSummary TTL = 24h. The revision counter outlives the
# state blob by exactly this window so that expiry is reportable and revisions
# stay monotone across it. This is not a second state TTL — nothing but the
# number survives here.
REVISION_HORIZON_SECONDS = 24 * 3600

# How long the hourly tally of unreadable safety entries is kept. Long enough
# to answer "was that a rollout or has it been going on all day?" — see
# `count_unreadable_safety_entries`.
UNREADABLE_HORIZON_SECONDS = 48 * 3600


class SlotState(str, Enum):
    """Canon §16.2 / spec §3.2 — three values, and `null` collapses none of them.

    `KNOWN`     a confirmed piece of evidence closed this slot.
    `UNKNOWN`   never asked, or asked and not answered.
    `FLEXIBLE`  the person said "doesn't matter / any" — out loud.

    `FLEXIBLE` satisfies required context and **forbids** asking about the slot
    again (spec §13.3 п.1). `UNKNOWN` satisfies nothing. A single nullable field
    would make one of the two invisible, and the invisible one would be
    `FLEXIBLE` — which reads as "still missing" and produces a question the
    person has already answered.

    There is deliberately no `ERASED` member. "Deliberately erased counts as
    filled" is a real live property of the ask-eligibility policy
    (`apps/orchestrator/memory_ask.py`, inventory §6) and it is preserved — but
    as a suppression rule inside the ask policy (slice E11), not as a fourth
    slot value. Canon §16.2 froze three; a fourth here would reopen it.
    """

    KNOWN = "known"
    UNKNOWN = "unknown"
    FLEXIBLE = "flexible"


@dataclass(frozen=True, slots=True)
class SlotValue:
    """One slot's standing, with the evidence that produced it.

    `evidence_id` is what makes a later retraction traceable (spec §13.5,
    `REASK_ANSWER_RETRACTED`): to know that an answer was withdrawn you have to
    know which answer closed the slot.
    """

    state: SlotState = SlotState.UNKNOWN
    value: str | None = None
    evidence_id: str | None = None
    at_revision: int | None = None

    def __post_init__(self) -> None:
        if self.state is SlotState.KNOWN and self.value is None:
            raise ValueError(
                "SlotValue(KNOWN) requires a value — a KNOWN slot with no value is UNKNOWN"
            )
        if self.state is not SlotState.KNOWN and self.value is not None:
            raise ValueError(
                f"SlotValue({self.state.value}) must not carry a value: {self.value!r}"
            )


UNKNOWN_SLOT = SlotValue()


@dataclass(frozen=True, slots=True)
class ConversationState:
    """Canon §13.4's state, reduced to what the readiness decision reads.

    Not the transcript, not UserMemory, not domain truth — those have their own
    owners and lifetimes (OD-DR-4 says exactly that of ResumeSummary). This is
    the decision-facing slice: which revision we are at, and what is settled.
    """

    conversation_id: str
    revision: int
    slots: dict[str, SlotValue] = field(default_factory=dict)
    epoch_started_at_revision: int = 1
    last_activity_at: datetime | None = None
    #: The Safety Engine's verdict for this conversation.
    #:
    #: The default is `not_evaluated()` — `UNKNOWN` — and that is not the
    #: silent default §11.4 forbids. The two are opposites: a default of
    #: `NORMAL` would **grant** permission nobody gave, while `UNKNOWN` gives
    #: `BLOCKED(SAFETY_UNKNOWN)` and grants nothing. A default is admissible
    #: exactly when forgetting to set it withholds rather than permits.
    #:
    #: It lives on the state rather than being passed alongside it because the
    #: verdict and the revision it was computed for are one fact (see
    #: `SafetyResult`), and `f()` checks that fact against `state.revision`.
    #: Carrying them separately is what lets a verdict from before the person's
    #: last message be read as an answer to their last message.
    safety: SafetyResult = field(default_factory=SafetyResult.not_evaluated)

    def slot(self, name: str) -> SlotValue:
        """A slot never asked about is `UNKNOWN` — never `None`.

        Absence answering with `UNKNOWN` is half of "UNKNOWN ≠ FLEXIBLE": a
        caller cannot accidentally read a missing key as "no preference".
        """

        return self.slots.get(name, UNKNOWN_SLOT)

    def with_slot(self, name: str, value: SlotValue) -> ConversationState:
        """Return a new state with one slot replaced. The state itself is frozen."""

        updated = dict(self.slots)
        updated[name] = value
        return ConversationState(
            conversation_id=self.conversation_id,
            revision=self.revision,
            slots=updated,
            epoch_started_at_revision=self.epoch_started_at_revision,
            last_activity_at=self.last_activity_at,
            safety=self.safety,
        )

    def with_safety(self, verdict: SafetyResult) -> ConversationState:
        """Return a new state carrying `verdict`. The state itself is frozen.

        Deliberately not a setter and deliberately not validating the revision:
        the producer may evaluate for a revision this object has already moved
        past, and hiding that here would turn a checkable staleness (P3, which
        `f()` enforces against `state.revision`) into a value that was quietly
        refused at the door. The verdict is carried as given; whether it is
        fresh enough is the engine's question, asked out loud.
        """

        return ConversationState(
            conversation_id=self.conversation_id,
            revision=self.revision,
            slots=dict(self.slots),
            epoch_started_at_revision=self.epoch_started_at_revision,
            last_activity_at=self.last_activity_at,
            safety=verdict,
        )


class StateLifecycle(str, Enum):
    """What a read found. Three outcomes, because two would hide the interesting one."""

    LIVE = "live"
    EXPIRED = "expired"
    ABSENT = "absent"


@dataclass(frozen=True, slots=True)
class StateExpiry:
    """The expiry event itself — a value the caller can carry, log and audit.

    The owner's requirement, verbatim: both TTLs must have an owner in code, and
    expiry must be an event rather than a silent disappearance. This dataclass
    is that event; `load()` is its owner.
    """

    conversation_id: str
    last_revision: int
    detected_at: datetime
    ttl_seconds: int = STATE_TTL_SECONDS


@dataclass(frozen=True, slots=True)
class LoadResult:
    lifecycle: StateLifecycle
    state: ConversationState | None = None
    expiry: StateExpiry | None = None


@functools.lru_cache(maxsize=1)
def _redis_client() -> redis.Redis:
    """Cached sync client, same shape as `apps.orchestrator.memory.short_term`.

    Tests monkeypatch this attribute directly; keeping the signature identical
    to the existing module means one pattern in the codebase, not two.
    """

    url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(url, decode_responses=True)


def _state_key(conversation_id: str) -> str:
    return f"{_KEY_PREFIX}:state:{conversation_id}"


def _revision_key(conversation_id: str) -> str:
    return f"{_KEY_PREFIX}:rev:{conversation_id}"


def next_revision(conversation_id: str) -> int:
    """Hand out the next `state_revision` for this conversation. Atomic, monotone.

    `INCR` on a dedicated key, not a field in the blob: two turns arriving
    together must not receive the same number, and a read-modify-write cannot
    promise that. The 24h `EXPIRE` is refreshed on every call — the counter
    outlives the state blob deliberately (module docstring).

    Revisions start at 1. `SemanticUserEvent` rejects 0 because `confirm()`
    compares with a strict `>`, and "before any event" needs a number below the
    first real one.
    """

    client = _redis_client()
    key = _revision_key(conversation_id)
    pipe = client.pipeline()
    pipe.incr(key)
    pipe.expire(key, REVISION_HORIZON_SECONDS)
    result = pipe.execute()
    return int(result[0])


def _unreadable_key(bucket: str) -> str:
    return f"{_KEY_PREFIX}:safety_unreadable:{bucket}"


def _hour_bucket(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H")


def _tally_unreadable(*, now: datetime | None = None) -> int | None:
    """Count one unreadable safety entry into this hour's bucket.

    ### Why a counter and not only the log line

    Tolerating an unreadable entry rests on one claim: it is a rollout, so it
    lasts minutes and stops. That claim is checkable only by frequency — and a
    log line nobody counts makes "a rollout just happened" and "this has been
    broken since yesterday" look identical, because both are a scatter of lines
    in a stream. A few in one hour is a deploy; the same lines every hour is a
    defect hiding in the noise of deploys.

    So the tolerance carries its own measure. Hourly buckets, because the unit
    of the claim is "minutes, not hours", and a total since boot cannot answer
    that.

    ### It must never cost a turn

    Returns `None` if the count could not be taken. An observability write that
    can raise turns a degraded read into a lost turn — which is a worse defect
    than the one being measured, and one introduced by the measuring.
    """

    try:
        client = _redis_client()
        key = _unreadable_key(_hour_bucket(now))
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, UNREADABLE_HORIZON_SECONDS)
        return int(pipe.execute()[0])
    except Exception:  # noqa: BLE001 — measuring must not break the thing measured
        logger.warning("dre.state.unreadable_tally_failed", exc_info=True)
        return None


def count_unreadable_safety_entries(
    *, hours: int = 24, now: datetime | None = None
) -> dict[str, int]:
    """Unreadable safety entries per hour, most recent hour first.

    The answer to "was that a rollout?" as a number rather than an impression.
    An hour with no entries is absent from the mapping rather than present as
    zero — the buckets only exist once something lands in them, and inventing
    zeros would claim knowledge of hours past the horizon.
    """

    client = _redis_client()
    start = now or datetime.now(UTC)
    tally: dict[str, int] = {}
    for offset in range(hours):
        bucket = _hour_bucket(start - timedelta(hours=offset))
        raw = cast("str | None", client.get(_unreadable_key(bucket)))
        if raw is not None:
            tally[bucket] = int(raw)
    return tally


def peek_revision(conversation_id: str) -> int | None:
    """Current revision without consuming one. `None` once past the 24h horizon."""

    # `redis.Redis` is typed for both the sync and the async client, so every
    # command reads as `Awaitable[Any] | Any`. This module is the sync client
    # throughout — same cast as `apps.orchestrator.memory.short_term:164`.
    raw = cast("str | None", _redis_client().get(_revision_key(conversation_id)))
    return None if raw is None else int(raw)


#: Fields of `SafetyResult` the codec knows how to carry, in a stable order.
#:
#: Named rather than derived so the payload shape does not drift when the type
#: gains a field — and guarded, in `tests/test_safety_in_state.py`, against
#: `dataclasses.fields(SafetyResult)`, so that a field added upstream fails a
#: check instead of being dropped on the floor at read time. A claim of
#: completeness with no guard behind it lives only until the next addition.
_SAFETY_CODEC_FIELDS: tuple[str, ...] = (
    "state",
    "evaluated_at_revision",
    # DRF-1629 / §127. `handoff` arrived on `SafetyResult` after this codec was
    # written, and it arrived exactly the way the guard below predicted: as a
    # red check naming the field, not as a value silently dropped at read time.
    # It belongs in the payload because it is part of the promise made to the
    # person — "we will hand you to a human" is not "we refuse" — and a promise
    # that survives the engine but not Redis is a promise the next turn forgets.
    "handoff",
    "rule_id",
    "policy_version",
    "required_slots",
    "forbidden_capabilities",
    # Свод владельца 11.09 §3. Оба пришли тем же путём, что `handoff`: сторож
    # полноты покраснел и назвал их. `activated_at` — единственные стенные
    # часы в вердикте; они едут в payload (§3 требует их между ходами) и НЕ
    # едут в дайджест (см. `SafetyResult.digest_fields`).
    "not_applicable_for",
    "activated_at",
)


class StoredVerdictWithoutOrigin(ValueError):
    """A blob carried a `"safety"` object whose state is `UNKNOWN`.

    Nothing in this package writes that: `_encode` omits the key entirely for a
    verdict that does not exist, because **absence already means "not
    evaluated"**. So a stored `UNKNOWN` has a second origin, and the two are
    indistinguishable once written: "the engine did not run" and "an adapter
    swallowed an error and returned the fail-closed value" arrive identical.

    That second one is the dangerous half, and it is dangerous precisely
    because it is fail-closed. A wrong `NORMAL` gets noticed — it lets through
    something that should have stopped. A wrong `UNKNOWN` looks like caution,
    blocks for a plausible reason, and nobody ever asks which reason. The
    mechanism would block correctly nine times and on a swallowed error the
    tenth, indistinguishably from outside.

    `SafetyResult.__post_init__` cannot catch this and not from weakness:
    `UNKNOWN` with no revision is its *permitted* pair. Origin is not visible
    from the value. The storage format is the only place it can be kept, and it
    keeps it by having no way to say it.
    """


def _encode_safety(verdict: SafetyResult) -> dict[str, Any] | None:
    """`None` for a verdict that does not exist — the caller omits the key.

    Not `null`, not an object saying `UNKNOWN`: one representation per fact.
    """

    if not verdict.is_known:
        return None
    return {
        "state": verdict.state.value,
        "evaluated_at_revision": verdict.evaluated_at_revision,
        # A known verdict always carries one: `SafetyResult.__post_init__`
        # refuses to construct otherwise. So this is never `None` here, and
        # writing it unconditionally keeps the payload shape fixed.
        "handoff": verdict.handoff.value if verdict.handoff else None,
        "rule_id": verdict.rule_id,
        "policy_version": verdict.policy_version,
        "required_slots": list(verdict.required_slots),
        "forbidden_capabilities": list(verdict.forbidden_capabilities),
        "not_applicable_for": verdict.not_applicable_for,
        "activated_at": verdict.activated_at.isoformat() if verdict.activated_at else None,
    }


def _decode_safety(entry: Any) -> SafetyResult:
    """No key — including a payload written before the key existed — is `UNKNOWN`.

    This is the whole point of the field being stored at all. A state blob
    written yesterday has no `"safety"` key, and a decoder that answered
    `NORMAL` there would let the fail-closed construction collapse **silently,
    on data already in Redis**. `SafetyResult` has no default for `state` for
    exactly this reason; the decoder is a second door into the same type and
    the prohibition applies to it whole.

    Three inputs, three different answers, and the differences are deliberate:

    * **key absent** — a fact with one meaning. `UNKNOWN`, no complaint.
    * **key present, unreadable shape** — `UNKNOWN`, because an older reader
      meeting a newer writer during a rolling deploy is a real event, and
      killing the person's turn over it buys nothing a block does not. What is
      lost is the verdict, and the honest reading of a lost verdict is that we
      were not told.
    * **key present, readable, says `UNKNOWN`** — refused. Nothing writes it,
      so it can only come from a writer that had no verdict and wrote one
      anyway. See `StoredVerdictWithoutOrigin`.

    The middle case is tolerance about *shape*; the last is strictness about
    *value*. A format may outlive its readers; it may not hold a fact it has a
    shorter way of not holding.
    """

    if entry is None:
        return SafetyResult.not_evaluated()
    if not isinstance(entry, dict):
        # The count goes in the same line as the event: a line saying "this is
        # the 4th this hour" and a line saying "the 900th" describe different
        # incidents, and without the number they are the same line.
        logger.warning(
            "dre.state.safety_entry_unreadable type=%s this_hour=%s — вердикт потерян, "
            "читаем как UNKNOWN",
            type(entry).__name__,
            _tally_unreadable(),
        )
        return SafetyResult.not_evaluated()
    try:
        state = SafetyState(entry["state"])
    except (KeyError, ValueError):
        logger.warning(
            "dre.state.safety_entry_unreadable reason=state this_hour=%s", _tally_unreadable()
        )
        return SafetyResult.not_evaluated()

    if state not in KNOWN_SAFETY_STATES:
        raise StoredVerdictWithoutOrigin(
            f"a stored safety verdict says {entry['state']!r}, which is the absence of a "
            "verdict. Absence is written by omitting the key; an object saying so has a "
            "writer behind it, and «was never evaluated» is now indistinguishable from "
            "«something swallowed an error and returned the fail-closed value»."
        )
    stored_handoff = entry.get("handoff")
    try:
        return _build_verdict(entry, state=state, stored_handoff=stored_handoff)
    except ValueError as exc:
        # A verdict that exists but does not satisfy its own type — today that
        # means a blob written before §127 added `handoff`. The verdict is
        # real; what it promised the person is not recorded.
        #
        # Not `Handoff.NONE`: that reads as "no handoff was required", a
        # decision the engine never made, and §127 exists precisely to keep
        # that apart from a crisis handoff. Not a raised error either: this has
        # an innocent origin (a payload older than the field) and killing the
        # turn buys nothing a block does not.
        #
        # So: unreadable shape, same as any other — `UNKNOWN`, counted, and
        # therefore visible. The blast radius is bounded by the state TTL: two
        # hours after §127 ships, no such blob exists.
        logger.warning(
            "dre.state.safety_entry_unreadable reason=incomplete this_hour=%s detail=%s",
            _tally_unreadable(),
            exc,
        )
        return SafetyResult.not_evaluated()


def _build_verdict(
    entry: dict[str, Any], *, state: SafetyState, stored_handoff: Any
) -> SafetyResult:
    return SafetyResult(
        state=state,
        evaluated_at_revision=entry.get("evaluated_at_revision"),
        # A blob written before §127 landed has no `"handoff"` key, and it
        # cannot be invented here: `Handoff.NONE` would read as "no handoff was
        # required", which is a decision the engine never made. Passing `None`
        # lets `__post_init__` refuse a known verdict without one — loudly, at
        # the point of reading, rather than by quietly promising less than the
        # verdict promised. Same rule as `state`: the decoder is a second door
        # into the type and may not answer for the engine.
        handoff=Handoff(stored_handoff) if stored_handoff else None,
        rule_id=entry.get("rule_id"),
        policy_version=entry.get("policy_version"),
        required_slots=tuple(entry.get("required_slots") or ()),
        forbidden_capabilities=tuple(entry.get("forbidden_capabilities") or ()),
        not_applicable_for=entry.get("not_applicable_for") or None,
        activated_at=(
            datetime.fromisoformat(entry["activated_at"]) if entry.get("activated_at") else None
        ),
    )


def _encode(state: ConversationState) -> str:
    payload: dict[str, Any] = {
        # v2 — DRF-1629: the "safety" key below. The version says which shape
        # was read; it does not decide anything. A v1 blob is read correctly
        # because `_decode_safety` handles the missing key, not because the
        # number is inspected.
        "v": 2,
        "conversation_id": state.conversation_id,
        "revision": state.revision,
        "epoch_started_at_revision": state.epoch_started_at_revision,
        "last_activity_at": (
            state.last_activity_at.isoformat() if state.last_activity_at else None
        ),
        "slots": {
            name: {
                "state": slot.state.value,
                "value": slot.value,
                "evidence_id": slot.evidence_id,
                "at_revision": slot.at_revision,
            }
            for name, slot in sorted(state.slots.items())
        },
    }
    # Omitted, not written as null: a verdict that does not exist is said by
    # the key not being there. One representation per fact — see
    # `StoredVerdictWithoutOrigin` for what the second one would cost.
    encoded_safety = _encode_safety(state.safety)
    if encoded_safety is not None:
        payload["safety"] = encoded_safety
    return json.dumps(payload, ensure_ascii=False)


def _decode(raw: str) -> ConversationState:
    payload = json.loads(raw)
    last_activity = payload.get("last_activity_at")
    return ConversationState(
        conversation_id=str(payload["conversation_id"]),
        revision=int(payload["revision"]),
        slots={
            name: SlotValue(
                state=SlotState(entry["state"]),
                value=entry.get("value"),
                evidence_id=entry.get("evidence_id"),
                at_revision=entry.get("at_revision"),
            )
            for name, entry in payload.get("slots", {}).items()
        },
        epoch_started_at_revision=int(payload.get("epoch_started_at_revision", 1)),
        last_activity_at=datetime.fromisoformat(last_activity) if last_activity else None,
        safety=_decode_safety(payload.get("safety")),
    )


def save(state: ConversationState, *, now: datetime | None = None) -> None:
    """Write the state and refresh its 2h inactivity TTL.

    The TTL is refreshed by writing, which is what makes it an *inactivity* TTL
    rather than a session cap: a conversation that keeps producing turns keeps
    its state, a conversation that goes quiet loses it two hours later — and
    reports that loss (`load`).
    """

    stamped = ConversationState(
        conversation_id=state.conversation_id,
        revision=state.revision,
        slots=dict(state.slots),
        epoch_started_at_revision=state.epoch_started_at_revision,
        last_activity_at=now or datetime.now(UTC),
        safety=state.safety,
    )
    _redis_client().set(
        _state_key(state.conversation_id),
        _encode(stamped),
        ex=STATE_TTL_SECONDS,
    )


def load(conversation_id: str, *, now: datetime | None = None) -> LoadResult:
    """Read the state, and say which of the three things happened.

    `EXPIRED` carries the last revision, so the caller can open the next epoch
    above it instead of restarting the numbering — see the module docstring on
    why restarting would make a pre-gap callback look newer than the state that
    replaced it.

    `EXPIRED` is reported on every read until a fresh state is written. That is
    intentional: a one-shot flag would turn the event back into a disappearance
    the moment it was lost.
    """

    client = _redis_client()
    raw = cast("str | None", client.get(_state_key(conversation_id)))
    if raw is not None:
        return LoadResult(lifecycle=StateLifecycle.LIVE, state=_decode(raw))

    last_revision = peek_revision(conversation_id)
    if last_revision is None:
        return LoadResult(lifecycle=StateLifecycle.ABSENT)

    expiry = StateExpiry(
        conversation_id=conversation_id,
        last_revision=last_revision,
        detected_at=now or datetime.now(UTC),
    )
    logger.info(
        "decision_readiness.state.expired conversation=%s last_revision=%s ttl_seconds=%s",
        conversation_id,
        last_revision,
        STATE_TTL_SECONDS,
    )
    return LoadResult(lifecycle=StateLifecycle.EXPIRED, expiry=expiry)


def open_epoch(conversation_id: str, *, after: LoadResult) -> ConversationState:
    """Start a fresh state, continuing the revision numbering rather than resetting it.

    Called with an `EXPIRED` or `ABSENT` read. The first revision comes from
    `next_revision()`, which for an expired conversation resumes above the last
    number handed out (the counter outlives the blob) and for an absent one
    starts at 1.
    """

    if after.lifecycle is StateLifecycle.LIVE:
        raise ValueError("open_epoch called on a live state — the existing state must be used")

    revision = next_revision(conversation_id)
    return ConversationState(
        conversation_id=conversation_id,
        revision=revision,
        slots={},
        epoch_started_at_revision=revision,
        # A new epoch inherits no verdict, and says so rather than omitting it.
        # The previous epoch's safety answer was computed for a revision this
        # one is already past; carrying it across the gap would be the stale
        # input §15.2 blocks on, wearing a fresh state's number.
        safety=SafetyResult.not_evaluated(),
    )
