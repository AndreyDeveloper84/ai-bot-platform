"""§126 — nothing is silently unreachable. §114 — no test leans on a neighbour.

Two owner rulings of 10.09.2026, both applied to this package because both were
found to apply to it, not because either named it.

**§126.** "Недостижимое состояние, о котором умолчали, читается как покрытие."
This package had that gap in seven places. `test_the_engine_can_reach_every_one_of_the_five_states`
proves the readiness states are all reachable — and a completeness claim on one
axis, standing next to silence on another, reads as completeness in general.

**§114.** "каждый тест САМ СОЗДАЁТ и САМ УБИРАЕТ свои mocks/patches; результат
теста НЕ ЗАВИСИТ от порядка выполнения; результат НЕ ЗАВИСИТ от соседних
файлов."

The second one is worth being precise about, because the obvious evidence is
the wrong evidence. Running the suite in five layouts and getting green five
times proves that **those five layouts did not catch it** — nothing more. The
defect that started all this passed for weeks under one scheduler and failed
under another. So the test below does not run layouts; it checks the property
directly: every mutation of state outside this package goes through
`monkeypatch`, which undoes itself, and none is done by hand.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from apps.orchestrator.decision_readiness import declared_states as ds

TESTS_DIR = pathlib.Path(__file__).parent


# --- §126: declared, or produced, and never quietly neither ------------------


def test_every_declared_member_is_classified() -> None:
    """The partition. A new enum member forces a decision instead of sliding in.

    This is the assertion that would have caught the original gap: seven members
    existed with no producer and nothing said so.
    """

    every = ds.all_member_labels()
    unreachable = ds.unreachable_labels()
    produced = ds.produced_labels()

    assert every  # presence: the enums were actually read
    assert unreachable <= every, f"register names something not in the enums: {unreachable - every}"
    assert produced | unreachable == every
    assert produced & unreachable == set()


def test_the_register_is_not_empty_and_not_everything() -> None:
    """Both ends are failure modes.

    Empty means the package claims full coverage, which is the silence §126
    forbids. Everything means the register has stopped distinguishing and is
    just a list of the enums.
    """

    assert len(ds.DECLARED_WITHOUT_PRODUCER) >= 1
    assert len(ds.unreachable_labels()) < len(ds.all_member_labels())


def test_caution_is_declared_unreachable_by_name() -> None:
    """§126 names this one. If the ruling is ever satisfied by a real controlled
    rule, this entry must be retired — and the retirement is the evidence."""

    assert "SafetyState.CAUTION" in ds.unreachable_labels()

    entry = next(i for i in ds.DECLARED_WITHOUT_PRODUCER if i.label == "SafetyState.CAUTION")
    assert entry.count_today == 0
    assert "matrix" in entry.blocked_on


@pytest.mark.parametrize(
    "entry", ds.DECLARED_WITHOUT_PRODUCER, ids=[i.label for i in ds.DECLARED_WITHOUT_PRODUCER]
)
def test_each_entry_says_what_would_retire_it(entry: ds.Undeclared) -> None:
    """A register entry with no exit condition is a permanent excuse.

    `blocked_on` has to name something checkable — a lane, a ruling, a missing
    configuration — so the entry can be retired by evidence rather than opinion.
    """

    assert len(entry.blocked_on) > 40
    assert entry.count_today == 0


def test_the_covered_enums_are_the_ones_the_package_has() -> None:
    """Rule 28 applied to this register: it must know what it is covering.

    A contract enum added later and left out of `COVERED_ENUMS` would be
    unguarded while the register still looked complete.
    """

    import apps.orchestrator.decision_readiness as pkg

    covered = {enum.__name__ for enum in ds.COVERED_ENUMS}

    # These are the contract enumerations whose members describe *cases the
    # system distinguishes*. Enums that are pure plumbing (lifecycle of a Redis
    # read, the shape of a decision) are out of scope and named here so the
    # exclusion is a decision rather than an oversight.
    plumbing = {
        "ReadinessState",  # covered by its own reachability test in E12
        "BlockerType",
        "DecisionType",
        "StateLifecycle",
        "SlotState",
        "SlotVerdict",
        "UserEventKind",
        "Surface",
        "NeedKind",
        "VolatilityKind",
        "PresentationRequirement",
        "OptionRole",
        "AskOutcome",
        "ExpiryMechanism",
        "Mode",
        "Delegation",
        "ConflictSource",
        "Severity",
        "BlockedBy",
    }

    from enum import Enum

    in_package = {
        name
        for name, obj in vars(pkg).items()
        if isinstance(obj, type) and issubclass(obj, Enum) and obj is not Enum
    }

    assert in_package  # presence: enums were actually found on the package
    unclassified = in_package - covered - plumbing
    assert unclassified == set(), (
        f"contract enum(s) {sorted(unclassified)} are neither covered by the register "
        "nor listed as plumbing — classify them, do not leave them assumed"
    )


# --- §114: every patch undoes itself -----------------------------------------


_SELF_UNDOING = {"monkeypatch", "mocker"}


def _hand_patches(tree: ast.AST) -> list[str]:
    """Assignments and setattr calls that mutate something outside the test.

    Looks for the two shapes that outlive a test: `sys.modules[x] = y` and a
    bare `setattr(module, ...)`. `monkeypatch.setattr` and `with patch(...)`
    both undo themselves and are not flagged.
    """

    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Attribute)
                    and target.value.attr == "modules"
                ):
                    offenders.append(f"sys.modules[...] = ... at line {node.lineno}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "setattr":
                offenders.append(f"bare setattr(...) at line {node.lineno}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "setattr" and isinstance(node.func.value, ast.Name):
                if node.func.value.id not in _SELF_UNDOING:
                    offenders.append(f"{node.func.value.id}.setattr(...) at line {node.lineno}")
    return offenders


#: A file that leaks in both of the shapes the checker looks for. Kept at module
#: level so every absence assertion below can use it as its presence control: a
#: `_hand_patches` that had stopped matching anything would report every file
#: clean, and those assertions would pass while guarding nothing.
_LEAKY_SAMPLE = ast.parse(
    "import sys\ndef test_x():\n    sys.modules['openai'] = object()\n    setattr(sys, 'flag', 1)\n"
)


@pytest.mark.parametrize("path", sorted(TESTS_DIR.glob("test_*.py")), ids=lambda p: p.name)
def test_no_test_patches_anything_it_does_not_undo(path: pathlib.Path) -> None:
    """§114's first clause, checked directly instead of by running layouts.

    Five green layouts would prove only that those five did not catch it. The
    defect that produced this ruling passed under one scheduler for weeks and
    failed under another; a layout is a sample, and this is the property.
    """

    assert _hand_patches(_LEAKY_SAMPLE)  # presence, on the same checker

    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = _hand_patches(tree)

    assert offenders == [], f"{path.name}: {offenders}"


def test_the_patch_check_would_catch_a_real_leak() -> None:
    """Positive control. A checker that matches nothing passes on every file."""

    found = _hand_patches(_LEAKY_SAMPLE)

    assert len(found) == 2
    assert any("sys.modules" in f for f in found)
    assert any("setattr" in f for f in found)


def test_monkeypatch_is_not_flagged() -> None:
    """The other half of the control: the self-undoing form must pass, or the
    guard would simply forbid patching and be ignored."""

    fine = ast.parse(
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(mod, 'client', object())\n"
        "    monkeypatch.setitem(sys.modules, 'openai', object())\n"
    )

    assert _hand_patches(_LEAKY_SAMPLE)  # presence, on the same checker
    assert _hand_patches(fine) == []
