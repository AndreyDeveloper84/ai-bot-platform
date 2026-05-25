"""Monitor Redis Streams PEL length + optionally alert (issue #500 Item 1).

Wires the adversarial-pass D-2 "PEL length alert" item from
``docs/runbooks/strict-tenant-refuse-flip.md``. The PEL (Pending Entries
List) grows by one for every consumed-but-unACKed entry; under
``STRICT_TENANT_REFUSE=True`` with a misbehaving ingress, this can grow
unboundedly and exhaust Redis memory.

### Usage

::

    # Print the count, exit 0 (no thresholds).
    python manage.py monitor_pel

    # Check against thresholds; exit 1 = warning, 2 = page.
    python manage.py monitor_pel --warning 1000 --page 5000

    # JSON output for ingestion into Prometheus / Grafana.
    python manage.py monitor_pel --format json

    # Custom stream + group (defaults match the consumer in production).
    python manage.py monitor_pel --stream ingress:max --group consumers

### Exit codes (when --warning / --page set)

- ``0`` — PEL count below ``--warning`` threshold
- ``1`` — at or above ``--warning`` but below ``--page``
- ``2`` — at or above ``--page``
- ``3`` — non-NOGROUP Redis error (transient or sustained)
- Non-zero from a healthy script means the monitoring stack should
  alert. Document this in the alert config wired by the operator.

### Cron paging tuning + transient-blip dedup (AS5 / #577)

Single Redis blip otherwise surfaces here as exit 3 — without dedup,
a 1-minute cron would page once per run. Shipped 2026-05-25 (#577):
the command itself dedups by counting **consecutive** Redis faults
in a small JSON state file. Only after ``N`` consecutive non-NOGROUP
Redis errors does the exit transition from 0 (warning-only) to 3
(page). One successful run resets the counter.

* State file path: ``--state-file <path>`` или env
  ``MONITOR_PEL_STATE_FILE``. Default
  ``/var/run/ai-bot-platform/monitor_pel.state`` — оператор настроит
  per environment.
* Threshold: ``--transient-runs N`` или env ``MONITOR_PEL_TRANSIENT_RUNS``,
  default ``3``.

Defense-in-depth: on Redis fault команда ALSO clears the lru_cache
of ``apps.ingress.streams._client`` — следующая попытка построит
fresh connection pool (same self-heal pattern that ``ceilings.py``
uses on connection faults).

The runbook §«Adversarial-pass D-2 — operational ceilings» calls for
N=1000 (warning) / N=5000 (page) on the ``ingress:max`` stream under
the ``consumers`` group; defaults match.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


# Defaults per runbook §«D-2 operational ceilings».
_DEFAULT_STREAM = "ingress:max"
_DEFAULT_GROUP = "consumers"

# #577 dedup defaults.
_DEFAULT_STATE_FILE = "/var/run/ai-bot-platform/monitor_pel.state"
_DEFAULT_TRANSIENT_RUNS = 3


class Command(BaseCommand):
    help = "Monitor Redis Streams PEL length + alert when thresholds breached (#500)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--stream",
            default=_DEFAULT_STREAM,
            help=f"Redis stream key. Default: {_DEFAULT_STREAM}",
        )
        parser.add_argument(
            "--group",
            default=_DEFAULT_GROUP,
            help=f"Consumer group name. Default: {_DEFAULT_GROUP}",
        )
        parser.add_argument(
            "--warning",
            type=int,
            default=None,
            help="PEL length threshold for warning (exit 1). Per runbook: 1000.",
        )
        parser.add_argument(
            "--page",
            type=int,
            default=None,
            help="PEL length threshold for paging (exit 2). Per runbook: 5000.",
        )
        parser.add_argument(
            "--format",
            choices=("text", "json"),
            default="text",
            help="Output format. JSON for monitoring stack ingestion.",
        )
        # #577: transient-blip dedup state.
        parser.add_argument(
            "--state-file",
            default=os.environ.get("MONITOR_PEL_STATE_FILE", _DEFAULT_STATE_FILE),
            help=(
                "Path to consecutive-failures state file. Env "
                "MONITOR_PEL_STATE_FILE overrides. Default: "
                f"{_DEFAULT_STATE_FILE}"
            ),
        )
        parser.add_argument(
            "--transient-runs",
            type=int,
            default=int(
                os.environ.get("MONITOR_PEL_TRANSIENT_RUNS", _DEFAULT_TRANSIENT_RUNS),
            ),
            help=(
                "How many consecutive Redis faults before exiting 3. "
                f"Default {_DEFAULT_TRANSIENT_RUNS}. Set to 0 to disable "
                "dedup (every fault → exit 3 immediately)."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        stream = options["stream"]
        group = options["group"]
        warning = options["warning"]
        page = options["page"]
        fmt = options["format"]
        state_file = options["state_file"]
        transient_runs = max(0, int(options["transient_runs"]))

        from apps.ingress.streams import _client

        redis = _client()

        # XPENDING (summary form, 2-arg) returns a LIST per redis-py:
        # ``[total_pending, min_id, max_id, [[consumer_name, count], ...]]``.
        # Some wrappers / versions normalise to dict; handle both.
        # Tech-lead adversarial pass on PR #528 AS1 caught the original
        # dict-only assumption — in production this caused the monitor
        # to self-page on every run because the dict.get() raised
        # AttributeError on the real list shape.
        #
        # IDLE-form (XPENDING with consumer + range args) returns a list
        # of [msg_id, consumer, idle_ms, deliveries] tuples — not used
        # by this monitor; summary form is sufficient for length checks.
        try:
            info = redis.xpending(stream, group)
            if isinstance(info, dict):
                pending = int(info.get("pending", 0))
            elif isinstance(info, list) and info:
                pending = int(info[0])
            else:
                pending = 0
        except Exception as exc:  # noqa: BLE001
            # Distinguish "group doesn't exist" (transient pre-bootstrap)
            # from real Redis trouble. redis-py raises ResponseError with
            # "NOGROUP" in the message for the former.
            if "NOGROUP" in str(exc):
                pending = 0
                # NOGROUP — это cold-start, не настоящий fault.
                # Reset consecutive counter (если был накопил).
                _reset_state_file(state_file)
            else:
                # Real fault. #577: defense-in-depth — клиаpим
                # lru_cache на Redis-уровне (same self-heal как ceilings.py),
                # потом проверяем consecutive counter.
                _safe_cache_clear(_client)
                consecutive = _bump_state_file(state_file)
                _emit_fault(self, exc, fmt, stream, group, consecutive, transient_runs)
                if transient_runs == 0 or consecutive >= transient_runs:
                    # Threshold breached — exit 3 (page).
                    sys.exit(3)
                # Below threshold — exit 0 with WARNING log only.
                # Cron не paged, оператор увидит на dashboard.
                logger.warning(
                    "monitor_pel.transient_fault stream=%s group=%s "
                    "consecutive=%d/%d err=%s — exit 0 (below threshold)",
                    stream,
                    group,
                    consecutive,
                    transient_runs,
                    exc,
                )
                return

        # XPENDING succeeded — reset consecutive counter.
        _reset_state_file(state_file)

        # Pick severity.
        severity = "ok"
        exit_code = 0
        if page is not None and pending >= page:
            severity = "page"
            exit_code = 2
        elif warning is not None and pending >= warning:
            severity = "warning"
            exit_code = 1

        if fmt == "json":
            self.stdout.write(
                json.dumps(
                    {
                        "stream": stream,
                        "group": group,
                        "pending": pending,
                        "severity": severity,
                        "warning_threshold": warning,
                        "page_threshold": page,
                    }
                )
            )
        else:
            line = f"stream={stream} group={group} pending={pending} severity={severity}"
            if warning is not None or page is not None:
                line += f" (warning={warning} page={page})"
            self.stdout.write(line)

        if exit_code:
            sys.exit(exit_code)


# ───────────────────────────────────────────────────────────────────────
# #577 — consecutive-failures state file helpers
# ───────────────────────────────────────────────────────────────────────


def _read_state_file(path: str) -> int:
    """Прочитать consecutive_failures counter из state-file.

    Tolerates: отсутствующий файл, malformed JSON, missing key, не-int.
    Возвращает 0 в любом из этих случаев. Это безопасный default — если
    state потерян, начнём counter с нуля (рискуем pageoм за следующие
    N consecutive faults). Лучше чем фантомный stale counter.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return 0
    try:
        return int(data.get("consecutive_failures", 0))
    except (TypeError, ValueError):
        return 0


def _write_state_file(path: str, count: int) -> None:
    """Атомарно записать counter (write→fsync→rename pattern).

    Не raise: если запись failed (например permission denied,
    disk full, parent dir missing) — log + continue. Без state file
    counter каждый run начинает с 0 — мы переходим в более paranoid
    режим (page after first fault) но не блокируем работу команды.
    """
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(
            "monitor_pel.state_dir_mkdir_failed path=%s err=%s",
            path,
            exc,
        )
        return

    payload = json.dumps({"consecutive_failures": int(count)})
    try:
        # Atomic write через temp file + rename — защищает от corrupted
        # state если процесс убит mid-write (cron timeout etc.).
        fd, tmp_path = tempfile.mkstemp(
            prefix=".monitor_pel-",
            suffix=".tmp",
            dir=str(target.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, target)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as exc:
        logger.warning(
            "monitor_pel.state_write_failed path=%s err=%s",
            path,
            exc,
        )


def _bump_state_file(path: str) -> int:
    """Инкрементировать counter; возвращает новое значение.

    Idempotent на ошибки чтения/записи — если оба failed, возвращает
    минимум 1 (сегодняшний fault зачитан).
    """
    current = _read_state_file(path)
    new_value = current + 1
    _write_state_file(path, new_value)
    return new_value


def _reset_state_file(path: str) -> None:
    """Reset counter после successful XPENDING / NOGROUP.

    Если state file не существует — no-op (не создаём пустой row).
    Это economy: cron на свежем deploy не плодит /var/run-стурус
    мусор пока не было ни одной ошибки.
    """
    if _read_state_file(path) == 0:
        return  # уже 0 либо файла нет
    _write_state_file(path, 0)


def _safe_cache_clear(client_factory: Any) -> None:
    """Defense-in-depth: clear lru_cache on Redis fault.

    Same self-heal pattern as ``apps/workers/ceilings.py`` — next call
    rebuilds connection pool с fresh socket. Idempotent: если
    factory не lru_cached (test stubs), просто noop.
    """
    try:
        client_factory.cache_clear()
    except AttributeError:
        pass


def _emit_fault(
    cmd: BaseCommand,
    exc: Exception,
    fmt: str,
    stream: str,
    group: str,
    consecutive: int,
    threshold: int,
) -> None:
    """Print fault info в нужном формате (text|json) + дополнительные
    поля consecutive/threshold чтобы monitoring stack видел dedup status."""
    if fmt == "json":
        cmd.stdout.write(
            json.dumps(
                {
                    "error": str(exc),
                    "stream": stream,
                    "group": group,
                    "consecutive_failures": consecutive,
                    "transient_threshold": threshold,
                }
            ),
        )
    else:
        cmd.stderr.write(
            f"redis error: {exc} (consecutive={consecutive}/{threshold})",
        )
