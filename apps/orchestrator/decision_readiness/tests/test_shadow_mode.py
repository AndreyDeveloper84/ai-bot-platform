"""E10 — the engine runs and cannot act, and the flag says where its value came from.

Two properties, and both are asserted structurally rather than by behaviour,
because behaviour tests pass on the version that would break tomorrow:

* a shadow run has **no way** to hand back something a surface could execute —
  not a rule about not using the result, an absence of the result;
* the flag reports off, unset and unreadable as three different things, and the
  unreadable case resolves closed.
"""

from __future__ import annotations

import inspect
import logging
from types import ModuleType

import pytest

from apps.orchestrator.decision_readiness import audit
from apps.orchestrator.decision_readiness import decision as dec
from apps.orchestrator.decision_readiness import shadow
from apps.orchestrator.decision_readiness.tests.conftest import make_input


def _referenced_names(module: ModuleType) -> set[str]:
    """Every name the module's **code** mentions — docstrings and comments excluded.

    A raw text search does not work here, and the reason is worth keeping: this
    module's docstring explains at length that it must not build a
    `StructuredDecision`, so a text search for that word finds the prohibition
    and reports it as a violation. The prose that forbids a thing and the code
    that does it look identical to `grep`.
    """

    import ast

    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name.split(".")[-1])
            if node.asname:
                names.add(node.asname)
    return names


class CollectingSink:
    def __init__(self) -> None:
        self.records: list[audit.DecisionEvidence] = []

    def record(self, evidence: audit.DecisionEvidence) -> None:
        self.records.append(evidence)


@pytest.fixture()
def shadow_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shadow.settings, shadow.SHADOW_SETTING, True, raising=False)


# --- the engine runs and cannot act ------------------------------------------


def test_a_shadow_run_produces_a_record_and_no_decision(shadow_on: None) -> None:
    sink = CollectingSink()

    record = shadow.observe(make_input(), evaluation_id="ev-1", sink=sink)

    assert record.outcome is shadow.ShadowOutcome.OBSERVED
    assert record.evidence is not None  # presence: the engine really ran
    assert len(sink.records) == 1
    assert record.influenced_the_turn is False


def test_the_record_has_no_field_a_surface_could_execute() -> None:
    """The mechanism, not the intention.

    A rule "do not act on shadow output" holds until somebody acts on it, and
    the call site looks identical the day it starts steering a conversation.
    There is no decision on the way out because there is nowhere to put one.
    """

    fields = set(shadow.ShadowRecord.__dataclass_fields__)

    assert "evidence" in fields  # presence: the right object was inspected
    assert "decision" not in fields
    assert "next_question" not in fields
    assert "structured_decision" not in fields
    assert "options" not in fields


def test_observe_never_returns_a_structured_decision(shadow_on: None) -> None:
    """The return annotation and the value agree, and neither is a decision."""

    assert inspect.signature(shadow.observe).return_annotation == "ShadowRecord"

    record = shadow.observe(make_input(), evaluation_id="ev-2", sink=CollectingSink())
    carried = [getattr(record, f) for f in shadow.ShadowRecord.__dataclass_fields__]

    assert carried  # presence: the record really has fields
    assert not isinstance(record, dec.StructuredDecision)
    assert not any(isinstance(value, dec.StructuredDecision) for value in carried)


def test_shadow_does_not_build_the_projection() -> None:
    """`project()` is what turns a verdict into something executable. Shadow must
    not call it — a caller who wants that has to write it themselves, which is a
    visible act rather than a silent one."""

    names = _referenced_names(shadow)

    assert "evaluate" in names  # presence: the module's code really was read
    assert "project" not in names
    assert "StructuredDecision" not in names


def test_the_engine_is_not_called_at_all_when_shadow_is_off() -> None:
    """An evaluation that happens and is discarded still costs the turn its
    latency. "Off" should be off."""

    sink = CollectingSink()

    record = shadow.observe(make_input(), evaluation_id="ev-3", sink=sink)

    assert record.evidence is None
    assert sink.records == []


# --- three different falses --------------------------------------------------


def test_an_absent_key_is_not_a_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(shadow.settings, shadow.SHADOW_SETTING, raising=False)

    reading = shadow.shadow_flag()

    assert reading.value is False
    assert reading.source is shadow.FlagSource.READ_DEFAULT
    assert reading.configured is False


def test_an_explicit_false_is_a_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    """The positive control for the test above: without it, a flag that always
    reported READ_DEFAULT would pass."""

    monkeypatch.setattr(shadow.settings, shadow.SHADOW_SETTING, False, raising=False)

    reading = shadow.shadow_flag()

    assert reading.value is False
    assert reading.source is shadow.FlagSource.SETTINGS
    assert reading.configured is True


def test_the_string_false_does_not_switch_shadow_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`bool("false")` is `True`.

    An operator writing `DRE_SHADOW_ENABLED="false"` in an environment file
    would, under a naive read, have turned shadow mode **on**. This is the
    assertion that would catch that.
    """

    monkeypatch.setattr(shadow.settings, shadow.SHADOW_SETTING, "false", raising=False)

    reading = shadow.shadow_flag()

    assert reading.value is False
    assert reading.source is shadow.FlagSource.SETTINGS


@pytest.mark.parametrize("word", ["true", "TRUE", " on ", "1", "yes"])
def test_the_words_that_mean_on(word: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shadow.settings, shadow.SHADOW_SETTING, word, raising=False)

    assert shadow.shadow_flag().value is True


@pytest.mark.parametrize("word", ["false", "off", "0", "no", ""])
def test_the_words_that_mean_off(word: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shadow.settings, shadow.SHADOW_SETTING, word, raising=False)

    reading = shadow.shadow_flag()

    assert reading.value is False
    assert reading.source is shadow.FlagSource.SETTINGS


def test_a_value_nobody_can_read_resolves_closed_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flag nobody can read must not be the thing that switches something on."""

    monkeypatch.setattr(shadow.settings, shadow.SHADOW_SETTING, ["maybe"], raising=False)

    reading = shadow.shadow_flag()

    assert reading.value is False
    assert reading.source is shadow.FlagSource.MALFORMED

    record = shadow.observe(make_input(), evaluation_id="ev-4", sink=CollectingSink())
    assert record.outcome is shadow.ShadowOutcome.UNREADABLE_FLAG


def test_the_three_offs_are_three_outcomes() -> None:
    """Collapsing them would make two of the three invisible, and only one of
    the three is somebody's decision."""

    assert (
        len(
            {
                shadow.FlagSource.SETTINGS,
                shadow.FlagSource.READ_DEFAULT,
                shadow.FlagSource.MALFORMED,
            }
        )
        == 3
    )
    assert set(shadow._OUTCOME_WHEN_OFF) == set(shadow.FlagSource)
    assert len(set(shadow._OUTCOME_WHEN_OFF.values())) == 3


def test_a_broken_flag_does_not_break_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    class Hostile:
        def __bool__(self) -> bool:
            raise RuntimeError("nobody should call this")

    monkeypatch.setattr(shadow.settings, shadow.SHADOW_SETTING, Hostile(), raising=False)

    reading = shadow.shadow_flag()

    assert reading.value is False
    assert reading.source is shadow.FlagSource.MALFORMED


# --- the sink ----------------------------------------------------------------


def test_the_log_line_carries_codes_and_not_wording(
    shadow_on: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Canon §13.5: analytics keys off semantic ids. A log of sentences has to be
    re-parsed by anyone who wants to count anything, and that is discovered six
    months later."""

    with caplog.at_level(logging.INFO, logger=shadow.logger.name):
        shadow.observe(make_input(), evaluation_id="ev-5", sink=shadow.LoggingSink())

    line = "".join(r.getMessage() for r in caplog.records)

    assert "reason_codes" in line  # presence: the line really was emitted
    assert "STATE_READY" in line
    assert "Пенза" not in line  # no utterance, no source_ref value (§19 PII)


def test_the_sink_protocol_is_what_a_real_one_must_satisfy() -> None:
    """§19 puts persistence with the Ayla backend. This is the shape, not a stub
    standing in for it."""

    assert isinstance(shadow.LoggingSink(), shadow.EvidenceSink)
    assert isinstance(shadow.NullSink(), shadow.EvidenceSink)
    assert not isinstance(object(), shadow.EvidenceSink)


# --- what E10 deliberately leaves alone --------------------------------------


def test_shadow_proposes_no_threshold() -> None:
    """OD-DR-1: calibration happens on real data, by somebody with the authority
    to choose. A producer that also chose would make the two indistinguishable
    afterwards."""

    names = _referenced_names(shadow)

    assert "audit" in names  # presence: the module's code really was read
    assert "tau_separation" not in names
    assert "n_broad" not in names
    assert not any("calibrat" in name.lower() for name in names)
