"""Shadow mode — the engine runs and cannot act. Slice E10, spec §18.2 step 6.

> 6. Движок за флагом, теневой режим: только аудит, без влияния на диалог.

### "Cannot influence" is a type, not a promise

The obvious implementation is to call `evaluate()` and then not use the result.
That works until somebody uses it, and nothing would notice: the call site would
look identical the day it started steering a conversation.

So the shadow run returns a **record** and there is no decision on its way out.
`observe()` cannot hand back a `StructuredDecision`, because it does not build
one and its return type has no room for one. A consumer that wanted to act on
shadow output would have to write the projection itself, which is a visible act
rather than a silent one.

The engine's own output is kept inside the record for the audit — the whole
point is to see what it *would* have decided — but it arrives as `DecisionEvidence`,
which is data about a decision, not a decision.

### The flag is a reading, not a boolean

`getattr(settings, "DRE_SHADOW_ENABLED", False)` returns `False` in two very
different situations: somebody set it to false, and nobody has heard of the key.
Reporting both as "shadow was off" makes the second invisible, and a month later
"why was there no audit trail" has no answer.

So `shadow_flag()` returns the value **and where it came from**. The audit
carries the source. This is the same rule this package applies everywhere else:
a default read at the call site is not the system's default.

### What this module deliberately does not do

**No thresholds are calibrated here, and none are proposed.** OD-DR-1 (CLOSED)
says calibration happens on real data, in shadow, before any threshold exists.
This module produces the data. Choosing `tau_separation` from it is a separate
act with an owner, and doing it from inside the producer would be the same error
as picking a "reasonable" default: indistinguishable afterwards from a decision
somebody made.

**No storage is designed.** §19 puts persistence with the Ayla backend, next to
the recommendation's own evidence. `EvidenceSink` is the shape a real sink must
satisfy; `LoggingSink` is the one that exists today, and it writes reason codes
rather than sentences, because analytics keys on codes (canon §13.5).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from django.conf import settings

from apps.orchestrator.decision_readiness import audit
from apps.orchestrator.decision_readiness.engine import ReadinessInput, evaluate

logger = logging.getLogger(__name__)

#: The setting that turns shadow evaluation on. Absent by default and off by
#: default, in that order: a package that turned itself on by being installed
#: would be influencing a pilot nobody agreed to run it on.
SHADOW_SETTING = "DRE_SHADOW_ENABLED"


class FlagSource(str, Enum):
    """Where a flag's value came from. Three different `False`s, not two.

    `SETTINGS` — somebody set the key to a value that reads as a boolean, and
    the value is theirs.

    `READ_DEFAULT` — the key does not exist. The value below is this module's,
    not the system's.

    `MALFORMED` — the key exists and its value is not a boolean anybody meant.
    This one is easy to miss and is the dangerous one: `bool("false")` is
    `True`, so an operator who wrote `DRE_SHADOW_ENABLED="false"` in an
    environment file would have turned shadow mode **on**. It is reported
    separately and resolved to `False`, because a flag nobody can read must not
    be the thing that switches something on.
    """

    SETTINGS = "settings"
    READ_DEFAULT = "read_default"
    MALFORMED = "malformed"


@dataclass(frozen=True, slots=True)
class FlagReading:
    """A flag value that knows where it came from.

    Returning a bare `bool` here would collapse "switched off" and "never
    configured" into one answer, and only the first of those is a decision.
    """

    value: bool
    source: FlagSource

    @property
    def configured(self) -> bool:
        return self.source is FlagSource.SETTINGS


#: Strings an operator plausibly writes in an environment file, and what they
#: mean. Anything outside this set is `MALFORMED` rather than coerced: guessing
#: at "1", "yes", "on" is cheap, and guessing wrong turns something on.
_TRUE_WORDS = frozenset({"true", "1", "yes", "on"})
_FALSE_WORDS = frozenset({"false", "0", "no", "off", ""})


def shadow_flag() -> FlagReading:
    """Read the shadow flag, and say where the answer came from.

    Never raises: a broken flag must not break a turn. It resolves to `False`
    with the source named, so the audit can tell "off", "unset" and "unreadable"
    apart — and only the first of the three is somebody's decision.
    """

    if not hasattr(settings, SHADOW_SETTING):
        return FlagReading(value=False, source=FlagSource.READ_DEFAULT)

    raw = getattr(settings, SHADOW_SETTING)

    if isinstance(raw, bool):
        return FlagReading(value=raw, source=FlagSource.SETTINGS)

    if isinstance(raw, str):
        word = raw.strip().lower()
        if word in _TRUE_WORDS:
            return FlagReading(value=True, source=FlagSource.SETTINGS)
        if word in _FALSE_WORDS:
            return FlagReading(value=False, source=FlagSource.SETTINGS)

    logger.warning(
        "decision_readiness.shadow.flag_unreadable setting=%s type=%s — resolved to off",
        SHADOW_SETTING,
        type(raw).__name__,
    )
    return FlagReading(value=False, source=FlagSource.MALFORMED)


@runtime_checkable
class EvidenceSink(Protocol):
    """Where a shadow record goes. The real one belongs to the Ayla backend (§19)."""

    def record(self, evidence: audit.DecisionEvidence) -> None: ...


class LoggingSink:
    """The sink that exists today: one structured line per evaluation.

    Codes, never wording — canon §13.5 keys analytics off semantic ids, and a
    log of sentences would have to be re-parsed by anyone who wanted to count
    anything. No `source_ref` values are expanded and no utterance is written;
    §19's PII rule applies to this line as much as to the record.
    """

    def __init__(self, log: logging.Logger | None = None) -> None:
        self._log = log or logger

    def record(self, evidence: audit.DecisionEvidence) -> None:
        self._log.info(
            "decision_readiness.shadow %s",
            json.dumps(
                {
                    "evaluation_id": evidence.readiness_evaluation_id,
                    "readiness_key": evidence.readiness_key,
                    "state_revision": evidence.state_revision,
                    "readiness_state": evidence.readiness_state,
                    "allow_recommend": evidence.allow_recommend,
                    "delegation": evidence.delegation.value,
                    "reason_codes": list(evidence.reason_codes),
                    "measures": evidence.measures,
                    "question_id": (evidence.question or {}).get("question_id"),
                    "ask_reason": (evidence.question or {}).get("ask_reason"),
                    "ask_reason_mechanism": (evidence.question or {}).get("ask_reason_mechanism"),
                    "rejected_signals": len(evidence.model_signals_rejected),
                    "spec_version": evidence.spec_version,
                    "policy_version": evidence.policy_version,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )


class NullSink:
    """Accepts and discards. For callers that only want the record returned."""

    def record(self, evidence: audit.DecisionEvidence) -> None:  # noqa: D102
        return None


class ShadowOutcome(str, Enum):
    """What a shadow run did. Four answers, and three of them are not failures.

    `DISABLED` and `NOT_CONFIGURED` are kept apart for the same reason the flag
    sources are: one is a decision, the other is an absence. `UNREADABLE_FLAG`
    is the third, and it is the one that would otherwise be silent.
    """

    OBSERVED = "observed"
    DISABLED = "disabled"
    NOT_CONFIGURED = "not_configured"
    UNREADABLE_FLAG = "unreadable_flag"


_OUTCOME_WHEN_OFF: dict[FlagSource, ShadowOutcome] = {
    FlagSource.SETTINGS: ShadowOutcome.DISABLED,
    FlagSource.READ_DEFAULT: ShadowOutcome.NOT_CONFIGURED,
    FlagSource.MALFORMED: ShadowOutcome.UNREADABLE_FLAG,
}


@dataclass(frozen=True, slots=True)
class ShadowRecord:
    """Everything a shadow run produces. Note what is missing.

    There is no `decision` field and no `next_question` field. The engine's
    verdict is inside `evidence` — data *about* a decision — and nothing here
    can be handed to a surface and executed. That absence is the mechanism by
    which shadow mode cannot influence a turn; it is asserted by a test rather
    than promised by a docstring.
    """

    outcome: ShadowOutcome
    flag: FlagReading
    evidence: audit.DecisionEvidence | None = None

    @property
    def influenced_the_turn(self) -> bool:
        """Always false, and checkable.

        Kept as a property so the claim has somewhere to live and something to
        assert against, rather than existing only in prose.
        """

        return False


def observe(
    request: ReadinessInput,
    *,
    evaluation_id: str,
    sink: EvidenceSink | None = None,
) -> ShadowRecord:
    """Run the engine for the record only.

    Returns a `ShadowRecord`. It does not return a decision, and it does not
    build one: a caller that wanted to act on this would have to construct the
    projection itself, which is a visible act.

    When the flag is off — or was never configured — the engine is not called at
    all. That is deliberate: an evaluation that happens but is discarded still
    costs the turn its latency, and a shadow mode that is "off" should be off.
    """

    flag = shadow_flag()
    if not flag.value:
        return ShadowRecord(outcome=_OUTCOME_WHEN_OFF[flag.source], flag=flag)

    output = evaluate(request)
    evidence = audit.build(request, output, evaluation_id=evaluation_id)
    (sink or LoggingSink()).record(evidence)
    return ShadowRecord(outcome=ShadowOutcome.OBSERVED, flag=flag, evidence=evidence)
