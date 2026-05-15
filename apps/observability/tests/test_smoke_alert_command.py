"""smoke_alert management command tests (Sprint 10 / O3 / DRF-864)."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


pytestmark = pytest.mark.django_db


class TestSmokeAlertCommand:
    def test_dry_run_does_not_call_page(self) -> None:
        with patch("apps.observability.management.commands.smoke_alert.page") as mock_page:
            stdout = StringIO()
            call_command(
                "smoke_alert",
                "--severity=warning",
                "--title=test",
                "--dry-run",
                stdout=stdout,
            )
        mock_page.assert_not_called()
        assert "[dry-run]" in stdout.getvalue()

    def test_real_run_invokes_page_with_severity(self) -> None:
        with patch(
            "apps.observability.management.commands.smoke_alert.page",
            return_value=True,
        ) as mock_page:
            call_command(
                "smoke_alert",
                "--severity=error",
                "--title=test",
                "--body=detail",
                stdout=StringIO(),
            )
        mock_page.assert_called_once()
        args, kwargs = mock_page.call_args
        assert args[0] == "error"
        assert args[1] == "test"
        assert args[2] == "detail"
        # Per-run dedup_key ensures repeat smokes don't dedup.
        assert kwargs["dedup_key"].startswith("smoke:")

    def test_failure_exits_nonzero(self) -> None:
        with patch(
            "apps.observability.management.commands.smoke_alert.page",
            return_value=False,
        ):
            with pytest.raises(SystemExit) as exc_info:
                call_command(
                    "smoke_alert",
                    "--severity=warning",
                    "--title=t",
                    stdout=StringIO(),
                    stderr=StringIO(),
                )
            assert exc_info.value.code == 1

    def test_empty_title_rejected(self) -> None:
        with pytest.raises(CommandError, match="title must be non-empty"):
            call_command("smoke_alert", "--title=   ", stdout=StringIO())

    def test_default_severity_is_warning(self) -> None:
        # Default severity must be the safe one — accidental runs don't wake.
        with patch(
            "apps.observability.management.commands.smoke_alert.page",
            return_value=True,
        ) as mock_page:
            call_command("smoke_alert", "--title=t", stdout=StringIO())
        assert mock_page.call_args.args[0] == "warning"
