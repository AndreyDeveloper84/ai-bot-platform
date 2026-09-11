"""Controlled policy: the numbers, and the ones that do not exist yet.

Spec §16.1 splits this table in two, and the split is the whole content of this
module.

**Derived, not decided.** `MAX_ASKS_PER_QUESTION_ID = 2` is not a taste: it
follows from the admission rule. A question that was answered and that, after
the answer, changes nothing is by definition no longer allowed (§13.3), so the
only lawful second ask is one justified by one of the five re-ask reasons
(§13.5). A third ask under the same reason would mean the reason did not work.
`MAX_CONSECUTIVE_ASKS_WITHOUT_NEW_EVIDENCE = 2` comes from the same place.
`MAX_LEDGER_ENTRIES = 20` is a technical bound modelled on `refusal_memo`'s 5,
raised because a register of questions is longer than a register of refusals.

**Uncalibrated, and therefore absent.** `tau_separation` and `N_broad` have
**no value**. Not a default, not a sensible starting point — no value.

OD-DR-1 (CLOSED): thresholds are calibrated in shadow mode on real data first;
hard required-context rules need no calibration. DRF-1519 asks the owner for
these two before anything is built on them. A number picked here would be
indistinguishable, a week later, from one the owner chose — and it would survive
the calibration that was supposed to replace it.

So they are `UNCALIBRATED`, a sentinel that is not a number. Every arithmetic
comparison against it raises `TypeError` rather than quietly succeeding, which
means a code path that forgot to check cannot silently take a branch. The engine
checks first and blocks (§15.2, "policy_version входа ≠ загруженной" — the same
class of "the policy this decision needs is not loaded").

`k`, the ranking comparison depth, is a third case: §16.1 calls it derived from
the number of cards the surface shows. It is a surface fact, so this module does
not hold a value for it either — the adapter passes it, and `ask_allowed`
declines to claim ranking impact without it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


class Uncalibrated:
    """A threshold that has no value yet. Deliberately not a number.

    `0.0 < UNCALIBRATED` raises `TypeError`. That is the point: a forgotten
    check fails loudly instead of taking whichever branch a placeholder implied.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "UNCALIBRATED"

    def __bool__(self) -> bool:
        raise TypeError(
            "UNCALIBRATED has no truth value. A threshold that was never calibrated "
            "cannot be compared or defaulted — see OD-DR-1 and DRF-1519."
        )


UNCALIBRATED: Final = Uncalibrated()

# Derived from the admission rule (§13.5), not chosen.
MAX_ASKS_PER_QUESTION_ID: Final[int] = 2
MAX_CONSECUTIVE_ASKS_WITHOUT_NEW_EVIDENCE: Final[int] = 2

# Technical bound, by the `refusal_memo` precedent (§13.4).
MAX_LEDGER_ENTRIES: Final[int] = 20


@dataclass(frozen=True, slots=True)
class ControlledPolicy:
    """One `policy_version`'s worth of numbers.

    `policy_version` is monotone and travels with every output and every ledger
    record (§18.1). Outputs under different policy versions are not comparable
    in analytics, and — separately — a change of policy version does **not**
    grant the right to re-ask, because it is not part of `question_id`.
    """

    policy_version: int
    tau_separation: float | Uncalibrated = UNCALIBRATED
    n_broad: int | Uncalibrated = UNCALIBRATED
    ranking_comparison_depth: int | None = None
    max_asks_per_question_id: int = MAX_ASKS_PER_QUESTION_ID
    max_consecutive_asks_without_new_evidence: int = MAX_CONSECUTIVE_ASKS_WITHOUT_NEW_EVIDENCE
    max_ledger_entries: int = MAX_LEDGER_ENTRIES

    @property
    def separation_threshold_is_calibrated(self) -> bool:
        return not isinstance(self.tau_separation, Uncalibrated)


UNCALIBRATED_POLICY: Final = ControlledPolicy(policy_version=0)
