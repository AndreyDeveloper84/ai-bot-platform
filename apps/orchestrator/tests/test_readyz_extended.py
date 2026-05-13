"""Extended /readyz/ probe coverage (DRF-735 / Sprint 8 / G4).

Covers the two new health probes (chromadb_auth + audit_cleanup_recent)
that Sprint 8 layered on top of the Sprint 6 G3 stub.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from apps.orchestrator.health import (
    check_audit_cleanup_recent,
    check_chromadb_auth,
    pipeline_health,
)


class TestChromadbProbe:
    def test_no_remote_host_short_circuits_ok(self, settings) -> None:
        settings.CHROMA_HTTP_HOST = ""
        result = check_chromadb_auth()
        assert result["ok"] is True
        assert result["detail"] == "no_remote_chromadb"

    def test_remote_returns_200_ok(self, settings) -> None:
        settings.CHROMA_HTTP_HOST = "chromadb.internal"
        settings.CHROMA_HTTP_PORT = 8001
        settings.CHROMA_AUTH_TOKEN = "test-token"  # noqa: S105

        mock_resp = MagicMock(status_code=200)
        with patch("httpx.head", return_value=mock_resp) as mock_head:
            result = check_chromadb_auth()

        assert result["ok"] is True
        assert mock_head.call_args.kwargs["headers"] == {"Authorization": "Bearer test-token"}

    def test_remote_returns_401_unhealthy(self, settings) -> None:
        settings.CHROMA_HTTP_HOST = "chromadb.internal"
        settings.CHROMA_AUTH_TOKEN = "wrong-token"  # noqa: S105

        mock_resp = MagicMock(status_code=401)
        with patch("httpx.head", return_value=mock_resp):
            result = check_chromadb_auth()

        assert result["ok"] is False
        assert "401" in result["error"]

    def test_network_error_unhealthy(self, settings) -> None:
        settings.CHROMA_HTTP_HOST = "chromadb.internal"

        with patch("httpx.head", side_effect=ConnectionError("refused")):
            result = check_chromadb_auth()

        assert result["ok"] is False
        assert "ConnectionError" in result["error"]


class TestAuditCleanupProbe:
    @pytest.mark.django_db
    def test_empty_audit_table_ok(self) -> None:
        result = check_audit_cleanup_recent()
        assert result["ok"] is True
        assert result["detail"] == "audit_table_empty"

    @pytest.mark.django_db
    def test_recent_audit_row_ok(self) -> None:
        from apps.audit.models import AuditLog

        AuditLog.all_tenants.create(action="t.recent", target="x", payload={})
        result = check_audit_cleanup_recent()
        assert result["ok"] is True

    @pytest.mark.django_db
    def test_stale_audit_row_unhealthy(self) -> None:
        from apps.audit.models import AuditLog

        row = AuditLog.all_tenants.create(action="t.stale", target="x", payload={})
        # auto_now_add can't be overridden at create; manually push back the timestamp.
        stale_ts = datetime.now(tz=timezone.utc) - timedelta(hours=26)
        AuditLog.all_tenants.filter(pk=row.pk).update(created_at=stale_ts)

        result = check_audit_cleanup_recent()
        assert result["ok"] is False
        assert "old" in result["error"]


class TestAggregator:
    @pytest.mark.django_db
    def test_pipeline_health_includes_new_probes(self, settings) -> None:
        settings.CHROMA_HTTP_HOST = ""
        result = pipeline_health()
        # Sprint 6 G3 entries.
        assert "intent_router" in result
        assert "skill_registry" in result
        # Sprint 8 G4 additions.
        assert "chromadb_auth" in result
        assert "audit_cleanup" in result
