"""Tests for the d2_flood_drill management command (issue #500).

Smoke tests + dry-run + precondition coverage. Integration with the
real consume loop + Redis is exercised by the bash drill in
``docs/runbooks/strict-tenant-refuse-d2-ceilings-checklist.md`` —
this suite covers the parts that DON'T need a real Redis (CLI shape,
exit codes, assertion construction).
"""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command


pytestmark = pytest.mark.django_db


def _run(**kwargs) -> tuple[str, str, int]:
    """Invoke d2_flood_drill, capture stdout/stderr + exit code."""
    out = StringIO()
    err = StringIO()
    exit_code = 0
    try:
        call_command("d2_flood_drill", stdout=out, stderr=err, **kwargs)
    except SystemExit as exc:
        exit_code = int(exc.code) if exc.code is not None else 0
    return out.getvalue(), err.getvalue(), exit_code


class TestDryRun:
    def test_dry_run_reports_expected_behavior(self, settings):
        """Dry-run path: no XADD, prints what WOULD happen."""

        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100
        out, err, code = _run(dry_run=True, count=200, format="json")

        assert code == 0
        payload = json.loads(out)
        assert payload["dry_run"] is True
        assert payload["would_inject"] == 200
        assert payload["rate_limit"] == 100
        assert payload["expected_audit_delta"] == 100  # min(count, rate_limit)
        assert payload["expected_silenced_emits"] == 100  # count - rate_limit

    def test_dry_run_below_rate_limit_silences_zero(self, settings):
        """count <= rate_limit → cap doesn't engage in dry-run prediction."""

        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 500
        out, _, code = _run(dry_run=True, count=200, format="json")

        assert code == 0
        payload = json.loads(out)
        assert payload["expected_audit_delta"] == 200
        assert payload["expected_silenced_emits"] == 0


class TestPrecondition:
    def test_warns_when_strict_mode_off(self, settings):
        """A3 (PEL alert) requires STRICT_TENANT_REFUSE=true. When off,
        the drill prints a clear warning so the operator doesn't
        misinterpret a soft-pass as full verification."""

        settings.STRICT_TENANT_REFUSE = False
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100

        with (
            patch(
                "apps.workers.management.commands.d2_flood_drill.Command._count_audit_rows",
                side_effect=[0, 5],
            ),
            patch("apps.ingress.streams._client") as fake_client,
            patch("apps.workers.consumer.consume_once", return_value=0),
        ):
            fake_client.return_value.xadd = lambda *a, **kw: None
            _, err, _ = _run(count=5, format="json")

        assert "PRECONDITION" in err
        assert "STRICT_TENANT_REFUSE=False" in err

    def test_dry_run_does_not_warn(self, settings):
        """Dry-run skips the strict-mode check (no actual injection
        means no need for strict mode at this point)."""

        settings.STRICT_TENANT_REFUSE = False
        _, err, code = _run(dry_run=True, count=200, format="json")

        assert code == 0
        # Dry-run is silent on stderr.
        assert "PRECONDITION" not in err


class TestAssertions:
    """Assertion logic tested via mocked `_count_audit_rows`."""

    def test_rate_budget_assertion_passes_when_delta_at_cap(self, settings):
        """count=200, cap=100, audit delta=100 → A1 passes (delta == cap)."""

        settings.STRICT_TENANT_REFUSE = False  # skip A3 strict-mode
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100

        baseline = 0
        post = 100

        with (
            patch(
                "apps.workers.management.commands.d2_flood_drill.Command._count_audit_rows",
                side_effect=[baseline, post],
            ),
            patch("apps.ingress.streams._client") as fake_client,
            patch(
                "apps.workers.consumer.consume_once",
                return_value=0,
            ),
        ):
            fake_client.return_value.xadd = lambda *a, **kw: None
            out, err, code = _run(count=200, format="json")

        payload = json.loads(out)
        a1 = next(a for a in payload["assertions"] if a["name"] == "rate_budget_effective")
        assert a1["ok"] is True
        assert payload["audit_delta_rows"] == 100
        # In log-only mode, A3 is degraded-pass + A2 + A1 are real.
        assert payload["passed"] is True

    def test_rate_budget_assertion_fails_when_delta_exceeds_cap(self, settings):
        """count=200, cap=100, audit delta=200 → rate gate didn't fire
        → A1 fails. Drill exits non-zero."""

        settings.STRICT_TENANT_REFUSE = False
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100

        with (
            patch(
                "apps.workers.management.commands.d2_flood_drill.Command._count_audit_rows",
                side_effect=[0, 200],
            ),
            patch("apps.ingress.streams._client") as fake_client,
            patch("apps.workers.consumer.consume_once", return_value=0),
        ):
            fake_client.return_value.xadd = lambda *a, **kw: None
            _, err, code = _run(count=200, format="json")

        assert code == 1
        assert "DRILL FAILED" in err

    def test_rate_gate_engaged_assertion_na_when_count_below_cap(self, settings):
        """count=50, cap=100 → gate not expected to fire → A2 reports
        N/A (not failure)."""

        settings.STRICT_TENANT_REFUSE = False
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100

        with (
            patch(
                "apps.workers.management.commands.d2_flood_drill.Command._count_audit_rows",
                side_effect=[0, 50],
            ),
            patch("apps.ingress.streams._client") as fake_client,
            patch("apps.workers.consumer.consume_once", return_value=0),
        ):
            fake_client.return_value.xadd = lambda *a, **kw: None
            out, _, code = _run(count=50, format="json")

        payload = json.loads(out)
        a2 = next(a for a in payload["assertions"] if a["name"] == "rate_gate_engaged")
        # N/A reports ok=True with explanatory msg
        assert a2["ok"] is True
        assert "N/A" in a2["msg"]
        assert code == 0


class TestPelAlertAssertion:
    def test_pel_alert_skipped_when_log_only_mode(self, settings):
        """In log-only mode, A3 (PEL alert) is degraded — soft-pass with
        a clear message rather than hard-failing the drill."""

        settings.STRICT_TENANT_REFUSE = False
        settings.WORKER_TENANT_MISSING_RATE_LIMIT = 100

        with (
            patch(
                "apps.workers.management.commands.d2_flood_drill.Command._count_audit_rows",
                side_effect=[0, 100],
            ),
            patch("apps.ingress.streams._client") as fake_client,
            patch("apps.workers.consumer.consume_once", return_value=0),
        ):
            fake_client.return_value.xadd = lambda *a, **kw: None
            out, _, code = _run(count=200, format="json")

        payload = json.loads(out)
        a3 = next(a for a in payload["assertions"] if a["name"] == "pel_alert_fires")
        assert a3["ok"] is True
        assert "DEGRADED" in a3["msg"]
