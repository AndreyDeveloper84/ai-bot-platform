"""JSON log shape integration test (DRF-715 / Sprint 8 / J3).

Drives a real `LogRecord` through the J2 ``ContextFilter`` + J1
``JsonFormatter`` and asserts every line is parseable JSON with the
required keys.

The orchestrator pipeline is not exercised here — that's a heavyweight
fixture and the contract we care about is the formatter/filter pair,
not the call sites. The G1 stack test (DRF-732) covers logs inside a
real turn() once F1/F2/G1 land in Sprint 8 Wave 5+.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest

from apps.observability.logging import ContextFilter, JsonFormatter
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


@pytest.fixture
def captured() -> Iterator[list[str]]:
    """Custom handler that captures JSON-formatted lines into a list.

    Beats ``caplog`` for this test because we need the FINAL formatted
    string — caplog captures records (pre-format) by default.
    """
    sink: list[str] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            sink.append(self.format(record))

    handler = _ListHandler()
    handler.addFilter(ContextFilter())
    handler.setFormatter(JsonFormatter())

    logger = logging.getLogger("apps.test.j3")
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)

    yield sink

    logger.removeHandler(handler)


@pytest.fixture
def shared_logger() -> logging.Logger:
    return logging.getLogger("apps.test.j3")


_REQUIRED_KEYS = ("ts", "level", "logger", "msg", "tenant_id", "trace_id")


class TestEveryLineIsValidJSON:
    def test_basic_info_log(self, captured: list[str], shared_logger: logging.Logger) -> None:
        shared_logger.info("hello %s", "world")
        assert len(captured) == 1
        parsed = json.loads(captured[0])
        for key in _REQUIRED_KEYS:
            assert key in parsed, f"missing required key {key!r}: {parsed}"
        assert parsed["msg"] == "hello world"

    def test_multiple_lines_each_parse(
        self, captured: list[str], shared_logger: logging.Logger
    ) -> None:
        shared_logger.info("line 1")
        shared_logger.warning("line 2")
        shared_logger.error("line 3")
        assert len(captured) == 3
        for line in captured:
            parsed = json.loads(line)
            for key in _REQUIRED_KEYS:
                assert key in parsed, f"missing {key} in {parsed}"


class TestTenantContextThreaded:
    @pytest.mark.django_db
    def test_log_inside_tenant_scope_carries_tenant_id(
        self, captured: list[str], shared_logger: logging.Logger
    ) -> None:
        tenant = Tenant.objects.create(slug="j3-tenant", name="J3")
        with tenant_scope(tenant):
            shared_logger.info("inside scope")

        parsed = json.loads(captured[0])
        assert parsed["tenant_id"] == str(tenant.id)

    def test_log_outside_tenant_scope_empty_tenant_id(
        self, captured: list[str], shared_logger: logging.Logger
    ) -> None:
        shared_logger.info("no scope")
        parsed = json.loads(captured[0])
        assert parsed["tenant_id"] == ""


class TestExtraThreaded:
    def test_extra_keys_in_payload(
        self, captured: list[str], shared_logger: logging.Logger
    ) -> None:
        shared_logger.info("turn done", extra={"chunks": 3, "score": 0.91})
        parsed = json.loads(captured[0])
        assert parsed["extra"]["chunks"] == 3
        assert parsed["extra"]["score"] == 0.91


class TestNoEmbeddedNewlines:
    def test_multiline_msg_collapses(
        self, captured: list[str], shared_logger: logging.Logger
    ) -> None:
        """Log shippers split on newline — formatter must emit one line
        per record even when the message text contains \\n.
        """
        shared_logger.info("multi\nline\nmsg")
        assert len(captured) == 1
        assert "\n" not in captured[0]
        # JSON content still parses + preserves the literal \n.
        parsed = json.loads(captured[0])
        assert "\n" in parsed["msg"]


class TestRequiredKeysDriftGuard:
    def test_no_required_key_absent(
        self, captured: list[str], shared_logger: logging.Logger
    ) -> None:
        """Drift assertion — if a future refactor accidentally drops one
        of the required keys (ts / level / logger / msg / tenant_id /
        trace_id), this test names which one and fails the build before
        the log shipper does in production.
        """
        shared_logger.info("drift sentinel")
        parsed = json.loads(captured[0])
        missing = [k for k in _REQUIRED_KEYS if k not in parsed]
        assert not missing, f"required JSON log keys missing: {missing}"
