"""Short-term Redis memory primitive tests (DRF-454 / Sprint 2 / C1).

Uses a `_FakeRedis` stub for hermetic testing — same pattern as Sprint 1
G3 idempotency-race tests. No live Redis required in CI; tests assert
the LIST/LTRIM/EXPIRE contract rather than Redis itself.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest

from apps.audit.models import AuditLog
from apps.orchestrator.memory import short_term
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


class _FakeRedis:
    """Minimal in-memory Redis surface — LIST + EXPIRE only.

    Pipeline is a no-op wrapper that buffers commands and replays them
    on .execute(); matches the contract short_term.append uses.
    """

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.ttls: dict[str, int] = {}
        self.deletions: list[str] = []
        self._pending: list[tuple[str, tuple[Any, ...]]] = []

    def pipeline(self) -> "_FakeRedis":
        self._pending = []
        return self

    def rpush(self, key: str, value: str) -> None:
        self._pending.append(("rpush", (key, value)))

    def ltrim(self, key: str, start: int, end: int) -> None:
        self._pending.append(("ltrim", (key, start, end)))

    def expire(self, key: str, ttl: int) -> None:
        self._pending.append(("expire", (key, ttl)))

    def execute(self) -> list[Any]:
        for cmd, args in self._pending:
            if cmd == "rpush":
                key, value = args
                self.lists.setdefault(key, []).append(value)
            elif cmd == "ltrim":
                key, start, end = args
                lst = self.lists.get(key, [])
                # Negative indexing: Python slice already matches Redis
                # LTRIM semantics for `-N, -1` (last N items).
                if end == -1:
                    self.lists[key] = lst[start:]
                else:
                    self.lists[key] = lst[start : end + 1 if end >= 0 else len(lst) + end + 1]
            elif cmd == "expire":
                key, ttl = args
                self.ttls[key] = ttl
        self._pending = []
        return []

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        lst = self.lists.get(key, [])
        if end == -1:
            return lst[start:]
        return lst[start : end + 1]

    def delete(self, key: str) -> None:
        self.deletions.append(key)
        self.lists.pop(key, None)
        self.ttls.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def tenant_a() -> Tenant:
    return Tenant.objects.create(slug="memory-a", name="A")


class TestAppendRecallRoundtrip:
    def test_recall_returns_appended_messages_in_order(self, fake_redis, tenant_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        conv_id = uuid4()
        with tenant_scope(tenant_a):
            short_term.append(conv_id, role="user", content="hi")
            short_term.append(conv_id, role="assistant", content="hello")
            short_term.append(conv_id, role="user", content="how are you")

            msgs = short_term.recall(conv_id)
        assert [m["content"] for m in msgs] == ["hi", "hello", "how are you"]
        assert [m["role"] for m in msgs] == ["user", "assistant", "user"]

    def test_extras_round_trip(self, fake_redis, tenant_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        conv_id = uuid4()
        with tenant_scope(tenant_a):
            short_term.append(conv_id, role="user", content="x", trace_id="trace-1", token_count=5)
            msgs = short_term.recall(conv_id)
        assert msgs[0]["trace_id"] == "trace-1"
        assert msgs[0]["token_count"] == 5


class TestSlidingWindow:
    def test_ltrim_enforces_depth(self, fake_redis, tenant_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        settings.SHORT_TERM_MEMORY_DEPTH = 3
        conv_id = uuid4()
        with tenant_scope(tenant_a):
            for i in range(5):
                short_term.append(conv_id, role="user", content=f"msg-{i}")
            msgs = short_term.recall(conv_id)
        # Only last 3 retained.
        assert [m["content"] for m in msgs] == ["msg-2", "msg-3", "msg-4"]

    def test_recall_n_limits_returned(self, fake_redis, tenant_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        settings.SHORT_TERM_MEMORY_DEPTH = 10
        conv_id = uuid4()
        with tenant_scope(tenant_a):
            for i in range(5):
                short_term.append(conv_id, role="user", content=f"m-{i}")
            last_two = short_term.recall(conv_id, n=2)
        assert [m["content"] for m in last_two] == ["m-3", "m-4"]


class TestTtl:
    def test_expire_called_on_every_append(self, fake_redis, tenant_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        settings.SHORT_TERM_MEMORY_TTL_SECONDS = 7200
        conv_id = uuid4()
        with tenant_scope(tenant_a):
            short_term.append(conv_id, role="user", content="x")
        key = short_term._key(conv_id)
        assert fake_redis.ttls[key] == 7200


class TestClear:
    def test_clear_deletes_key_and_audits(self, fake_redis, tenant_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        conv_id = uuid4()
        with tenant_scope(tenant_a):
            short_term.append(conv_id, role="user", content="x")
            short_term.clear(conv_id)
        # Empty recall after clear.
        assert short_term.recall(conv_id) == []
        # Audit row for clear.
        assert AuditLog.all_tenants.filter(action="memory.cleared").exists()


class TestAuditEmission:
    def test_append_writes_audit_without_raw_content(self, fake_redis, tenant_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        conv_id = uuid4()
        with tenant_scope(tenant_a):
            short_term.append(conv_id, role="user", content="секрет фразы")
        rows = AuditLog.all_tenants.filter(action="memory.append")
        assert rows.exists()
        row = rows.first()
        # Raw content body must NOT be in payload.
        payload = row.payload
        assert "секрет фразы" not in json.dumps(payload, ensure_ascii=False)
        # Metadata is.
        assert payload["role"] == "user"
        assert payload["content_length"] == len("секрет фразы")


class TestRobustness:
    def test_malformed_item_is_skipped_not_raised(self, fake_redis, tenant_a, settings, caplog):
        settings.STRICT_TENANT_SCOPE = "strict"
        conv_id = uuid4()
        key = short_term._key(conv_id)
        # Inject one good + one malformed item directly.
        fake_redis.lists[key] = [
            '{"role": "user", "content": "ok"}',
            "not-json-at-all",
        ]
        with tenant_scope(tenant_a):
            msgs = short_term.recall(conv_id)
        assert len(msgs) == 1
        assert msgs[0]["content"] == "ok"
        assert any("malformed" in r.getMessage() for r in caplog.records)
