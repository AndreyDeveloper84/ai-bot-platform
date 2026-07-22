"""Staging settings (Sprint 4 / D1 — DRF-492).

Staging is the first environment where ``STRICT_TENANT_SCOPE`` flips to
``strict`` per PHASE0_DESIGN §6 (IM-2 timing). Tests have run under
strict since Sprint 1 via ``tests/conftest.py`` autouse fixture, but
this is the first env-level lock-in.

### Locked decision — 2026-05-11 (Sprint 4 / D1)

- ``STRICT_TENANT_SCOPE = "strict"`` on staging starting Sprint 4.
- Prod stays in ``audit`` mode (raises Sentry event, doesn't crash)
  until Sprint 8 shadow-mode soak shows zero violations for ≥ 2 weeks.
- Per design §6: 2-week window down from original 4w; Phase 0 prod
  traffic is synthetic-heavy until canary cutover so a shorter soak
  is enough.

In strict mode any code path that touches a TenantScopedManager
without ``tenant_scope`` raises ``CrossTenantError`` instead of just
auditing. This is the desired terminal state.
"""

from .base import *  # noqa: F401,F403

DEBUG = False

# Sprint 4 / D1 — IM-2 timing.
STRICT_TENANT_SCOPE = "strict"

# Pilot staging — pre-#246 tenant-verify bridge (Round-2 AS8). Without
# TenantUserRelationship (lands with #246) the ingest would fail closed
# on every legitimate Ayla delivery (staging round-trip finding №2).
# staging opts into the documented bridge; production.py stays
# fail-closed until #246 ships. The boot-time WARNING
# (startup_checks.warn_if_tenant_verify_fail_open) keeps the exposure
# window visible in ops.
EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True

# Rate-limit IP resolution (ingest_ip, Round-2 AS2): staging nginx adds
# exactly ONE trusted hop in front of uvicorn (nginx → bot), so one
# leading XFF hop is discarded before taking the client IP. 0 would
# trust a client-controllable XFF blindly (AS2 spoof); 2+ would trust
# the publisher's raw header against our single edge.
EVENT_INGEST_TRUSTED_PROXY_DEPTH = 1
