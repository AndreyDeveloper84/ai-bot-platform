"""The closed registry of reason codes — §16.

Every output carries at least one. Analytics keys off these, never off wording
(canon §13.5): a code survives a rephrasing and a translation, a sentence does
not.

The registry is **closed**, and closed means versioned: adding a code, or adding
a re-ask reason, is a `spec_version` change rather than a quiet edit (§18.1).
`ALL_CODES` exists so a test can say so — a registry nobody checks is a list.

Codes are emitted sorted (§17.1). That is not tidiness: determinism is the
property this whole engine is being built to have, and an unordered list would
make two identical decisions produce two different records.
"""

from __future__ import annotations

from typing import Final

# --- states ------------------------------------------------------------------
STATE_READY: Final = "STATE_READY"
STATE_NEEDS_DISCRIMINATION: Final = "STATE_NEEDS_DISCRIMINATION"
STATE_NEEDS_REQUIRED_CONTEXT: Final = "STATE_NEEDS_REQUIRED_CONTEXT"
STATE_INSUFFICIENT_EVIDENCE: Final = "STATE_INSUFFICIENT_EVIDENCE"
STATE_BLOCKED: Final = "STATE_BLOCKED"

# --- safety ------------------------------------------------------------------
SAFETY_STOP: Final = "SAFETY_STOP"
SAFETY_CLARIFY_REQUIRED: Final = "SAFETY_CLARIFY_REQUIRED"
SAFETY_CAUTION_CONSTRAINED: Final = "SAFETY_CAUTION_CONSTRAINED"
SAFETY_UNKNOWN: Final = "SAFETY_UNKNOWN"
#: Свод владельца 11.09 §3: «NORMAL — проверка применима, выполнена, значимых
#: сигналов нет; допустим ELIG_SAFETY_CLEARED». Выдаётся ТОЛЬКО за NORMAL.
#: NOT_APPLICABLE его не выдаёт по замыслу: «обычное выполнение без заявления о
#: пройденной проверке» — проверки не было, заявлять нечего. Сторож в
#: tests/test_safety_not_applicable.py, с положительным контролем на NORMAL.
ELIG_SAFETY_CLEARED: Final = "ELIG_SAFETY_CLEARED"

# --- blockers ----------------------------------------------------------------
BLOCK_NO_ADMISSIBLE_CANDIDATES: Final = "BLOCK_NO_ADMISSIBLE_CANDIDATES"
BLOCK_CATALOG_NOT_RECOMMENDABLE: Final = "BLOCK_CATALOG_NOT_RECOMMENDABLE"
BLOCK_READINESS_INPUT_UNAVAILABLE: Final = "BLOCK_READINESS_INPUT_UNAVAILABLE"
BLOCK_ASK_BUDGET_EXHAUSTED: Final = "BLOCK_ASK_BUDGET_EXHAUSTED"

# --- required context (parameterised by slot) --------------------------------
REQ_CONTEXT_UNSATISFIED: Final = "REQ_CONTEXT_UNSATISFIED"
REQ_CONTEXT_SATISFIED_FLEXIBLE: Final = "REQ_CONTEXT_SATISFIED_FLEXIBLE"
REQ_CONTEXT_NOT_REQUIRED: Final = "REQ_CONTEXT_NOT_REQUIRED"

# --- ask policy --------------------------------------------------------------
ASK_ALLOWED_ADMISSIBILITY: Final = "ASK_ALLOWED_ADMISSIBILITY"
ASK_ALLOWED_RANKING: Final = "ASK_ALLOWED_RANKING"
ASK_ALLOWED_EXECUTION_PARAM: Final = "ASK_ALLOWED_EXECUTION_PARAM"
ASK_SUPPRESSED_NO_IMPACT: Final = "ASK_SUPPRESSED_NO_IMPACT"
ASK_SUPPRESSED_ALREADY_ASKED: Final = "ASK_SUPPRESSED_ALREADY_ASKED"
ASK_SUPPRESSED_SLOT_FLEXIBLE: Final = "ASK_SUPPRESSED_SLOT_FLEXIBLE"
ASK_REASK_ANSWER_RETRACTED: Final = "ASK_REASK_ANSWER_RETRACTED"
ASK_REASK_ANSWER_EXPIRED: Final = "ASK_REASK_ANSWER_EXPIRED"
ASK_REASK_SEMANTICS_CHANGED: Final = "ASK_REASK_SEMANTICS_CHANGED"
ASK_REASK_CANDIDATE_SET_CHANGED: Final = "ASK_REASK_CANDIDATE_SET_CHANGED"
ASK_REASK_SAFETY_REEVALUATION: Final = "ASK_REASK_SAFETY_REEVALUATION"

# --- evidence ----------------------------------------------------------------
EVID_MODEL_SIGNAL_NOT_EVIDENCE: Final = "EVID_MODEL_SIGNAL_NOT_EVIDENCE"
EVID_ORIGIN_UNKNOWN_DISCARDED: Final = "EVID_ORIGIN_UNKNOWN_DISCARDED"
EVID_EXPIRED_DISCARDED: Final = "EVID_EXPIRED_DISCARDED"
EVID_RETRACTED: Final = "EVID_RETRACTED"

# --- delegation --------------------------------------------------------------
DELEG_APPLIED_HIGH: Final = "DELEG_APPLIED_HIGH"
DELEG_CEILING_REQUIRED_CONTEXT: Final = "DELEG_CEILING_REQUIRED_CONTEXT"
DELEG_CEILING_SAFETY: Final = "DELEG_CEILING_SAFETY"
DELEG_CEILING_INSUFFICIENT_EVIDENCE: Final = "DELEG_CEILING_INSUFFICIENT_EVIDENCE"

# --- measures ----------------------------------------------------------------
MEASURE_SEPARATION_BELOW_TAU: Final = "MEASURE_SEPARATION_BELOW_TAU"
MEASURE_CONFLICTS_PRESENT: Final = "MEASURE_CONFLICTS_PRESENT"

# --- slots -------------------------------------------------------------------
SLOT_NULL_TREATED_UNKNOWN: Final = "SLOT_NULL_TREATED_UNKNOWN"


ALL_CODES: Final[frozenset[str]] = frozenset(
    {
        STATE_READY,
        STATE_NEEDS_DISCRIMINATION,
        STATE_NEEDS_REQUIRED_CONTEXT,
        STATE_INSUFFICIENT_EVIDENCE,
        STATE_BLOCKED,
        SAFETY_STOP,
        SAFETY_CLARIFY_REQUIRED,
        SAFETY_CAUTION_CONSTRAINED,
        SAFETY_UNKNOWN,
        ELIG_SAFETY_CLEARED,
        BLOCK_NO_ADMISSIBLE_CANDIDATES,
        BLOCK_CATALOG_NOT_RECOMMENDABLE,
        BLOCK_READINESS_INPUT_UNAVAILABLE,
        BLOCK_ASK_BUDGET_EXHAUSTED,
        REQ_CONTEXT_UNSATISFIED,
        REQ_CONTEXT_SATISFIED_FLEXIBLE,
        REQ_CONTEXT_NOT_REQUIRED,
        ASK_ALLOWED_ADMISSIBILITY,
        ASK_ALLOWED_RANKING,
        ASK_ALLOWED_EXECUTION_PARAM,
        ASK_SUPPRESSED_NO_IMPACT,
        ASK_SUPPRESSED_ALREADY_ASKED,
        ASK_SUPPRESSED_SLOT_FLEXIBLE,
        ASK_REASK_ANSWER_RETRACTED,
        ASK_REASK_ANSWER_EXPIRED,
        ASK_REASK_SEMANTICS_CHANGED,
        ASK_REASK_CANDIDATE_SET_CHANGED,
        ASK_REASK_SAFETY_REEVALUATION,
        EVID_MODEL_SIGNAL_NOT_EVIDENCE,
        EVID_ORIGIN_UNKNOWN_DISCARDED,
        EVID_EXPIRED_DISCARDED,
        EVID_RETRACTED,
        DELEG_APPLIED_HIGH,
        DELEG_CEILING_REQUIRED_CONTEXT,
        DELEG_CEILING_SAFETY,
        DELEG_CEILING_INSUFFICIENT_EVIDENCE,
        MEASURE_SEPARATION_BELOW_TAU,
        MEASURE_CONFLICTS_PRESENT,
        SLOT_NULL_TREATED_UNKNOWN,
    }
)

# Three codes take a slot name: `REQ_CONTEXT_UNSATISFIED:city`. Kept as a
# separate set so the membership check can strip the parameter instead of
# rejecting every parameterised code as unknown.
PARAMETERISED_CODES: Final[frozenset[str]] = frozenset(
    {REQ_CONTEXT_UNSATISFIED, REQ_CONTEXT_SATISFIED_FLEXIBLE, REQ_CONTEXT_NOT_REQUIRED}
)


def slot_code(code: str, slot: str) -> str:
    """`REQ_CONTEXT_UNSATISFIED:city` — the §16 form for the three slot codes."""

    if code not in PARAMETERISED_CODES:
        raise ValueError(f"{code} does not take a slot parameter")
    return f"{code}:{slot}"


def is_known(code: str) -> bool:
    """Membership in the closed registry, parameter stripped."""

    base = code.split(":", 1)[0]
    return base in ALL_CODES


def canonical(codes: object) -> tuple[str, ...]:
    """Sorted and de-duplicated — the form every output carries (§17.1)."""

    if not isinstance(codes, (list, tuple, set, frozenset)):
        raise TypeError(f"expected a collection of reason codes, got {type(codes).__name__}")
    return tuple(sorted({str(code) for code in codes}))
