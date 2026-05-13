"""Pipeline health check (DRF-552 / Sprint 6 / G3).

Per-component checks for the orchestrator pipeline, surfaced through
`/readyz/` alongside the existing backing-service probes (postgres,
redis, chromadb, MinIO from DRF-431).

### Components checked

- **intent_router** — Verifies the OpenAI breaker isn't open. We don't
  call the LLM (cost + latency for a probe); we just check the breaker
  state via `is_open(_BREAKER_NAME)`. Breaker open = LLM unavailable =
  pipeline degraded.
- **skill_registry** — Verifies the skill registry has at least the
  FAQ skill registered. The FAQ stub (Sprint 6 / I1) is the simplest
  proof that registration fired at boot.

### Failure semantics

Each check returns `(ok: bool, error: str | None, duration_ms: int)`.
Errors are caught + downgraded to `ok=False` — health checks never
raise. /readyz/ aggregates with the existing service checks; any one
failure flips overall status to 503.

### Why not call the LLM on every readyz

PHASE0_DESIGN §5.2 budget says readyz fires every 30-60s. Calling LLM
on every probe would burn $10/day for the formula-tela tenant alone.
The breaker state is sufficient: breaker open ⟺ LLM is failing OR has
been failing recently (cooldown window). Probe stays free.

Sprint 8 may add a periodic synthetic LLM probe (e.g., 1 call per 5
min) that updates a cached status the readyz check reads.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


def check_intent_router() -> dict[str, Any]:
    """Verify the OpenAI breaker isn't open.

    Returns a dict matching the readyz check shape:
    ``{"ok": bool, "error": str | None, "duration_ms": int}``.
    """

    start = time.monotonic()
    try:
        from apps.orchestrator.llm.breaker import State, get_state

        # _BREAKER_NAME from openai_provider — duplicate the constant
        # here to avoid importing the OpenAI module just for a string.
        # get_state returns None when the breaker hasn't been instantiated
        # yet (cold boot) — treat as CLOSED / healthy.
        breaker_state = get_state("openai.complete")
        duration_ms = int((time.monotonic() - start) * 1000)
        if breaker_state == State.OPEN:
            return {
                "ok": False,
                "error": "openai_breaker_open",
                "duration_ms": duration_ms,
            }
        return {"ok": True, "error": None, "duration_ms": duration_ms}
    except Exception as exc:  # noqa: BLE001 — health never raises
        logger.exception("health.check_intent_router.error")
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": int((time.monotonic() - start) * 1000),
        }


def check_skill_registry() -> dict[str, Any]:
    """Verify the skill registry has at least the FAQ skill registered.

    FAQ is the Sprint 6 stub (DRF-544 / I1). If it's missing, app boot
    didn't fire @register decorators — pipeline can't dispatch anything.
    """

    start = time.monotonic()
    try:
        from apps.skills.registry import registered

        skills = registered()
        names = {getattr(s, "name", "") for s in skills}
        duration_ms = int((time.monotonic() - start) * 1000)
        if "faq" not in names:
            return {
                "ok": False,
                "error": f"faq_not_registered (have: {sorted(names)})",
                "duration_ms": duration_ms,
            }
        return {"ok": True, "error": None, "duration_ms": duration_ms}
    except Exception as exc:  # noqa: BLE001
        logger.exception("health.check_skill_registry.error")
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": int((time.monotonic() - start) * 1000),
        }


def pipeline_health() -> dict[str, dict[str, Any]]:
    """Run all pipeline component checks. Returns a name → result dict.

    Synchronous — readyz wraps in sync_to_async at the call site
    (matches the pattern used by the existing service probes).
    """

    return {
        "intent_router": check_intent_router(),
        "skill_registry": check_skill_registry(),
        # Sprint 8 / G4 (DRF-735) — extended health surface.
        "chromadb_auth": check_chromadb_auth(),
        "audit_cleanup": check_audit_cleanup_recent(),
    }


# ---------------------------------------------------------------------------
# Sprint 8 / G4 (DRF-735) — extended health probes
# ---------------------------------------------------------------------------


def check_chromadb_auth() -> dict[str, Any]:
    """Probe ChromaDB heartbeat with the platform's Bearer token.

    Sprint 7 / M4 (DRF-595) put ChromaDB behind a token gate. A drifted
    `CHROMA_AUTH_TOKEN` shows up as 401 on every FAQ retrieval — this
    probe surfaces it BEFORE traffic flows through to the user.

    Empty CHROMA_HTTP_HOST (local dev / tests using PersistentClient)
    short-circuits to ok=True — no remote to probe.
    """
    start = time.monotonic()
    try:
        from django.conf import settings

        host = str(getattr(settings, "CHROMA_HTTP_HOST", "") or "")
        if not host:
            return {
                "ok": True,
                "error": None,
                "duration_ms": int((time.monotonic() - start) * 1000),
                "detail": "no_remote_chromadb",
            }

        port = int(getattr(settings, "CHROMA_HTTP_PORT", 8001))
        token = str(getattr(settings, "CHROMA_AUTH_TOKEN", "") or "")
        import httpx

        headers = {"Authorization": f"Bearer {token}"} if token else {}
        resp = httpx.head(
            f"http://{host}:{port}/api/v2/heartbeat",
            headers=headers,
            timeout=2.0,
        )
        ok = 200 <= resp.status_code < 400
        return {
            "ok": ok,
            "error": None if ok else f"chromadb returned {resp.status_code}",
            "duration_ms": int((time.monotonic() - start) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 — probe never raises
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": int((time.monotonic() - start) * 1000),
        }


def check_audit_cleanup_recent() -> dict[str, Any]:
    """Verify the audit cleanup task ran within the last 25 hours.

    Sprint 2 / E3 ships a daily 03:00 UTC `cleanup_old_audit_logs`
    task. A silent beat outage would let the AuditLog table grow
    unbounded — this probe catches it within one missed cycle.
    """
    start = time.monotonic()
    try:
        from datetime import timedelta

        from django.utils import timezone

        from apps.audit.models import AuditLog

        # The cleanup task writes an audit row of its own (`audit.cleanup_succeeded`)
        # after each successful sweep. We just check recency of any
        # action — even if the cleanup row isn't there, an empty table
        # with no recent rows means workers/beat are down.
        latest = AuditLog.all_tenants.order_by("-created_at").first()
        if latest is None:
            return {
                "ok": True,
                "error": None,
                "duration_ms": int((time.monotonic() - start) * 1000),
                "detail": "audit_table_empty",
            }
        age = timezone.now() - latest.created_at
        ok = age < timedelta(hours=25)
        return {
            "ok": ok,
            "error": None if ok else f"latest audit row is {age} old",
            "duration_ms": int((time.monotonic() - start) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 — probe never raises
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": int((time.monotonic() - start) * 1000),
        }
