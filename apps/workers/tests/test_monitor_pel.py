"""Tests for monitor_pel management command (issue #500 Item 1)."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command


class _FakeRedis:
    """Stand-in with xpending returning a configurable shape.

    redis-py's actual XPENDING summary form returns a LIST:
    ``[total_pending, min_id, max_id, [[consumer_name, count], ...]]``.
    Some wrappers normalise to dict. Default mode is ``list`` (matches
    production redis-py), with ``dict`` mode kept for backward compat
    testing — both shapes must work.
    """

    def __init__(
        self,
        pending: int = 0,
        raise_: Exception | None = None,
        shape: str = "list",
    ) -> None:
        self.pending = pending
        self.raise_ = raise_
        self.shape = shape

    def xpending(self, stream: str, group: str):
        if self.raise_ is not None:
            raise self.raise_
        if self.shape == "dict":
            return {
                "pending": self.pending,
                "min": None,
                "max": None,
                "consumers": [],
            }
        # Production redis-py shape: list summary.
        return [self.pending, None, None, []]


def _run(*args, **kwargs):
    """Run the command, capturing stdout."""
    out = StringIO()
    err = StringIO()
    exit_code: int | None = None
    try:
        call_command("monitor_pel", *args, stdout=out, stderr=err, **kwargs)
        exit_code = 0
    except SystemExit as exc:
        exit_code = int(exc.code) if exc.code is not None else 0
    return out.getvalue(), err.getvalue(), exit_code


class TestThresholds:
    def test_pending_below_warning_exits_zero(self):
        with patch("apps.ingress.streams._client", return_value=_FakeRedis(pending=500)):
            out, _, exit_code = _run("--warning", "1000", "--page", "5000")
        assert exit_code == 0
        assert "pending=500" in out
        assert "severity=ok" in out

    def test_pending_at_warning_exits_one(self):
        with patch("apps.ingress.streams._client", return_value=_FakeRedis(pending=1000)):
            out, _, exit_code = _run("--warning", "1000", "--page", "5000")
        assert exit_code == 1
        assert "severity=warning" in out

    def test_pending_at_page_exits_two(self):
        with patch("apps.ingress.streams._client", return_value=_FakeRedis(pending=5000)):
            out, _, exit_code = _run("--warning", "1000", "--page", "5000")
        assert exit_code == 2
        assert "severity=page" in out

    def test_no_thresholds_exits_zero_with_count(self):
        """Bare call (no --warning / --page) just reports count, exit 0."""
        with patch("apps.ingress.streams._client", return_value=_FakeRedis(pending=42)):
            out, _, exit_code = _run()
        assert exit_code == 0
        assert "pending=42" in out


class TestOutputFormats:
    def test_json_format(self):
        with patch("apps.ingress.streams._client", return_value=_FakeRedis(pending=1500)):
            out, _, _ = _run("--format", "json", "--warning", "1000", "--page", "5000")
        data = json.loads(out)
        assert data["pending"] == 1500
        assert data["severity"] == "warning"
        assert data["warning_threshold"] == 1000
        assert data["page_threshold"] == 5000

    def test_custom_stream_group(self):
        with patch("apps.ingress.streams._client", return_value=_FakeRedis(pending=7)):
            out, _, _ = _run(
                "--stream",
                "ingress:telegram",
                "--group",
                "tg-consumers",
                "--format",
                "json",
            )
        data = json.loads(out)
        assert data["stream"] == "ingress:telegram"
        assert data["group"] == "tg-consumers"


class TestXpendingShapes:
    """AS1 regression guard — production redis-py returns LIST, not dict.

    Tech-lead adversarial pass on PR #528 caught the original dict-only
    assumption; without these tests the fake would have continued to
    mask the production AttributeError → exit 3 → self-page loop."""

    def test_list_shape_parsed(self):
        """Production redis-py: ``[total, min, max, [[consumer, count], ...]]``."""
        with patch(
            "apps.ingress.streams._client",
            return_value=_FakeRedis(pending=1234, shape="list"),
        ):
            out, _, _ = _run("--format", "json")
        data = json.loads(out)
        assert data["pending"] == 1234

    def test_dict_shape_parsed(self):
        """Some redis-py wrappers normalise to dict — keep back-compat."""
        with patch(
            "apps.ingress.streams._client",
            return_value=_FakeRedis(pending=1234, shape="dict"),
        ):
            out, _, _ = _run("--format", "json")
        data = json.loads(out)
        assert data["pending"] == 1234


class TestFailureModes:
    def test_nogroup_treated_as_zero_pending(self):
        """Cold-start: stream/group not yet bootstrapped → pending=0,
        not a hard crash. Avoids paging on a freshly-deployed environment
        before the consumer first ACKs."""
        import redis.exceptions

        err = redis.exceptions.ResponseError("NOGROUP No such key 'ingress:max' or consumer group")
        with patch("apps.ingress.streams._client", return_value=_FakeRedis(raise_=err)):
            out, _, exit_code = _run("--warning", "1000", "--page", "5000")
        assert exit_code == 0
        assert "pending=0" in out

    def test_real_redis_error_exits_three_when_dedup_disabled(self, tmp_path):
        """Genuine Redis fault (not NOGROUP) → exit 3 если dedup отключён
        через ``--transient-runs 0``. Сохраняет pre-#577 behavior для
        callers которые предпочитают immediate page."""
        with patch(
            "apps.ingress.streams._client",
            return_value=_FakeRedis(raise_=RuntimeError("connection refused")),
        ):
            _, err, exit_code = _run(
                "--state-file",
                str(tmp_path / "state.json"),
                "--transient-runs",
                "0",  # dedup off
            )
        assert exit_code == 3
        assert "connection refused" in err


# ───────────────────────────────────────────────────────────────────────
# #577 — transient-blip dedup behaviour
# ───────────────────────────────────────────────────────────────────────


class TestTransientBlipDedup:
    """N consecutive Redis faults перед exit 3. Cron не paged-ит каждые
    60s на single blip. Acceptance criteria из issue #577:

    * N=3 consecutive exit-3 before paging
    * 2 consecutive transient errors → exit 0
    * 3rd consecutive → exit 3
    * Default tunable через ``MONITOR_PEL_TRANSIENT_RUNS``
    """

    def test_first_fault_exits_zero_below_threshold(self, tmp_path):
        """1st consecutive fault → exit 0 + warning log (counter=1, threshold=3)."""
        state = tmp_path / "state.json"
        with patch(
            "apps.ingress.streams._client",
            return_value=_FakeRedis(raise_=RuntimeError("transient blip")),
        ):
            _, err, exit_code = _run(
                "--state-file",
                str(state),
                "--transient-runs",
                "3",
            )
        assert exit_code == 0
        assert state.exists()
        import json as _json

        assert _json.loads(state.read_text())["consecutive_failures"] == 1

    def test_third_consecutive_fault_exits_three(self, tmp_path):
        """3 consecutive faults подряд: 1st+2nd → 0, 3rd → 3."""
        state = tmp_path / "state.json"
        with patch(
            "apps.ingress.streams._client",
            return_value=_FakeRedis(raise_=RuntimeError("persistent fault")),
        ):
            _, _, exit_1 = _run(
                "--state-file",
                str(state),
                "--transient-runs",
                "3",
            )
            _, _, exit_2 = _run(
                "--state-file",
                str(state),
                "--transient-runs",
                "3",
            )
            _, _, exit_3 = _run(
                "--state-file",
                str(state),
                "--transient-runs",
                "3",
            )
        assert exit_1 == 0
        assert exit_2 == 0
        assert exit_3 == 3

    def test_successful_run_resets_counter(self, tmp_path):
        """После recovery (XPENDING succeeded) counter сбрасывается до 0.
        Следующий fault опять начинает с 1, не с прежнего state."""
        state = tmp_path / "state.json"
        import json as _json

        # 2 fault → counter=2
        with patch(
            "apps.ingress.streams._client",
            return_value=_FakeRedis(raise_=RuntimeError("blip")),
        ):
            _run("--state-file", str(state), "--transient-runs", "3")
            _run("--state-file", str(state), "--transient-runs", "3")
        assert _json.loads(state.read_text())["consecutive_failures"] == 2

        # Recovery success → counter reset
        with patch(
            "apps.ingress.streams._client",
            return_value=_FakeRedis(pending=100),
        ):
            _run("--state-file", str(state), "--transient-runs", "3")
        assert _json.loads(state.read_text())["consecutive_failures"] == 0

        # Следующий fault — это снова первый, exit 0 не exit 3.
        with patch(
            "apps.ingress.streams._client",
            return_value=_FakeRedis(raise_=RuntimeError("new blip")),
        ):
            _, _, exit_code = _run(
                "--state-file",
                str(state),
                "--transient-runs",
                "3",
            )
        assert exit_code == 0

    def test_nogroup_resets_counter_not_increments(self, tmp_path):
        """NOGROUP (consumer cold-start) — это НЕ fault. Counter сбрасывается.
        Защищает от ложного increment в cron loop сразу после deploy."""
        import json as _json
        import redis.exceptions

        state = tmp_path / "state.json"

        # Fault → counter=1
        with patch(
            "apps.ingress.streams._client",
            return_value=_FakeRedis(raise_=RuntimeError("blip")),
        ):
            _run("--state-file", str(state), "--transient-runs", "3")
        assert _json.loads(state.read_text())["consecutive_failures"] == 1

        # NOGROUP → counter reset, exit 0.
        nogroup_err = redis.exceptions.ResponseError("NOGROUP no such key")
        with patch(
            "apps.ingress.streams._client",
            return_value=_FakeRedis(raise_=nogroup_err),
        ):
            _, _, exit_code = _run("--state-file", str(state), "--transient-runs", "3")
        assert exit_code == 0
        assert _json.loads(state.read_text())["consecutive_failures"] == 0

    def test_dedup_disabled_when_transient_runs_zero(self, tmp_path):
        """``--transient-runs 0`` означает «no dedup» — любой fault → exit 3.
        Сохраняет escape hatch на случай когда оператор хочет immediate page."""
        with patch(
            "apps.ingress.streams._client",
            return_value=_FakeRedis(raise_=RuntimeError("fault")),
        ):
            _, _, exit_code = _run(
                "--state-file",
                str(tmp_path / "s.json"),
                "--transient-runs",
                "0",
            )
        assert exit_code == 3

    def test_env_var_threshold_override(self, tmp_path, monkeypatch):
        """Env ``MONITOR_PEL_TRANSIENT_RUNS=1`` → page after first fault."""
        monkeypatch.setenv("MONITOR_PEL_TRANSIENT_RUNS", "1")
        with patch(
            "apps.ingress.streams._client",
            return_value=_FakeRedis(raise_=RuntimeError("fault")),
        ):
            _, _, exit_code = _run("--state-file", str(tmp_path / "s.json"))
        assert exit_code == 3

    def test_corrupt_state_file_falls_back_to_zero(self, tmp_path):
        """Если state file пишет garbage (поломан, half-written) — counter
        treats as 0. Не raise, не stale-counter."""
        state = tmp_path / "state.json"
        state.write_text("{not valid json")

        with patch(
            "apps.ingress.streams._client",
            return_value=_FakeRedis(raise_=RuntimeError("blip")),
        ):
            _, _, exit_code = _run("--state-file", str(state), "--transient-runs", "3")
        # Counter обнулился (= 1 после первого fault), exit 0.
        assert exit_code == 0
        import json as _json

        assert _json.loads(state.read_text())["consecutive_failures"] == 1

    def test_unwritable_state_dir_does_not_crash(self, tmp_path, caplog):
        """Если state file не может быть записан (permissions, missing parent),
        команда не crashes — just log warning, fall back в paranoid mode
        (counter всегда 0, page on first fault если threshold=0; иначе
        exit 0 на каждый fault — оператор увидит в логе)."""
        import logging as _logging

        unwritable = "/nonexistent/dir/that/cannot/be/created/state.json"

        # Just verify не crash. Не валидируем точное поведение exit (зависит
        # от того что _bump_state_file возвращает в случае write fail).
        with patch(
            "apps.ingress.streams._client",
            return_value=_FakeRedis(raise_=RuntimeError("blip")),
        ):
            with caplog.at_level(
                _logging.WARNING, logger="apps.workers.management.commands.monitor_pel"
            ):
                _, _, exit_code = _run(
                    "--state-file",
                    unwritable,
                    "--transient-runs",
                    "3",
                )
        # Не raise — это главное.
        assert exit_code in (0, 3)
