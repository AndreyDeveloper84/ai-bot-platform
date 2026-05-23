"""Operator-side ceilings for STRICT_TENANT_REFUSE rollout (issue #500).

Bounds the runtime impact when the strict-flip flushes a deluge of
``worker.tenant_required_missing`` events — without these ceilings the
audit table doubles in days and the on-call alert pipeline floods
under a misbehaving ingress (1000 entries/hour with empty
``resolved_tenant_id``).

The two adversarial-pass D-2 items addressed here:

* **Item 2 — per-handler rate budget on ``worker.tenant_required_missing``**
  Drops audit emits past a configurable budget per (handler, hour).
  Budget defaults to 100/hour per handler — high enough to capture a
  normal flap, low enough to keep audit-table growth bounded under a
  thousand-event-per-hour deluge.

* **Item 4 — alert suppression / dedup on (handler, hour)**
  Same redis key naturally dedups: once the budget is exhausted,
  subsequent emits the same hour are silently dropped. A single bad
  ingress can no longer flood the on-call page through the audit
  fanout.

### Fail-open philosophy

Telemetry is observability, not the critical path. If Redis is
unreachable or the rate-limit logic itself throws, the function
returns ``True`` — we'd rather risk audit-table growth than silently
lose visibility into a worker fault.

### Why Redis, not a DB rate-limit table

The rate budget needs to survive worker restarts (multi-process
ingress consumer) and reset on hour boundaries. Redis ``INCR`` +
``EXPIRE`` is cheaper than a Postgres rate-limit table that we'd
have to garbage-collect.
"""

from __future__ import annotations

import logging
import time

import redis.exceptions
from django.conf import settings


# Connection-fault exception surface — any of these implies the cached
# Redis connection pool is suspect and the lru_cache should be cleared.
# AS2-NEW (PR #528 round-3 double-pass): typed isinstance check, not
# string-match. Excludes redis.RedisError (too broad — DataError /
# ResponseError do NOT indicate transport fault) and excludes builtin
# ConnectionError (Python network-level errors propagate through
# redis-py as redis.ConnectionError anyway).
_CONNECTION_FAULT_EXC: tuple[type[Exception], ...] = (
    redis.exceptions.ConnectionError,
    redis.exceptions.TimeoutError,
    redis.exceptions.BusyLoadingError,
    redis.exceptions.ChildDeadlockedError,
    redis.exceptions.ClusterDownError,
    redis.exceptions.MasterDownError,
    redis.exceptions.MaxConnectionsError,
    redis.exceptions.ReadOnlyError,
)

logger = logging.getLogger(__name__)


# Default budget: 100 emits per (handler, hour). Configurable via
# ``WORKER_TENANT_MISSING_RATE_LIMIT`` env var. Set to 0 to disable
# the ceiling entirely (every emit fires — escape hatch for diagnostics).
_DEFAULT_RATE_LIMIT = 100

# Window length matches the dedup granularity stated in the runbook
# (``(handler, hour)``). One hour = naturally aligns with on-call
# rotation boundaries and the audit-table baseline growth alert window.
_WINDOW_TTL_SECONDS = 3600

# Lua-script для атомарного INCR+EXPIRE (#574).
#
# До этого фикса rate-budget делал два отдельных Redis вызова: INCR,
# потом EXPIRE. Между ними окно race condition: если процесс крашится
# после INCR но до EXPIRE, ключ остаётся без TTL → счётчик копится
# бесконечно → handler-budget permastucks at 0 до тех пор пока
# оператор вручную не удалит ключ через ``redis-cli DEL``.
#
# Lua-скрипт выполняется атомарно на Redis-сервере: либо обе команды
# выполняются, либо ни одна. INCR+EXPIRE становится single-step
# transaction без race window. Бонус: 1 round-trip вместо 2 →
# хот-path emit-а становится дешевле в 2 раза.
#
# Возвращает текущий счётчик (как раньше INCR).
_INCR_AND_EXPIRE_LUA = """
local count = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ARGV[1])
return count
"""

# redis-py ``register_script`` возвращает callable который автоматически:
# 1. На первом вызове делает ``SCRIPT LOAD`` чтобы получить SHA1 hash.
# 2. На последующих — отправляет ``EVALSHA <sha1>`` (8-байтовый hash
#    дешевле чем full script body).
# 3. Если сервер отвечает ``NOSCRIPT`` (например после ``SCRIPT FLUSH``
#    или Redis restart) — автоматически fallback на ``EVAL`` с full body
#    + re-cache.
#
# Module-global (один script на процесс) вместо ``dict[id(client)] →
# Script``: redis-py Script callable client-agnostic для EVALSHA пути
# (binding к `registered_client` не influencing SHA1 dispatch), поэтому
# 1 instance достаточно. Adversarial pass #574 вектор 1: dict-кэш
# по id(client) накапливал бы entries после каждого
# `_client.cache_clear()` reconnect — slow leak. Module global +
# explicit reset в том же месте где cache_clear решает problem.
_incr_and_expire_script: object | None = None


def _get_incr_and_expire_script(redis_client: object) -> object | None:
    """Возвращает callable Lua-script. Lazy register на первом вызове.

    Если redis_client не поддерживает ``register_script`` (тестовые
    fake-ы без полного surface-а) — возвращает None, caller falls back
    на старый INCR + EXPIRE path.
    """
    global _incr_and_expire_script
    if _incr_and_expire_script is not None:
        return _incr_and_expire_script
    register = getattr(redis_client, "register_script", None)
    if register is None:
        return None
    _incr_and_expire_script = register(_INCR_AND_EXPIRE_LUA)
    return _incr_and_expire_script


def _invalidate_incr_and_expire_script() -> None:
    """Сбросить cached Script object. Вызывается в том же месте где
    ``_client.cache_clear()`` — после Redis failover нужен fresh
    register на новый client (SCRIPT LOAD против нового сервера)."""
    global _incr_and_expire_script
    _incr_and_expire_script = None


# Dedup window for the fail-open WARNING logs (`redis_unavailable` /
# `incr_failed`). Code Reviewer §H.3 follow-up on PR #528: without
# this dedup, a sustained Redis outage logs WARNING at the full emit
# rate (every consumed entry). 60s window keeps the signal visible
# in the operator log without flooding.
_FAIL_OPEN_LOG_DEDUP_SECONDS = 60.0

# Module-level last-logged timestamps per log-event key. Per-process
# state — workers don't share. Acceptable: each worker independently
# rate-limits its own log spam; aggregate log volume scales with
# worker count, not emit rate.
_last_fail_open_log_at: dict[str, float] = {}


def _should_log_fail_open(key: str) -> bool:
    """Return True if a fail-open WARNING for ``key`` should fire.

    Dedups module-locally on a 60-second sliding window. The first call
    after a quiet period returns True; subsequent calls within the
    window return False. State is per-worker-process — restart resets
    the dedup, which is the right behaviour (we WANT a fresh signal
    after a restart confirms Redis is still down).
    """
    now = time.monotonic()
    last = _last_fail_open_log_at.get(key, 0.0)
    if now - last >= _FAIL_OPEN_LOG_DEDUP_SECONDS:
        _last_fail_open_log_at[key] = now
        return True
    return False


def should_emit_tenant_missing(handler_name: str) -> bool:
    """Return True if a ``worker.tenant_required_missing`` emit should fire.

    Tracks a rolling counter per (handler_name, hour_bucket) in Redis.
    Returns False once the count exceeds ``WORKER_TENANT_MISSING_RATE_LIMIT``
    (default 100) — the caller MUST then skip the DB-writing ``emit()``
    call AND the alert-side pipeline (the dedup gives both Item 2 and
    Item 4 in one mechanism).

    Args:
      handler_name: ``type(self).__name__`` of the calling handler.
                    Two different handler classes get independent budgets.

    Returns:
      True  — emit allowed. Increments the counter for this window.
      False — budget exhausted for this (handler, hour). Caller drops
              the emit. Logs a one-shot WARNING on the exact transition
              (count == limit + 1) so operators see when the ceiling
              first triggered.

    ### Fail-open paths

    Returns True (NOT False) when:

    * ``WORKER_TENANT_MISSING_RATE_LIMIT`` is 0 / negative (disabled).
    * Redis client construction throws.
    * ``INCR`` / ``EXPIRE`` throw.

    Rationale: better to risk an audit-table spike than lose visibility
    when the rate-limit pipeline itself is the broken thing.
    """
    limit = int(getattr(settings, "WORKER_TENANT_MISSING_RATE_LIMIT", _DEFAULT_RATE_LIMIT))
    if limit <= 0:
        return True

    from apps.ingress.streams import _client

    try:
        redis = _client()
    except Exception as exc:  # noqa: BLE001
        # Fail-open: log (deduped, 60s window) and let the emit proceed.
        # Without dedup a sustained Redis outage would flood the worker
        # log at the full emit rate. Code Reviewer §H.3 follow-up PR #528.
        if _should_log_fail_open("redis_unavailable"):
            logger.warning(
                "workers.ceilings.redis_unavailable err=%s — emit allowed (fail-open)",
                exc,
            )
        return True

    hour_bucket = int(time.time()) // _WINDOW_TTL_SECONDS
    key = f"worker:ceil:tenant_required_missing:{handler_name}:{hour_bucket}"

    try:
        # #574: атомарный INCR+EXPIRE через Lua script. Закрывает race
        # window: до этого было 2 отдельных Redis вызова (INCR, потом
        # EXPIRE), и если процесс крашился между ними — ключ оставался
        # без TTL → счётчик копился бесконечно → handler permabudgeted.
        # Lua-скрипт выполняется атомарно на сервере: либо обе команды
        # либо ни одна. Бонус: 1 round-trip вместо 2.
        #
        # Test-stub fallback: если фейковый redis client не поддерживает
        # register_script (типичные unit-test mocks без полного
        # surface-а) — _get_incr_and_expire_script возвращает None,
        # тогда падает на старый non-atomic путь. Production redis-py
        # всегда даёт register_script.
        script = _get_incr_and_expire_script(redis)
        if script is not None:
            # redis-py Script callable: script(keys=[...], args=[...]).
            # mypy не видит signature на dynamic-loaded callable.
            count = int(script(keys=[key], args=[_WINDOW_TTL_SECONDS]))  # type: ignore[operator]
        else:
            # Test-fallback non-atomic path (race window present, но это
            # ОК для тестов — они не симулируют crash между calls).
            count = int(redis.incr(key))  # type: ignore[arg-type]
            redis.expire(key, _WINDOW_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        # AS2 (PR #528 tech-lead pass): `_client()` is lru_cached at the
        # streams.py layer. On Redis restart / failover the cached pool
        # serves dead sockets indefinitely — every emit hits this path
        # and the rate budget is permanently bypassed until worker
        # restart. Clear the cache on ConnectionError so the NEXT call
        # rebuilds the pool with a fresh socket.
        #
        # AS2-NEW (round-3 double-pass): replaced string-match heuristic
        # with typed exception tuple. The string-match would catch any
        # exception named with "Connection" / "Timeout" (false-positive
        # on user-defined subclasses) and miss cluster/topology faults
        # that don't carry those names. The narrow set below is the
        # canonical "transport-level fault" surface from redis-py — any
        # of these implies the connection pool is suspect.
        if isinstance(exc, _CONNECTION_FAULT_EXC):
            try:
                _client.cache_clear()
            except AttributeError:
                # _client is not lru_cached in test stubs — fine, no
                # cache to clear, the monkeypatched callable rebuilds
                # the fake fresh on every invocation already.
                pass
            # #574 vector 1 (adversarial pass): сбросить cached Lua
            # Script вместе с client cache. Иначе после Redis failover
            # старый Script object binding-нут к умершему connection
            # pool — следующий emit либо получит NOSCRIPT (redis-py
            # его automatically replay-ит, но это лишний round-trip),
            # либо stale state.
            _invalidate_incr_and_expire_script()
        # Deduped (60s window) — see _should_log_fail_open rationale.
        # #574 vector 4 (adversarial pass): log key называется
        # ``rate_budget_failed``, а не ``incr_failed`` — после Lua-fix
        # упасть может либо script call либо fallback incr-call,
        # «incr_failed» вводит оператора в заблуждение при диагностике.
        if _should_log_fail_open(f"rate_budget_failed:{handler_name}"):
            logger.warning(
                "workers.ceilings.rate_budget_failed handler=%s err=%s — emit allowed (fail-open)",
                handler_name,
                exc,
            )
        return True

    # Semantics: ``count > limit`` means EXACTLY ``limit`` emits fire
    # per window; the (limit+1)-th call is the first to be blocked.
    # Default ``WORKER_TENANT_MISSING_RATE_LIMIT=100`` therefore
    # produces up to 100 audit rows per (handler, hour), not 101.
    # Tech-lead pass AS7 (PR #528) flagged a possible off-by-one — the
    # math here is intentional; the docstring documents it explicitly.
    if count > limit:
        if count == limit + 1:
            # Log the ceiling trigger exactly once per window — operator
            # can grep the worker log to confirm Item 2 fired. Don't
            # double-emit through events.emit because that would
            # defeat the rate limit.
            logger.warning(
                "workers.ceilings.tenant_missing_rate_exceeded handler=%s "
                "count=%d limit=%d window=hourly — subsequent emits this "
                "hour silently dropped (operator-side ceiling, issue #500)",
                handler_name,
                count,
                limit,
            )
        return False
    return True
