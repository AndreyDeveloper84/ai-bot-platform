"""Identity resolution services (DRF-434 / Sprint 2 / A2).

Single entry point for finding-or-creating a :class:`BotUser` from an
incoming channel event. Channel handlers (Sprint 2 / D3) call this on
every webhook turn — must be cheap, idempotent, and safe under
concurrent requests from the same user.

### Resolution rules

1. **Primary key**: `(current_tenant, channel, channel_user_id)`. This
   triplet is the BotUser natural key (per A1 `unique_together`). Same
   ``channel_user_id`` under different tenants ⇒ two BotUsers, by design.
2. **Phone consolidation**: deferred to Sprint 3 — see ADR-0007 (and the
   plan's locked decision #4). With one channel today there is no
   cross-channel phone collision to consolidate; pre-emptive logic
   would test against itself.
3. **Opportunistic enrichment**: when a row already exists, we update
   *blank* fields if the caller passes non-blank values
   (``display_name``, ``phone``, ``chat_id``). We **never overwrite a
   non-blank value** — that's a privacy-respect invariant: once the
   user has corrected their name in admin, the next webhook must not
   stomp it.
4. **Tenant context required**: callers must enter ``tenant_scope(t)``
   before calling. Under strict mode the underlying ORM manager raises
   ``CrossTenantError``; under audit mode the read returns empty and
   we'd create a phantom user — the resolver detects ``current_tenant()
   is None`` early and raises a clean ``ValueError`` regardless of
   STRICT_TENANT_SCOPE so the bug is loud everywhere.

### Telemetry

Each call emits exactly one event (``identity.bot_user.created`` or
``identity.bot_user.resolved``) with the bot_user UUID + channel + a
boolean ``enriched`` flag. Phone is **never** in the payload — only the
``phone_present`` boolean — per PII rule from A1.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from apps.events.services import emit
from apps.identity.models import BotUser
from apps.identity.services.global_tenant import get_global_bot_tenant
from apps.tenancy.context import current_tenant

logger = logging.getLogger(__name__)


def resolve_or_create_bot_user(
    *,
    channel: str,
    channel_user_id: str,
    display_name: str = "",
    phone: str = "",
    chat_id: str = "",
) -> BotUser:
    """Return the BotUser for ``(current_tenant, channel, channel_user_id)``.

    Args:
      channel: Channel slug — "max", "telegram", "whatsapp", "web".
      channel_user_id: Stable user identifier within the channel.
      display_name: Channel-reported display name. Updated on existing
                    rows ONLY when the row's `display_name` is blank.
      phone: E.164 phone. Same opportunistic-enrichment rule.
      chat_id: Channel-side chat identifier for outbound. Same rule.

    Returns:
      A `BotUser` instance, either just-created or pre-existing.

    Raises:
      ValueError: ``current_tenant()`` is None. The caller must enter
                  `tenant_scope(t)` before invocation. We raise even in
                  audit mode so this resolver fails loudly regardless of
                  the platform-wide scope toggle.
    """

    tenant = current_tenant()
    if tenant is None:
        raise ValueError(
            "resolve_or_create_bot_user requires a tenant in scope. "
            "Wrap the call in `tenant_scope(t)` (or rely on the ingress "
            "middleware) before invoking."
        )

    defaults: dict[str, Any] = {
        "display_name": display_name,
        "phone": phone,
        "chat_id": chat_id,
    }
    # Use ``all_tenants`` for the lookup because the default
    # ``TenantScopedManager.get_queryset`` already filters by tenant —
    # passing ``tenant=tenant`` to ``get_or_create`` via the default
    # manager would trip the cross-tenant filter detection. The
    # `tenant=tenant` kwarg in lookups is canonical per the manager's
    # `filter()` override.
    bot_user, created = BotUser.objects.get_or_create(
        tenant=tenant,
        channel=channel,
        channel_user_id=channel_user_id,
        defaults=defaults,
    )

    enriched = False
    if not created:
        # Opportunistic enrichment — fill blanks, never overwrite.
        update_fields: list[str] = []
        for field_name, incoming in (
            ("display_name", display_name),
            ("phone", phone),
            ("chat_id", chat_id),
        ):
            current = getattr(bot_user, field_name)
            if incoming and not current:
                setattr(bot_user, field_name, incoming)
                update_fields.append(field_name)
        if update_fields:
            # last_seen is `auto_now` → bump on save automatically.
            update_fields.append("last_seen")
            bot_user.save(update_fields=update_fields)
            enriched = True
        else:
            # Touch last_seen so `auto_now` fires even when nothing else
            # changed — observed-presence is part of the contract.
            bot_user.save(update_fields=["last_seen"])

    emit(
        "identity.bot_user.created" if created else "identity.bot_user.resolved",
        payload={
            "bot_user_id": str(bot_user.id),
            "channel": channel,
            "phone_present": bool(phone),  # never the raw value
            "enriched": enriched,
        },
    )
    logger.info(
        "identity.bot_user.%s id=%s tenant=%s channel=%s",
        "created" if created else "resolved",
        bot_user.id,
        tenant.id,
        channel,
    )
    return bot_user


def resolve_or_create_global_bot_user(
    *,
    channel: str,
    channel_user_id: str,
    ayla_user_id: "uuid.UUID | str | None" = None,
    display_name: str = "",
    phone: str = "",
    chat_id: str = "",
) -> BotUser:
    """Resolve-or-create the GLOBAL (tenant-less) BotUser under the sentinel.

    The nationwide bot (#1019 / EPIC #1014) serves every salon; discovery runs
    at ``current_tenant()=None`` and a tenant is selected only at booking. This
    is the resolver for that path — the sibling of
    :func:`resolve_or_create_bot_user`, which is reserved for the legacy
    per-tenant direct-salon bots and is left untouched.

    Unlike the per-tenant resolver this does **not** require (and does not
    enter) a ``tenant_scope``: the BotUser is parked under the ``global_bot``
    sentinel tenant purely to satisfy ``unique_together (tenant, channel,
    channel_user_id)`` without a schema migration. We use ``BotUser.all_tenants``
    (the non-scoped manager) so the write bypasses ``TenantScopedManager``
    enforcement and ``current_tenant()`` stays ``None`` throughout — exactly the
    discovery contract.

    Args:
      channel: Channel slug — "max", "telegram", "whatsapp", "web".
      channel_user_id: Stable user identifier within the channel.
      ayla_user_id: Canonical Ayla User UUID, when already known. Set on create
                    and blank-filled on resolve (never overwrites a non-null).
      display_name / phone / chat_id: opportunistic blank-fill enrichment, same
                    privacy-respect rule as the per-tenant resolver.

    Returns:
      A `BotUser` owned by the sentinel ``global_bot`` tenant.
    """
    sentinel = get_global_bot_tenant()

    defaults: dict[str, Any] = {
        "display_name": display_name,
        "phone": phone,
        "chat_id": chat_id,
    }
    if ayla_user_id:
        defaults["ayla_user_id"] = ayla_user_id

    # all_tenants (non-scoped) — the global path runs at current_tenant()=None
    # by design, so the scoped manager would raise/return-empty. The explicit
    # tenant=sentinel kwarg satisfies the natural key.
    bot_user, created = BotUser.all_tenants.get_or_create(
        tenant=sentinel,
        channel=channel,
        channel_user_id=channel_user_id,
        defaults=defaults,
    )

    enriched = False
    if not created:
        # Opportunistic enrichment — fill blanks, never overwrite. ayla_user_id
        # joins the blank-fill set so a later real value populates a NULL but a
        # known value is never stomped.
        update_fields: list[str] = []
        for field_name, incoming in (
            ("display_name", display_name),
            ("phone", phone),
            ("chat_id", chat_id),
            ("ayla_user_id", ayla_user_id),
        ):
            current = getattr(bot_user, field_name)
            if incoming and not current:
                setattr(bot_user, field_name, incoming)
                update_fields.append(field_name)
        if update_fields:
            update_fields.append("last_seen")
            bot_user.save(update_fields=update_fields)
            enriched = True
        else:
            bot_user.save(update_fields=["last_seen"])

    emit(
        "identity.bot_user.created" if created else "identity.bot_user.resolved",
        payload={
            "bot_user_id": str(bot_user.id),
            "channel": channel,
            "phone_present": bool(phone),  # never the raw value
            "enriched": enriched,
            "global": True,
        },
    )
    logger.info(
        "identity.bot_user.%s id=%s tenant=%s(global) channel=%s",
        "created" if created else "resolved",
        bot_user.id,
        sentinel.id,
        channel,
    )
    return bot_user


def delete_bot_user_data(bot_user) -> int:
    """Soft-delete a BotUser's conversations + staff threads, hard-delete the BotUser.

    Single entry point for the 152-ФЗ "удалить мои данные" workflow
    (Sprint 3 PrivacyConsentSkill calls this) and any future admin
    "wipe this user" action.

    Per Sprint 2.5 review H1: `Conversation.bot_user` is now PROTECT,
    so a stray `BotUser.delete()` raises ProtectedError instead of
    silently cascading away conversation history. This function is
    the only legitimate caller — it soft-deletes Conversations first
    (preserving forensic Messages via mark_deleted), then hard-deletes
    the BotUser row.

    DRF-1276: ``StaffAssistantThread.bot_user`` is PROTECT for the same
    reason, and until now nothing cleared the threads — an employee who
    ever wrote to the assistant made the whole wipe abort on
    ProtectedError, rolling back the rows above with it. Threads get the
    same treatment as conversations: soft-delete breadcrumb, then hard
    delete (``StaffAssistantMessage`` cascades off the thread).

    Returns:
      Number of conversations soft-deleted (excludes Messages — those
      stay in DB tied to the now-soft-deleted Conversation).
    """

    from apps.audit.services import write_audit
    from apps.conversations.models import Conversation, StaffAssistantThread
    from django.db import transaction

    write_audit(
        "identity.bot_user.delete_started",
        target="BotUser",
        target_id=bot_user.id,
        payload={"channel": bot_user.channel},
    )

    soft_deleted = 0
    staff_threads_deleted = 0
    with transaction.atomic():
        # Use `Conversation.all_tenants.filter(bot_user=...)` — reverse
        # related-manager `bot_user.conversations` is a Django auto-built
        # RelatedManager which doesn't carry our custom escape hatch.
        conversations = Conversation.all_tenants.filter(bot_user=bot_user)
        # First: mark every active conversation as soft-deleted (audit
        # trail breadcrumb for the legal flow).
        for conv in conversations:
            if conv.is_active or conv.deleted_at is None:
                conv.mark_deleted()
                soft_deleted += 1
        # Now hard-delete: cascades through Messages. Sprint 3
        # PrivacyConsentSkill writes its own audit BEFORE this call.
        conversations.delete()
        threads = StaffAssistantThread.all_tenants.filter(bot_user=bot_user)
        for thread in threads:
            if thread.is_active or thread.deleted_at is None:
                thread.mark_deleted()
                staff_threads_deleted += 1
        threads.delete()
        bot_user.delete()

    write_audit(
        "identity.bot_user.delete_finished",
        target="BotUser",
        target_id=bot_user.id,
        payload={
            "conversations_deleted": soft_deleted,
            "staff_threads_deleted": staff_threads_deleted,
        },
    )
    return soft_deleted
