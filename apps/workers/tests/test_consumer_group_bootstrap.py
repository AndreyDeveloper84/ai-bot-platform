"""A newly registered stream must not stop the working ones (DRF-1061).

### The failure this pins

``consume_once`` reads every registered stream in ONE ``XREADGROUP``, and
Redis answers one command with one reply: if any single stream in the list
has no consumer group, the whole call returns ``NOGROUP`` and none of the
other streams are served.

Groups are created lazily by ``enqueue``, on the first webhook for that
channel. So a stream that is registered but has never received traffic has
no group — and merely registering its handler stops the already-working
streams from being drained. The consumer then raises on every poll,
crash-loops under ``restart: unless-stopped``, and the live client bot goes
quiet with nothing in the logs pointing at the new handler.

Verified against the pilot's Redis before writing this: a mixed read of
``ingress:max_global`` (live, 246 entries) plus a non-existent stream
returns

    NOGROUP No such key 'ingress:probe-nonexistent' or consumer group
    'consumers' in XREADGROUP with GROUP option

### Why the existing suite missed it

``_FakeStreamRedis`` in ``test_consumer.py`` silently ``continue``s over a
stream with no group, so every consumer test passes whether or not the
groups exist. That stub cannot fail this way. The fake here is deliberately
stricter — it raises ``NOGROUP`` exactly where the real server does.
"""

from __future__ import annotations

from typing import Any

import pytest
import redis

from apps.ingress import streams
from apps.workers import consumer
from apps.workers.base import TenantAwareTask
from apps.workers.registry import clear_registry, register

pytestmark = pytest.mark.django_db


class _StrictRedis:
    """Fake that fails like the real server when a group is missing."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.groups: set[tuple[str, str]] = set()
        self.created_groups: list[tuple[str, str]] = []

    def xgroup_create(self, name: str, groupname: str, id: str = "$", mkstream: bool = False):  # noqa: A002
        if (name, groupname) in self.groups:
            raise redis.ResponseError("BUSYGROUP Consumer Group name already exists")
        self.groups.add((name, groupname))
        self.created_groups.append((name, groupname))
        self.streams.setdefault(name, [])

    def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[str, str],  # noqa: A002
        count: int = 10,
        block: int = 0,
    ) -> list[Any]:
        # One command, one reply: the first missing group fails everything.
        for stream_name in streams:
            if (stream_name, groupname) not in self.groups:
                raise redis.ResponseError(
                    f"NOGROUP No such key '{stream_name}' or consumer group "
                    f"'{groupname}' in XREADGROUP with GROUP option"
                )
        return []

    def xack(self, *args, **kwargs) -> int:
        return 1


@pytest.fixture
def strict_redis(monkeypatch) -> _StrictRedis:
    fake = _StrictRedis()
    monkeypatch.setattr(streams, "_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


def _register(stream: str) -> None:
    @register(stream)
    class _Handler(TenantAwareTask):
        requires_tenant = False

        def handle(self, payload: dict[str, Any]) -> None:  # pragma: no cover
            pass


class TestGroupBootstrap:
    def test_a_never_used_stream_does_not_break_the_read(self, strict_redis):
        """The regression: registering a new handler must be survivable."""

        _register("ingress:max_salon")

        # Must not raise. Before the fix this was NOGROUP on every poll.
        assert consumer.consume_once() == 0

    def test_a_new_stream_does_not_starve_the_live_one(self, strict_redis):
        # The live stream already has its group, created by enqueue long ago.
        strict_redis.xgroup_create("ingress:max_global", "consumers")
        _register("ingress:max_global")
        _register("ingress:max_salon")

        assert consumer.consume_once() == 0
        # Both are readable now, so the client bot keeps being drained.
        assert ("ingress:max_salon", "consumers") in strict_redis.groups

    def test_groups_are_created_for_every_registered_stream(self, strict_redis):
        _register("ingress:max")
        _register("ingress:max_global")
        _register("ingress:max_salon")

        consumer.consume_once()

        assert {name for name, _ in strict_redis.created_groups} == {
            "ingress:max",
            "ingress:max_global",
            "ingress:max_salon",
        }

    def test_existing_groups_are_left_alone(self, strict_redis):
        # BUSYGROUP is swallowed — the call is idempotent per poll, and a
        # second poll must not error or reset anything.
        _register("ingress:max_salon")

        consumer.consume_once()
        consumer.consume_once()

        assert strict_redis.created_groups == [("ingress:max_salon", "consumers")]

    def test_explicit_stream_argument_is_also_bootstrapped(self, strict_redis):
        # `--streams` narrows the target list; those still need groups.
        assert consumer.consume_once(streams=["ingress:max_salon"]) == 0
        assert ("ingress:max_salon", "consumers") in strict_redis.groups
