"""Shared lookup for the ``global_bot`` system tenant (#1019 / EPIC #1014).

Single source of truth for resolving the sentinel system tenant that owns the
global, tenant-less bot identity. The global ingress path
(:mod:`apps.channels.max.handler`) and the global resolver
(:func:`apps.identity.services.resolver.resolve_or_create_global_bot_user`)
call :func:`get_global_bot_tenant` instead of re-implementing the slug lookup.

Provisioning: the row is seeded deterministically by the data migration
``apps/identity/migrations/0014_seed_global_bot_tenant`` (no schema change).
This helper uses ``get_or_create`` as an idempotent safety net so it never
returns ``None`` mid-conversation — a missing sentinel would orphan every
global user.

Why ``Tenant.all_objects`` (the non-scoped manager): the global path runs at
``current_tenant()=None`` by design; the scoped ``Tenant.objects`` is fine for
Tenant itself, but ``all_objects`` keeps a deactivated sentinel visible and
makes the no-enforcement intent explicit.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG

if TYPE_CHECKING:
    from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


def get_global_bot_tenant() -> "Tenant":
    """Resolve-or-create the sentinel tenant that owns tenant-less global BotUsers.

    Returns the :class:`apps.tenancy.models.Tenant` row matching
    :data:`GLOBAL_BOT_TENANT_SLUG`. Primary provisioning is the seed data
    migration; the ``get_or_create`` here is an idempotent fallback so fresh
    environments / tests that haven't backfilled still resolve a row rather
    than failing soft to ``None`` mid-conversation.

    Not process-cached: it is a single indexed lookup, and the global resolver
    already does a ``get_or_create`` for the BotUser on the same turn, so the
    marginal cost is negligible — and skipping ``lru_cache`` avoids the
    cache-clear footgun between tests.
    """
    # Local import to avoid Django app-loading order issues at module import.
    from apps.tenancy.models import Tenant

    tenant, created = Tenant.all_objects.get_or_create(
        slug=GLOBAL_BOT_TENANT_SLUG,
        defaults={"name": "Global Bot Identity", "is_system": True},
    )
    if created:
        logger.warning(
            "identity.global_tenant.created slug=%s — seed migration had not run; created lazily",
            GLOBAL_BOT_TENANT_SLUG,
        )
    return tenant
