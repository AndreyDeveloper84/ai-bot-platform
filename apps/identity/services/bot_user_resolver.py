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

0. **The one row that carries a working role** (DRF-1755, срез 2 DRF-1705).
   Among every row of this identity, «working» means: an active
   ``TenantStaff`` in its tenant, or a live ``CatalogMaster`` linked to it.
   Exactly one such row → that row, whichever bot signed. This is what
   lets a solo master into the cabinet from the salon bot: the salon
   bot's own tenant holds a ``customer`` row for them (their first
   message created it), and steps 1–3 below never looked past it — the
   fallback in step 3 needs the bot-tenant row to be ABSENT, and on the
   solo path it is always present. Zero working rows → steps 1–3, so a
   plain customer keeps today's answer. Two or more → DRF-1766 (salon
   chooser); until then the oldest tenant wins deterministically, with a
   WARNING naming all of them. See :func:`resolve_working_bot_user`.
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


def resolve_working_bot_user(channel_user_id: str, *, surface: str = "miniapp") -> BotUser | None:
    """The row of this MAX identity that carries a working role, or ``None``.

    «Working» is the same word :func:`apps.identity.services.solo_onboarding.is_solo_provider`
    uses: an active ``TenantStaff`` (``deactivated_at IS NULL``) on the
    row, or a live ``CatalogMaster`` (``archived_at IS NULL``) linked to
    it. Soft-deleted rows are never working — a deleted account cannot be
    somebody's staff answer.

    Returns:

    * ``None`` — no row, or no working row. The caller falls back to the
      signing-bot rule; nothing about a plain customer changes.
    * the single working row — regardless of which bot signed.
    * with several working rows (0 identities on the pilot, 12.09.2026):
      the row whose tenant is the oldest, ``pk`` as tie-break — and a
      WARNING with every candidate. Deterministic on purpose and NOT by
      ``last_seen`` (DRF-1653: a race with the clock). The real answer
      for this case is a salon chooser (DRF-1766); until it exists the
      WARNING is the trigger to build it.
    """

    if not channel_user_id:
        return None

    rows = list(
        BotUser.all_tenants.filter(
            channel="max", channel_user_id=channel_user_id, deleted_at__isnull=True
        ).select_related("tenant")
    )
    if not rows:
        return None

    # Local imports, as role_resolver does: identity must not pull catalog
    # and tenancy into its import graph for every process touching a BotUser.
    from apps.catalog.models import CatalogMaster
    from apps.tenancy.models import TenantStaff

    ids = [row.pk for row in rows]
    staff_rows = set(
        TenantStaff.all_tenants.filter(
            bot_user_id__in=ids, deactivated_at__isnull=True
        ).values_list("bot_user_id", flat=True)
    )
    master_rows = set(
        CatalogMaster.all_tenants.filter(
            linked_bot_user_id__in=ids, archived_at__isnull=True
        ).values_list("linked_bot_user_id", flat=True)
    )
    working = [row for row in rows if row.pk in staff_rows or row.pk in master_rows]
    if not working:
        return None
    if len(working) == 1:
        return working[0]

    working.sort(key=lambda row: (row.tenant.created_at, str(row.pk)))
    logger.warning(
        "%s.auth.several_working_tenants channel_user_id=%s tenants=%s picked=%s — "
        "salon chooser not built yet (DRF-1766)",
        surface,
        channel_user_id,
        [row.tenant.slug for row in working],
        working[0].tenant.slug,
    )
    return working[0]


def is_staff_surface(verified: Any) -> bool:
    """Did the SALON bot sign this initData — i.e. is this the masters' surface?

    The customer surface asks «who are you as a client» and must keep
    answering with the signing bot's tenant: a solo master booking a
    haircut somewhere is a customer there. Only the staff surface asks
    «which of your rows is staff». The signature is the one trustworthy
    statement of which bot opened the Mini App (DRF-1061).
    """

    bot_slug = getattr(verified, "bot_slug", "") or ""
    if not bot_slug:
        return False
    from apps.channels.bot_registry import SALON_STREAM, effective_registry, resolve_by_slug

    entry = resolve_by_slug(bot_slug, effective_registry())
    return entry is not None and entry.stream == SALON_STREAM


def resolve_bot_user(verified: Any, *, surface: str = "miniapp") -> BotUser | None:
    """Find the BotUser this request belongs to, or ``None``.

    ``surface`` only labels the log line — the resolution rule is
    identical for every Mini App surface, and that is the point.

    Callers are the STAFF surfaces (``master_api``, ``admin_api``), so
    step 0 — the working row — applies unconditionally here. ``/api/v1/me``
    serves both audiences and applies it only behind :func:`is_staff_surface`.
    """

    working = resolve_working_bot_user(verified.user_id, surface=surface)
    if working is not None:
        return working

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

    # DRF-1653 — `-last_seen` alone is not a total order, and the third step
    # is the one place where that matters.
    #
    # `last_seen` is `auto_now=True` (apps/identity/models.py:167), so two rows
    # touched in the same clock tick carry the identical value. On a tie
    # Postgres returns whichever row it reaches first, and "whichever" is not
    # stable: an `UPDATE` moves a row within the heap, so the same person, the
    # same two rows and the same query can resolve to a different tenant on two
    # consecutive requests. This is authentication — the answer decides whether
    # someone is an owner or a stranger — and an answer that changes without an
    # input changing is worse than a wrong one, because it cannot be
    # reproduced, reported, or tested.
    #
    # `pk` as the tie-break makes no claim about which row is better. It claims
    # only that the same data yields the same answer. Choosing by recency is
    # step 3's stated rule; when recency does not distinguish, nothing here
    # knows more, and the honest fix is determinism rather than a new
    # preference invented at the bottom of the fallback chain.
    return qs.select_related("tenant").order_by("-last_seen", "pk").first()


__all__ = [
    "is_staff_surface",
    "resolve_bot_user",
    "resolve_tenant_slug_for_init_data",
    "resolve_working_bot_user",
]
