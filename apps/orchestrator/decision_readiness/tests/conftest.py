"""Shared builders for the engine tests.

Everything here is a *complete* input by default — safety evaluated, probe
present, register readable, threshold calibrated. That is the opposite of the
production default on purpose: the fail-closed tests each remove one thing and
assert the block, and a fixture that started out broken could not tell which
removal caused which block.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.orchestrator.decision_readiness import engine as eng
from apps.orchestrator.decision_readiness import evidence as ev
from apps.orchestrator.decision_readiness import questions as q
from apps.orchestrator.decision_readiness.candidates import (
    CandidateSetSignature,
    HypotheticalAnswer,
)
from apps.orchestrator.decision_readiness.events import user_text_event
from apps.orchestrator.decision_readiness.ledger import QuestionLedger
from apps.orchestrator.decision_readiness.policy import ControlledPolicy
from apps.orchestrator.decision_readiness.required_context import (
    Always,
    Mode,
    RequiredContextSpec,
    RequiredSlot,
    SlotOwner,
)
from apps.orchestrator.decision_readiness.safety_input import Handoff, SafetyResult, SafetyState
from apps.orchestrator.decision_readiness.state import ConversationState

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
REVISION = 3


class SplittingProbe:
    """A resolver stand-in where every answer lands on a different set.

    Deliberately generous: these tests are about the engine's state machine, and
    a probe that answered "nothing changes" would make every question
    inadmissible and every assertion about states an assertion about the stub.
    The `ask_allowed` rule itself is exercised against a stricter stub in
    `test_ask_impact.py`, where "nothing changes" is the point.
    """

    def __init__(self, *, narrows: bool = True, separation_after: float | None = 0.9) -> None:
        self.narrows = narrows
        self.separation_after = separation_after

    def probe(self, answer: HypotheticalAnswer) -> CandidateSetSignature:
        eligible = (f"m-{answer.value}",)
        return CandidateSetSignature(
            digest=f"d-{answer.value}",
            visible_count=len(eligible),
            recommendation_eligible_count=len(eligible),
            eligible_ids=eligible,
            ordered_ids=eligible,
            separation=self.separation_after,
        )

    def narrowed_by(self, evidence: object) -> bool:
        return self.narrows


def candidates(**kwargs: object) -> CandidateSetSignature:
    defaults: dict[str, object] = {
        "digest": "d0",
        "visible_count": 3,
        "recommendation_eligible_count": 3,
        "eligible_ids": ("m1", "m2", "m3"),
        "ordered_ids": ("m1", "m2", "m3"),
        "separation": 0.9,
        "spanning_fields": frozenset({"price_band"}),
    }
    defaults.update(kwargs)
    return CandidateSetSignature(**defaults)  # type: ignore[arg-type]


def calibrated_policy(**kwargs: object) -> ControlledPolicy:
    defaults: dict[str, object] = {
        "policy_version": 1,
        "tau_separation": 0.5,
        "ranking_comparison_depth": 3,
    }
    defaults.update(kwargs)
    return ControlledPolicy(**defaults)  # type: ignore[arg-type]


def required_slot(**kwargs: object) -> RequiredSlot:
    defaults: dict[str, object] = {
        "slot": "city",
        "owner": SlotOwner.CATALOG,
        "required_when": Always(),
        "question_id": "unused",
    }
    defaults.update(kwargs)
    return RequiredSlot(**defaults)  # type: ignore[arg-type]


def city_evidence(slot: str = "city", value: str = "Пенза") -> ev.ConfirmedEvidence:
    return ev.from_user_text(
        evidence_id="c-city",
        slot=slot,
        value=value,
        event=user_text_event(
            event_id="e1",
            conversation_id="conv-1",
            revision=2,
            message_id="msg-1",
            raw_text=value,
            observed_at=NOW,
        ),
    )


def discrimination_entry(**kwargs: object) -> q.QuestionCatalogEntry:
    defaults: dict[str, object] = {
        "kind": q.QuestionKind.DISCRIMINATION,
        "target_slots": ("price_band",),
        "mode": q.MODE_CONFIRM_ONE,
        "semantics_version": 1,
        "discriminator_key": "price_band",
        "answer_domain": ("low", "high"),
        "options": (
            q.SemanticOption("o-low", q.OptionRole.CHOICE, "low", "Подешевле"),
            q.SemanticOption("o-high", q.OptionRole.CHOICE, "high", "Подороже"),
            q.SemanticOption("o-esc", q.OptionRole.ESCAPE, "other", "Другое"),
            q.SemanticOption("o-del", q.OptionRole.DELEGATE, "choose_for_me", "Выбери сам"),
        ),
    }
    defaults.update(kwargs)
    return q.QuestionCatalogEntry(**defaults)  # type: ignore[arg-type]


def required_context_entry(**kwargs: object) -> q.QuestionCatalogEntry:
    defaults: dict[str, object] = {
        "kind": q.QuestionKind.REQUIRED_CONTEXT,
        "target_slots": ("city",),
        "mode": q.MODE_CONFIRM_ONE,
        "semantics_version": 1,
        "answer_domain": ("Пенза", "Москва"),
    }
    defaults.update(kwargs)
    return q.QuestionCatalogEntry(**defaults)  # type: ignore[arg-type]


def broadening_entry(**kwargs: object) -> q.QuestionCatalogEntry:
    defaults: dict[str, object] = {
        "kind": q.QuestionKind.BROADENING,
        "target_slots": ("goal",),
        "mode": q.MODE_FREE,
        "semantics_version": 1,
        "answer_domain": ("relax", "treat"),
    }
    defaults.update(kwargs)
    return q.QuestionCatalogEntry(**defaults)  # type: ignore[arg-type]


def default_spec() -> RequiredContextSpec:
    """One CATALOG-owned slot, satisfied by `city_evidence()`.

    Not empty, and that matters: with an empty table `spec.need_slots()` is
    empty, so §11.2's grounding condition can never hold and the engine can
    never leave `INSUFFICIENT_EVIDENCE`. That is correct fail-closed behaviour
    for a policy table nobody has filled in — it is just not the fixture for
    testing the states above it.
    """

    return RequiredContextSpec(policy_version=1, slots=(required_slot(),))


def make_input(**kwargs: object) -> eng.ReadinessInput:
    """A complete, satisfiable input. Tests remove one thing at a time."""

    defaults: dict[str, object] = {
        "state_revision": REVISION,
        "state": ConversationState(conversation_id="conv-1", revision=REVISION),
        "mode": Mode.DISCOVERY,
        "safety": SafetyResult(
            state=SafetyState.NORMAL, evaluated_at_revision=REVISION, handoff=Handoff.NONE
        ),
        "candidates": candidates(),
        "policy": calibrated_policy(),
        "required_context_spec": default_spec(),
        "question_ledger": QuestionLedger(),
        "availability": eng.InputAvailability(
            ledger_readable=True, probe_available=True, candidates_fresh=True
        ),
        "measures": eng.Measures(completeness=1.0, conflicts=0),
        "catalog": q.QuestionCatalog(
            entries=(discrimination_entry(), required_context_entry(), broadening_entry())
        ),
        "evidence": (city_evidence(),),
        "probe": SplittingProbe(),
    }
    defaults.update(kwargs)
    return eng.ReadinessInput(**defaults)  # type: ignore[arg-type]


@pytest.fixture()
def complete_input() -> eng.ReadinessInput:
    return make_input()
