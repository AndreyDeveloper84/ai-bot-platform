"""Tests for monitor_pel management command (issue #500 Item 1)."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command


class _FakeRedis:
    """Stand-in with xpending returning a configurable dict."""

    def __init__(self, pending: int = 0, raise_: Exception | None = None) -> None:
        self.pending = pending
        self.raise_ = raise_

    def xpending(self, stream: str, group: str):
        if self.raise_ is not None:
            raise self.raise_
        return {"pending": self.pending, "min": None, "max": None, "consumers": []}


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

    def test_real_redis_error_exits_three(self):
        """Genuine Redis fault (not NOGROUP) → exit 3 so the monitoring
        stack alerts on the monitor itself being broken."""
        with patch(
            "apps.ingress.streams._client",
            return_value=_FakeRedis(raise_=RuntimeError("connection refused")),
        ):
            _, err, exit_code = _run()
        assert exit_code == 3
        assert "connection refused" in err
