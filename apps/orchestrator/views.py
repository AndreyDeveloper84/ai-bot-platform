"""Health and readiness endpoints (DRF-431 / E2).

Split per review revision 4A:
- ``/healthz/`` is the k8s liveness probe — 200-OK unconditional, no
  external calls. Cheap, runs every few seconds.
- ``/readyz/`` is the k8s readiness probe — checks every backing
  service in parallel (postgres, redis, chromadb, MinIO). Returns 200
  with per-check status when all healthy, 503 with which-failed body
  when any are down. Used by Sprint 8 shadow + Sprint 9 canary cutover.

Why split:
  Liveness probes fire frequently (every 5–10s). If liveness checks
  the DB, every kubelet poll opens a connection — wasteful and easy
  to overload. Readiness probes fire less frequently (every 30–60s)
  and gate whether traffic should route to this pod.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from django.conf import settings
from django.db import connections
from django.http import JsonResponse

logger = logging.getLogger(__name__)


def healthz(request) -> JsonResponse:
    """Liveness probe: 200 OK unconditionally.

    Returning 200 here means "this pod is alive — the WSGI loop is
    running". It does NOT mean "ready to serve traffic". A pod can be
    alive but not ready (e.g. boot phase before DB connection
    established, or shedding traffic during shutdown).
    """

    return JsonResponse({"status": "ok"})


async def readyz(request) -> JsonResponse:
    """Readiness probe: matrix check of every backing service.

    Returns:
      200 with ``{"status": "ok", "checks": {...per-service...}}`` if
      every service responds healthy within 1 second.
      503 with ``{"status": "fail", "checks": {...}}`` if any service
      is down or times out. Each ``checks[name]`` dict has
      ``{"ok": bool, "error": str | None, "duration_ms": int}``.

    Checks run in parallel via ``asyncio.gather`` with a 1s timeout per
    service. Total /readyz/ latency stays under 2s even when something
    is hung.
    """

    checks = await asyncio.gather(
        _check("postgres", _ping_postgres),
        _check("redis", _ping_redis),
        _check("chromadb", _ping_chromadb),
        _check("minio", _ping_minio),
        return_exceptions=False,
    )
    result = dict(checks)
    all_ok = all(check["ok"] for check in result.values())
    status_code = 200 if all_ok else 503
    return JsonResponse(
        {
            "status": "ok" if all_ok else "fail",
            "checks": result,
        },
        status=status_code,
    )


async def _check(name: str, fn) -> tuple[str, dict[str, Any]]:
    """Wrap a check fn in timing + error handling."""

    import time

    start = time.monotonic()
    try:
        await asyncio.wait_for(fn(), timeout=1.0)
        return name, {
            "ok": True,
            "error": None,
            "duration_ms": int((time.monotonic() - start) * 1000),
        }
    except TimeoutError:
        return name, {"ok": False, "error": "timeout", "duration_ms": 1000}
    except Exception as exc:  # noqa: BLE001 — readiness is a status surface
        return name, {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": int((time.monotonic() - start) * 1000),
        }


async def _ping_postgres() -> None:
    """Run a trivial query against the default database."""

    from asgiref.sync import sync_to_async

    def _ping_sync() -> None:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()

    await sync_to_async(_ping_sync, thread_sensitive=False)()


async def _ping_redis() -> None:
    """PING the Redis server using the default URL."""

    import redis.asyncio as aioredis

    client = aioredis.from_url(getattr(settings, "REDIS_URL", "redis://localhost:6379/0"))
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _ping_chromadb() -> None:
    """HTTP heartbeat on chromadb."""

    import httpx

    host = getattr(settings, "CHROMA_HTTP_HOST", "localhost")
    port = getattr(settings, "CHROMA_HTTP_PORT", 8000)
    url = f"http://{host}:{port}/api/v2/heartbeat"
    async with httpx.AsyncClient(timeout=1.0) as client:
        response = await client.get(url)
        response.raise_for_status()


async def _ping_minio() -> None:
    """HTTP /minio/health/live check."""

    import httpx

    endpoint = getattr(settings, "S3_ENDPOINT_URL", "http://localhost:9000")
    url = f"{endpoint.rstrip('/')}/minio/health/live"
    async with httpx.AsyncClient(timeout=1.0) as client:
        response = await client.get(url)
        response.raise_for_status()
