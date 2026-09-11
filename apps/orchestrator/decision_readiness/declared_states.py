"""Which declared values nothing in this system can currently produce — §126.

The owner's ruling of 10.09.2026, on `CAUTION`, and it generalises:

> Оставить в контракте, но недостижимым, пока нет реальных controlled rules,
> которые его производят. **Не создавать фиктивные правила только ради покрытия
> enum/state.** Документировать явно: текущий policy set производит 0 `CAUTION`
> rules.

And the reason, which is the part worth keeping in mind here:

> Недостижимое состояние, о котором сказано вслух, — это названный предел.
> Недостижимое состояние, о котором умолчали, **читается как покрытие**:
> следующий увидит четыре значения в перечислении и решит, что система
> различает четыре случая.

This package had exactly that gap. `test_the_engine_can_reach_every_one_of_the_five_states`
proves the five readiness states are all reachable — and said nothing about the
seven other members that are not. A reader seeing four safety states and three
delegation levels would conclude the system tells seven cases apart. It does not
yet, and the difference between "does not yet" and "does" belongs in the code.

### The rule this module enforces

**Every member of every contract enum is either produced somewhere, or listed
here with a reason.** There is no silent third category, and a new member forces
a classification rather than sliding in as assumed coverage.

The list is data, not a lint: nothing here is inferred by scanning for
constructors, because a static scan cannot tell a producer from a consumer.
`SafetyState.CAUTION` is *read* in `engine.f` and *written* by nothing — a
grep sees both as a mention. So the classification is declared by a person and
guarded for completeness, which is the honest division of labour.

### What this is not

It is not a list of things to go and build. Several entries are unreachable
because another lane has not shipped their producer yet, and a couple are
unreachable because the owner has not given the policy. Filling them in from
here is exactly the "фиктивное правило" the ruling forbids: it would give a
state a producer and so **lie in data rather than in documentation**.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from apps.orchestrator.decision_readiness.engine import Delegation
from apps.orchestrator.decision_readiness.evidence import EvidenceOrigin
from apps.orchestrator.decision_readiness.questions import AskReason, ImpactClaim, QuestionKind
from apps.orchestrator.decision_readiness.required_context import SlotOwner
from apps.orchestrator.decision_readiness.safety_input import Handoff, SafetyState


@dataclass(frozen=True, slots=True)
class Undeclared:
    """One declared value that nothing currently produces.

    `blocked_on` names what would have to exist for a producer to appear, so the
    entry can be retired by evidence rather than by opinion. `count_today` is
    the ruling's own phrasing — "the current policy set produces 0 of these" —
    kept as a number because a number can be checked and a sentence cannot.
    """

    member: Enum
    blocked_on: str
    count_today: int = 0

    @property
    def label(self) -> str:
        return f"{type(self.member).__name__}.{self.member.name}"


#: Declared, and nothing in the running system can produce them today.
#:
#: Retiring an entry means a producer now exists — and the guard below will say
#: so by going red, which is the point: the register must not outlive the gap it
#: records.
DECLARED_WITHOUT_PRODUCER: tuple[Undeclared, ...] = (
    Undeclared(
        member=SafetyState.CAUTION,
        blocked_on=(
            "the safety signal matrix (§16.2, canon §7 'Still open'). The engine "
            "consumes CAUTION and emits SAFETY_CAUTION_CONSTRAINED; no rule anywhere "
            "returns it. §126 names this one explicitly."
        ),
    ),
    Undeclared(
        member=QuestionKind.SAFETY_CLARIFICATION,
        blocked_on=(
            "the same matrix. The kind has a priority in §13.6 and an exemption in "
            "§13.3 п.3, and no catalog entry can legitimately carry it until safety "
            "policy exists to demand one."
        ),
    ),
    Undeclared(
        member=SlotOwner.SAFETY,
        blocked_on=(
            "the same matrix. §11.4 states it directly: while the table is empty the "
            "spec contains no SAFETY-owned slots, so delegation's ceiling over them "
            "is a rule with nothing yet under it."
        ),
    ),
    Undeclared(
        member=AskReason.REASK_SAFETY_REEVALUATION,
        blocked_on=(
            "a producer of SafetyResult (slice E8, lane A). The re-ask reason needs a "
            "transition into CLARIFY, and nothing computes safety states yet."
        ),
    ),
    Undeclared(
        member=ImpactClaim.RANKING,
        blocked_on=(
            "`ranking_comparison_depth` (k). §16.1 calls it derived from the number of "
            "cards a surface shows; until an adapter supplies one, `ask_allowed` "
            "declines to claim ranking impact — deliberately, since guessing k would "
            "only ever permit more questions."
        ),
    ),
    Undeclared(
        member=Delegation.MEDIUM,
        blocked_on=(
            "a producer. §10.1 raises delegation only by an explicit DELEGATE option or "
            "confirmed delegating text, and both produce HIGH; `g` treats everything "
            "below HIGH alike. MEDIUM exists in the contract and is asserted by nothing."
        ),
    ),
    Undeclared(
        member=Handoff.NONE,
        blocked_on=(
            "a producer of SafetyResult (slice E8, lane A). The whole triple is "
            "unreachable for the same reason: nothing computes a safety verdict yet, "
            "so nothing promises a next step either. §127 froze the three values; this "
            "records that none of them has an author today."
        ),
    ),
    Undeclared(
        member=Handoff.RECOMMENDED,
        blocked_on=(
            "a producer of SafetyResult (slice E8, lane A). See Handoff.NONE — the "
            "three are listed separately because the register is a partition over "
            "members, not over enums, and a member that gains a producer must be "
            "retired on its own."
        ),
    ),
    Undeclared(
        member=Handoff.REQUIRED,
        blocked_on=(
            "a producer of SafetyResult (slice E8, lane A). This is the one §127 cares "
            "about most — a crisis handoff — and it is the one with no author, which is "
            "worth saying out loud rather than leaving to be inferred."
        ),
    ),
    Undeclared(
        member=EvidenceOrigin.PROMOTED_MEMORY,
        blocked_on=(
            "a live caller of `from_promoted_memory`. The intake function exists and is "
            "tested; nothing in the running system promotes memory into evidence yet, "
            "and OD-DR-3 keeps it out of safety slots when it does."
        ),
    ),
)


#: The enums this register claims to cover. Named explicitly so the guard can
#: check the register against them rather than against itself.
COVERED_ENUMS: tuple[type[Enum], ...] = (
    SafetyState,
    Handoff,
    QuestionKind,
    SlotOwner,
    AskReason,
    ImpactClaim,
    Delegation,
    EvidenceOrigin,
)


def unreachable_labels() -> frozenset[str]:
    return frozenset(item.label for item in DECLARED_WITHOUT_PRODUCER)


def all_member_labels() -> frozenset[str]:
    return frozenset(f"{enum.__name__}.{member.name}" for enum in COVERED_ENUMS for member in enum)


def produced_labels() -> frozenset[str]:
    """Everything the register does **not** claim to be unreachable.

    Deliberately defined by subtraction. The alternative — a second hand-written
    list of "these are produced" — would be two lists that can disagree, and the
    disagreement would be invisible.
    """

    return all_member_labels() - unreachable_labels()
