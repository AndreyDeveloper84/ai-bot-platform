"""Local registers, turned into engine inputs — slice E11.

Three small registers already exist and each one already knows something the
engine needs. None of them is deleted here and none is called from the engine:
this module turns what they hold into inputs, which is the only way a mechanism
can join the engine without becoming a second authority beside it.

| register | key | TTL | write contract | what it knows |
|---|---|---|---|---|
| `apps.orchestrator.refusal_memo` | `no_match` | 1800s | best-effort | the catalog answered "nobody" for a (spec, city) |
| `apps.skills.health_screening.memo` | `health_screening_asked` | 1800s | best-effort | screening asked its questions recently |
| `apps.orchestrator.memory_ask` pending | Redis | 24h | best-effort | one profile question is awaiting an answer |

and the engine's own `question_ledger` (`decision_readiness`, fail-closed).

### The rule transfers; the behaviour must not be assumed to

The screening memo is the case that makes this a rule rather than a caution.
Read at source (`apps/skills/health_screening/skill.py:100-105`):

    signal = classify(context.message_text)
    if signal == PainSignal.NONE:
        return False
    if signal == PainSignal.RED_FLAG:
        return True
    return not screening_asked_recently(context.conversation)

One condition — "was this asked recently" — with **two different actions**
depending on severity: a red flag passes unconditionally, and the memo gates
only a repeated soft signal. `handle()` says the same in the other direction: a
red flag is never written to the memo, because there is nothing there to
suppress.

`question_ledger` suppresses uniformly. Migrating this memo into it as "a
register of what was asked" would carry the condition across and drop the
asymmetry — and the first thing that would break is a red flag suppressed as a
repeat. That is a safety-grade regression produced by a faithful-looking port.

So suppressions here carry the severities they are **allowed** to suppress, and
the answer to "may this be suppressed" is a set membership rather than a
boolean. A severity outside the set cannot be suppressed by any register,
however the caller is later edited.

### A part of a measure is not the measure

§11.5 says `conflicts` aggregates three things: a non-empty refusal memo, a
missing part of a composite request, and a regrounding that fired. This module
can produce the first. It must not therefore hand back a number called
`conflicts`, because a caller reading a partial as the whole would see zero
conflicts on a turn where one of the other two was standing — and §11.5's job
is to stop the bot recommending over an unresolved refusal (DRF-1474).

`ConflictInputs` therefore requires each of the three to be either supplied or
explicitly declared absent, and yields `None` until they all are. `None` blocks
(`engine._unavailable_reason`), which is the honest state today.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConflictSource(str, Enum):
    """The three contributions §11.5 names. There is no fourth, and no partial."""

    REFUSAL_MEMO = "refusal_memo"
    INCOMPLETE_COMPOSITE_REQUEST = "incomplete_composite_request"
    REGROUNDING = "regrounding"


@dataclass(frozen=True, slots=True)
class ConflictContribution:
    """One source's count, named. `count` may be zero — a measured zero."""

    source: ConflictSource
    count: int

    def __post_init__(self) -> None:
        if self.count < 0:
            raise ValueError("a conflict count cannot be negative")


@dataclass(frozen=True, slots=True)
class ConflictInputs:
    """The three contributions, each supplied or explicitly declared absent.

    `unavailable` is not decoration. A source that nobody computed is different
    from a source that computed zero, and only the second may be added up.
    """

    contributions: tuple[ConflictContribution, ...] = ()
    unavailable: frozenset[ConflictSource] = field(default_factory=frozenset)

    def total(self) -> int | None:
        """The `conflicts` measure, or `None` while any source is unaccounted for."""

        seen = {item.source for item in self.contributions}
        if seen & self.unavailable:
            raise ValueError(
                f"{sorted(s.value for s in seen & self.unavailable)} is both supplied and "
                "declared unavailable — one of the two claims is wrong"
            )
        if seen | self.unavailable != set(ConflictSource):
            return None
        if self.unavailable:
            return None
        return sum(item.count for item in self.contributions)

    def missing(self) -> tuple[str, ...]:
        """Which sources are neither supplied nor declared absent — for the audit."""

        seen = {item.source for item in self.contributions}
        return tuple(sorted(s.value for s in set(ConflictSource) - seen - self.unavailable))


def refusal_conflicts(conversation: Any) -> ConflictContribution:
    """Read `refusal_memo` and report its contribution. Never raises.

    Best-effort on purpose, and it stays best-effort: this register's own
    contract is "never break a turn over a hint" (`refusal_memo`'s docstring),
    and a failure here becomes an *unavailable* source rather than a zero — see
    `ConflictInputs`. What must not happen is a read failure arriving as "no
    conflicts", which would be the fail-open reading of an absent fact.
    """

    from apps.orchestrator.refusal_memo import recall_refusals

    return ConflictContribution(
        source=ConflictSource.REFUSAL_MEMO, count=len(recall_refusals(conversation))
    )


# --- severity-scoped suppression --------------------------------------------


class Severity(str, Enum):
    """How serious the thing being asked about is.

    Named generically rather than after `PainSignal` because the asymmetry is
    not specific to pain: any register that suppresses repeats needs to say what
    it may not suppress.
    """

    ROUTINE = "routine"
    ELEVATED = "elevated"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class RegisterSuppression:
    """A local register's claim that something was already asked.

    `may_suppress` is the set of severities this register is entitled to silence.
    It is a set and not a flag because the live rule it is carrying across is a
    set: the screening memo may silence a repeated soft signal and may not
    silence a red flag (`health_screening/skill.py:100-105`).
    """

    register: str
    asked_recently: bool
    may_suppress: frozenset[Severity]
    ttl_seconds: int

    def suppresses(self, severity: Severity) -> bool:
        """Whether this register silences a question at that severity.

        A severity outside `may_suppress` is never silenced, whatever
        `asked_recently` says. That ordering — severity first, register second —
        is the same order `matches()` uses, and the reason is the same: the cost
        of a repeated question is annoyance, and the cost of silence on an
        alarming sign is somebody's health.
        """

        if severity not in self.may_suppress:
            return False
        return self.asked_recently


#: The screening memo, as it actually behaves. `CRITICAL` is deliberately absent
#: from `may_suppress`: `HealthScreeningSkill.matches` returns `True` for a red
#: flag before consulting the memo at all, and `handle()` never writes a red
#: flag to it. Adding `CRITICAL` here would not be a config change — it would
#: reverse a decision made in the skill.
SCREENING_MAY_SUPPRESS: frozenset[Severity] = frozenset({Severity.ROUTINE, Severity.ELEVATED})


def screening_suppression(conversation: Any) -> RegisterSuppression:
    """`health_screening.memo` as an engine input, severity scope included."""

    from apps.skills.health_screening.memo import (
        STATE_TTL_SECONDS,
        screening_asked_recently,
    )

    return RegisterSuppression(
        register="health_screening_asked",
        asked_recently=bool(screening_asked_recently(conversation)),
        may_suppress=SCREENING_MAY_SUPPRESS,
        ttl_seconds=STATE_TTL_SECONDS,
    )


# --- migration policy: the TTL economies, written down ----------------------


@dataclass(frozen=True, slots=True)
class RegisterEconomy:
    """One register's forgetting rule and what its expiry costs.

    Recorded rather than harmonised. The three registers disagree on how long a
    fact stays true, and the disagreement is mostly correct: thirty minutes is
    "a conversation" for a catalog refusal, twenty-four hours is the cooldown a
    profile question is pinned to, and the engine's own register lives as long
    as the conversation state (2h, OD-DR-4).

    The one entry the inventory flagged as wrong against the canon is marked as
    such rather than quietly fixed here: forgetting a *resolved* fact after
    thirty minutes contradicts canon §16, and changing it is its own decision
    with its own owner.
    """

    register: str
    ttl_seconds: int
    contract: str
    cost_of_loss: str
    contradicts_canon: bool = False


REGISTER_ECONOMIES: tuple[RegisterEconomy, ...] = (
    RegisterEconomy(
        register="no_match (refusal_memo)",
        ttl_seconds=1800,
        contract="best-effort",
        cost_of_loss="one repeated refusal",
    ),
    RegisterEconomy(
        register="health_screening_asked",
        ttl_seconds=1800,
        contract="best-effort",
        cost_of_loss="one repeated soft-signal screen; a red flag is never suppressed",
    ),
    RegisterEconomy(
        register="memory_ask pending (Redis)",
        ttl_seconds=24 * 3600,
        contract="best-effort",
        cost_of_loss="one profile question re-asked after the external 24h cooldown",
    ),
    RegisterEconomy(
        register="decision_readiness (question_ledger)",
        ttl_seconds=2 * 3600,
        contract="fail-closed",
        cost_of_loss="a DRF-1542 question loop",
    ),
    RegisterEconomy(
        register="resolved facts forgotten after 30 minutes",
        ttl_seconds=1800,
        contract="best-effort",
        cost_of_loss="a settled question re-opens inside one session",
        contradicts_canon=True,
    ),
)
