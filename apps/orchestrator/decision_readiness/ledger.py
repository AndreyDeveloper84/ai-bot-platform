"""What has already been asked, and whether it may be asked again — §13.4–13.5.

### The register that DRF-1542 did not have

On the pilot, `health_screening` produced the same screen five times in a row.
The middle three turns each contained an answer. Nothing recorded that the
question had been asked, so nothing could notice that it had been answered.

`apps/orchestrator/refusal_memo.py` is the shape being reused: a small
per-conversation register in `Conversation.skill_state` under a disjoint key,
bounded, where a repeat of the same key **updates** rather than appends. One
thing is deliberately different, and it is the important one.

### The write contract is fail-closed, not best-effort

`refusal_memo`'s contract is "never break a turn over a hint" — losing a write
there costs one repeated refusal. Losing a write here costs a DRF-1542 loop, so
this register is a condition of correctness rather than a hint. Failure to read
or write raises `LedgerUnavailable`, which the adapter turns into
`availability.ledger_readable = False` → `BLOCKED(READINESS_INPUT_UNAVAILABLE)`
→ **the question is not asked**.

Raising rather than returning a status is itself the contract: a returned
boolean can be ignored, and an ignored boolean is precisely the best-effort
behaviour being replaced.

### The record key is (qid, asked_at_revision)

A webhook retry or a duplicated callback re-delivers the same event. Keyed this
way, the second delivery finds the record already there, does not append, and
does not increment `ask_count` — so transport retries cannot spend the ask
budget (§17.3).

### This module holds no clock and no database

Expiry is decided by the adapter (P2) and arrives as a flag; persistence lives
in `ledger_store.py`. What is here is data and policy, so that the engine can
import it without importing Django or a clock (§17.1).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from apps.orchestrator.decision_readiness.policy import ControlledPolicy
from apps.orchestrator.decision_readiness.questions import AskReason, QuestionKind

STATE_KEY = "decision_readiness"
LEDGER_VERSION = 1


class LedgerUnavailable(Exception):
    """The register could not be read or written.

    Not an error to log and continue past. §13.4 makes it
    `availability.ledger_readable = False`, and §11 step 2 turns that into
    `BLOCKED(READINESS_INPUT_UNAVAILABLE)` — no recommendation and no question.
    """


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One question, as asked. Fields are §13.4's, unchanged."""

    qid: str
    kind: QuestionKind
    slots: tuple[str, ...]
    semantics_version: int
    policy_version: int
    asked_at: str
    asked_at_revision: int
    ask_count: int = 1
    resolved: bool = False
    resolved_by_evidence_id: str | None = None
    resolved_at_revision: int | None = None
    last_reask_reason: str | None = None

    @property
    def key(self) -> tuple[str, int]:
        return (self.qid, self.asked_at_revision)


@dataclass(frozen=True, slots=True)
class QuestionLedger:
    """The register for one conversation.

    `consecutive_asks_without_new_evidence` is the second ceiling of §13.5. It
    counts turns that asked and got nothing back on the target slots — the
    arithmetic that makes "five identical screens" impossible independently of
    every other barrier.
    """

    entries: tuple[LedgerEntry, ...] = ()
    consecutive_asks_without_new_evidence: int = 0

    def latest_for(self, qid: str) -> LedgerEntry | None:
        """The most recent record for this question id, by revision."""

        matching = [entry for entry in self.entries if entry.qid == qid]
        if not matching:
            return None
        return max(matching, key=lambda entry: entry.asked_at_revision)

    def find(self, qid: str, asked_at_revision: int) -> LedgerEntry | None:
        for entry in self.entries:
            if entry.key == (qid, asked_at_revision):
                return entry
        return None

    def total_asks(self, qid: str) -> int:
        return sum(entry.ask_count for entry in self.entries if entry.qid == qid)

    # --- mutations, all returning a new ledger ---------------------------

    def record_ask(
        self,
        entry: LedgerEntry,
        *,
        policy: ControlledPolicy,
    ) -> QuestionLedger:
        """Append, or return unchanged if this exact delivery is already recorded.

        The idempotency is keyed `(qid, asked_at_revision)`: a retried webhook
        or a duplicated callback must not spend the ask budget (§17.3).
        """

        if self.find(entry.qid, entry.asked_at_revision) is not None:
            return self

        kept = (*self.entries, entry)
        if len(kept) > policy.max_ledger_entries:
            # Drop the oldest by revision. Bounded like `refusal_memo`, longer
            # because a register of questions outlives a register of refusals.
            kept = tuple(
                sorted(kept, key=lambda e: e.asked_at_revision)[-policy.max_ledger_entries :]
            )
        return replace(self, entries=kept)

    def mark_resolved(
        self,
        qid: str,
        *,
        evidence_id: str,
        at_revision: int,
    ) -> QuestionLedger:
        """Record that an answer arrived, and which evidence carried it."""

        updated = tuple(
            replace(
                entry,
                resolved=True,
                resolved_by_evidence_id=evidence_id,
                resolved_at_revision=at_revision,
            )
            if entry.qid == qid and not entry.resolved
            else entry
            for entry in self.entries
        )
        return replace(self, entries=updated, consecutive_asks_without_new_evidence=0)

    def note_ask_without_new_evidence(self) -> QuestionLedger:
        return replace(
            self,
            consecutive_asks_without_new_evidence=self.consecutive_asks_without_new_evidence + 1,
        )

    # --- serialisation ---------------------------------------------------

    def to_state(self) -> dict[str, Any]:
        return {
            "v": LEDGER_VERSION,
            "dry_asks": self.consecutive_asks_without_new_evidence,
            "asked": [
                {
                    "qid": entry.qid,
                    "kind": entry.kind.value,
                    "slots": list(entry.slots),
                    "semantics_version": entry.semantics_version,
                    "policy_version": entry.policy_version,
                    "asked_at": entry.asked_at,
                    "asked_at_revision": entry.asked_at_revision,
                    "ask_count": entry.ask_count,
                    "resolved": entry.resolved,
                    "resolved_by_evidence_id": entry.resolved_by_evidence_id,
                    "resolved_at_revision": entry.resolved_at_revision,
                    "last_reask_reason": entry.last_reask_reason,
                }
                for entry in self.entries
            ],
        }

    @classmethod
    def from_state(cls, payload: dict[str, Any] | None) -> QuestionLedger:
        """Rebuild from `skill_state`. A malformed payload is unavailable, not empty.

        The distinction matters more here than almost anywhere else: an empty
        register says "nothing has been asked", which is a licence to ask. A
        register that could not be parsed says nothing of the sort, and reading
        it as empty is how a loop restarts after a bad deploy.
        """

        if payload is None:
            return cls()
        try:
            if int(payload.get("v", 0)) != LEDGER_VERSION:
                raise LedgerUnavailable(
                    f"ledger version {payload.get('v')!r} is not {LEDGER_VERSION}"
                )
            entries = tuple(
                LedgerEntry(
                    qid=str(raw["qid"]),
                    kind=QuestionKind(raw["kind"]),
                    slots=tuple(raw.get("slots", ())),
                    semantics_version=int(raw["semantics_version"]),
                    policy_version=int(raw["policy_version"]),
                    asked_at=str(raw["asked_at"]),
                    asked_at_revision=int(raw["asked_at_revision"]),
                    ask_count=int(raw.get("ask_count", 1)),
                    resolved=bool(raw.get("resolved", False)),
                    resolved_by_evidence_id=raw.get("resolved_by_evidence_id"),
                    resolved_at_revision=raw.get("resolved_at_revision"),
                    last_reask_reason=raw.get("last_reask_reason"),
                )
                for raw in payload.get("asked", [])
            )
        except LedgerUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - any malformed shape is unavailability
            raise LedgerUnavailable(f"ledger payload is unreadable: {exc}") from exc
        return cls(
            entries=entries,
            consecutive_asks_without_new_evidence=int(payload.get("dry_asks", 0)),
        )


# --- §13.5: may this question be asked again? -------------------------------


class AskOutcome(str, Enum):
    FIRST_ASK = "first_ask"
    REASK_ALLOWED = "reask_allowed"
    SUPPRESSED_ALREADY_ASKED = "suppressed_already_asked"
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True, slots=True)
class AskPermission:
    outcome: AskOutcome
    reason: AskReason | None = None
    detail: str | None = None

    @property
    def allowed(self) -> bool:
        return self.outcome in {AskOutcome.FIRST_ASK, AskOutcome.REASK_ALLOWED}


@dataclass(frozen=True, slots=True)
class ReaskConditions:
    """The five conditions of §13.5, as they arrive from outside the engine.

    Each is computed by somebody who is allowed a clock, a catalog or a safety
    verdict. None is computed by a model: `ModelSignal` appears in none of the
    five, which is why the model cannot re-open a question by restating it.
    """

    answer_retracted: bool = False
    answer_expired: bool = False
    semantics_changed: bool = False
    candidate_set_changed: bool = False
    safety_reevaluation: bool = False

    def first_matching(self) -> AskReason | None:
        """The §13.5 table order, so that two simultaneous conditions still give
        one deterministic answer (§17.1)."""

        if self.answer_retracted:
            return AskReason.REASK_ANSWER_RETRACTED
        if self.answer_expired:
            return AskReason.REASK_ANSWER_EXPIRED
        if self.semantics_changed:
            return AskReason.REASK_SEMANTICS_CHANGED
        if self.candidate_set_changed:
            return AskReason.REASK_CANDIDATE_SET_CHANGED
        if self.safety_reevaluation:
            return AskReason.REASK_SAFETY_REEVALUATION
        return None


ASK_SUPPRESSED_ALREADY_ASKED = "ASK_SUPPRESSED_ALREADY_ASKED"
BLOCK_ASK_BUDGET_EXHAUSTED = "BLOCK_ASK_BUDGET_EXHAUSTED"


def ask_permission(
    qid: str,
    *,
    ledger: QuestionLedger,
    policy: ControlledPolicy,
    conditions: ReaskConditions | None = None,
) -> AskPermission:
    """§13.5. A repeat needs one of five named reasons, and the budget must hold.

    The consecutive-ask ceiling is checked **before** anything else, including a
    first ask: once two turns in a row have asked and got nothing back, the next
    thing to do is not another question (§15.1 — refusing to ask is the half of
    fail-closed that is easy to forget).
    """

    conditions = conditions or ReaskConditions()

    if (
        ledger.consecutive_asks_without_new_evidence
        >= policy.max_consecutive_asks_without_new_evidence
    ):
        return AskPermission(
            outcome=AskOutcome.BUDGET_EXHAUSTED,
            detail=(
                f"{ledger.consecutive_asks_without_new_evidence} consecutive asks produced "
                "no new confirmed evidence"
            ),
        )

    previous = ledger.latest_for(qid)
    if previous is None:
        return AskPermission(outcome=AskOutcome.FIRST_ASK, reason=AskReason.FIRST_ASK)

    if ledger.total_asks(qid) >= policy.max_asks_per_question_id:
        return AskPermission(
            outcome=AskOutcome.BUDGET_EXHAUSTED,
            detail=f"question {qid} already asked {ledger.total_asks(qid)} times",
        )

    reason = conditions.first_matching()
    if reason is None:
        return AskPermission(
            outcome=AskOutcome.SUPPRESSED_ALREADY_ASKED,
            detail=ASK_SUPPRESSED_ALREADY_ASKED,
        )

    return AskPermission(outcome=AskOutcome.REASK_ALLOWED, reason=reason)


def new_evidence_on(
    slots: Sequence[str],
    *,
    evidence_slots: Sequence[str],
) -> bool:
    """Did this turn bring confirmed evidence on any of the target slots?

    Takes slot names rather than evidence objects so that the caller cannot pass
    model signals by accident — there is no type here that a signal fits.
    """

    return bool(set(slots) & set(evidence_slots))
