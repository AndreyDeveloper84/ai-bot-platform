"""Tests for /healthz/ and /readyz/ endpoints (DRF-431 / E2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.test import AsyncClient

# django_db: readyz aggregates the Sprint-8/G4 audit_cleanup probe,
# which reads AuditLog.
pytestmark = [pytest.mark.asyncio, pytest.mark.django_db]


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
        # Sprint 6 / G3 added pipeline component checks alongside the service probes;
        # Sprint 8 / G4 (DRF-735) added chromadb_auth + audit_cleanup.
        expected = {
            "postgres",
            "redis",
            "chromadb",
            "minio",
            "intent_router",
            "skill_registry",
            "chromadb_auth",
            "audit_cleanup",
        }
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


class TestReadyzChromaSemantics:
    """Chroma readiness semantics: disabled != broken (DRF-955)."""

    async def test_readyz_ok_when_chromadb_disabled(self, settings):
        settings.CHROMA_HTTP_HOST = ""
        client = AsyncClient()
        with (
            patch("apps.orchestrator.views._ping_postgres", AsyncMock()),
            patch("apps.orchestrator.views._ping_redis", AsyncMock()),
            patch("apps.orchestrator.views._ping_minio", AsyncMock()),
        ):
            response = await client.get("/readyz/")
        assert response.status_code == 200
        body = response.json()
        assert body["checks"]["chromadb"]["ok"] is True
        assert body["checks"]["chromadb"]["error"] is None

    async def test_readyz_ok_when_chromadb_whitespace_disabled(self, settings):
        settings.CHROMA_HTTP_HOST = "   "
        client = AsyncClient()
        with (
            patch("apps.orchestrator.views._ping_postgres", AsyncMock()),
            patch("apps.orchestrator.views._ping_redis", AsyncMock()),
            patch("apps.orchestrator.views._ping_minio", AsyncMock()),
        ):
            response = await client.get("/readyz/")
        assert response.status_code == 200
        body = response.json()
        assert body["checks"]["chromadb"]["ok"] is True
        assert body["checks"]["chromadb"]["error"] is None

    async def test_readyz_ok_when_chromadb_configured_and_healthy(self, settings):
        settings.CHROMA_HTTP_HOST = "chromadb.internal"
        settings.CHROMA_HTTP_PORT = 8001

        class _FakeResponse:
            def raise_for_status(self) -> None: ...

        class _FakeClient:
            def __init__(self, **_kwargs) -> None: ...

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, url: str):
                return _FakeResponse()

        client = AsyncClient()
        with (
            patch("apps.orchestrator.views._ping_postgres", AsyncMock()),
            patch("apps.orchestrator.views._ping_redis", AsyncMock()),
            patch("httpx.AsyncClient", side_effect=lambda **kw: _FakeClient(**kw)),
            patch("httpx.head", return_value=MagicMock(status_code=200)),
            patch("apps.orchestrator.views._ping_minio", AsyncMock()),
        ):
            response = await client.get("/readyz/")
        assert response.status_code == 200
        body = response.json()
        assert body["checks"]["chromadb"]["ok"] is True

    async def test_readyz_503_when_chromadb_configured_and_broken(self, settings):
        settings.CHROMA_HTTP_HOST = "chromadb.internal"

        def _raise(*_args, **_kwargs):
            raise ConnectionError("refused")

        client = AsyncClient()
        with (
            patch("apps.orchestrator.views._ping_postgres", AsyncMock()),
            patch("apps.orchestrator.views._ping_redis", AsyncMock()),
            patch("httpx.AsyncClient", side_effect=lambda **kw: _raise()),
            patch("httpx.head", return_value=MagicMock(status_code=200)),
            patch("apps.orchestrator.views._ping_minio", AsyncMock()),
        ):
            response = await client.get("/readyz/")
        assert response.status_code == 503
        body = response.json()
        assert body["checks"]["chromadb"]["ok"] is False
        assert "ConnectionError" in body["checks"]["chromadb"]["error"]

    async def test_readyz_503_when_chromadb_auth_fails(self, settings):
        settings.CHROMA_HTTP_HOST = "chromadb.internal"
        settings.CHROMA_AUTH_TOKEN = "wrong-token"  # noqa: S105

        class _UnauthorizedResponse:
            def __init__(self, status_code: int):
                self.status_code = status_code

        class _FakeClient:
            def __init__(self, **_kwargs) -> None: ...

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, url: str):
                class _OK:
                    status_code = 200

                    def raise_for_status(self) -> None: ...

                return _OK()

        client = AsyncClient()
        with (
            patch("apps.orchestrator.views._ping_postgres", AsyncMock()),
            patch("apps.orchestrator.views._ping_redis", AsyncMock()),
            patch("httpx.AsyncClient", side_effect=lambda **kw: _FakeClient(**kw)),
            patch("httpx.head", return_value=_UnauthorizedResponse(401)),
            patch("apps.orchestrator.views._ping_minio", AsyncMock()),
        ):
            response = await client.get("/readyz/")
        assert response.status_code == 503
        body = response.json()
        assert body["checks"]["chromadb"]["ok"] is True
        assert body["checks"]["chromadb_auth"]["ok"] is False
        assert "401" in body["checks"]["chromadb_auth"]["error"]

    async def test_readyz_503_when_other_dependency_broken_while_chromadb_disabled(self, settings):
        settings.CHROMA_HTTP_HOST = ""
        client = AsyncClient()
        with (
            patch("apps.orchestrator.views._ping_postgres", AsyncMock()),
            patch(
                "apps.orchestrator.views._ping_redis",
                AsyncMock(side_effect=ConnectionError("redis down")),
            ),
            patch("apps.orchestrator.views._ping_minio", AsyncMock()),
        ):
            response = await client.get("/readyz/")
        assert response.status_code == 503
        body = response.json()
        assert body["checks"]["redis"]["ok"] is False
        assert body["checks"]["chromadb"]["ok"] is True
