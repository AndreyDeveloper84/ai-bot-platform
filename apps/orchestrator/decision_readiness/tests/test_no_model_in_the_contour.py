"""NO MODEL CONFIDENCE — §17.2 and §7 Z2, as a guard rather than an intention.

DRF-1519 asks for one number: how many places decide "ask or recommend" from the
model's self-assessment. This package's answer has to be zero, and a comment
saying so is not a guard (`docs/EXECUTOR-RULES.md` §4).

Two independent checks, because either alone can be satisfied while the property
is false:

* the **import graph** of the decision modules contains no LLM provider — an
  engine that cannot reach a model cannot consult one;
* `evaluate()` still answers when every provider in the process is replaced by a
  stand that raises on contact (Z2).

The second is the one that catches an import added at call time.
"""

from __future__ import annotations

import ast
import pathlib
import sys
from types import ModuleType

import pytest

from apps.orchestrator.decision_readiness import engine as eng
from apps.orchestrator.decision_readiness.tests.conftest import make_input

PACKAGE_ROOT = pathlib.Path(eng.__file__).parent

# The decision contour: everything `evaluate()` can reach. `ledger_store` is
# deliberately outside it — it touches the database and the engine never calls
# it, which is why the register arrives in the input instead.
DECISION_MODULES = (
    "engine.py",
    "decision.py",
    "questions.py",
    "required_context.py",
    "evidence.py",
    "events.py",
    "candidates.py",
    "safety_input.py",
    "ledger.py",
    "policy.py",
    "reason_codes.py",
    "state.py",
    "audit.py",
)

FORBIDDEN_IMPORT_MARKERS = (
    "openai",
    "anthropic",
    "ayla_ai_core",
    "apps.orchestrator.llm",
    "apps.orchestrator.concierge",
    "apps.orchestrator.discovery",
    "apps.orchestrator.pipeline",
    "apps.llm",
)


def _imported_names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_every_decision_module_is_present_in_the_check() -> None:
    """The guard must know what it is guarding (`EXECUTOR-RULES` §28).

    A list that quietly stopped matching the package would keep passing while
    covering less and less.
    """

    on_disk = {
        path.name
        for path in PACKAGE_ROOT.glob("*.py")
        if path.name not in {"__init__.py", "ledger_store.py"}
    }

    assert on_disk == set(DECISION_MODULES), (
        "a module was added to or removed from the decision contour; add it to "
        "DECISION_MODULES or say in ledger_store.py's docstring why it is outside"
    )


@pytest.mark.parametrize("module_name", DECISION_MODULES)
def test_the_decision_contour_imports_no_language_model(module_name: str) -> None:
    imported = _imported_names(PACKAGE_ROOT / module_name)

    offending = {
        name
        for name in imported
        for marker in FORBIDDEN_IMPORT_MARKERS
        if name == marker or name.startswith(f"{marker}.")
    }

    assert offending == set(), f"{module_name} imports {sorted(offending)}"


def test_the_marker_list_would_actually_catch_something() -> None:
    """Positive control: a check that matches nothing passes on an empty rule."""

    fake = {"openai", "apps.orchestrator.llm.openai_provider"}
    caught = {
        name
        for name in fake
        for marker in FORBIDDEN_IMPORT_MARKERS
        if name == marker or name.startswith(f"{marker}.")
    }

    assert caught == fake


def test_evaluate_answers_with_every_provider_replaced_by_a_landmine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Z2 — "the model thinks there is enough information" has no code path.

    Every provider module in the process is swapped for one that raises on any
    attribute access. If `evaluate()` reached a model at call time, this would
    raise rather than answer.
    """

    class Landmine(ModuleType):
        def __getattr__(self, item: str) -> object:
            raise AssertionError(
                f"the decision contour reached a language model provider ({self.__name__}.{item})"
            )

    for name in list(sys.modules):
        if name.split(".")[0] in {"openai", "anthropic"} or name.startswith("ayla_ai_core"):
            monkeypatch.setitem(sys.modules, name, Landmine(name))

    output = eng.evaluate(make_input())

    assert output.readiness_state is eng.ReadinessState.READY


def test_no_input_field_carries_a_number_the_model_produced() -> None:
    """Z1 — `ReadinessInput` has no model-sourced numeric field.

    `model_signals` carries values, and they are strings on a `ModelSignal`
    whose only exit is `confirm()`. Nothing on the input is a score.
    """

    from apps.orchestrator.decision_readiness.evidence import ModelSignal

    signal_fields = ModelSignal.__dataclass_fields__

    assert "confidence" not in signal_fields
    assert "score" not in signal_fields
    assert "probability" not in signal_fields
    # The one numeric field a signal has is a revision — an ordering fact from
    # the transport, not a self-assessment.
    assert signal_fields["produced_at_revision"].type in {"int", int}
