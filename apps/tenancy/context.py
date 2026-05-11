"""Tenant context propagation — ContextVar primitives (DRF-418 / A3).

Implements ADR-0003: every code path inside a request or worker job runs
inside a TenantContext set by the ingress (HTTP middleware or worker
consumer). Code anywhere in the platform reads ``current_tenant()`` and
the tenancy of *that* read matches the tenancy of the surrounding
request — without forcing every function in the call chain to grow a
``tenant=`` parameter.

Why ``contextvars.ContextVar`` and not threading.local:
- ContextVar propagates correctly across ``await``, ``asyncio.gather``,
  Django's ``sync_to_async`` / ``async_to_sync`` boundaries.
- threading.local is per-OS-thread, which silently empties on coroutine
  resumption (an asyncio scheduler can land the next yield on any thread
  in the pool). That makes it unsafe for async Django + Celery.

Why explicit token / try-finally instead of a single ``set()``:
- ContextVar reset is token-based: ``token = var.set(x); var.reset(token)``.
- Without reset, the value leaks into the next message handled by the
  same task/thread under reuse. Use ``tenant_scope()`` to enforce the
  pair correctly.

Lifecycle (HTTP request):

    ┌──────────────────────────────────────────────────────────────────┐
    │ TenantContextMiddleware.__call__                                 │
    │                                                                  │
    │   header X-Tenant → Tenant.objects.get(slug=…)                   │
    │                                  │                               │
    │                                  ▼                               │
    │             with tenant_scope(tenant):     ← set_tenant + token  │
    │                 response = get_response(request)                 │
    │                 # downstream sees current_tenant() == tenant     │
    │             # exit → reset_tenant(token) automatically           │
    │                                                                  │
    │   next request: ContextVar is back to its prior value (None)     │
    └──────────────────────────────────────────────────────────────────┘

Lifecycle (worker consumer):

    ┌──────────────────────────────────────────────────────────────────┐
    │ apps.workers.consumer.run                                        │
    │                                                                  │
    │   XREADGROUP → payload with resolved_tenant_id                   │
    │                                  │                               │
    │                                  ▼                               │
    │             with tenant_scope(tenant):                           │
    │                 handler(payload)                                 │
    │             # exit → reset                                       │
    │                                                                  │
    │   next message: clean context                                    │
    └──────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from apps.tenancy.models import Tenant


_TENANT: ContextVar["Tenant | None"] = ContextVar("tenant", default=None)
_TRACE_ID: ContextVar[str | None] = ContextVar("trace_id", default=None)


def current_tenant() -> "Tenant | None":
    """Return the current tenant set by middleware or worker entry.

    None means no tenant is in scope — either the request hit an excluded
    path (admin, health probes, auth bootstrap), or the tenant lookup
    silently fell through under audit-mode tolerance. Caller decides what
    None means for its own contract; the canonical scoped Manager
    (``apps.tenancy.managers.TenantScopedManager``) treats it as "no
    rows" in strict mode and as "log + return empty" in audit mode.
    """

    return _TENANT.get()


def set_tenant(tenant: "Tenant | None") -> Token["Tenant | None"]:
    """Set the current tenant and return the reset token.

    Always pair with ``reset_tenant(token)`` in a try/finally. Prefer
    ``tenant_scope()`` for routine use — it pairs the calls automatically.
    """

    return _TENANT.set(tenant)


def reset_tenant(token: Token["Tenant | None"]) -> None:
    """Restore the prior tenant value using the token from ``set_tenant``.

    Resetting an unrelated token raises ``ValueError`` — that is intentional
    protection against misuse (token A from set_tenant(t1) followed by
    reset with token B from set_tenant(t2) is a bug).
    """

    _TENANT.reset(token)


@contextmanager
def tenant_scope(tenant: "Tenant | None") -> Iterator["Tenant | None"]:
    """Context manager: set tenant for the scope, reset on exit.

    Use this in middleware, in worker consumer entry points, and in any
    place that wants a definite tenant for the duration of a block. The
    try/finally is what stops tenant leak across request / message
    boundaries.

    Example:
        with tenant_scope(t):
            do_work_that_reads_current_tenant()
        # current_tenant() is now back to the prior value.
    """

    token = set_tenant(tenant)
    try:
        yield tenant
    finally:
        reset_tenant(token)


def current_trace_id() -> str | None:
    """Return the current trace_id, or None if no trace context is in scope.

    Per review revision 1A (replay-first emission from Sprint 1): every
    code path inside a request or worker job runs under a trace_id set
    by ingress. Used by ``apps.events.emit()`` to correlate events
    across the pipeline. Sprint 5 replay infrastructure rehydrates the
    full call graph from these trace IDs.
    """

    return _TRACE_ID.get()


@contextmanager
def trace_id_scope(trace_id: str | None) -> Iterator[str | None]:
    """Context manager: set trace_id for the scope, reset on exit.

    Pair with ``tenant_scope`` in middleware and worker consumers. Both
    contexts must be set together — emitted events without trace_id are
    orphans for replay.

    Example:
        with tenant_scope(t), trace_id_scope("abc123"):
            do_pipeline_work()
    """

    token = _TRACE_ID.set(trace_id)
    try:
        yield trace_id
    finally:
        _TRACE_ID.reset(token)
