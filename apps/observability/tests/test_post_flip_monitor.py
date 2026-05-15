"""STRICT_TENANT_SCOPE post-flip monitor tests (DRF-731 / Sprint 8 / F2)."""

from __future__ import annotations

import datetime as _dt
from collections.abc import Generator
from unittest.mock import patch

import pytest
from django.conf import settings

from apps.audit.models import AuditLog
from apps.observability.tasks import monitor_post_flip_violations


pytestmark = pytest.mark.django_db


def _iso(dt: _dt.datetime) -> str:
    return dt.replace(tzinfo=_dt.timezone.utc).isoformat()


@pytest.fixture(autouse=True)
def _reset_env() -> Generator[None, None, None]:
    settings.STRICT_SCOPE_FLIP_AT = ""
    settings.ADMIN_MAX_CHAT_ID = ""
    settings.MAX_BOT_TOKEN = ""
    yield
    settings.STRICT_SCOPE_FLIP_AT = ""


class TestIdleStates:
    def test_no_flip_at_is_noop(self) -> None:
        result = monitor_post_flip_violations.apply().get()
        assert result["status"] == "idle"
        assert result["reason"] == "no_flip_at"

    def test_malformed_flip_at_is_error_noop(self) -> None:
        settings.STRICT_SCOPE_FLIP_AT = "not-a-date"
        result = monitor_post_flip_violations.apply().get()
        assert result["status"] == "error"

    def test_window_closed_after_24h(self) -> None:
        old = _dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(hours=25)
        settings.STRICT_SCOPE_FLIP_AT = _iso(old)
        result = monitor_post_flip_violations.apply().get()
        assert result["status"] == "window_closed"


class TestCleanWindow:
    def test_no_violations_writes_audit_heartbeat(self) -> None:
        flip = _dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(minutes=30)
        settings.STRICT_SCOPE_FLIP_AT = _iso(flip)

        with patch("apps.observability.tasks._page_post_flip_violation") as mock_page:
            monitor_post_flip_violations.apply().get()

        # No violations → no page.
        mock_page.assert_not_called()
        # Heartbeat audit row written.
        assert AuditLog.all_tenants.filter(action="observability.post_flip.checked").exists()


class TestViolationDetected:
    def test_violation_in_window_pages(self) -> None:
        flip = _dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(minutes=10)
        settings.STRICT_SCOPE_FLIP_AT = _iso(flip)

        # Seed a violation AFTER flip_at.
        row = AuditLog.all_tenants.create(
            action="tenant_scope_violation",
            target="Conversation",
            payload={},
        )
        # auto_now_add can't be overridden at create — push to 5 min ago.
        AuditLog.all_tenants.filter(pk=row.pk).update(
            created_at=_dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(minutes=5),
        )

        with patch("apps.observability.tasks._page_post_flip_violation") as mock_page:
            result = monitor_post_flip_violations.apply().get()

        assert result["status"] == "checked"
        assert result["violations"] == 1
        mock_page.assert_called_once()

    def test_violation_before_flip_ignored(self) -> None:
        """A scope violation BEFORE the flip is pre-existing — not a
        post-flip regression. Must not page.
        """
        flip = _dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(minutes=10)
        settings.STRICT_SCOPE_FLIP_AT = _iso(flip)

        row = AuditLog.all_tenants.create(action="tenant_scope_violation", payload={})
        # Older than flip_at by 1 hour.
        AuditLog.all_tenants.filter(pk=row.pk).update(
            created_at=_dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(hours=2),
        )

        with patch("apps.observability.tasks._page_post_flip_violation") as mock_page:
            result = monitor_post_flip_violations.apply().get()

        assert result["violations"] == 0
        mock_page.assert_not_called()


class TestPageRouting:
    """Sprint 10 / O2 (DRF-863) refactor: F2 routes through
    :func:`apps.observability.alerting.page`, not inline MAX. Tests now
    mock `page` directly — its own coverage lives in test_alerting.py.
    """

    def test_violation_calls_alerting_page_with_critical(self) -> None:
        flip = _dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(minutes=10)
        settings.STRICT_SCOPE_FLIP_AT = _iso(flip)

        row = AuditLog.all_tenants.create(action="tenant_scope_violation", payload={})
        AuditLog.all_tenants.filter(pk=row.pk).update(
            created_at=_dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(minutes=5),
        )

        with patch("apps.observability.alerting.page") as mock_page:
            monitor_post_flip_violations.apply().get()

        mock_page.assert_called_once()
        args, kwargs = mock_page.call_args
        assert args[0] == "critical"
        assert "STRICT_TENANT_SCOPE post-flip violation" in args[1]
        assert kwargs["dedup_key"].startswith("f2_post_flip_violation:")
