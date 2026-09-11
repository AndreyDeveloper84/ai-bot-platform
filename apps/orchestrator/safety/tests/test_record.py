"""A-2, producer half: the verdict written is the verdict read, at the same revision.

The seam under test is the one between `assessment.py` (what the Safety
Engine says) and `decision_readiness.state` (what the readiness engine
reads). Every test here goes through the real codec and the real `load()` —
nothing asserts on the object that was handed to `save()`, because that
object is not what the consumer will see.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from datetime import UTC, datetime

import pytest

from apps.orchestrator.decision_readiness import state as state_mod
from apps.orchestrator.decision_readiness.safety_input import Handoff, SafetyState
from apps.orchestrator.decision_readiness.state import (
    STATE_TTL_SECONDS,
    ConversationState,
    SlotState,
    SlotValue,
    StateLifecycle,
)
from apps.orchestrator.decision_readiness.tests.fakes import FakeRedis
from apps.orchestrator.safety import pre_check as pre_check_mod
from apps.orchestrator.safety import record as record_mod
from apps.orchestrator.safety.assessment import to_readiness_input
from apps.orchestrator.safety.pre_check import SafetyVerdict
from apps.orchestrator.safety.record import record_verdict

CONV = "conv-record"
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    client = FakeRedis()
    monkeypatch.setattr(state_mod, "_redis_client", lambda: client)
    return client


def _result(verdict: SafetyVerdict, matched: list[str] | None = None):
    return pre_check_mod.SafetyResult(
        verdict=verdict, matched_patterns=matched if matched is not None else ["x"]
    )


def _live_state(revision_slots: bool = True) -> ConversationState:
    """A state that already exists, with a slot in it, saved through the real path."""
    revision = state_mod.next_revision(CONV)
    slots = {}
    if revision_slots:
        slots["city"] = SlotValue(state=SlotState.KNOWN, value="Пенза", at_revision=revision)
    state = ConversationState(
        conversation_id=CONV,
        revision=revision,
        slots=slots,
        epoch_started_at_revision=revision,
    )
    state_mod.save(state, now=NOW)
    return state


# ─── the seam ───────────────────────────────────────────────────────────────


class TestTheVerdictWrittenIsTheVerdictRead:
    def test_absent_state_gets_an_epoch_and_the_verdict(self, fake_redis: FakeRedis) -> None:
        assert state_mod.load(CONV).lifecycle is StateLifecycle.ABSENT

        recorded = record_verdict(CONV, _result(SafetyVerdict.CLARIFY), now=NOW)

        found = state_mod.load(CONV)
        assert found.lifecycle is StateLifecycle.LIVE
        assert found.state is not None
        assert found.state.revision == 1
        assert found.state.safety == to_readiness_input(recorded.assessment)
        assert found.state.safety.evaluated_at_revision == found.state.revision
        assert recorded.found is StateLifecycle.ABSENT
        assert recorded.expiry is None

    def test_live_state_keeps_its_slots_and_its_revision(self, fake_redis: FakeRedis) -> None:
        before = _live_state()
        assert before.safety.state is SafetyState.UNKNOWN

        recorded = record_verdict(CONV, _result(SafetyVerdict.ALLOW), now=NOW)

        after = state_mod.load(CONV).state
        assert after is not None
        assert after.revision == before.revision, "the recorder never advances the revision"
        assert after.slots == before.slots, "recording a verdict is not a slot update"
        assert after.safety.state is SafetyState.NORMAL
        assert after.safety.evaluated_at_revision == before.revision
        assert recorded.found is StateLifecycle.LIVE

    @pytest.mark.parametrize(
        ("verdict", "state", "handoff"),
        [
            (SafetyVerdict.ALLOW, SafetyState.NORMAL, Handoff.NONE),
            (SafetyVerdict.CLARIFY, SafetyState.CLARIFY, Handoff.NONE),
            (SafetyVerdict.BLOCK, SafetyState.STOP, Handoff.NONE),
            (SafetyVerdict.HANDOFF, SafetyState.STOP, Handoff.REQUIRED),
        ],
    )
    def test_every_verdict_survives_the_round_trip_with_its_promise(
        self, fake_redis: FakeRedis, verdict: SafetyVerdict, state: SafetyState, handoff: Handoff
    ) -> None:
        record_verdict(CONV, _result(verdict), now=NOW)
        read = state_mod.load(CONV).state
        assert read is not None
        assert (read.safety.state, read.safety.handoff) == (state, handoff)

    def test_the_two_stops_are_still_two_after_redis(self, fake_redis: FakeRedis) -> None:
        """§127 across the seam, not only inside `assessment.py`."""
        record_verdict("conv-block", _result(SafetyVerdict.BLOCK), now=NOW)
        record_verdict("conv-handoff", _result(SafetyVerdict.HANDOFF), now=NOW)
        block = state_mod.load("conv-block").state
        handoff = state_mod.load("conv-handoff").state
        assert block is not None and handoff is not None
        assert block.safety.state is handoff.safety.state is SafetyState.STOP
        assert block.safety.handoff is Handoff.NONE
        assert handoff.safety.handoff is Handoff.REQUIRED

    def test_the_consumer_reads_it_as_fresh(self, fake_redis: FakeRedis) -> None:
        """The engine's own P3 row: `evaluated_at_revision < state_revision` is
        the staleness it blocks on. After a record, that row must not fire."""
        record_verdict(CONV, _result(SafetyVerdict.ALLOW), now=NOW)
        read = state_mod.load(CONV).state
        assert read is not None
        assert read.safety.evaluated_at_revision is not None
        assert not (read.safety.evaluated_at_revision < read.revision)


# ─── expiry is an event ─────────────────────────────────────────────────────


class TestAnExpiredStateIsAnEventNotAnInheritance:
    def test_expired_state_opens_a_new_epoch_above_the_old_revision(
        self, fake_redis: FakeRedis
    ) -> None:
        old = _live_state()
        record_verdict(CONV, _result(SafetyVerdict.HANDOFF), now=NOW)
        fake_redis.expire_key(state_mod._state_key(CONV))
        assert state_mod.load(CONV).lifecycle is StateLifecycle.EXPIRED

        recorded = record_verdict(CONV, _result(SafetyVerdict.ALLOW), now=NOW)

        assert recorded.found is StateLifecycle.EXPIRED
        assert recorded.expiry is not None
        assert recorded.expiry.last_revision == old.revision
        read = state_mod.load(CONV).state
        assert read is not None
        assert read.revision > old.revision, "numbering continues, it does not restart"
        assert read.slots == {}, "a new epoch inherits nothing"
        # And the crisis verdict of the previous epoch is NOT what the new one carries.
        assert read.safety.state is SafetyState.NORMAL
        assert read.safety.handoff is Handoff.NONE

    def test_the_two_hour_bound_is_the_states_own_ttl(self, fake_redis: FakeRedis) -> None:
        """§123's two hours are not re-implemented here; `save()` owns them."""
        record_verdict(CONV, _result(SafetyVerdict.ALLOW), now=NOW)
        assert fake_redis.ttls[state_mod._state_key(CONV)] == STATE_TTL_SECONDS == 2 * 3600


# ─── nothing is swallowed, nothing is invented ──────────────────────────────


class TestTheRecorderHidesNothing:
    def test_a_failed_write_leaves_the_call(
        self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _live_state()

        def refuse(*_a, **_k):
            raise ConnectionError("redis down")

        monkeypatch.setattr(fake_redis, "set", refuse)
        with pytest.raises(ConnectionError):
            record_verdict(CONV, _result(SafetyVerdict.ALLOW), now=NOW)

    def test_an_unmappable_verdict_writes_nothing(self, fake_redis: FakeRedis) -> None:
        _live_state()
        before = fake_redis.values[state_mod._state_key(CONV)]
        with pytest.raises(KeyError):
            record_verdict(CONV, _result("not-a-verdict"), now=NOW)  # type: ignore[arg-type]
        assert fake_redis.values[state_mod._state_key(CONV)] == before

    def test_no_try_and_no_default_in_the_recorder(self) -> None:
        """Read on the tree, not the prose — the docstring above names both words."""
        tree = ast.parse(textwrap.dedent(inspect.getsource(record_verdict)))
        fn = tree.body[0]
        assert isinstance(fn, ast.FunctionDef)
        body = [
            n
            for n in fn.body
            if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))
        ]
        kinds = {type(n).__name__ for stmt in body for n in ast.walk(stmt)}
        assert body, "the function has code"
        assert "Try" not in kinds
        assert "BoolOp" not in kinds, "an `or` here is where a default would hide"
        called = {
            n.func.attr
            for stmt in body
            for n in ast.walk(stmt)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "not_evaluated" not in called, "the recorder records evaluations, not their absence"

    def test_the_wall_clock_stays_out_of_the_payload(self, fake_redis: FakeRedis) -> None:
        recorded = record_verdict(CONV, _result(SafetyVerdict.ALLOW), now=NOW)
        assert recorded.assessment.evaluated_at == NOW
        raw = fake_redis.values[state_mod._state_key(CONV)]
        safety_payload = raw.split('"safety"', 1)[1]
        # Presence first: the payload is there and carries the verdict …
        assert '"normal"' in safety_payload
        # … and the clock is in the blob (last_activity_at) but not in the verdict.
        assert NOW.isoformat() in raw
        assert NOW.isoformat() not in safety_payload

    def test_the_module_exports_exactly_the_seam(self) -> None:
        assert record_mod.__all__ == ["Recorded", "record_verdict"]
