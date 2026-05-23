"""Synthetic flood drill для STRICT_TENANT_REFUSE pre-flip checklist (#500 AS6).

Закрывает «синтетический нагрузочный тест в staging» из §D-2 runbook-а
``docs/runbooks/strict-tenant-refuse-flip.md`` — без него pre-flip
checklist остаётся неполным.

### Что делает

1. **Pre-checks** (3 штуки) — gate-условия чтобы дрилл не был ложным
   no-op-ом:

   - Worker процесс жив (``pgrep -f "workers.consumer"`` находит PID).
   - Stream + consumer group существуют + есть active consumer в группе.
   - Канареечная entry XADD-ом доходит до handler-а за 5 секунд
     (proxy для «обработка действительно идёт»).

2. **Baseline** — фиксирует количество строк ``apps_audit_event``
   ровно в момент старта (для сравнения с пост-дрилл состоянием) +
   текущий PEL count (Pending Entries List — это «лист подтверждённых,
   но не обработанных entries» в Redis Streams; считаем чтобы не
   путать с дрилл-генерируемыми).

3. **Run** — XADD-ит ``--target-count`` (default 200) entries в
   ``ingress:max`` с пустым ``resolved_tenant_id`` поверх ``--duration``
   (default 300 сек), равномерно. Worker подхватывает, MaxHandler
   увидит ``requires_tenant=True`` + пустой tenant → попадёт в ceiling
   code path (см. ``apps/workers/ceilings.py``).

4. **Wait-for-drain** — ждёт пока PEL вернётся к baseline ± 5 entries.
   Без этого ассерции могут отработать на полу-обработанной выборке.

5. **Positive assertions** (все должны выполниться):

   - В логе worker-а ≥ 1 строка с ``rate_budget_exhausted``
     (проверяем что ceiling действительно сработал, не «дрилл прошёл
     потому что worker не запущен»).
   - В audit-таблице между baseline и now ≥ 50 строк
     ``worker.tenant_required_missing`` (handler путь работал).
   - Audit delta ≤ 100 строк (ceiling cap-нул).

6. **Cleanup** — DELETE строк audit между ``drill_start_ts`` и
   ``drill_end_ts`` с event_type=``worker.tenant_required_missing``.
   Чтобы пост-дрилл диагностика не путалась с настоящими событиями
   (см. issue #576 для долгосрочного решения через payload-tag).

### Контракт по exit codes

- ``0`` — все 3 positive assertions прошли; cleanup сделан.
- ``1`` — pre-check 1 не прошёл: worker процесс не запущен.
- ``2`` — pre-check 2 не прошёл: consumer group / handler не зарегистрирован.
- ``3`` — pre-check 3 не прошёл: канареечная entry не дошла за 5 сек
  (handler не consume-ит).
- ``4`` — positive assertion failed: ``rate_budget_exhausted``
  WARNING не появился (ceiling не сработал).
- ``5`` — positive assertion failed: audit delta < 50 (handler путь
  не выполнялся).
- ``6`` — positive assertion failed: audit delta > 100 (ceiling не
  cap-нул).
- ``7`` — что-то поломалось в самой command во время основной фазы
  (pre-checks / baseline / flood / drain / assertions). Cleanup НЕ
  запускался.
- ``8`` — assertions ПРОШЛИ (drill semantically PASS), но cleanup DELETE
  кинул exception. Audit-таблица содержит drill-rows которые нужно
  удалить вручную через ``--skip-cleanup`` повторный запуск ИЛИ через
  ``DELETE FROM apps_audit_event WHERE event_type = 'worker.tenant_required_missing'
  AND payload->>'handler' = 'MaxHandler' AND created_at BETWEEN <drill_start_ts>
  AND <drill_end_ts>;``. Тех. решение flip-а — оператор смотрит
  ``--json`` output (assertion поля показывают PASS) и продолжает
  pre-flip checklist; cleanup делает вручную.

### AS6-#7 (adversarial pass round-2): target_count vs RATE_LIMIT

Drill default ``target_count=200`` намеренно больше дефолта
``WORKER_TENANT_MISSING_RATE_LIMIT=100`` чтобы ceiling гарантированно
сработал (101-я entry уйдёт в drop, ``rate_budget_exhausted`` WARNING
вылетит). Если оператор переопределил limit в staging до значения
``≥ target_count``, drill не сможет triggerнуть ceiling — assertion 4
упадёт с exit 4 и оператор час дебага без понимания почему.

Команда читает ``settings.WORKER_TENANT_MISSING_RATE_LIMIT`` на старте
и log-ает WARNING если invariant нарушен. Команда продолжает (это не
exit), потому что есть валидные use cases с limit=0 (disabled
ceiling для baseline-теста).

### Использование

::

    python manage.py run_strict_flip_drill
        --duration 300         # секунд для генерации (default 300)
        --target-count 200     # сколько entries (default 200)
        --worker-log /var/log/ai-bot-platform/worker.log
        --skip-cleanup         # для отладки: оставить строки в audit
        --json                 # машинно-читаемый итог

### Postgres-only

Использует ``COUNT(*)`` + ``DELETE`` против ``apps_audit_event``. SQLite
теоретически работает но дрилл специфичен под staging Postgres-среду.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

logger = logging.getLogger(__name__)


# Defaults — все настраиваются флагами командной строки.
_DEFAULT_STREAM = "ingress:max"
_DEFAULT_GROUP = "consumers"
_DEFAULT_DURATION_SEC = 300
_DEFAULT_TARGET_COUNT = 200
_DEFAULT_WORKER_LOG = "/var/log/ai-bot-platform/worker.log"
_DEFAULT_AUDIT_TABLE = "apps_audit_event"
_DEFAULT_HANDLER_NAME = "MaxHandler"

# Canary (канареечная entry) — сколько ждать пока handler её увидит,
# прежде чем считать дрилл setup сломанным.
_CANARY_TIMEOUT_SEC = 5.0

# Wait-for-drain — сколько ждать пока PEL вернётся к baseline.
_DRAIN_POLL_INTERVAL_SEC = 2.0
_DRAIN_MAX_WAIT_SEC = 60.0
_DRAIN_BASELINE_TOLERANCE = 5

# Positive assertions thresholds — см. §D-2 runbook-а.
_MIN_AUDIT_DELTA = 50
_MAX_AUDIT_DELTA = 100


class Command(BaseCommand):
    help = "Pre-flip synthetic flood drill для STRICT_TENANT_REFUSE (#500 AS6)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--stream", default=_DEFAULT_STREAM)
        parser.add_argument("--group", default=_DEFAULT_GROUP)
        parser.add_argument("--handler-name", default=_DEFAULT_HANDLER_NAME)
        parser.add_argument(
            "--duration",
            type=int,
            default=_DEFAULT_DURATION_SEC,
            help="Сколько секунд генерировать entries (default 300).",
        )
        parser.add_argument(
            "--target-count",
            type=int,
            default=_DEFAULT_TARGET_COUNT,
            help="Сколько entries XADD-ить за duration (default 200).",
        )
        parser.add_argument(
            "--worker-log",
            default=_DEFAULT_WORKER_LOG,
            help="Путь к worker log для grep на rate_budget_exhausted.",
        )
        parser.add_argument(
            "--audit-table",
            default=_DEFAULT_AUDIT_TABLE,
            help="Имя таблицы audit для baseline + cleanup (default apps_audit_event).",
        )
        parser.add_argument(
            "--skip-cleanup",
            action="store_true",
            help="НЕ удалять audit строки после assertions (отладка).",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Машинно-читаемый итог в stdout JSON-ом.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from django.conf import settings as dj_settings

        opts = _Opts(
            **{
                k: options[k]
                for k in (
                    "stream",
                    "group",
                    "handler_name",
                    "duration",
                    "target_count",
                    "worker_log",
                    "audit_table",
                    "skip_cleanup",
                    "json",
                )
            }
        )
        if not opts.audit_table.replace("_", "").isalnum():
            raise CommandError(
                f"unsafe audit_table identifier {opts.audit_table!r}; expected snake_case"
            )

        # AS6-#7 invariant: target_count > RATE_LIMIT иначе ceiling
        # никогда не сработает и assertion 4 даст false-negative.
        rate_limit = int(getattr(dj_settings, "WORKER_TENANT_MISSING_RATE_LIMIT", 100))
        if rate_limit > 0 and opts.target_count <= rate_limit:
            self.stdout.write(
                f"⚠ ВНИМАНИЕ: target_count ({opts.target_count}) <= "
                f"WORKER_TENANT_MISSING_RATE_LIMIT ({rate_limit}). Ceiling не "
                "успеет сработать — assertion 4 (rate_budget_exhausted WARNING) "
                "упадёт даже если drill в порядке. Подними --target-count > "
                f"{rate_limit} ИЛИ это намеренный baseline-тест с ceiling-disabled."
            )

        # #593 (PR #585 adversarial pass): SETNX lock против concurrent
        # запусков. Два оператора параллельно → флоды складываются →
        # audit delta пробьёт MAX → false-positive exit 6 → паника перед
        # flip-датой. Lock TTL = duration + 120s (запас на cleanup) —
        # auto-release если drill crash-нулся.
        from apps.ingress.streams import _client

        redis = _client()
        lock_acquired = _acquire_drill_lock(redis, ttl_seconds=opts.duration + 120)
        if not lock_acquired:
            holder_pid = _drill_lock_holder(redis)
            raise CommandError(
                f"Drill уже запущен (PID {holder_pid!r}). Подожди завершения "
                f"(~{opts.duration + 120}s макс. — lock TTL) ИЛИ если ты "
                f"уверен что предыдущий запуск умер: "
                f"`redis-cli DEL {_DRILL_LOCK_KEY}`."
            )

        result = _DrillResult()
        # Main фаза: pre-checks + baseline + flood + drain + assertions.
        # ВАЖНО: cleanup НЕ здесь — иначе DELETE exception затрёт
        # успешный assertion verdict (adversarial pass #5).
        try:
            try:
                self._run_drill(opts, result)
            except _DrillFailed as exc:
                result.exit_code = exc.exit_code
                result.fail_reason = exc.reason
            except Exception as exc:  # noqa: BLE001
                result.exit_code = 7
                result.fail_reason = f"unexpected error: {type(exc).__name__}: {exc}"
                logger.exception("drill.unexpected_error")

            # Cleanup-фаза отдельным блоком. Только если assertions
            # прошли И cleanup явно не отключён. Failure cleanup-а
            # даёт exit 8 — PASS verdict сохраняется в --json output.
            if result.exit_code == 0 and not opts.skip_cleanup:
                try:
                    assert result.drill_start_ts is not None  # mypy narrow
                    assert result.drill_end_ts is not None
                    self.stdout.write("== Cleanup ==")
                    result.cleanup_deleted = _cleanup_audit_rows(
                        opts.audit_table,
                        opts.handler_name,
                        result.drill_start_ts,
                        result.drill_end_ts,
                    )
                    self.stdout.write(f"  удалено строк: {result.cleanup_deleted}")
                except Exception as exc:  # noqa: BLE001
                    result.exit_code = 8
                    result.cleanup_failed = True
                    result.fail_reason = (
                        f"assertions PASS, but cleanup failed: "
                        f"{type(exc).__name__}: {exc}. "
                        "Drill semantically успешен — продолжай pre-flip checklist. "
                        "Drill-rows в audit-таблице удали вручную (см. docstring §exit 8)."
                    )
                    logger.exception("drill.cleanup_failed")
            elif opts.skip_cleanup:
                self.stdout.write("== Cleanup: SKIPPED по флагу --skip-cleanup ==")
        finally:
            # #593: release lock ВСЕГДА — даже на exception в drill.
            # Без finally-блока crash в main фазе оставит stale lock на
            # TTL секунд, блокируя оператора от retry.
            _release_drill_lock(redis)

        self._emit_result(opts, result)
        if result.exit_code:
            sys.exit(result.exit_code)

    def _run_drill(self, opts: "_Opts", result: "_DrillResult") -> None:
        from apps.ingress.streams import _client

        redis = _client()

        # ── Pre-checks ──
        self.stdout.write("== Pre-checks ==")
        _check_worker_running()
        result.worker_alive = True
        self.stdout.write("  [1/3] worker процесс жив")

        _check_group_exists(redis, opts.stream, opts.group)
        result.group_alive = True
        self.stdout.write(f"  [2/3] consumer group {opts.group} активна на {opts.stream}")

        canary_drain_ms = _check_canary(redis, opts)
        result.canary_drain_ms = canary_drain_ms
        self.stdout.write(
            f"  [3/3] канарей за {canary_drain_ms:.0f}ms (порог {_CANARY_TIMEOUT_SEC * 1000:.0f}ms)"
        )

        # ── Baseline ──
        self.stdout.write("== Baseline ==")
        result.audit_count_before = _audit_count(opts.audit_table, opts.handler_name)
        # #592 (PR #585 adversarial pass): timestamps берём из БД, не
        # из Python. Если drill запущен на одном host-е, audit-таблица
        # лежит на другом (типичный staging: k8s + managed Postgres),
        # clock drift ±200ms-±2s оставит первые/последние audit rows
        # за пределами Python-окна → cleanup их пропустит → загрязнение.
        # SELECT NOW() в той же DB что и audit-таблица = нет дрифта.
        result.drill_start_ts = _db_now()
        result.pel_count_before = _pel_count(redis, opts.stream, opts.group)
        self.stdout.write(
            f"  audit_count={result.audit_count_before} pel_count={result.pel_count_before}"
        )

        # ── Run ──
        self.stdout.write(f"== Run: XADD {opts.target_count} entries за {opts.duration}s ==")
        _generate_flood(redis, opts, on_progress=self._on_flood_progress)

        # ── Wait-for-drain ──
        self.stdout.write("== Wait-for-drain ==")
        _wait_for_drain(
            redis,
            opts.stream,
            opts.group,
            baseline=result.pel_count_before,
            on_progress=lambda c: self.stdout.write(f"  pel={c}"),
        )
        result.drill_end_ts = _db_now()  # #592: DB-side timestamp

        # ── Positive assertions ──
        self.stdout.write("== Positive assertions ==")
        result.warning_count = _grep_warning_count(opts.worker_log, result.drill_start_ts)
        if result.warning_count < 1:
            raise _DrillFailed(
                4,
                f"ceiling не сработал: rate_budget_exhausted WARNING count = "
                f"{result.warning_count} (требуется ≥ 1). Проверь "
                f"WORKER_TENANT_MISSING_RATE_LIMIT (default 100; "
                f"current target_count = {opts.target_count}).",
            )
        self.stdout.write(f"  [1/3] WARNING count = {result.warning_count} (>= 1)")

        result.audit_count_after = _audit_count(opts.audit_table, opts.handler_name)
        result.audit_delta = result.audit_count_after - result.audit_count_before
        if result.audit_delta < _MIN_AUDIT_DELTA:
            raise _DrillFailed(
                5,
                f"handler путь не выполнялся в полной мере: audit delta = "
                f"{result.audit_delta} (требуется ≥ {_MIN_AUDIT_DELTA}). Возможно "
                f"worker не успел обработать XADD-нутые entries — увеличь --duration.",
            )
        if result.audit_delta > _MAX_AUDIT_DELTA:
            raise _DrillFailed(
                6,
                f"ceiling не cap-нул: audit delta = {result.audit_delta} "
                f"(требуется ≤ {_MAX_AUDIT_DELTA}). Это критическая регрессия — "
                f"проверь apps/workers/ceilings.py + WORKER_TENANT_MISSING_RATE_LIMIT.",
            )
        self.stdout.write(
            f"  [2-3/3] audit delta = {result.audit_delta} "
            f"(в диапазоне [{_MIN_AUDIT_DELTA}, {_MAX_AUDIT_DELTA}])"
        )

        # Cleanup происходит в handle() — НЕ внутри try/except этого
        # метода, иначе DELETE exception затирает PASS verdict. См.
        # adversarial pass #5 на PR #585.

        result.exit_code = 0

    def _on_flood_progress(self, sent: int, total: int) -> None:
        if sent % 50 == 0 or sent == total:
            self.stdout.write(f"  XADD {sent}/{total}")

    def _emit_result(self, opts: "_Opts", result: "_DrillResult") -> None:
        if opts.json:
            self.stdout.write(json.dumps(result.to_dict(), default=str))
            return
        if result.exit_code == 0:
            self.stdout.write("== PASS ==")
        else:
            self.stdout.write(f"== FAIL (exit {result.exit_code}) ==")
            self.stderr.write(result.fail_reason or "(reason missing)")


# ─────────────────────────────────────────────────────────────────────
# Pre-checks
# ─────────────────────────────────────────────────────────────────────


def _check_worker_running() -> None:
    """Pre-check 1: процесс ``workers.consumer`` найден pgrep-ом."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", "workers.consumer"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise _DrillFailed(
            1, f"не удалось выполнить pgrep: {exc}. На Windows? Запусти drill на Linux-staging."
        )
    if out.returncode != 0 or not out.stdout.strip():
        raise _DrillFailed(
            1,
            "worker процесс не запущен. "
            "Запусти `systemctl start ai-bot-platform-dev-consumer` "
            "и подожди 5 секунд.",
        )


def _check_group_exists(redis: Any, stream: str, group: str) -> None:
    """Pre-check 2: consumer group существует + есть active consumer."""
    try:
        groups = redis.xinfo_groups(stream)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "no such key" in msg.lower():
            raise _DrillFailed(
                2,
                f"stream {stream!r} не существует. "
                "Worker ещё не запустился ни разу? Дай ему минуту и повтори.",
            )
        raise _DrillFailed(2, f"xinfo_groups({stream}) фейл: {exc}")

    matching = [g for g in groups if g.get("name") == group]
    if not matching:
        raise _DrillFailed(
            2,
            f"consumer group {group!r} не существует на stream {stream!r}. "
            f"Доступные: {[g.get('name') for g in groups]}",
        )

    try:
        consumers = redis.xinfo_consumers(stream, group)
    except Exception as exc:  # noqa: BLE001
        raise _DrillFailed(2, f"xinfo_consumers фейл: {exc}")

    if not consumers:
        raise _DrillFailed(
            2,
            f"group {group!r} есть, но 0 активных consumers. "
            "Worker не attach-нулся к группе. Проверь worker лог.",
        )


def _check_canary(redis: Any, opts: "_Opts") -> float:
    """Pre-check 3: одна канареечная entry должна дойти за 5 сек.

    Возвращает фактическое время в миллисекундах от XADD до момента
    когда worker её ACK-нул (PEL не содержит её).
    """
    canary_id = redis.xadd(
        opts.stream,
        {"resolved_tenant_id": "", "_drill_canary": "1"},
    )
    if isinstance(canary_id, bytes):
        canary_id = canary_id.decode("utf-8")

    started = time.monotonic()
    deadline = started + _CANARY_TIMEOUT_SEC
    while time.monotonic() < deadline:
        pending = redis.xpending_range(
            opts.stream, opts.group, min=canary_id, max=canary_id, count=10
        )
        if not pending:
            return (time.monotonic() - started) * 1000.0
        time.sleep(0.1)

    raise _DrillFailed(
        3,
        f"канареечная entry {canary_id} не обработана за {_CANARY_TIMEOUT_SEC}s. "
        "Handler не consume-ит. Проверь `journalctl -u ai-bot-platform-dev-consumer`.",
    )


# ─────────────────────────────────────────────────────────────────────
# Baseline + flood + drain + assertions + cleanup
# ─────────────────────────────────────────────────────────────────────


def _audit_count(table: str, handler_name: str) -> int:
    """COUNT drill-tagged строк в audit-таблице.

    Issue #576: baseline+after counts фильтруются по
    ``payload->>'drill' = 'true'`` тэгу — drill-команда таким образом
    считает ТОЛЬКО synthetic events которые она сама породила.

    **Зачем фильтровать:** без drill-фильтра ``audit_count_before``
    включает все legitimate tenant-missing events от других bug-ов в
    staging-е. Если в drill-окно по совпадению попал реальный
    incident — delta может пробить ``_MAX_AUDIT_DELTA=100`` →
    false-positive exit 6 «ceiling не cap-нул» → паника перед flip-date.
    Drill-фильтр исключает не-drill noise.

    Side effect: если предыдущий cleanup упал (exit 8) и оператор не
    почистил вручную → baseline_before > 0 (residual drill rows от
    прошлого запуска). Это honestly отражено в delta и не ломает
    assertion при условии что текущий drill добавил ≥ MIN_AUDIT_DELTA
    новых tagged rows. Не считается багом.
    """
    sql = (
        f"SELECT COUNT(*) FROM {table} "
        "WHERE event_type = 'worker.tenant_required_missing' "
        "AND payload->>'handler' = %s "
        "AND payload->>'drill' = 'true'"
    )
    with connection.cursor() as cur:
        cur.execute(sql, [handler_name])
        return int(cur.fetchone()[0])


_DRILL_LOCK_KEY = "strict_flip_drill:lock"


def _acquire_drill_lock(redis: Any, *, ttl_seconds: int) -> bool:
    """SETNX-style lock acquire через redis.set(nx=True, ex=ttl).

    redis-py ``set(key, value, nx=True, ex=ttl)`` атомарно ставит ключ
    ТОЛЬКО если его ещё нет + ставит TTL. Это canonical SETNX-pattern
    из Redis docs §«Distributed locks» (упрощённая версия, без Redlock —
    нам не нужна устойчивость к partition, drill это staging-only).

    Возвращает True если lock acquired, False если занят. Никаких
    exceptions — caller сам решает что делать через CommandError.

    TTL включает запас на cleanup (~120s сверху от duration). Если
    drill крашится без `finally`-release, lock auto-expire-нется на TTL.

    ⚠ **Fail-open поведение:** если ``redis.set`` кинет exception
    (Redis недоступен, partition, …), функция возвращает **True**
    как если бы lock был успешно acquired. Это осознанный trade-off:
    drill всё равно упадёт на следующем Redis-вызове (``_pel_count``
    в baseline, ``_check_canary``), поэтому защита от concurrent
    runs выполнить мы не сможем, но добавлять отдельный exit для
    «lock module недоступен» — overkill. Self-adversarial pass на
    #593 подтвердил приемлемость этого пути для staging-only tool.
    """
    pid = os.getpid()
    try:
        # redis-py возвращает True/None (None если key уже есть и nx=True).
        result = redis.set(_DRILL_LOCK_KEY, str(pid), nx=True, ex=ttl_seconds)
    except Exception as exc:  # noqa: BLE001
        logger.warning("drill_lock.acquire_failed err=%s — fail-open", exc)
        return True
    return bool(result)


def _drill_lock_holder(redis: Any) -> str | None:
    """Возвращает PID процесса который держит lock (или None).

    Полезно для сообщения оператору: «занято процессом 12345» — он
    может проверить `ps -p 12345` живой ли тот процесс.
    """
    try:
        val = redis.get(_DRILL_LOCK_KEY)
    except Exception:  # noqa: BLE001
        return None
    if val is None:
        return None
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return str(val)


def _release_drill_lock(redis: Any) -> None:
    """Безусловно DELETE-ит lock-ключ. Никаких own-PID checks.

    Why no PID check on release: lock TTL уже короче чем любой
    разумный stale-window. Если ключа нет (auto-expired или другой
    процесс DEL-нул) — DEL idempotent. Если ключ принадлежит другому
    процессу (TTL expired + новый процесс acquire-нул) — мы НЕ должны
    были этот release вызвать, это означает баг в caller-логике
    (превысили TTL без cleanup). Лучше fail loud при разработке чем
    защитный no-op.

    На самом деле редкая race-условие: процесс N запустил drill, lock
    expired через TTL, процесс M acquire-нул, процесс N всё ещё бежит
    и в `finally` DELETE-нёт M-овский lock. Митигация: TTL ставится
    с большим запасом (duration + 120s); процесс N должен закончиться
    за это окно или быть прибит.
    """
    try:
        redis.delete(_DRILL_LOCK_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("drill_lock.release_failed err=%s", exc)


def _db_now() -> datetime:
    """Возвращает «сейчас» по часам DB-сервера.

    #592 (PR #585 adversarial pass): cleanup в drill-команде использует
    timestamp range вокруг drill_start_ts и drill_end_ts. Если эти
    timestamps берутся через ``datetime.now(timezone.utc)`` на Django
    host-е, а audit-таблица пишется на отдельном Postgres host-е (типичный
    staging: k8s + managed Postgres), clock drift ±200ms-±2s оставит
    первые/последние audit rows за пределами окна → cleanup их пропустит.

    Postgres ``NOW()`` возвращает timestamp с timezone (timestamptz),
    Django маппит его в aware ``datetime``. Используем тот же DB-server-time
    для baseline и для end-марки — нулевой drift.

    SQLite fallback: ``CURRENT_TIMESTAMP`` без timezone. Test-only —
    drill команда production-ом запускается на Postgres staging-е.
    """
    with connection.cursor() as cur:
        if connection.vendor == "postgresql":
            cur.execute("SELECT NOW()")
        else:
            # SQLite: CURRENT_TIMESTAMP возвращает naive UTC, нормализуем.
            cur.execute("SELECT CURRENT_TIMESTAMP")
        row = cur.fetchone()[0]
        if isinstance(row, datetime):
            if row.tzinfo is None:
                return row.replace(tzinfo=timezone.utc)
            return row
        # Какой-то совсем экзотический бэкенд → fallback на Python.
        return datetime.now(timezone.utc)


def _pel_count(redis: Any, stream: str, group: str) -> int:
    """PEL count (summary form XPENDING — list shape, см. AS1 fix в monitor_pel)."""
    try:
        info = redis.xpending(stream, group)
    except Exception:  # noqa: BLE001
        return 0
    if isinstance(info, dict):
        return int(info.get("pending", 0))
    if isinstance(info, list) and info:
        return int(info[0])
    return 0


def _generate_flood(redis: Any, opts: "_Opts", on_progress: Any) -> None:
    """XADD ``target_count`` entries за ``duration`` секунд равномерно."""
    interval = opts.duration / max(opts.target_count, 1)
    sent = 0
    started = time.monotonic()
    while sent < opts.target_count:
        next_send_at = started + (sent + 1) * interval
        redis.xadd(
            opts.stream,
            {"resolved_tenant_id": "", "_drill": "1"},
        )
        sent += 1
        on_progress(sent, opts.target_count)
        sleep_for = next_send_at - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)


def _wait_for_drain(
    redis: Any,
    stream: str,
    group: str,
    *,
    baseline: int,
    on_progress: Any,
) -> None:
    """Ждём пока PEL вернётся к baseline ± tolerance."""
    deadline = time.monotonic() + _DRAIN_MAX_WAIT_SEC
    while time.monotonic() < deadline:
        current = _pel_count(redis, stream, group)
        on_progress(current)
        if current <= baseline + _DRAIN_BASELINE_TOLERANCE:
            return
        time.sleep(_DRAIN_POLL_INTERVAL_SEC)
    raise _DrillFailed(
        7,
        f"PEL не drain-нулся за {_DRAIN_MAX_WAIT_SEC}s. "
        "Worker лагает или сломался. Сheck worker log.",
    )


def _grep_warning_count(log_path: str, since: datetime) -> int:
    """Считаем сколько раз ``rate_budget_exhausted`` появилось в логе ПОСЛЕ since.

    Лог-формат worker-а: JSON-строки с полем ``ts`` в ISO 8601.
    Если лога нет (тестовая среда без файла) — возвращаем 0; assertion
    верхнего уровня тогда упадёт с exit code 4 → оператор увидит.
    """
    if not os.path.exists(log_path):
        return 0
    pattern = re.compile(r"rate_budget_exhausted|tenant_missing_rate_exceeded")
    ts_pattern = re.compile(r'"ts"\s*:\s*"([^"]+)"')
    count = 0
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not pattern.search(line):
                continue
            m = ts_pattern.search(line)
            if not m:
                count += 1
                continue
            try:
                ts = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
            except ValueError:
                count += 1
                continue
            if ts >= since:
                count += 1
    return count


def _cleanup_audit_rows(
    table: str,
    handler_name: str,
    start_ts: datetime,
    end_ts: datetime,
) -> int:
    """DELETE drill-tagged строк audit в указанном временном окне.

    Issue #576 (fundamental fix): cleanup сходит ТОЛЬКО на строки с
    ``payload->>'drill' = 'true'`` — это маркер который drill команда
    проставляет в synthetic entries через XADD field ``_drill=1``, а
    ``apps/workers/base.py`` пробрасывает в emit payload.

    **Двойной фильтр (defense-in-depth):**

    - **Primary**: ``payload->>'drill' = 'true'`` — семантика «удаляем
      то что drill породил, не любую активность в окне». Защищает от
      случайного удаления legitimate ``tenant_required_missing`` событий
      которые попали в drill-окно (например рассинхронизация в Sentinel
      failover, временной баг в ingress).
    - **Secondary**: ``created_at BETWEEN start_ts AND end_ts`` —
      defense на случай если в audit залежались drill-tagged строки от
      прошлых drill runs (предыдущий cleanup упал с exit 8 → оператор
      не удалил вручную). Tag-only cleanup такие строки бы тоже снёс,
      что технически OK (они всё равно drill-мусор), но «удалим только
      то что СЕЙЧАСНИЙ drill породил» — более предсказуемый контракт.

    Issue #592 timestamp-fix (DB-side NOW()) остаётся актуальным —
    secondary defense polagается на корректные timestamps для разделения
    «сегодняшнего» drill-а от «вчерашнего».
    """
    sql = (
        f"DELETE FROM {table} "
        "WHERE event_type = 'worker.tenant_required_missing' "
        "AND payload->>'handler' = %s "
        "AND payload->>'drill' = 'true' "  # #576 primary defense
        "AND created_at BETWEEN %s AND %s"  # #592 secondary defense
    )
    with connection.cursor() as cur:
        cur.execute(sql, [handler_name, start_ts, end_ts])
        return cur.rowcount


# ─────────────────────────────────────────────────────────────────────
# Internal data containers
# ─────────────────────────────────────────────────────────────────────


class _Opts:
    """Свёрнутая структура опций — чище чем пробрасывать dict повсюду."""

    def __init__(
        self,
        *,
        stream: str,
        group: str,
        handler_name: str,
        duration: int,
        target_count: int,
        worker_log: str,
        audit_table: str,
        skip_cleanup: bool,
        json: bool,
    ) -> None:
        self.stream = stream
        self.group = group
        self.handler_name = handler_name
        self.duration = duration
        self.target_count = target_count
        self.worker_log = worker_log
        self.audit_table = audit_table
        self.skip_cleanup = skip_cleanup
        self.json = json


class _DrillResult:
    """Накопитель промежуточных метрик для финального отчёта."""

    def __init__(self) -> None:
        self.exit_code: int = 0
        self.fail_reason: str | None = None
        self.worker_alive: bool = False
        self.group_alive: bool = False
        self.canary_drain_ms: float | None = None
        self.audit_count_before: int | None = None
        self.audit_count_after: int | None = None
        self.audit_delta: int | None = None
        self.pel_count_before: int | None = None
        self.drill_start_ts: datetime | None = None
        self.drill_end_ts: datetime | None = None
        self.warning_count: int | None = None
        self.cleanup_deleted: int | None = None
        self.cleanup_failed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "fail_reason": self.fail_reason,
            "worker_alive": self.worker_alive,
            "group_alive": self.group_alive,
            "canary_drain_ms": self.canary_drain_ms,
            "audit_count_before": self.audit_count_before,
            "audit_count_after": self.audit_count_after,
            "audit_delta": self.audit_delta,
            "pel_count_before": self.pel_count_before,
            "drill_start_ts": self.drill_start_ts,
            "drill_end_ts": self.drill_end_ts,
            "warning_count": self.warning_count,
            "cleanup_deleted": self.cleanup_deleted,
            "cleanup_failed": self.cleanup_failed,
        }


class _DrillFailed(Exception):
    """Drill упал на pre-check ИЛИ на positive assertion. exit_code 1..6."""

    def __init__(self, exit_code: int, reason: str) -> None:
        super().__init__(reason)
        self.exit_code = exit_code
        self.reason = reason
