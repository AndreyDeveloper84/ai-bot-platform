"""Tests for Redis Streams enqueue (DRF-424 / C2).

Uses a `fakeredis` substitute via monkeypatch so tests don't depend
on a running Redis. CI's docker compose Redis service handles the
real integration test in G1 (E2E webhook → consumer).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import redis

from apps.events.models import Event
from apps.ingress import streams
from apps.ingress.streams import DEFAULT_GROUP_NAME, enqueue
from apps.tenancy.context import trace_id_scope

pytestmark = pytest.mark.django_db


class _FakeRedis:
    """Minimal in-memory stub of the redis.Redis API we use.

    Just enough surface to verify behaviour: XGROUP CREATE (idempotent),
    XADD (returns generated entry id), XRANGE (read-back).
    """

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.groups: dict[str, set[str]] = {}
        self._counter = 0

    def xgroup_create(self, name: str, groupname: str, id: str, mkstream: bool):  # noqa: A002 — match redis.Redis API
        existing = self.groups.setdefault(name, set())
        if groupname in existing:
            raise redis.ResponseError(f"BUSYGROUP Consumer Group name already exists for {name}")
        existing.add(groupname)
        self.streams.setdefault(name, [])

    def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self._counter += 1
        entry_id = f"1700000000-{self._counter}"
        self.streams.setdefault(stream, []).append((entry_id, dict(fields)))
        return entry_id

    def xrange(self, stream: str) -> list[tuple[str, dict[str, str]]]:
        return list(self.streams.get(stream, []))


@pytest.fixture
def fake_redis(monkeypatch):
    """Replace ``streams._client`` with a fresh _FakeRedis per test."""

    fake = _FakeRedis()
    # The module caches the client via lru_cache; bypass by patching
    # the function itself.
    monkeypatch.setattr(streams, "_client", lambda: fake)
    return fake


class TestEnqueue:
    def test_xadd_writes_entry(self, fake_redis):
        entry_id = enqueue("max", {"hello": "world"})
        assert entry_id.startswith("1700000000-")
        entries = fake_redis.xrange("ingress:max")
        assert len(entries) == 1
        eid, fields = entries[0]
        assert eid == entry_id
        assert json.loads(fields["data"]) == {"hello": "world"}

    def test_creates_consumer_group_first_time(self, fake_redis):
        enqueue("max", {})
        assert "ingress:max" in fake_redis.groups
        assert DEFAULT_GROUP_NAME in fake_redis.groups["ingress:max"]

    def test_subsequent_enqueue_doesnt_re_create_group(self, fake_redis):
        # First enqueue creates the group.
        enqueue("max", {"event": 1})
        # A second enqueue must NOT raise BUSYGROUP — should be swallowed.
        entry2 = enqueue("max", {"event": 2})
        assert entry2 is not None
        assert len(fake_redis.xrange("ingress:max")) == 2

    def test_trace_id_promoted_to_top_level(self, fake_redis):
        with trace_id_scope("trace-streams"):
            enqueue("max", {"event": "x"})
        entries = fake_redis.xrange("ingress:max")
        _, fields = entries[0]
        assert fields["trace_id"] == "trace-streams"

    def test_trace_id_generated_when_no_context(self, fake_redis):
        # No trace_id in context → enqueue generates a fallback UUID.
        enqueue("max", {})
        entries = fake_redis.xrange("ingress:max")
        _, fields = entries[0]
        assert fields["trace_id"]  # non-empty
        # UUID4 has 4 hyphens.
        assert fields["trace_id"].count("-") == 4

    def test_different_channels_use_separate_streams(self, fake_redis):
        enqueue("max", {"x": 1})
        enqueue("telegram", {"y": 2})
        assert "ingress:max" in fake_redis.streams
        assert "ingress:telegram" in fake_redis.streams
        assert len(fake_redis.xrange("ingress:max")) == 1
        assert len(fake_redis.xrange("ingress:telegram")) == 1

    def test_payload_json_serialisation_handles_unicode(self, fake_redis):
        enqueue("max", {"text": "Привет 🌟"})
        _, fields = fake_redis.xrange("ingress:max")[0]
        assert json.loads(fields["data"])["text"] == "Привет 🌟"

    def test_payload_json_serialisation_handles_complex_types(self, fake_redis):
        # `default=str` in the json.dumps call handles UUID / datetime
        # so callers don't have to pre-serialise.
        from uuid import uuid4

        u = uuid4()
        enqueue("max", {"id": u, "nested": {"k": [1, 2, 3]}})
        _, fields = fake_redis.xrange("ingress:max")[0]
        decoded = json.loads(fields["data"])
        assert decoded["id"] == str(u)
        assert decoded["nested"]["k"] == [1, 2, 3]


class TestEventsEmitted:
    def test_emits_ingress_enqueued_event(self, fake_redis):
        with trace_id_scope("trace-emit-1"):
            enqueue("max", {})
        events = Event.objects.filter(event_type="ingress.enqueued")
        assert events.count() == 1
        ev = events.first()
        assert ev.payload["channel"] == "max"
        assert ev.payload["stream"] == "ingress:max"
        assert ev.trace_id == "trace-emit-1"


class TestUnexpectedRedisError:
    """Non-BUSYGROUP errors must propagate."""

    def test_unexpected_xgroup_error_propagates(self, monkeypatch):
        fake = MagicMock()
        fake.xgroup_create.side_effect = redis.ResponseError("WRONGTYPE op against stream")
        monkeypatch.setattr(streams, "_client", lambda: fake)

        with pytest.raises(redis.ResponseError, match="WRONGTYPE"):
            enqueue("max", {})
