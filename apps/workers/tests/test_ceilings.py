"""Tests for D-2 operator-side ceilings (issue #500).

Pins the rate-budget contract:
- 100 emits per (handler, hour) by default
- 101st returns False (caller drops the emit)
- WARNING logged once on the exact transition
- Disabled when limit <= 0
- Fail-open on Redis unreachable
- Per-handler independent budgets
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from apps.workers.ceilings import should_emit_tenant_missing


# ---------------------------------------------------------------------------
# Fake Redis — minimal INCR + EXPIRE surface
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Tiny in-memory stand-in for the parts of redis-py we use."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.expires: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key: str, ttl: int) -> bool:
        self.expires[key] = ttl
        return True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr("apps.ingress.streams._client", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_first_call_allows_emit(self, settings, fake_redis):
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100
        assert should_emit_tenant_missing("MaxHandler") is True

    def test_under_budget_all_allowed(self, settings, fake_redis):
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 5
        for _ in range(5):
            assert should_emit_tenant_missing("MaxHandler") is True

    def test_at_budget_boundary_blocks(self, settings, fake_redis):
        """101st call (when limit=100) blocks. The 100th still passes —
        ``> limit`` not ``>= limit``."""
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 3
        assert should_emit_tenant_missing("MaxHandler") is True  # 1
        assert should_emit_tenant_missing("MaxHandler") is True  # 2
        assert should_emit_tenant_missing("MaxHandler") is True  # 3 — at boundary
        assert should_emit_tenant_missing("MaxHandler") is False  # 4 — over

    def test_ttl_always_reset_idempotent(self, settings, fake_redis):
        """TTL is reset on EVERY INCR — idempotent and survives a process
        crash mid-INCR-EXPIRE (counter without TTL would permastick a
        handler's budget at 0). Code Reviewer adversarial pass on #528.
        Trade-off: one extra Redis RTT per emit at the ceiling rate (100/hr)
        vs. unrecoverable permabudgeted state on crash. RTT loss is in
        the noise at this rate."""
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100
        should_emit_tenant_missing("MaxHandler")
        first_key = next(iter(fake_redis.expires))
        assert fake_redis.expires[first_key] == 3600

        # Second call: TTL re-set (also 3600 — sliding by tens of ms is
        # fine, and "always reset" is the recoverable behaviour).
        fake_redis.expires.clear()
        should_emit_tenant_missing("MaxHandler")
        assert fake_redis.expires == {first_key: 3600}

    def test_per_handler_budgets_are_independent(self, settings, fake_redis):
        """MaxHandler and TelegramHandler get their own buckets — one
        handler's deluge can't starve another."""
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 2
        # Exhaust MaxHandler's budget.
        assert should_emit_tenant_missing("MaxHandler") is True
        assert should_emit_tenant_missing("MaxHandler") is True
        assert should_emit_tenant_missing("MaxHandler") is False  # over
        # TelegramHandler still has full budget.
        assert should_emit_tenant_missing("TelegramHandler") is True
        assert should_emit_tenant_missing("TelegramHandler") is True


# ---------------------------------------------------------------------------
# Ceiling transition logging
# ---------------------------------------------------------------------------


class TestCeilingTransitionLog:
    def test_logs_warning_once_at_transition(self, settings, fake_redis, caplog):
        """The first DENIED call logs WARNING — operators can grep for
        the trigger. Subsequent denied calls do NOT re-log (would defeat
        the rate limit by emitting via the logger pipeline)."""
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 2
        # Exhaust budget.
        should_emit_tenant_missing("MaxHandler")
        should_emit_tenant_missing("MaxHandler")
        with caplog.at_level(logging.WARNING, logger="apps.workers.ceilings"):
            # Trigger: count == limit + 1 == 3
            result = should_emit_tenant_missing("MaxHandler")
            assert result is False
            assert any(
                "tenant_missing_rate_exceeded" in rec.message and "MaxHandler" in rec.message
                for rec in caplog.records
            )
            caplog.clear()
            # Subsequent denied call: NO new WARNING.
            should_emit_tenant_missing("MaxHandler")
            assert all("tenant_missing_rate_exceeded" not in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Disable / fail-open paths
# ---------------------------------------------------------------------------


class TestDisableEscapeHatch:
    def test_limit_zero_disables_ceiling(self, settings, fake_redis):
        """0 = disabled. Function returns True without touching Redis —
        verified by no entries in the fake store."""
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 0
        for _ in range(50):
            assert should_emit_tenant_missing("MaxHandler") is True
        assert fake_redis.store == {}

    def test_negative_limit_disables_ceiling(self, settings, fake_redis):
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = -1
        assert should_emit_tenant_missing("MaxHandler") is True
        assert fake_redis.store == {}


class TestFailOpen:
    @pytest.fixture(autouse=True)
    def _reset_dedup_state(self):
        """Each test starts with a clean fail-open dedup map.

        Module-level _last_fail_open_log_at persists across test cases;
        without this reset a later test sees the prior test's WARNING
        suppressed by the 60s dedup window.
        """
        from apps.workers import ceilings

        ceilings._last_fail_open_log_at.clear()
        yield
        ceilings._last_fail_open_log_at.clear()

    def test_redis_client_raises_returns_true(self, settings, monkeypatch, caplog):
        """If the Redis client construction itself blows up, ceiling is
        bypassed — telemetry is observability, not the critical path."""
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100

        def _raise() -> None:
            raise RuntimeError("redis down")

        monkeypatch.setattr("apps.ingress.streams._client", _raise)
        with caplog.at_level(logging.WARNING, logger="apps.workers.ceilings"):
            assert should_emit_tenant_missing("MaxHandler") is True
            assert any("redis_unavailable" in rec.message for rec in caplog.records)

    def test_incr_raises_returns_true(self, settings, monkeypatch, caplog):
        """INCR throwing (e.g. transient Redis error mid-operation) →
        fail-open. Better to risk audit-table spike than lose visibility."""
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100
        broken_redis = MagicMock()
        broken_redis.incr.side_effect = RuntimeError("INCR timeout")
        monkeypatch.setattr("apps.ingress.streams._client", lambda: broken_redis)
        with caplog.at_level(logging.WARNING, logger="apps.workers.ceilings"):
            assert should_emit_tenant_missing("MaxHandler") is True
            assert any("incr_failed" in rec.message for rec in caplog.records)

    def test_connection_error_clears_lru_cache(self, settings, monkeypatch):
        """AS2 (PR #528 tech-lead pass): ConnectionError during INCR must
        clear the lru_cache so the NEXT call rebuilds the connection pool.

        Without this fix a transient Redis blip poisons the cached pool —
        cached dead sockets serve every subsequent emit, fail-open path
        fires every time, rate budget permanently bypassed on the worker
        until restart. Exactly the window when ceilings are needed.
        """
        import redis.exceptions

        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100

        cache_clears: list[bool] = []
        broken = MagicMock()
        broken.incr.side_effect = redis.exceptions.ConnectionError("connection refused")

        def _client_factory():
            return broken

        # Mimic the real lru_cache-wrapped callable: ``_client.cache_clear``
        # is what production code calls. Use SimpleNamespace-like mock so
        # the attribute exists for the production cache_clear() call.
        _client_factory.cache_clear = lambda: cache_clears.append(True)  # type: ignore[attr-defined]
        monkeypatch.setattr("apps.ingress.streams._client", _client_factory)

        # First call: ConnectionError → fail-open returns True, cache_clear called.
        assert should_emit_tenant_missing("MaxHandler") is True
        assert cache_clears == [True], "AS2: ConnectionError must trigger cache_clear()"

    def test_non_connection_error_does_not_clear_cache(self, settings, monkeypatch):
        """AS2 fix scoped narrowly — a non-connection exception (e.g.
        DataError on a malformed key) should NOT clear the cache. The
        pool is still healthy, only the operation is broken."""
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100

        cache_clears: list[bool] = []
        broken = MagicMock()
        broken.incr.side_effect = RuntimeError("DataError: malformed key")

        def _client_factory():
            return broken

        _client_factory.cache_clear = lambda: cache_clears.append(True)  # type: ignore[attr-defined]
        monkeypatch.setattr("apps.ingress.streams._client", _client_factory)

        assert should_emit_tenant_missing("MaxHandler") is True
        assert cache_clears == [], "Generic errors must NOT clear cache (AS2 narrow scope)"


class TestFailOpenLogDedup:
    """The fail-open WARNING logs must dedup on a 60s window so a sustained
    Redis outage doesn't flood the worker log at the full emit rate.
    Code Reviewer §H.3 follow-up on PR #528 (#500)."""

    @pytest.fixture(autouse=True)
    def _reset_dedup_state(self):
        from apps.workers import ceilings

        ceilings._last_fail_open_log_at.clear()
        yield
        ceilings._last_fail_open_log_at.clear()

    def test_redis_unavailable_logged_once_per_window(self, settings, monkeypatch, caplog):
        """100 calls during a Redis outage → exactly ONE WARNING line."""
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100

        def _raise() -> None:
            raise RuntimeError("redis down")

        monkeypatch.setattr("apps.ingress.streams._client", _raise)
        with caplog.at_level(logging.WARNING, logger="apps.workers.ceilings"):
            for _ in range(100):
                should_emit_tenant_missing("MaxHandler")
        unavailable_lines = [r for r in caplog.records if "redis_unavailable" in r.message]
        assert len(unavailable_lines) == 1

    def test_incr_failed_deduped_per_handler(self, settings, monkeypatch, caplog):
        """Different handlers get independent dedup keys — one floods,
        the other still gets its first WARNING through."""
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100
        broken = MagicMock()
        broken.incr.side_effect = RuntimeError("INCR boom")
        monkeypatch.setattr("apps.ingress.streams._client", lambda: broken)

        with caplog.at_level(logging.WARNING, logger="apps.workers.ceilings"):
            for _ in range(10):
                should_emit_tenant_missing("MaxHandler")
            for _ in range(10):
                should_emit_tenant_missing("TelegramHandler")

        max_warns = [
            r for r in caplog.records if "incr_failed" in r.message and "MaxHandler" in r.message
        ]
        tg_warns = [
            r
            for r in caplog.records
            if "incr_failed" in r.message and "TelegramHandler" in r.message
        ]
        assert len(max_warns) == 1
        assert len(tg_warns) == 1

    def test_dedup_window_releases_after_elapsed_time(self, settings, monkeypatch, caplog):
        """After the 60s window elapses, the next call logs again."""
        from apps.workers import ceilings

        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100

        def _raise() -> None:
            raise RuntimeError("down")

        monkeypatch.setattr("apps.ingress.streams._client", _raise)

        # Freeze monotonic to a controllable value.
        fake_time = [1000.0]
        monkeypatch.setattr(ceilings.time, "monotonic", lambda: fake_time[0])

        with caplog.at_level(logging.WARNING, logger="apps.workers.ceilings"):
            should_emit_tenant_missing("MaxHandler")  # log #1
            fake_time[0] += 30.0  # still within window
            should_emit_tenant_missing("MaxHandler")  # suppressed
            fake_time[0] += 31.0  # past 60s window (now at +61s)
            should_emit_tenant_missing("MaxHandler")  # log #2

        unavailable_lines = [r for r in caplog.records if "redis_unavailable" in r.message]
        assert len(unavailable_lines) == 2
