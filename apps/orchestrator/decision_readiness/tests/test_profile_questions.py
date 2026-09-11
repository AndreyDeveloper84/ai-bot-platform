"""E11 — "deliberately erased counts as filled" survives the migration.

The property lives in the Ayla backend, verified at source there
(`users/personalization_engine.py:124`, `origin/dev` `72eae3b2`):

    if sources.get(field) in {"explicit", "inferred", ERASED}:
        return Verdict(False, "already_have_data", field)

It is preserved here by refusing to compete with it. The assertions below are
therefore mostly about what does **not** exist: no constructor from a slot name,
no local synonym for a wire code, no way for a forget turn to become evidence.
"""

from __future__ import annotations

import inspect

import pytest

from apps.orchestrator.decision_readiness import profile_questions as pq
from apps.orchestrator.decision_readiness.questions import QuestionKind


class _WireVerdict:
    """Shaped like `apps.integrations.ayla.personal_context_client.AskEligibility`."""

    def __init__(self, **kwargs: object) -> None:
        self.should_ask = kwargs.get("should_ask", False)
        self.field = kwargs.get("field")
        self.prompt_hint = kwargs.get("prompt_hint")
        self.blocked_by = kwargs.get("blocked_by")
        self.explain = kwargs.get("explain")


def _allowed(
    field: str = "diet_type", hint: str = "А ты чего-то не ешь?"
) -> pq.AskEligibilityVerdict:
    return pq.AskEligibilityVerdict(should_ask=True, field=field, prompt_hint=hint)


# --- the rule: no question is derived from a slot ----------------------------


def test_a_profile_question_can_only_be_built_from_a_verdict() -> None:
    """No constructor takes a slot name, so there is no path from "UNKNOWN" to "ask".

    That path is the one that would resurrect an erased field: the engine would
    see the slot empty — which is exactly what an erasure leaves behind — and
    ask again.
    """

    builders = {
        name
        for name, obj in vars(pq).items()
        if inspect.isfunction(obj) and obj.__module__ == pq.__name__ and not name.startswith("_")
    }

    assert builders == {"from_verdict", "read_verdict"}  # presence, before the absence below

    params = inspect.signature(pq.from_verdict).parameters
    assert "verdict" in params
    assert "slot" not in params
    assert "slot_state" not in params


def test_an_allowed_verdict_becomes_one_catalog_entry() -> None:
    result = pq.from_verdict(_allowed())

    assert result.offers_a_question is True
    assert result.entry is not None
    assert result.entry.target_slots == ("diet_type",)
    assert result.entry.kind is QuestionKind.BROADENING
    assert result.prompt_hint == "А ты чего-то не ешь?"


def test_a_refusal_offers_nothing_and_keeps_the_wire_code() -> None:
    result = pq.from_verdict(
        pq.AskEligibilityVerdict(should_ask=False, blocked_by="already_have_data")
    )

    assert result.offers_a_question is False
    assert result.suppressed_by == "already_have_data"


# --- the erasure ------------------------------------------------------------


def test_the_erasure_arrives_as_already_have_data() -> None:
    """The code that carries it. `ERASED` is in the same set as "we have this",
    so a forgotten field and a known field give the same answer to "may I ask"."""

    erased = pq.AskEligibilityVerdict(should_ask=False, blocked_by="already_have_data")

    assert erased.is_erasure_bearing() is True
    assert pq.ERASURE_BEARING_CODE is pq.BlockedBy.ALREADY_HAVE_DATA


def test_other_refusals_are_not_read_as_erasures() -> None:
    """The positive control for the test above: if everything were "erasure
    bearing", the flag would carry no information."""

    for code in ("cooldown_24h", "double_skip_pause", "first_interaction", "no_candidate"):
        assert (
            pq.AskEligibilityVerdict(should_ask=False, blocked_by=code).is_erasure_bearing()
            is False
        )


# --- the wire vocabulary ----------------------------------------------------


def test_the_six_wire_codes_are_the_ones_the_backend_actually_sends() -> None:
    """Read at source on `beautygo_backend` `origin/dev` `72eae3b2`:
    `personalization_engine.py:50-57` (ALLOWED_REASON_CODES minus "ok") plus
    `internal_personal_context_api.py:209` (`no_candidate`, the endpoint's own
    "the loop finished and nobody was eligible")."""

    assert {code.value for code in pq.BlockedBy} == {
        "no_candidate",
        "first_interaction",
        "already_have_data",
        "cooldown_24h",
        "double_skip_pause",
        "context_missing",
    }


def test_the_names_our_own_client_docstring_uses_are_not_wire_values() -> None:
    """`personal_context_client.py` says `cooldown` and `skipped_twice`. Neither
    has ever been sent. A comparison against those names would silently never
    match, which is why `blocked_by` is passed through untranslated."""

    wire = {code.value for code in pq.BlockedBy}

    assert "cooldown_24h" in wire  # presence: the real name is there
    assert "cooldown" not in wire
    assert "double_skip_pause" in wire
    assert "skipped_twice" not in wire


def test_an_unknown_code_arrives_as_itself() -> None:
    """The backend may add a code before this enum hears about it. An unknown one
    must not become a parse error or a silent None — the engine still has to be
    able to log what it was told."""

    verdict = pq.AskEligibilityVerdict(should_ask=False, blocked_by="brand_new_reason")

    assert verdict.known_reason() is None
    assert verdict.blocked_by == "brand_new_reason"
    assert pq.from_verdict(verdict).suppressed_by == "brand_new_reason"


# --- what cannot be asked ---------------------------------------------------


def test_a_field_the_bot_cannot_record_is_not_asked() -> None:
    """`favorite_masters`: askable by the backend, unrecordable here, because an
    answer cannot be resolved to the SpecialistProfile UUIDs the contract wants.
    A question whose answer cannot be stored changes nothing — §13.3 arriving
    from a different direction."""

    result = pq.from_verdict(_allowed(field="favorite_masters"))

    assert result.offers_a_question is False
    assert result.suppressed_by == pq.UNSUPPORTED_FIELD


def test_the_recordable_set_is_the_one_memory_ask_actually_has() -> None:
    from apps.orchestrator.memory_ask import _FIELD_PARSERS

    recordable = pq._recordable_fields()

    assert recordable  # presence: the set was really read
    assert recordable == frozenset(_FIELD_PARSERS)
    assert "favorite_masters" not in recordable


def test_a_verdict_with_no_wording_is_not_asked() -> None:
    """Asking anyway would mean the bot inventing the question, which is the one
    thing §13.1 does not let it do."""

    result = pq.from_verdict(_allowed(hint="  "))

    assert result.offers_a_question is False


def test_a_verdict_with_no_field_is_no_candidate() -> None:
    result = pq.from_verdict(pq.AskEligibilityVerdict(should_ask=True, field=None))

    assert result.suppressed_by == "no_candidate"


# --- reading the wire object ------------------------------------------------


def test_the_wire_object_is_read_by_shape_not_by_import() -> None:
    """Kept duck-typed so this module stays out of the client's import graph:
    the no-provider guard walks these imports and the client pulls in httpx."""

    source = inspect.getsource(pq)

    assert "personal_context_client" in source  # it is named in prose
    assert "from apps.integrations" not in source  # and never imported

    verdict = pq.read_verdict(_WireVerdict(should_ask=True, field="diet_type", prompt_hint="?"))

    assert verdict.should_ask is True
    assert verdict.field == "diet_type"


# --- the half of the property that is ours ----------------------------------


def test_a_memory_command_turn_admits_no_evidence() -> None:
    """`handler.py:2461-2466` refuses to write memory on a forget turn, because
    «забудь что я веган» contains «я веган» and the extractor would re-create
    the fact. The engine needs the same refusal one step further along."""

    forget_turn = pq.MemoryCommandTurn(was_memory_command=True)
    ordinary_turn = pq.MemoryCommandTurn(was_memory_command=False)

    assert ordinary_turn.admits_evidence() is True  # positive control first
    assert forget_turn.admits_evidence() is False


@pytest.mark.parametrize("was_command", [True, False])
def test_the_distinction_is_carried_not_re_derived_from_the_text(was_command: bool) -> None:
    """`MemoryCommandTurn` takes a flag, not a string.

    An instruction about memory and a statement to remember are
    indistinguishable by content — «забудь что я веган» contains «я веган»
    — so the distinction has to arrive from the turn that classified it.
    """

    params = inspect.signature(pq.MemoryCommandTurn).parameters

    assert "was_memory_command" in params
    assert "text" not in params
    assert pq.MemoryCommandTurn(was_memory_command=was_command).admits_evidence() is not was_command
