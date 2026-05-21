"""Channel handler registrations (DRF-443 / Sprint 2 / D4).

Each channel handler subclasses `TenantAwareTask` and is registered
to its `ingress:<channel>` stream via the `@register(...)` decorator.
The consumer loop (`apps/workers/consumer.py`) dispatches to the
registered handler based on the stream name.

This module exists so that importing `apps.channels` is enough to
register all available handlers. The AppConfig's `ready()` hook
imports it; that way the registration happens once per process boot
without any boilerplate at consumer-loop call sites.
"""

from __future__ import annotations

from typing import Any

from apps.channels.max import handler as max_handler
from apps.tenancy.context import current_trace_id
from apps.workers.base import TenantAwareTask
from apps.workers.registry import register


@register("ingress:max")
class MaxHandler(TenantAwareTask):
    """Dispatches MAX stream entries to `apps.channels.max.handler`.

    The TenantAwareTask base class has already entered `tenant_scope`
    and `trace_id_scope` from the Redis Stream entry's top-level
    fields by the time `handle()` is called. We just forward the
    decoded payload to the handler implementation.

    Tenancy retro B4: ``requires_tenant=True`` (inherited default).
    MAX inbound is *expected* to be tenant-routed — the ingress layer
    at ``apps/ingress/views.py`` resolves the tenant by bot-token
    before XADD and stamps ``resolved_tenant_id`` on the stream entry.
    Entries WITHOUT a resolved tenant indicate an ingress mis-binding
    (unknown bot token, mis-configured webhook URL, ingress
    regression — see ``apps/ingress/views.py:114-116`` where the
    ``resolved_tenant_id=""`` branch is explicit). Pre-B4 such entries
    silently ran with ``tenant_scope(None)``: reads returned empty +
    audit warn but the handler proceeded on phantom context. Post-B4
    once ``STRICT_TENANT_REFUSE`` flips, the dispatcher refuses such
    entries (raise ``TenantRequiredButMissing``). The consumer does
    NOT XACK on raise — **the entry stays in the PEL** until operator
    XCLAIM / XAUTOCLAIM intervention. No automatic DLQ retry is wired
    yet — see ``docs/runbooks/strict-tenant-refuse-flip.md``. Phase 0
    default (False) is log-only: ERROR log on missing tenant, handler
    still runs (parity with pre-B4 for the transition window).
    """

    def handle(self, payload: dict[str, Any]) -> None:
        # `current_trace_id()` reads what the base class set up; pass
        # explicitly so D3's `record_message` doesn't have to re-look-up.
        trace_id = current_trace_id()
        max_handler.handle_max_event(payload, trace_id=trace_id)
