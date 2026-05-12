"""Tests for /healthz/ and /readyz/ endpoints (DRF-431 / E2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from django.test import AsyncClient

pytestmark = pytest.mark.asyncio


class TestHealthz:
    """Liveness probe — always 200, no external calls."""

    async def test_returns_200_with_status_ok(self):
        client = AsyncClient()
        response = await client.get("/healthz/")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    async def test_no_db_call(self):
        # Patch the DB ping to ensure it's NEVER called by /healthz/.
        client = AsyncClient()
        with patch(
            "apps.orchestrator.views._ping_postgres",
            AsyncMock(side_effect=AssertionError("liveness should not ping DB")),
        ):
            response = await client.get("/healthz/")
        assert response.status_code == 200


class TestReadyzAllHealthy:
    """Readiness probe — 200 with matrix when all services respond."""

    async def test_returns_200_with_per_service_checks(self):
        client = AsyncClient()
        with (
            patch("apps.orchestrator.views._ping_postgres", AsyncMock()),
            patch("apps.orchestrator.views._ping_redis", AsyncMock()),
            patch("apps.orchestrator.views._ping_chromadb", AsyncMock()),
            patch("apps.orchestrator.views._ping_minio", AsyncMock()),
        ):
            response = await client.get("/readyz/")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        # Sprint 6 / G3 added pipeline component checks alongside the service probes.
        expected = {"postgres", "redis", "chromadb", "minio", "intent_router", "skill_registry"}
        assert set(body["checks"].keys()) == expected
        for check in body["checks"].values():
            assert check["ok"] is True
            assert check["error"] is None
            assert isinstance(check["duration_ms"], int)


class TestReadyzFailure:
    """Readiness probe — 503 with which-failed when any check is down."""

    async def test_503_when_redis_down(self):
        client = AsyncClient()
        with (
            patch("apps.orchestrator.views._ping_postgres", AsyncMock()),
            patch(
                "apps.orchestrator.views._ping_redis",
                AsyncMock(side_effect=ConnectionError("redis down")),
            ),
            patch("apps.orchestrator.views._ping_chromadb", AsyncMock()),
            patch("apps.orchestrator.views._ping_minio", AsyncMock()),
        ):
            response = await client.get("/readyz/")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "fail"
        assert body["checks"]["redis"]["ok"] is False
        assert "ConnectionError" in body["checks"]["redis"]["error"]
        # Other services still report ok.
        assert body["checks"]["postgres"]["ok"] is True

    async def test_503_when_chromadb_times_out(self):
        client = AsyncClient()

        async def hang() -> None:
            import asyncio

            await asyncio.sleep(10)

        with (
            patch("apps.orchestrator.views._ping_postgres", AsyncMock()),
            patch("apps.orchestrator.views._ping_redis", AsyncMock()),
            patch("apps.orchestrator.views._ping_chromadb", hang),
            patch("apps.orchestrator.views._ping_minio", AsyncMock()),
        ):
            response = await client.get("/readyz/")
        assert response.status_code == 503
        body = response.json()
        assert body["checks"]["chromadb"]["ok"] is False
        assert body["checks"]["chromadb"]["error"] == "timeout"
        assert body["checks"]["chromadb"]["duration_ms"] == 1000

    async def test_multiple_failures_all_reported(self):
        client = AsyncClient()
        with (
            patch(
                "apps.orchestrator.views._ping_postgres",
                AsyncMock(side_effect=RuntimeError("pg down")),
            ),
            patch("apps.orchestrator.views._ping_redis", AsyncMock()),
            patch(
                "apps.orchestrator.views._ping_chromadb",
                AsyncMock(side_effect=RuntimeError("chroma down")),
            ),
            patch("apps.orchestrator.views._ping_minio", AsyncMock()),
        ):
            response = await client.get("/readyz/")
        assert response.status_code == 503
        body = response.json()
        assert body["checks"]["postgres"]["ok"] is False
        assert body["checks"]["chromadb"]["ok"] is False
        assert body["checks"]["redis"]["ok"] is True
        assert body["checks"]["minio"]["ok"] is True


class TestExcludedFromTenantMiddleware:
    """The middleware MUST skip /healthz/ and /readyz/.

    Otherwise strict-mode tenant scope would 400 these probes (no
    X-Tenant header). The middleware's EXCLUDED_PATH_PREFIXES list is
    asserted in test_middleware.py; this test confirms the
    URL-routing wiring is correct so requests actually reach the views.
    """

    async def test_healthz_passes_strict_mode(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        client = AsyncClient()
        # No X-Tenant header — strict mode would 400 on /api/v1/* but
        # /healthz/ is in the exclusion list.
        response = await client.get("/healthz/")
        assert response.status_code == 200

    async def test_readyz_passes_strict_mode(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        client = AsyncClient()
        with (
            patch("apps.orchestrator.views._ping_postgres", AsyncMock()),
            patch("apps.orchestrator.views._ping_redis", AsyncMock()),
            patch("apps.orchestrator.views._ping_chromadb", AsyncMock()),
            patch("apps.orchestrator.views._ping_minio", AsyncMock()),
        ):
            response = await client.get("/readyz/")
        assert response.status_code == 200
