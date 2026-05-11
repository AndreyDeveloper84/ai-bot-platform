"""Prompt registry services (DRF-476 / Sprint 4 / A4).

Two entry points: ``publish_prompt`` flips one version active per
``(tenant, skill_name)``; ``unpublish_prompt`` is the symmetric
revert. Both are atomic, write audit rows, and fire cache
invalidation via Redis pub/sub on commit.

### Invariant: exactly one active per (tenant, skill_name)

``publish_prompt`` runs in ``transaction.atomic``:
  1. Audit row written FIRST (audit-before-action contract from
     Sprint 3 / D2 privacy) — operator trail survives even if the
     UPDATE later fails for a reason that doesn't rollback the audit.
  2. ``UPDATE`` deactivates every other active version under the
     same (tenant, skill_name).
  3. ``UPDATE`` sets the target version: ``is_active=True``,
     ``published_at=now()``.
  4. ``on_commit`` callback fires ``publish_invalidate`` on the
     Redis pub/sub channel.

The on_commit timing is load-bearing: it guarantees subscribers in
other workers never read the cache key while the DB transaction is
still in flight (would observe stale row + then evict — race
window).

### Tenant scope guard

Both services require ``current_tenant()`` to match the version's
tenant. Mismatch → raises ``ValueError`` (programmer error — caller
forgot to enter ``tenant_scope`` or is trying cross-tenant write).
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.audit.services import write_audit
from apps.promptreg import cache as cache_mod
from apps.promptreg.models import PromptVersion
from apps.tenancy.context import current_tenant

logger = logging.getLogger(__name__)


def publish_prompt(version: PromptVersion, *, user) -> PromptVersion:
    """Activate `version`, deactivating prior active versions atomically.

    Args:
      version: the PromptVersion row to activate. Must belong to the
               tenant currently in scope.
      user: ``auth.User`` performing the action — recorded in the audit
            row. Idiomatic: pass ``request.user`` from the admin.

    Returns:
      The refreshed ``version`` instance with ``is_active=True`` and
      ``published_at`` set.

    Raises:
      ValueError: missing or mismatched tenant context.
    """

    tenant = current_tenant()
    if tenant is None:
        raise ValueError(
            "promptreg.publish_prompt requires a tenant in scope. "
            "Wrap in `tenant_scope(t)` before invocation."
        )
    if version.tenant_id != tenant.id:
        raise ValueError(
            f"promptreg.publish_prompt: version.tenant={version.tenant_id} "
            f"does not match current_tenant={tenant.id}. Refusing cross-tenant write."
        )

    write_audit(
        "promptreg.prompt.published",
        target="PromptVersion",
        target_id=version.id,
        payload={
            "skill_name": version.skill_name,
            "version": version.version,
            "user_id": getattr(user, "id", None),
            "user_username": getattr(user, "username", ""),
        },
    )

    now = timezone.now()
    with transaction.atomic():
        # Deactivate every other active version for this (tenant, skill_name).
        # `all_tenants` to bypass the scoped manager — we already proved
        # tenant ownership above, and the UPDATE is keyed on tenant_id
        # explicitly anyway.
        PromptVersion.all_tenants.filter(
            tenant_id=tenant.id,
            skill_name=version.skill_name,
            is_active=True,
        ).exclude(pk=version.pk).update(is_active=False)

        # Activate the target. `update` (not save) so we don't bump
        # `created_at` and we get a single UPDATE statement.
        PromptVersion.all_tenants.filter(pk=version.pk).update(is_active=True, published_at=now)

    # Refresh the in-memory instance so callers see the new state.
    version.is_active = True
    version.published_at = now

    def _publish() -> None:
        cache_mod.publish_invalidate(tenant.id, "prompt", (version.skill_name,))

    transaction.on_commit(_publish)
    logger.info(
        "promptreg.prompt.published tenant=%s skill=%s version=%s",
        tenant.id,
        version.skill_name,
        version.version,
    )
    return version


def unpublish_prompt(version: PromptVersion, *, user) -> PromptVersion:
    """Reverse of publish_prompt — flip the version to is_active=False.

    The prior active version (if any) is NOT auto-restored — the caller
    decides what to publish next. Audit row records the deactivation.

    Idempotent on already-inactive versions (returns early without write).
    """

    tenant = current_tenant()
    if tenant is None:
        raise ValueError("promptreg.unpublish_prompt requires a tenant in scope.")
    if version.tenant_id != tenant.id:
        raise ValueError(
            f"promptreg.unpublish_prompt: version.tenant={version.tenant_id} "
            f"does not match current_tenant={tenant.id}."
        )

    # Re-read state so we don't double-write.
    version.refresh_from_db(fields=["is_active"])
    if not version.is_active:
        # Already deactivated; nothing to do, but log an audit row so the
        # operator's intent is recorded.
        write_audit(
            "promptreg.prompt.unpublish_noop",
            target="PromptVersion",
            target_id=version.id,
            payload={
                "skill_name": version.skill_name,
                "version": version.version,
                "user_id": getattr(user, "id", None),
            },
        )
        return version

    write_audit(
        "promptreg.prompt.unpublished",
        target="PromptVersion",
        target_id=version.id,
        payload={
            "skill_name": version.skill_name,
            "version": version.version,
            "user_id": getattr(user, "id", None),
            "user_username": getattr(user, "username", ""),
        },
    )

    with transaction.atomic():
        PromptVersion.all_tenants.filter(pk=version.pk).update(is_active=False)
    version.is_active = False

    def _publish() -> None:
        cache_mod.publish_invalidate(tenant.id, "prompt", (version.skill_name,))

    transaction.on_commit(_publish)
    logger.info(
        "promptreg.prompt.unpublished tenant=%s skill=%s version=%s",
        tenant.id,
        version.skill_name,
        version.version,
    )
    return version
