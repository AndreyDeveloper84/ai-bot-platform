"""Profile questions: Ayla's ask-eligibility verdict, taken as an input — slice E11.

### The one rule this module exists to hold

**The engine never derives a profile question from a slot being `UNKNOWN`.**

That sentence is the whole point, and the reason is a live property that lives
in a different repository. Read at source in the Ayla backend
(`beautygo_backend`, `users/personalization_engine.py:124`, `origin/dev`
`72eae3b2`):

    if sources.get(field) in {"explicit", "inferred", ERASED}:
        return Verdict(False, "already_have_data", field)

`ERASED` sits in the same set as "we have this". A field the person deliberately
erased counts as **filled**, and the question stops being asked. The engine's
docstring says it plainly (DRF-1366): "erased silences the question too".

If DecisionReadiness were allowed to look at a profile slot, see `UNKNOWN` and
decide to ask, it would resurrect exactly the question the erasure removed —
"забудь" would turn back into "спроси заново" on the next turn. The property is
preserved here by **not competing with that policy**, not by copying it: a copy
would drift from the original at the first edit in the catalog, and would drift
silently.

So a profile question can only be constructed from a verdict. There is no
constructor that takes a slot name, and a test asserts there is none.

### Composition, not a second authority

The verdict is an upstream filter on candidate questions, never a decision.
Both must permit: Ayla says the field may be asked at all, and the engine then
applies its own rules on top — ledger, budget, impact (§13.3–13.5). An AND, and
in the direction that asks fewer questions, which is the safe one.

### The wire vocabulary, read at source rather than from our own docstring

`blocked_by` is passed through untranslated. It has to be, because it has been
mistranslated once already: `apps/integrations/ayla/personal_context_client.py`
names the codes `cooldown` and `skipped_twice`, and the wire has never sent
either — the endpoint sends `cooldown_24h` and `double_skip_pause`. A comparison
against our own docstring's names would silently never match.

Verified on `beautygo_backend` `origin/dev` `72eae3b2`:

* `users/personalization_engine.py:50-57` — `ALLOWED_REASON_CODES`;
* `users/internal_personal_context_api.py:198-226` — the endpoint, which starts
  at `no_candidate` and replaces it with the first engine reason it meets,
  breaking early on `first_interaction` because that one is global.

`no_candidate` is a real wire value despite not being an engine branch; it is
the endpoint's own "the loop finished and nobody was eligible".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from apps.orchestrator.decision_readiness.questions import (
    MODE_FREE,
    QuestionCatalogEntry,
    QuestionKind,
)


class BlockedBy(str, Enum):
    """Every value `blocked_by` can carry, verbatim from the wire.

    Not renamed into engine vocabulary. A local synonym is how the existing
    mistranslation happened, and a renamed code cannot be matched against a log
    from the other side.
    """

    NO_CANDIDATE = "no_candidate"
    FIRST_INTERACTION = "first_interaction"
    ALREADY_HAVE_DATA = "already_have_data"
    COOLDOWN_24H = "cooldown_24h"
    DOUBLE_SKIP_PAUSE = "double_skip_pause"
    CONTEXT_MISSING = "context_missing"


#: The code that carries the erasure. Named separately because it is the one
#: whose meaning is not obvious from its name: `already_have_data` covers
#: `explicit`, `inferred` **and** `ERASED` — "we have it", "we inferred it" and
#: "you told us to forget it" are one answer to "may I ask", and that is
#: deliberate (DRF-1366).
ERASURE_BEARING_CODE = BlockedBy.ALREADY_HAVE_DATA


@dataclass(frozen=True, slots=True)
class AskEligibilityVerdict:
    """What the endpoint said. A value object, not a decision.

    `blocked_by` is `str` rather than `BlockedBy` on purpose: the backend may
    add a code before this enum learns about it, and an unknown code must arrive
    as itself rather than as a parse error or a silent `None`. `known_reason()`
    is how a caller asks whether it recognises it.
    """

    should_ask: bool
    field: str | None = None
    prompt_hint: str | None = None
    blocked_by: str | None = None
    explain: str | None = None

    def known_reason(self) -> BlockedBy | None:
        try:
            return BlockedBy(self.blocked_by) if self.blocked_by else None
        except ValueError:
            return None

    def is_erasure_bearing(self) -> bool:
        """Whether this refusal may be standing in for a deliberate erasure."""

        return self.known_reason() is ERASURE_BEARING_CODE


@dataclass(frozen=True, slots=True)
class ProfileQuestionInput:
    """The verdict, in engine terms: at most one candidate, plus why not."""

    entry: QuestionCatalogEntry | None
    suppressed_by: str | None = None
    prompt_hint: str | None = None

    @property
    def offers_a_question(self) -> bool:
        return self.entry is not None


#: Profile fields the bot can actually record an answer for. Mirrors
#: `apps.orchestrator.memory_ask._FIELD_PARSERS`, and the exclusion is the
#: interesting part: `favorite_masters` is askable by the backend and is *not*
#: asked here, because an answer cannot be resolved to the SpecialistProfile
#: UUIDs the contract requires. A question whose answer cannot be stored changes
#: nothing, which is §13.3's rule arriving from a different direction — do not
#: ask what you cannot record.
def _recordable_fields() -> frozenset[str]:
    from apps.orchestrator.memory_ask import _FIELD_PARSERS

    return frozenset(_FIELD_PARSERS)


UNSUPPORTED_FIELD = "profile_field_not_recordable"


def from_verdict(
    verdict: AskEligibilityVerdict,
    *,
    semantics_version: int = 1,
) -> ProfileQuestionInput:
    """Turn one verdict into at most one catalog entry.

    This is the **only** way a profile question comes into existence. It takes a
    verdict and no slot name, so there is no path from "the slot is UNKNOWN" to
    "ask about it" — see the module docstring on why that path must not exist.
    """

    if not verdict.should_ask:
        return ProfileQuestionInput(entry=None, suppressed_by=verdict.blocked_by)

    field = (verdict.field or "").strip()
    if not field:
        return ProfileQuestionInput(entry=None, suppressed_by=BlockedBy.NO_CANDIDATE.value)

    if field not in _recordable_fields():
        return ProfileQuestionInput(entry=None, suppressed_by=UNSUPPORTED_FIELD)

    hint = (verdict.prompt_hint or "").strip()
    if not hint:
        # The backend chose the field but sent no wording to render. Asking
        # anyway would mean the bot inventing the question, which is the one
        # thing §13.1 does not allow it to do.
        return ProfileQuestionInput(entry=None, suppressed_by=UNSUPPORTED_FIELD)

    return ProfileQuestionInput(
        entry=QuestionCatalogEntry(
            kind=QuestionKind.BROADENING,
            target_slots=(field,),
            mode=MODE_FREE,
            semantics_version=semantics_version,
            answer_domain=(),
        ),
        prompt_hint=hint,
    )


def read_verdict(payload: Any) -> AskEligibilityVerdict:
    """Adapt `apps.integrations.ayla.personal_context_client.AskEligibility`.

    Duck-typed rather than imported so that this module stays outside the wire
    client's import graph — the engine's no-provider guard walks these imports,
    and the client pulls in httpx and the whole integration layer.
    """

    return AskEligibilityVerdict(
        should_ask=bool(getattr(payload, "should_ask", False)),
        field=getattr(payload, "field", None),
        prompt_hint=getattr(payload, "prompt_hint", None),
        blocked_by=getattr(payload, "blocked_by", None),
        explain=getattr(payload, "explain", None),
    )


# --- the other half of the same property, and it is ours --------------------


@dataclass(frozen=True, slots=True)
class MemoryCommandTurn:
    """A turn that was a memory command — "забудь что я веган", "что ты знаешь".

    `apps/channels/max/handler.py:2461-2466` already refuses to write memory on
    such a turn, and says why: «забудь что я веган» contains the substring «я
    веган», so re-running the extractor would instantly re-create the fact the
    person just asked to erase, nullifying the 152-ФЗ erasure.

    The engine needs the same refusal for the same reason, one step further
    along: a forget turn must not become `ConfirmedEvidence` either. Today that
    guarantee is a comment in a channel handler; here it is a value the input
    adapter has to carry, and `admits_evidence()` is the place it is enforced.
    """

    was_memory_command: bool

    def admits_evidence(self) -> bool:
        """Whether this turn may produce evidence at all.

        A memory command may not. The person's words on such a turn are an
        instruction *about* what is remembered, not a statement to remember —
        and the two are indistinguishable by content, which is exactly why the
        distinction has to be carried rather than re-derived from the text.
        """

        return not self.was_memory_command
