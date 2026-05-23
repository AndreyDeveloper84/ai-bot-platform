"""Тесты для management command run_strict_flip_drill (#500 AS6).

Контракт pin-ится:

* exit codes 1..6 — каждое pre-check / assertion failure
* exit code 0 — happy path с правильным cleanup
* baseline + after delta computed correctly
* canary timeout правильно ловится
* cleanup DELETE-ит только дрилл-rows (по handler + timestamp range)
* `--skip-cleanup` оставляет строки
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest.mock import MagicMock

import pytest
from django.core.management import call_command


# ───────────────────────────────────────────────────────────────────────
# Helpers + fakes
# ───────────────────────────────────────────────────────────────────────


class _FakeRedis:
    """Минимальный fake redis-py клиента — только то что использует drill."""

    def __init__(
        self,
        *,
        group_exists: bool = True,
        active_consumer: bool = True,
        canary_acked: bool = True,
        pel_count: int = 0,
        xadd_returns: bytes = b"1234-0",
    ) -> None:
        self.group_exists = group_exists
        self.active_consumer = active_consumer
        self.canary_acked = canary_acked
        self.pel_count_value = pel_count
        self.xadd_returns = xadd_returns
        self.xadd_calls: list[dict] = []

    def xinfo_groups(self, stream: str):
        if not self.group_exists:
            return []
        return [{"name": "consumers", "consumers": 1}]

    def xinfo_consumers(self, stream: str, group: str):
        if not self.active_consumer:
            return []
        return [{"name": "worker-0", "pending": 0}]

    def xadd(self, stream: str, fields: dict):
        self.xadd_calls.append({"stream": stream, "fields": fields})
        return self.xadd_returns

    def xpending(self, stream: str, group: str):
        # Production redis-py возвращает list: [count, min, max, consumers]
        return [self.pel_count_value, None, None, []]

    def xpending_range(self, stream: str, group: str, *, min, max, count):
        # Если canary acked — pending range пустой → drill полагает что
        # entry ушла. Если canary НЕ acked — возвращаем фейковую запись.
        if self.canary_acked:
            return []
        return [{"message_id": min, "consumer": "worker-0", "time_since_delivered": 1000}]


@pytest.fixture
def fake_redis():
    return _FakeRedis()


def _run_command(*args, **kwargs):
    """Запускает command, ловит SystemExit, возвращает (stdout, stderr, exit_code)."""
    out, err = StringIO(), StringIO()
    exit_code = 0
    try:
        call_command("run_strict_flip_drill", *args, stdout=out, stderr=err, **kwargs)
    except SystemExit as exc:
        exit_code = int(exc.code) if exc.code is not None else 0
    return out.getvalue(), err.getvalue(), exit_code


# ───────────────────────────────────────────────────────────────────────
# Pre-check failures (exit codes 1, 2, 3)
# ───────────────────────────────────────────────────────────────────────


class TestPreCheckFailures:
    def test_worker_not_running_exits_1(self, monkeypatch):
        # pgrep returns rc=1 + empty stdout → "no match" в Unix-смысле.
        proc = MagicMock(returncode=1, stdout="", stderr="")
        monkeypatch.setattr("subprocess.run", lambda *a, **k: proc)

        _, err, exit_code = _run_command("--target-count", "10", "--duration", "1")
        assert exit_code == 1
        assert "worker процесс не запущен" in err

    def test_pgrep_missing_exits_1(self, monkeypatch):
        # На Windows pgrep отсутствует — FileNotFoundError.
        def _raise(*a, **k):
            raise FileNotFoundError("pgrep")

        monkeypatch.setattr("subprocess.run", _raise)
        _, err, exit_code = _run_command("--target-count", "10", "--duration", "1")
        assert exit_code == 1
        assert "pgrep" in err.lower() or "windows" in err.lower()

    def test_group_missing_exits_2(self, monkeypatch):
        proc = MagicMock(returncode=0, stdout="12345\n", stderr="")
        monkeypatch.setattr("subprocess.run", lambda *a, **k: proc)
        redis = _FakeRedis(group_exists=False)
        monkeypatch.setattr("apps.ingress.streams._client", lambda: redis)

        _, err, exit_code = _run_command("--target-count", "10", "--duration", "1")
        assert exit_code == 2
        assert "не существует" in err or "consumer group" in err

    def test_no_active_consumers_exits_2(self, monkeypatch):
        proc = MagicMock(returncode=0, stdout="12345\n", stderr="")
        monkeypatch.setattr("subprocess.run", lambda *a, **k: proc)
        redis = _FakeRedis(active_consumer=False)
        monkeypatch.setattr("apps.ingress.streams._client", lambda: redis)

        _, err, exit_code = _run_command("--target-count", "10", "--duration", "1")
        assert exit_code == 2
        assert "consumers" in err.lower()

    def test_canary_timeout_exits_3(self, monkeypatch):
        proc = MagicMock(returncode=0, stdout="12345\n", stderr="")
        monkeypatch.setattr("subprocess.run", lambda *a, **k: proc)
        redis = _FakeRedis(canary_acked=False)
        monkeypatch.setattr("apps.ingress.streams._client", lambda: redis)
        # time.monotonic кручу так чтобы canary loop сразу превысил deadline.
        from apps.workers.management.commands import run_strict_flip_drill as mod

        ticks = iter([1000.0, 1000.0, 1010.0])
        monkeypatch.setattr(mod.time, "monotonic", lambda: next(ticks))
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)

        _, err, exit_code = _run_command("--target-count", "10", "--duration", "1")
        assert exit_code == 3
        # «канаре» — общий substring для «канарейка» / «канареечная».
        assert "канаре" in err.lower() or "canary" in err.lower()


# ───────────────────────────────────────────────────────────────────────
# Pre-check unsafe table identifier (exit at CommandError → 1 via Django)
# ───────────────────────────────────────────────────────────────────────


class TestArgValidation:
    def test_unsafe_audit_table_raises(self, monkeypatch):
        proc = MagicMock(returncode=0, stdout="12345\n", stderr="")
        monkeypatch.setattr("subprocess.run", lambda *a, **k: proc)

        from django.core.management.base import CommandError

        with pytest.raises(CommandError, match="unsafe"):
            call_command(
                "run_strict_flip_drill",
                "--audit-table",
                "drop_tables; --",
                stdout=StringIO(),
                stderr=StringIO(),
            )


# ───────────────────────────────────────────────────────────────────────
# Positive assertion failures (exit codes 4, 5, 6)
# ───────────────────────────────────────────────────────────────────────


def _setup_pre_checks_pass(monkeypatch, *, pel_count: int = 0):
    """Все 3 pre-checks проходят. Возвращает (redis, audit_counts_iter)."""
    proc = MagicMock(returncode=0, stdout="12345\n", stderr="")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: proc)
    redis = _FakeRedis(pel_count=pel_count)
    monkeypatch.setattr("apps.ingress.streams._client", lambda: redis)

    # Глушим time.sleep + ускоряем time.monotonic для drill loop.
    from apps.workers.management.commands import run_strict_flip_drill as mod

    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return redis


class TestAssertionFailures:
    def test_warning_missing_exits_4(self, monkeypatch, tmp_path):
        _setup_pre_checks_pass(monkeypatch)
        empty_log = tmp_path / "worker.log"
        empty_log.write_text("[no warnings here]\n")

        # audit counts: before=10, after=70 (delta=60 — between 50 и 100)
        from apps.workers.management.commands import run_strict_flip_drill as mod

        counts = iter([10, 70])
        monkeypatch.setattr(mod, "_audit_count", lambda *a, **k: next(counts))

        _, err, exit_code = _run_command(
            "--target-count",
            "10",
            "--duration",
            "1",
            "--worker-log",
            str(empty_log),
        )
        assert exit_code == 4
        assert "ceiling не сработал" in err or "rate_budget_exhausted" in err

    def test_audit_delta_too_low_exits_5(self, monkeypatch, tmp_path):
        _setup_pre_checks_pass(monkeypatch)
        log = tmp_path / "worker.log"
        log.write_text('{"ts": "2099-01-01T00:00:00Z", "msg": "rate_budget_exhausted"}\n')

        # audit counts: before=10, after=15 (delta=5 — ниже 50)
        from apps.workers.management.commands import run_strict_flip_drill as mod

        counts = iter([10, 15])
        monkeypatch.setattr(mod, "_audit_count", lambda *a, **k: next(counts))
        monkeypatch.setattr(mod, "_grep_warning_count", lambda *a, **k: 1)

        _, err, exit_code = _run_command(
            "--target-count",
            "10",
            "--duration",
            "1",
            "--worker-log",
            str(log),
        )
        assert exit_code == 5
        assert "не выполнялся" in err.lower() or "audit delta" in err.lower()

    def test_audit_delta_too_high_exits_6(self, monkeypatch, tmp_path):
        _setup_pre_checks_pass(monkeypatch)
        log = tmp_path / "worker.log"
        log.write_text('{"ts": "2099-01-01T00:00:00Z", "msg": "rate_budget_exhausted"}\n')

        # audit counts: before=10, after=200 (delta=190 — выше 100)
        from apps.workers.management.commands import run_strict_flip_drill as mod

        counts = iter([10, 200])
        monkeypatch.setattr(mod, "_audit_count", lambda *a, **k: next(counts))
        monkeypatch.setattr(mod, "_grep_warning_count", lambda *a, **k: 1)

        _, err, exit_code = _run_command(
            "--target-count",
            "10",
            "--duration",
            "1",
            "--worker-log",
            str(log),
        )
        assert exit_code == 6
        assert "не cap-нул" in err.lower() or "ceiling" in err.lower()


# ───────────────────────────────────────────────────────────────────────
# Happy path (exit 0) + cleanup
# ───────────────────────────────────────────────────────────────────────


class TestHappyPath:
    def test_happy_path_exits_0_and_cleans(self, monkeypatch, tmp_path):
        _setup_pre_checks_pass(monkeypatch)
        log = tmp_path / "worker.log"
        log.write_text('{"ts": "2099-01-01T00:00:00Z", "msg": "rate_budget_exhausted"}\n')

        from apps.workers.management.commands import run_strict_flip_drill as mod

        counts = iter([10, 90])
        monkeypatch.setattr(mod, "_audit_count", lambda *a, **k: next(counts))
        monkeypatch.setattr(mod, "_grep_warning_count", lambda *a, **k: 3)

        cleanups: list[tuple] = []
        monkeypatch.setattr(
            mod,
            "_cleanup_audit_rows",
            lambda table, handler, s, e: cleanups.append((table, handler, s, e)) or 80,
        )

        out, _, exit_code = _run_command(
            "--target-count",
            "10",
            "--duration",
            "1",
            "--worker-log",
            str(log),
        )
        assert exit_code == 0
        assert "PASS" in out
        assert len(cleanups) == 1
        assert cleanups[0][0] == "apps_audit_event"
        assert cleanups[0][1] == "MaxHandler"

    def test_skip_cleanup_leaves_rows(self, monkeypatch, tmp_path):
        _setup_pre_checks_pass(monkeypatch)
        log = tmp_path / "worker.log"
        log.write_text('{"ts": "2099-01-01T00:00:00Z", "msg": "rate_budget_exhausted"}\n')

        from apps.workers.management.commands import run_strict_flip_drill as mod

        counts = iter([10, 90])
        monkeypatch.setattr(mod, "_audit_count", lambda *a, **k: next(counts))
        monkeypatch.setattr(mod, "_grep_warning_count", lambda *a, **k: 3)
        cleanup_called = [False]
        monkeypatch.setattr(
            mod,
            "_cleanup_audit_rows",
            lambda *a, **k: cleanup_called.__setitem__(0, True) or 0,
        )

        out, _, exit_code = _run_command(
            "--target-count",
            "10",
            "--duration",
            "1",
            "--worker-log",
            str(log),
            "--skip-cleanup",
        )
        assert exit_code == 0
        assert cleanup_called[0] is False
        assert "SKIPPED" in out

    def test_json_output(self, monkeypatch, tmp_path):
        _setup_pre_checks_pass(monkeypatch)
        log = tmp_path / "worker.log"
        log.write_text('{"ts": "2099-01-01T00:00:00Z", "msg": "rate_budget_exhausted"}\n')

        from apps.workers.management.commands import run_strict_flip_drill as mod

        counts = iter([10, 90])
        monkeypatch.setattr(mod, "_audit_count", lambda *a, **k: next(counts))
        monkeypatch.setattr(mod, "_grep_warning_count", lambda *a, **k: 3)
        monkeypatch.setattr(mod, "_cleanup_audit_rows", lambda *a, **k: 80)

        out, _, exit_code = _run_command(
            "--target-count",
            "10",
            "--duration",
            "1",
            "--worker-log",
            str(log),
            "--json",
        )
        assert exit_code == 0
        # JSON-итог идёт после человеческого вывода — берём последнюю строку.
        last_json_line = [line for line in out.splitlines() if line.startswith("{")][-1]
        data = json.loads(last_json_line)
        assert data["exit_code"] == 0
        assert data["audit_delta"] == 80
        assert data["warning_count"] == 3
        assert data["cleanup_deleted"] == 80


# ───────────────────────────────────────────────────────────────────────
# _grep_warning_count semantics
# ───────────────────────────────────────────────────────────────────────


class TestGrepWarningCount:
    """Изолированный тест парсера лога — timestamp фильтрация важна
    чтобы дрилл не считал WARNINGs от прошлых runs."""

    def test_counts_only_after_since(self, tmp_path):
        from apps.workers.management.commands.run_strict_flip_drill import (
            _grep_warning_count,
        )

        log = tmp_path / "worker.log"
        log.write_text(
            '{"ts": "2098-01-01T00:00:00Z", "msg": "rate_budget_exhausted"}\n'
            '{"ts": "2099-06-15T12:00:00Z", "msg": "rate_budget_exhausted"}\n'
            '{"ts": "2099-06-15T12:00:01Z", "msg": "tenant_missing_rate_exceeded"}\n'
            '{"ts": "2099-06-15T12:00:02Z", "msg": "unrelated"}\n'
        )
        since = datetime(2099, 1, 1, tzinfo=timezone.utc)
        assert _grep_warning_count(str(log), since) == 2

    def test_missing_log_returns_zero(self, tmp_path):
        from apps.workers.management.commands.run_strict_flip_drill import (
            _grep_warning_count,
        )

        since = datetime(2099, 1, 1, tzinfo=timezone.utc)
        assert _grep_warning_count(str(tmp_path / "does_not_exist.log"), since) == 0

    def test_no_timestamp_counts_unconditionally(self, tmp_path):
        """Лог-строки без `ts` (legacy non-JSON формат) — считаются если
        регексп совпал. Защитное поведение: лучше over-count чем
        потерять signal."""
        from apps.workers.management.commands.run_strict_flip_drill import (
            _grep_warning_count,
        )

        log = tmp_path / "worker.log"
        log.write_text("plain text rate_budget_exhausted line\n")
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        assert _grep_warning_count(str(log), since) == 1
