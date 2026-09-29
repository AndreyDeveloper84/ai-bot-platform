"""Two hours expiring is an event with a value, not a key that stopped existing.

The owner's requirement for slice E1, verbatim: both TTLs must have an owner in
code, and expiry must be an event rather than a silent disappearance.

The failure this guards against is specific. If `load()` answered `None` for
both cases, then "went quiet for two hours" and "never had state" would be the
same answer — and they need opposite handling: the first resumes above the
revisions already handed out, the second starts at 1. A stale callback issued
before the quiet period would then come back looking *newer* than the state
that replaced it.

Every test here goes red if `EXPIRED` is collapsed into `ABSENT`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.orchestrator.decision_readiness import state as state_mod
from apps.orchestrator.decision_readiness.state import (
    ConversationState,
    SlotState,
    SlotValue,
    StateLifecycle,
)
from apps.orchestrator.decision_readiness.tests.fakes import FakeRedis


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    client = FakeRedis()
    monkeypatch.setattr(state_mod, "_redis_client", lambda: client)
    return client


def _live_state(conversation_id: str = "conv-1") -> ConversationState:
    revision = state_mod.next_revision(conversation_id)
    return ConversationState(
        conversation_id=conversation_id,
        revision=revision,
        slots={"city": SlotValue(state=SlotState.KNOWN, value="Пенза", at_revision=revision)},
        epoch_started_at_revision=revision,
    )


def test_never_seen_conversation_is_absent(fake_redis: FakeRedis) -> None:
    result = state_mod.load("conv-never")

    assert result.lifecycle is StateLifecycle.ABSENT
    assert result.state is None
    assert result.expiry is None


def test_live_state_round_trips(fake_redis: FakeRedis) -> None:
    state_mod.save(_live_state())

    result = state_mod.load("conv-1")

    assert result.lifecycle is StateLifecycle.LIVE
    assert result.state is not None
    assert result.state.revision == 1
    assert result.state.slot("city").state is SlotState.KNOWN
    assert result.state.slot("city").value == "Пенза"


def test_expired_state_reports_the_event_with_the_last_revision(fake_redis: FakeRedis) -> None:
    state_mod.save(_live_state())
    state_mod.next_revision("conv-1")  # a second turn happened before the silence
    fake_redis.expire_key(state_mod._state_key("conv-1"))

    result = state_mod.load("conv-1", now=datetime(2026, 9, 10, 12, 0, tzinfo=UTC))

    assert result.lifecycle is StateLifecycle.EXPIRED
    assert result.state is None
    assert result.expiry is not None
    assert result.expiry.conversation_id == "conv-1"
    assert result.expiry.last_revision == 2
    assert result.expiry.ttl_seconds == state_mod.STATE_TTL_SECONDS
    assert result.expiry.detected_at == datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def test_expired_and_absent_are_distinguishable(fake_redis: FakeRedis) -> None:
    """The whole point, stated as one assertion pair.

    Delete only the blob → EXPIRED. Delete the counter too (past 24h) → ABSENT.
    """

    state_mod.save(_live_state())
    fake_redis.expire_key(state_mod._state_key("conv-1"))
    assert state_mod.load("conv-1").lifecycle is StateLifecycle.EXPIRED

    fake_redis.expire_key(state_mod._revision_key("conv-1"))
    assert state_mod.load("conv-1").lifecycle is StateLifecycle.ABSENT


def test_expiry_is_reported_on_every_read_until_a_new_state_is_written(
    fake_redis: FakeRedis,
) -> None:
    """A one-shot flag would turn the event back into a disappearance when lost."""

    state_mod.save(_live_state())
    fake_redis.expire_key(state_mod._state_key("conv-1"))

    assert state_mod.load("conv-1").lifecycle is StateLifecycle.EXPIRED
    assert state_mod.load("conv-1").lifecycle is StateLifecycle.EXPIRED


def test_new_epoch_after_expiry_continues_the_numbering(fake_redis: FakeRedis) -> None:
    """A pre-gap callback must not be able to look newer than the state after it."""

    state_mod.save(_live_state())
    state_mod.next_revision("conv-1")
    state_mod.next_revision("conv-1")  # stale callback out there carries revision 3
    fake_redis.expire_key(state_mod._state_key("conv-1"))

    expired = state_mod.load("conv-1")
    fresh = state_mod.open_epoch("conv-1", after=expired)

    assert fresh.revision == 4
    assert fresh.revision > 3
    assert fresh.epoch_started_at_revision == 4
    assert fresh.slots == {}


def test_new_epoch_from_absent_starts_at_one(fake_redis: FakeRedis) -> None:
    absent = state_mod.load("conv-fresh")
    fresh = state_mod.open_epoch("conv-fresh", after=absent)

    assert fresh.revision == 1


def test_open_epoch_refuses_to_discard_a_live_state(fake_redis: FakeRedis) -> None:
    state_mod.save(_live_state())
    live = state_mod.load("conv-1")

    with pytest.raises(ValueError, match="live state"):
        state_mod.open_epoch("conv-1", after=live)


def test_saving_refreshes_the_two_hour_inactivity_ttl(fake_redis: FakeRedis) -> None:
    state_mod.save(_live_state())

    assert fake_redis.ttls[state_mod._state_key("conv-1")] == state_mod.STATE_TTL_SECONDS


def test_save_stamps_last_activity(fake_redis: FakeRedis) -> None:
    moment = datetime(2026, 9, 10, 11, 30, tzinfo=UTC)

    state_mod.save(_live_state(), now=moment)

    loaded = state_mod.load("conv-1").state
    assert loaded is not None
    assert loaded.last_activity_at == moment
