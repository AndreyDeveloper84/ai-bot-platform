"""`state_revision` is monotone, atomic, and starts at 1 — gap G1, precondition P1.

Spec §4.1 P1 makes this a precondition of the whole engine: without a monotone
revision neither the idempotency key (§17.3) nor "is this callback stale"
(canon §13.4) can be computed. Verified absent on `883b7539` before this module
existed — `git grep state_revision -- apps/` returned nothing.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.decision_readiness import state as state_mod
from apps.orchestrator.decision_readiness.tests.fakes import FakeRedis


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    client = FakeRedis()
    monkeypatch.setattr(state_mod, "_redis_client", lambda: client)
    return client


def test_first_revision_is_one(fake_redis: FakeRedis) -> None:
    assert state_mod.next_revision("conv-1") == 1


def test_revisions_are_strictly_increasing(fake_redis: FakeRedis) -> None:
    handed_out = [state_mod.next_revision("conv-1") for _ in range(5)]

    assert handed_out == [1, 2, 3, 4, 5]
    # The positive guard rule (`docs/EXECUTOR-RULES.md` §3): `>= previous`
    # would stay green on a counter that never moved.
    assert len(set(handed_out)) == len(handed_out)


def test_two_conversations_do_not_share_a_counter(fake_redis: FakeRedis) -> None:
    assert state_mod.next_revision("conv-a") == 1
    assert state_mod.next_revision("conv-b") == 1
    assert state_mod.next_revision("conv-a") == 2


def test_counter_carries_the_24h_horizon_not_the_2h_ttl(fake_redis: FakeRedis) -> None:
    """The counter must outlive the state blob, or expiry stops being reportable."""

    state_mod.next_revision("conv-1")

    assert fake_redis.ttls[state_mod._revision_key("conv-1")] == state_mod.REVISION_HORIZON_SECONDS
    assert state_mod.REVISION_HORIZON_SECONDS > state_mod.STATE_TTL_SECONDS


def test_od_dr_4_ttls_are_the_ruled_values() -> None:
    """OD-DR-4 (CLOSED): ConversationState 2h, ResumeSummary 24h.

    `MEASUREMENT_DECISION_READINESS_CURRENT.md:552-556`. A guard, not decoration:
    these two numbers are an owner decision, and a silent edit to either is a
    change to a closed ruling rather than a tuning knob.
    """

    assert state_mod.STATE_TTL_SECONDS == 2 * 3600
    assert state_mod.REVISION_HORIZON_SECONDS == 24 * 3600


def test_peek_does_not_consume(fake_redis: FakeRedis) -> None:
    state_mod.next_revision("conv-1")

    assert state_mod.peek_revision("conv-1") == 1
    assert state_mod.peek_revision("conv-1") == 1
    assert state_mod.next_revision("conv-1") == 2


def test_peek_is_none_past_the_horizon(fake_redis: FakeRedis) -> None:
    state_mod.next_revision("conv-1")
    fake_redis.expire_key(state_mod._revision_key("conv-1"))

    assert state_mod.peek_revision("conv-1") is None
