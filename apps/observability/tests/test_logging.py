"""JsonFormatter tests (DRF-713 / Sprint 8 / J1)."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from apps.observability.logging import JsonFormatter


@pytest.fixture
def formatter() -> JsonFormatter:
    return JsonFormatter()


def _make_record(
    msg: str,
    *,
    level: int = logging.INFO,
    logger_name: str = "apps.test",
    extra: dict[str, Any] | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=logger_name,
        level=level,
        pathname="x.py",
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


class TestBasicShape:
    def test_required_keys(self, formatter: JsonFormatter) -> None:
        record = _make_record("hello world")
        out = json.loads(formatter.format(record))
        for key in ("ts", "level", "logger", "msg", "tenant_id", "trace_id", "span_id"):
            assert key in out, f"missing {key}: {out}"

    def test_level_and_logger(self, formatter: JsonFormatter) -> None:
        record = _make_record("boom", level=logging.WARNING, logger_name="apps.skills.faq")
        out = json.loads(formatter.format(record))
        assert out["level"] == "WARNING"
        assert out["logger"] == "apps.skills.faq"
        assert out["msg"] == "boom"

    def test_ts_is_iso8601_utc(self, formatter: JsonFormatter) -> None:
        record = _make_record("x")
        out = json.loads(formatter.format(record))
        # 2026-05-13T13:55:00.123Z shape
        assert out["ts"].endswith("Z")
        assert "T" in out["ts"]
        assert "." in out["ts"]  # millisecond precision


class TestExtra:
    def test_extra_keys_preserved(self, formatter: JsonFormatter) -> None:
        record = _make_record("x", extra={"chunks": 3, "score": 0.91})
        out = json.loads(formatter.format(record))
        assert out["extra"] == {"chunks": 3, "score": 0.91}

    def test_no_extra_block_when_empty(self, formatter: JsonFormatter) -> None:
        record = _make_record("x")
        out = json.loads(formatter.format(record))
        assert "extra" not in out

    def test_stdlib_fields_excluded_from_extra(self, formatter: JsonFormatter) -> None:
        """`pathname`, `funcName`, etc. must NOT leak into extra."""
        record = _make_record("x", extra={"chunks": 1})
        out = json.loads(formatter.format(record))
        assert "pathname" not in out.get("extra", {})
        assert "funcName" not in out.get("extra", {})


class TestContextFields:
    def test_context_filter_attributes_become_top_level(self, formatter: JsonFormatter) -> None:
        """J2 ContextFilter sets tenant_id / trace_id / span_id on the record;
        JsonFormatter must surface them as named keys (not under extra).
        """
        record = _make_record(
            "x",
            extra={
                "tenant_id": "t-abc",
                "trace_id": "0123456789abcdef0123456789abcdef",
                "span_id": "0123456789abcdef",
                "pipeline_step": "skill",
            },
        )
        out = json.loads(formatter.format(record))
        assert out["tenant_id"] == "t-abc"
        assert out["trace_id"] == "0123456789abcdef0123456789abcdef"
        assert out["span_id"] == "0123456789abcdef"
        assert out["pipeline_step"] == "skill"
        # Verify they are NOT duplicated under extra
        assert "tenant_id" not in out.get("extra", {})

    def test_missing_context_renders_empty_string(self, formatter: JsonFormatter) -> None:
        record = _make_record("x")
        out = json.loads(formatter.format(record))
        assert out["tenant_id"] == ""
        assert out["trace_id"] == ""


class TestExceptionInfo:
    def test_exc_info_rendered(self, formatter: JsonFormatter) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="apps.test",
                level=logging.ERROR,
                pathname="x.py",
                lineno=1,
                msg="caught",
                args=(),
                exc_info=sys.exc_info(),
            )
        out = json.loads(formatter.format(record))
        assert "exc_info" in out
        assert "ValueError" in out["exc_info"]


class TestSingleLine:
    def test_no_embedded_newlines(self, formatter: JsonFormatter) -> None:
        """JSON output must fit one log line — embedded newlines would
        confuse log shippers that split on `\\n`.
        """
        record = _make_record("multi\nline\nmessage", extra={"k": "v\nw"})
        result = formatter.format(record)
        # The format() output itself is single-line (json.dumps escapes
        # \n inside string values).
        assert "\n" not in result
