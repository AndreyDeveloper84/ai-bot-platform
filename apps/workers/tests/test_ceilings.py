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
    """Tiny in-memory stand-in for the parts of redis-py we use.

    Без ``register_script`` — fallback path (non-atomic INCR + EXPIRE)
    активируется. Это соответствует unit-test семантике: мы хотим
    проверить логику ceiling, не сам Lua механизм. Production redis-py
    имеет register_script и активирует Lua-path; покрывается отдельным
    fake-ом ``_FakeRedisWithLua`` в TestLuaAtomicAcquire.
    """

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
        """INCR throwing (transient Redis error mid-operation) → fail-open.
        Better to risk audit-table spike than lose visibility.

        #574: после Lua-fix-а phantom register_script + script callable
        тоже должен raise exc — иначе fail-open path не активируется
        (Lua path подавит RuntimeError из incr.side_effect)."""
        from apps.workers import ceilings

        ceilings._invalidate_incr_and_expire_script()

        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100
        broken_redis = MagicMock()
        exc = RuntimeError("INCR timeout")
        broken_redis.incr.side_effect = exc

        def _raising_script(*, keys, args):
            raise exc

        broken_redis.register_script = lambda lua: _raising_script

        monkeypatch.setattr("apps.ingress.streams._client", lambda: broken_redis)
        with caplog.at_level(logging.WARNING, logger="apps.workers.ceilings"):
            assert should_emit_tenant_missing("MaxHandler") is True
            assert any("rate_budget_failed" in rec.message for rec in caplog.records)

    def _broken_client_factory(self, exc: Exception):
        """Build a fake `_client` lookalike that raises ``exc`` on the
        rate-budget Redis call (both Lua и fallback пути) и exposes a
        ``cache_clear`` spy.

        #574: после Lua atomic fix-а production-путь идёт через Lua
        callable (``redis.register_script(...)``), не через прямой
        ``redis.incr()``. MagicMock автоматически создаст
        ``register_script`` который возвращает callable mock,
        не raise-ящий — тогда наш fail-open path не сработает, тесты
        упадут с false negative. Чтобы сохранить контракт «exception
        на rate-budget вызове → cache_clear + log», phantom Lua callable
        тоже raise-ит ``exc``.
        """
        broken = MagicMock()
        # Fallback path (no Lua): прямой INCR кидает exc.
        broken.incr.side_effect = exc

        # Lua-path: register_script возвращает callable, который тоже
        # кидает exc при выполнении.
        def _raising_script_callable(*, keys, args):
            raise exc

        broken.register_script = lambda lua_text: _raising_script_callable

        cache_clears: list[bool] = []

        def factory():
            return broken

        factory.cache_clear = lambda: cache_clears.append(True)  # type: ignore[attr-defined]
        # #574: сбросить script cache, иначе предыдущий тест может
        # пере-использовать кэшированный callable.
        from apps.workers import ceilings

        ceilings._invalidate_incr_and_expire_script()
        return factory, cache_clears

    def test_redis_connection_error_clears_lru_cache(self, settings, monkeypatch):
        """AS2 / AS2-NEW (PR #528 round-3): redis.exceptions.ConnectionError
        during INCR triggers cache_clear so the NEXT call rebuilds the
        connection pool. Without this a transient Redis blip permanently
        bypasses the rate budget on the worker until restart."""
        import redis.exceptions

        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100
        factory, cache_clears = self._broken_client_factory(
            redis.exceptions.ConnectionError("connection refused")
        )
        monkeypatch.setattr("apps.ingress.streams._client", factory)

        assert should_emit_tenant_missing("MaxHandler") is True
        assert cache_clears == [True]

    def test_redis_timeout_error_clears_lru_cache(self, settings, monkeypatch):
        """TimeoutError (different class, same transport-fault semantic)
        also clears the cache."""
        import redis.exceptions

        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100
        factory, cache_clears = self._broken_client_factory(
            redis.exceptions.TimeoutError("read timed out")
        )
        monkeypatch.setattr("apps.ingress.streams._client", factory)

        assert should_emit_tenant_missing("MaxHandler") is True
        assert cache_clears == [True]

    def test_redis_busy_loading_error_clears_lru_cache(self, settings, monkeypatch):
        """AS2-NEW round-4: BusyLoadingError surfaces during Redis
        startup / failover (replica promoted, RDB still loading).
        Pool sees stale connection; cache_clear forces rebuild against
        the now-primary endpoint. Sentinel/Cluster failover = NORMAL
        operation in production; this path must clear."""
        import redis.exceptions

        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100
        factory, cache_clears = self._broken_client_factory(
            redis.exceptions.BusyLoadingError("LOADING Redis is loading the dataset in memory")
        )
        monkeypatch.setattr("apps.ingress.streams._client", factory)

        assert should_emit_tenant_missing("MaxHandler") is True
        assert cache_clears == [True]

    def test_redis_cluster_down_error_clears_lru_cache(self, settings, monkeypatch):
        """AS2-NEW round-4: ClusterDownError fires on Redis Cluster
        topology change (slot migration mid-flight). String-match
        heuristic would have MISSED this — name doesn't contain
        'Connection' or 'Timeout'. Typed isinstance fixes the gap."""
        import redis.exceptions

        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100
        factory, cache_clears = self._broken_client_factory(
            redis.exceptions.ClusterDownError("CLUSTERDOWN The cluster is down")
        )
        monkeypatch.setattr("apps.ingress.streams._client", factory)

        assert should_emit_tenant_missing("MaxHandler") is True
        assert cache_clears == [True]

    def test_data_error_does_not_clear_cache(self, settings, monkeypatch):
        """AS2-NEW (round-3): DataError is a redis.RedisError subclass
        but NOT a transport fault — pool is healthy, only the operation
        is broken. Must NOT clear the cache. Regression guard against
        the OLD string-match heuristic that would have flagged any
        exception with 'Error' in the name as a connection fault."""
        import redis.exceptions

        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100
        factory, cache_clears = self._broken_client_factory(
            redis.exceptions.DataError("malformed key")
        )
        monkeypatch.setattr("apps.ingress.streams._client", factory)

        assert should_emit_tenant_missing("MaxHandler") is True
        assert cache_clears == [], "DataError must not clear cache (typed isinstance scope)"

    def test_generic_runtime_error_does_not_clear_cache(self, settings, monkeypatch):
        """A user-defined / library-side RuntimeError is not a Redis
        transport fault; the cache stays. The old string-match
        heuristic would have classified any exception with 'Timeout'
        in its name as a connection fault — typed isinstance fixes that."""
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100
        factory, cache_clears = self._broken_client_factory(
            RuntimeError("unrelated downstream failure")
        )
        monkeypatch.setattr("apps.ingress.streams._client", factory)

        assert should_emit_tenant_missing("MaxHandler") is True
        assert cache_clears == []


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
        from apps.workers import ceilings

        ceilings._invalidate_incr_and_expire_script()

        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100
        broken = MagicMock()
        exc = RuntimeError("INCR boom")
        broken.incr.side_effect = exc

        # #574: phantom Lua callable тоже raise-ит — иначе Lua path
        # подавит exception и fail-open warning не появится.
        def _raising_script(*, keys, args):
            raise exc

        broken.register_script = lambda lua: _raising_script
        monkeypatch.setattr("apps.ingress.streams._client", lambda: broken)

        with caplog.at_level(logging.WARNING, logger="apps.workers.ceilings"):
            for _ in range(10):
                should_emit_tenant_missing("MaxHandler")
            for _ in range(10):
                should_emit_tenant_missing("TelegramHandler")

        max_warns = [
            r
            for r in caplog.records
            if "rate_budget_failed" in r.message and "MaxHandler" in r.message
        ]
        tg_warns = [
            r
            for r in caplog.records
            if "rate_budget_failed" in r.message and "TelegramHandler" in r.message
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


# ───────────────────────────────────────────────────────────────────────
# #574 — Lua atomic INCR+EXPIRE
# ───────────────────────────────────────────────────────────────────────


class _FakeRedisWithLua:
    """Fake redis client который умеет ``register_script`` — активирует
    Lua-path в ``should_emit_tenant_missing``.

    Имитирует redis-py Script callable: фабрика создаёт callable, который
    при вызове выполняет наш Lua верстку «локально» через простые
    incr+expire (мы тестируем что вызов Lua callable идёт правильно, не
    что Redis Lua engine работает — это уже не наша зона).
    """

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.expires: dict[str, int] = {}
        self.script_calls: list[dict] = []
        self.script_load_count = 0

    def register_script(self, lua_text: str):
        self.script_load_count += 1
        self._registered_lua = lua_text

        def _script_callable(*, keys, args):
            # Имитация работы Lua-скрипта: INCR + EXPIRE атомарно.
            self.script_calls.append({"keys": keys, "args": args})
            key = keys[0]
            ttl = int(args[0])
            self.store[key] = self.store.get(key, 0) + 1
            self.expires[key] = ttl
            return self.store[key]

        return _script_callable

    # Fallback методы — НЕ должны вызываться когда register_script
    # доступен. Если в тесте вдруг вызываются — assertion явно поймает.
    def incr(self, key: str) -> int:
        raise AssertionError(
            "INCR не должен вызываться когда register_script доступен — "
            "это означает Lua-path не активировался (#574 регрессия)"
        )

    def expire(self, key: str, ttl: int) -> bool:
        raise AssertionError("EXPIRE не должен вызываться когда register_script доступен")


@pytest.fixture
def fake_redis_lua(monkeypatch):
    fake = _FakeRedisWithLua()
    monkeypatch.setattr("apps.ingress.streams._client", lambda: fake)
    # Сбросить script cache между тестами — иначе registered script от
    # предыдущего теста переиспользуется + script_load_count не растёт.
    from apps.workers import ceilings

    ceilings._invalidate_incr_and_expire_script()
    return fake


class TestLuaAtomicAcquire:
    """#574: атомарный INCR+EXPIRE через Lua-скрипт. Закрывает race
    window между двумя отдельными Redis вызовами — если процесс крашится
    между INCR и EXPIRE, ключ остаётся без TTL и handler permabudgeted.
    Lua выполняется атомарно на сервере: либо обе команды либо ни одна."""

    def test_lua_path_used_when_register_script_available(self, settings, fake_redis_lua):
        """register_script доступен → Lua callable вызывается вместо
        отдельных incr+expire. _FakeRedisWithLua.incr/expire raise-ит
        AssertionError если случайно вызвался — тест автоматически
        падает на любой регрессии."""
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100
        result = should_emit_tenant_missing("MaxHandler")
        assert result is True
        assert len(fake_redis_lua.script_calls) == 1

    def test_lua_called_with_correct_keys_and_args(self, settings, fake_redis_lua):
        """Lua скрипт получает (key, ttl) в правильных позициях.
        KEYS[1] = redis ключ, ARGV[1] = TTL в секундах."""
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100
        should_emit_tenant_missing("MaxHandler")

        call = fake_redis_lua.script_calls[0]
        assert len(call["keys"]) == 1
        # Ключ содержит handler name + hour bucket — точная форма
        # ``worker:ceil:tenant_required_missing:<handler>:<hour>``.
        assert call["keys"][0].startswith("worker:ceil:tenant_required_missing:MaxHandler:")
        assert call["args"] == [3600]  # _WINDOW_TTL_SECONDS

    def test_script_registered_once_then_cached(self, settings, fake_redis_lua):
        """register_script вызывается ОДИН раз для данного client-а; повторные
        emit-ы переиспользуют тот же script callable (через
        _invalidate_incr_and_expire_script). Без этого каждый emit делал бы
        SCRIPT LOAD round-trip — медленно и шумно в Redis log-е."""
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100
        for _ in range(5):
            should_emit_tenant_missing("MaxHandler")

        assert fake_redis_lua.script_load_count == 1, (
            "register_script должен вызываться лениво ровно один раз "
            "(закэшировано в _invalidate_incr_and_expire_script)"
        )
        assert len(fake_redis_lua.script_calls) == 5  # script_callable — 5 раз

    def test_lua_path_blocks_over_budget(self, settings, fake_redis_lua):
        """Поведение rate-budget идентично между Lua и fallback паттернами:
        101-я попытка при limit=100 → False (ceiling сработал)."""
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 3
        assert should_emit_tenant_missing("MaxHandler") is True  # 1
        assert should_emit_tenant_missing("MaxHandler") is True  # 2
        assert should_emit_tenant_missing("MaxHandler") is True  # 3 (at limit)
        assert should_emit_tenant_missing("MaxHandler") is False  # 4 (over)

    def test_fallback_path_active_when_no_register_script(self, settings, monkeypatch):
        """Защитный тест: client БЕЗ register_script (legacy redis-py
        versions / mock objects) → fallback на INCR+EXPIRE. Сейчас ВСЕ
        существующие тесты (TestHappyPath и др.) используют _FakeRedis
        без register_script — этот тест документирует контракт явно."""
        from apps.workers import ceilings

        ceilings._invalidate_incr_and_expire_script()

        class _NoLua:
            def __init__(self):
                self.store = {}
                self.incr_calls = 0
                self.expire_calls = 0

            def incr(self, key):
                self.store[key] = self.store.get(key, 0) + 1
                self.incr_calls += 1
                return self.store[key]

            def expire(self, key, ttl):
                self.expire_calls += 1
                return True

        no_lua = _NoLua()
        monkeypatch.setattr("apps.ingress.streams._client", lambda: no_lua)
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100
        assert should_emit_tenant_missing("MaxHandler") is True
        # Fallback path: оба incr и expire вызваны (не атомарно, но
        # тестов на crash-window не делаем — это runtime поведение).
        assert no_lua.incr_calls == 1
        assert no_lua.expire_calls == 1
