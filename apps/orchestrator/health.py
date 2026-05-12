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
    }
