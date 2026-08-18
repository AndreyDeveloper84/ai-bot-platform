"""Which ``BotUser`` row does this Mini App request belong to (DRF-1083 / DRF-1150).

The same MAX account legitimately has several :class:`BotUser` rows — one
per tenant, by the model's own uniqueness rule — and the staff link lives
on exactly one of them. Picking the wrong row is not a subtle difference:
the person is an owner in one and a stranger in the other.

Measured on the pilot 2026-08-16: MAX uid ``83146139`` has two rows, under
``global_bot`` (last_seen 15.08 16:58) and under ``formula-tela``
(15.08 09:28). The staff row and the master link are on the second. Any
resolution that sorts by recency picks the first.

``master_api`` learned this the hard way in DRF-1083 and grew a private
helper. ``admin_api`` kept the older shape — bot-tenant slug plus
``order_by("-last_seen")`` — and survived only because
``MAX_BOT_TENANT_SLUG`` happens to point at the right tenant today. Two
surfaces resolving identity by two rules is how they drift apart
(DRF-1128 is the same family), so the rule now lives in one place and
both call it.

Resolution order:

1. **The tenant of the bot whose token signed this initData.** A Mini App
   is opened from a bot, and the signature is the only trustworthy
   statement of which one (DRF-1061) — the URL cannot say, and the
   payload carries no bot id.
2. ``MAX_BOT_TENANT_SLUG`` — what the customer and admin surfaces have
   always used, kept so a single-bot deployment behaves as before.
3. Only if neither is configured: the historical cross-tenant pick by
   recency. Not removed outright because a deployment with no registry
   and no bot-tenant slug still has to resolve *something*, and for such
   a deployment there is only one row anyway.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

from apps.identity.models import BotUser

logger = logging.getLogger(__name__)


def resolve_tenant_slug_for_init_data(verified: Any) -> str:
    """Tenant slug this initData belongs to — signing bot first, setting second.

    Returns ``""`` when neither source is configured; callers then fall
    back to a cross-tenant lookup.
    """

    bot_slug = getattr(verified, "bot_slug", "") or ""
    if bot_slug:
        # Local import: the bot registry reads settings at call time, and
        # importing it at module load would pull channels into identity's
        # import graph for every process that touches a BotUser.
        from apps.channels.bot_registry import effective_registry, resolve_by_slug

        entry = resolve_by_slug(bot_slug, effective_registry())
        if entry is not None and entry.tenant_slug:
            return entry.tenant_slug

    return getattr(settings, "MAX_BOT_TENANT_SLUG", "") or ""


def resolve_bot_user(verified: Any, *, surface: str = "miniapp") -> BotUser | None:
    """Find the BotUser this request belongs to, or ``None``.

    ``surface`` only labels the log line — the resolution rule is
    identical for every Mini App surface, and that is the point.
    """

    tenant_slug = resolve_tenant_slug_for_init_data(verified)

    qs = BotUser.all_tenants.filter(channel="max", channel_user_id=verified.user_id)
    if tenant_slug:
        scoped = qs.filter(tenant__slug=tenant_slug).select_related("tenant").first()
        if scoped is not None:
            return scoped
        # Fall through rather than 404: a person may have been created
        # under a different tenant and linked there. Better to answer with
        # the row we can find than to deny someone who is genuinely staff.
        logger.info(
            "%s.auth.no_bot_user_in_bot_tenant tenant=%s channel_user_id=%s",
            surface,
            tenant_slug,
            verified.user_id,
        )

    return qs.select_related("tenant").order_by("-last_seen").first()


__all__ = ["resolve_bot_user", "resolve_tenant_slug_for_init_data"]
