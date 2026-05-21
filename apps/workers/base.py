"""TenantAwareTask base class (DRF-425 / C3).

Pattern: every worker handler subclasses ``TenantAwareTask`` and overrides
``handle(payload)``. The base class does the rest:

  ┌──────────────────────────────────────────────────────────────────┐
  │ consumer.py reads entry from Redis Stream                        │
  │      payload = {"data": {...}, "trace_id": "...",                 │
  │                  "resolved_tenant_id": "..."}                    │
  │                                  │                               │
  │                                  ▼                               │
  │             TenantAwareTask.__call__(payload)                    │
  │                                                                  │
  │             1. Resolve tenant from payload["resolved_tenant_id"] │
  │             2. Enter tenant_scope(tenant)                        │
  │             3. Enter trace_id_scope(payload["trace_id"])         │
  │             4. emit("worker.handler_started")                    │
  │             5. self.handle(payload["data"])                      │
  │             6. emit("worker.handler_completed")                  │
  │             7. (or "worker.handler_failed" + re-raise)           │
  │                                  │                               │
  │                                  ▼                               │
  │  consumer.py XACKs on success; PEL retains on exception.         │
  └──────────────────────────────────────────────────────────────────┘

Subclasses just override ``handle(payload)`` — no boilerplate.
Test-only helper: instantiate the class and call it directly with a
synthesised payload to exercise the handler in isolation.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar
from uuid import UUID

from django.conf import settings

from apps.events.services import emit
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


def resolve_tenant_by_id_string(tenant_id_raw: str):
    """Look up Tenant by UUID string, or None if empty/invalid/unknown.

    Module-level so the consumer loop (`apps.workers.consumer`) and
    every TenantAwareTask subclass share exactly one resolver
    implementation. Per Sprint 2.5 review M3: prevents divergence
    between consumer.py's inline lookup and `TenantAwareTask._resolve_tenant`.

    Uses ``all_objects`` (not the active-filtered ``objects``) so a
    queued webhook for a tenant that was deactivated between enqueue
    and consume still resolves — the consumer drains the queue then
    the handler can decide whether to act based on tenant state. The
    middleware uses ``objects`` for HTTP requests (strict ingress
    gate); this two-tier visibility is intentional, see docstring at
    bottom of `apps/tenancy/models.py`.
    """

    if not tenant_id_raw:
        return None
    try:
        tenant_uuid = UUID(tenant_id_raw)
    except (ValueError, AttributeError):
        return None

    try:
        return Tenant.all_objects.get(id=tenant_uuid)
    except Tenant.DoesNotExist:
        return None


class TenantRequiredButMissing(Exception):
    """Raised by ``TenantAwareTask.__call__`` when a handler declared
    ``requires_tenant = True`` but the stream entry's
    ``resolved_tenant_id`` is empty/invalid/unknown.

    Tenancy retro B4: pre-fix workers silently entered ``tenant_scope(None)``
    for tenant-required handlers; reads returned empty + audit warn but
    handlers proceeded. This exception lets the consumer refuse to
    dispatch instead of running on a phantom-tenant context.

    On raise, the consumer treats the exception like any handler
    failure: no XACK, so the entry **stays in the PEL** until
    operator XCLAIM / XAUTOCLAIM intervention. There is no DLQ
    stream wired in this repo as of 2026-05-21 — see the follow-up
    issue for the XAUTOCLAIM-based reaper. **PEL retention is the
    contract**; do not rely on automatic DLQ retry.

    Gated by ``settings.STRICT_TENANT_REFUSE`` (default False during
    Phase 0 rollout — log-only mode; flip to True after the dev-side
    soak proves no legitimate handler misses its tenant). The flip
    requires a worker restart — see
    ``docs/runbooks/strict-tenant-refuse-flip.md``.
    """


class TenantAwareTask(ABC):
    """Base class for stream-driven worker handlers.

    Subclasses MUST override ``handle(payload)``. Base class wraps every
    call in tenant + trace ContextVars and emits start/done/failed events.

    Tenancy retro B4 — tenant-required tag:

      ``requires_tenant: ClassVar[bool] = True``  (default)

    The default is conservative: every handler is presumed to need a
    tenant in scope. Handlers that legitimately run without one (system
    tasks: audit sweep, outbox dispatcher, health check, migration
    runner — none currently subclass TenantAwareTask but the pattern
    is here for future use) MUST override this to ``False`` with a
    docstring note explaining why.

    Enforcement is gated by ``settings.STRICT_TENANT_REFUSE``:

      * False (default — Phase 0 rollout): missing-tenant ON a
        ``requires_tenant=True`` handler logs ERROR but proceeds with
        ``tenant_scope(None)``. Same as pre-B4 behaviour, but loud.
      * True (post-soak):  missing-tenant raises
        :class:`TenantRequiredButMissing`. The consumer does NOT XACK
        on raise → entry stays in the PEL until operator XCLAIM /
        XAUTOCLAIM intervention. No automatic DLQ is wired yet — see
        ``docs/runbooks/strict-tenant-refuse-flip.md``.
    """

    requires_tenant: ClassVar[bool] = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """B2 (Tenancy retro B4 follow-up) — MRO bypass defence.

        A mixin sibling of ``TenantAwareTask`` that declares
        ``requires_tenant = False`` would silently shadow this base
        class's ``True`` default via MRO resolution::

            class _SystemMixin:
                requires_tenant = False  # silent bypass

            class MyHandler(_SystemMixin, TenantAwareTask):
                def handle(self, payload): ...

        ``MyHandler.requires_tenant`` resolves to ``False`` even though
        nobody wrote that on ``MyHandler`` itself. Strict-mode flips
        would then NOT refuse a missing-tenant entry for ``MyHandler``,
        defeating the whole defence.

        This guard refuses any class creation where a non-
        ``TenantAwareTask`` ancestor between the subclass and
        ``TenantAwareTask`` defines ``requires_tenant``. Explicit
        opt-out on the subclass itself (with a docstring justification)
        remains allowed — that lives in the subclass's own ``__dict__``
        and is therefore visible at review time.
        """

        super().__init_subclass__(**kwargs)

        mro = cls.__mro__
        try:
            ta_idx = mro.index(TenantAwareTask)
        except ValueError:
            return

        for ancestor in mro[1:ta_idx]:
            # Skip ancestors that are themselves TenantAwareTask
            # descendants — they're legitimate opt-out subclasses (e.g.
            # ``SystemTask(TenantAwareTask)`` setting requires_tenant=False
            # with docstring justification, then ``AuditSweepHandler``
            # extending ``SystemTask``). The guard targets external
            # mixins that shadow the attribute, not the documented
            # inheritance chain.
            if issubclass(ancestor, TenantAwareTask):
                continue
            if "requires_tenant" in ancestor.__dict__:
                raise TypeError(
                    f"{cls.__name__}: ancestor {ancestor.__name__} "
                    "declares `requires_tenant` — this would silently "
                    "shadow the TenantAwareTask defence (B2 / Tenancy "
                    "retro B4 follow-up). Declare `requires_tenant` "
                    f"directly on {cls.__name__} with a docstring "
                    f"justification, or remove it from {ancestor.__name__}."
                )

    @abstractmethod
    def handle(self, payload: dict[str, Any]) -> None:
        """User-defined handler. Override in subclass."""

    def __call__(self, raw_entry: dict[str, Any]) -> None:
        """Process one stream entry. Called by the consumer loop.

        Args:
          raw_entry: The full stream entry dict from Redis Streams.
            Expected keys: ``data`` (JSON-stringified payload),
            ``trace_id`` (top-level), ``resolved_tenant_id`` (top-level,
            optional — empty string when unknown).

        Behaviour:
          - Enters ``trace_id_scope`` first (B1 fix) so every emit
            below — including the requires_tenant-violation events —
            carries trace correlation even when ``__call__`` is invoked
            outside ``apps.workers.consumer`` (direct unit-test
            instantiation, replay rerun, future refactors).
          - Resolves tenant from ``resolved_tenant_id`` (or None).
          - Tenancy retro B4: enforces ``requires_tenant`` via the
            ``STRICT_TENANT_REFUSE`` settings flag — when strict + no
            tenant + requires_tenant=True, raises
            :class:`TenantRequiredButMissing`. The consumer doesn't
            XACK on raise, so the entry **stays in the PEL** for
            manual escalation. No automatic DLQ retry is wired — see
            the follow-up XAUTOCLAIM issue.
          - Enters ``tenant_scope`` (possibly with ``None`` in
            log-only mode) for the handler's execution.
          - Emits ``worker.handler_started`` then
            ``worker.handler_completed`` on success, or
            ``worker.handler_failed`` on exception + re-raise.
          - Re-raises any handler exception so the consumer doesn't
            XACK; the entry stays in the PEL.
        """

        tenant = self._resolve_tenant(raw_entry.get("resolved_tenant_id", ""))
        trace_id = raw_entry.get("trace_id", "")
        payload = self._extract_payload(raw_entry)

        # B1: enter trace_id_scope BEFORE any emit so trace correlation
        # holds even when __call__ is invoked outside consumer.py's
        # outer scope (direct unit tests, replay rerun, future refactor).
        with trace_id_scope(trace_id or None):
            # Tenancy retro B4: enforce or log the requires_tenant tag.
            if self.requires_tenant and tenant is None:
                # B3: STRICT_TENANT_REFUSE is read from `settings` each
                # call — but `settings.STRICT_TENANT_REFUSE` itself is
                # populated from os.environ ONCE at import time
                # (config/settings/base.py). The operator flip therefore
                # requires a worker process restart for the new value
                # to take effect. Documented in
                # docs/runbooks/strict-tenant-refuse-flip.md.
                strict = bool(getattr(settings, "STRICT_TENANT_REFUSE", False))
                if strict:
                    logger.error(
                        "worker.tenant_required_missing handler=%s trace=%s "
                        "resolved_tenant_id=%r — refusing dispatch "
                        "(entry retained in PEL; no auto-DLQ)",
                        type(self).__name__,
                        trace_id,
                        raw_entry.get("resolved_tenant_id", ""),
                    )
                    emit(
                        "worker.tenant_required_missing",
                        payload={
                            "handler": type(self).__name__,
                            "strict_mode": True,
                        },
                    )
                    raise TenantRequiredButMissing(
                        f"{type(self).__name__} requires a tenant but "
                        f"resolved_tenant_id is empty/invalid (trace={trace_id})"
                    )
                # Log-only rollout mode: loud ERROR but proceed.
                logger.error(
                    "worker.tenant_required_missing handler=%s trace=%s "
                    "resolved_tenant_id=%r — proceeding in log-only mode "
                    "(STRICT_TENANT_REFUSE=False)",
                    type(self).__name__,
                    trace_id,
                    raw_entry.get("resolved_tenant_id", ""),
                )
                emit(
                    "worker.tenant_required_missing",
                    payload={
                        "handler": type(self).__name__,
                        "strict_mode": False,
                    },
                )
            elif not self.requires_tenant and tenant is None:
                # Tenant-optional handler running without scope — INFO level.
                logger.info(
                    "worker.tenantless_handler handler=%s trace=%s",
                    type(self).__name__,
                    trace_id,
                )

            with tenant_scope(tenant):
                emit(
                    "worker.handler_started",
                    payload={"handler": type(self).__name__},
                )
                try:
                    self.handle(payload)
                except Exception as exc:  # noqa: BLE001 — emit then re-raise
                    logger.exception(
                        "worker.handler_failed handler=%s trace=%s",
                        type(self).__name__,
                        trace_id,
                    )
                    emit(
                        "worker.handler_failed",
                        payload={
                            "handler": type(self).__name__,
                            "error_class": type(exc).__name__,
                            "error_message": str(exc),
                        },
                    )
                    raise
                emit(
                    "worker.handler_completed",
                    payload={"handler": type(self).__name__},
                )

    @staticmethod
    def _resolve_tenant(tenant_id_raw: str):
        """Look up Tenant by UUID string, or None if empty/invalid/unknown."""

        return resolve_tenant_by_id_string(tenant_id_raw)

    @staticmethod
    def _extract_payload(raw_entry: dict[str, Any]) -> dict[str, Any]:
        """Pull the JSON payload from the stream entry's ``data`` field."""

        import json

        data = raw_entry.get("data")
        if not data:
            return {}
        if isinstance(data, dict):
            return data
        return json.loads(data)
