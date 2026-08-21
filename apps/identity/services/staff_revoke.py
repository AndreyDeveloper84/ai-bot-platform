"""Revoke a person's salon access (DRF-1227).

### Why this exists

``staff_invites`` could grant access and nothing could take it back.
``TenantStaff.deactivated_at`` was referenced thirteen times in live code —
every read filtered on it — and **written by no one outside tests**. The
same held for ``CatalogMaster.linked_bot_user``: redemption set it, nothing
cleared it. A salon could hire and never fire.

That asymmetry is worse than a missing feature. Granting access without a
way to remove it is not "half the functionality", it is an accumulation of
permanent grants: every code ever issued stays live for as long as the
tenant exists, including codes issued to people who left.

### What revocation means here

**Everything or nothing, for one person, in one salon.** There is no
"remove the admin role but keep receptionist" — that is a role *change*,
a different operation with a different intent, and it does not exist yet.
Offering it as a side door of revoke would make the dangerous operation
the flexible one.

Two things come off, in one transaction:

* every active ``TenantStaff`` row — soft-deactivated, never deleted, so
  "who held what and until when" stays answerable (the same reason the FKs
  are ``PROTECT``);
* ``CatalogMaster.linked_bot_user`` — cleared, so the person can no longer
  sign in as that master.

### What deliberately does NOT happen

**The master stays bookable.** ``invite_status`` is left at ``accepted``
and ``is_active`` untouched, because ``_MasterManager.bookable()`` filters
on exactly those two fields: flipping them would quietly pull the master
out of the salon's booking surface. Revoking a person's login and removing
a master from sale are different decisions — the second one already has a
home (archive, MM5). A revoke that silently emptied the schedule would be
found out by customers, not by the operator.

### The owner is refused

A tenant has exactly one active owner (partial unique index), and only an
owner may issue an owner code (``views_staff_invite``). So deactivating the
owner row leaves a salon that nobody can ever grant access to again — not
recoverable through any surface we ship. Handover is a real operation
(deactivate old + create new, atomically) and it belongs in its own path;
until it exists, this one refuses rather than offering a door with no way
back.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant, TenantStaff

logger = logging.getLogger(__name__)

MAX_REASON_LEN = 200


class RevokeError(Exception):
    """Base for refusals a caller can present to a human."""

    slug = "revoke_failed"


class OwnerRevokeRefused(RevokeError):
    """The owner row cannot be revoked — see the module docstring."""

    slug = "owner_revoke_refused"


@dataclass(frozen=True)
class RevokeResult:
    """What actually came off, so the caller can say it out loud."""

    roles_revoked: tuple[str, ...]
    master_unlinked: bool

    @property
    def changed(self) -> bool:
        """False when the person already had no access — not an error.

        Revoking twice is a normal thing for a person to do (they are not
        sure the first one worked), and answering the second attempt with
        an error would teach them it did not.
        """

        return bool(self.roles_revoked) or self.master_unlinked


def revoke_staff_access(
    *,
    tenant: Tenant,
    bot_user: BotUser,
    actor: BotUser | None = None,
    reason: str = "",
) -> RevokeResult:
    """Take away every salon-side capability ``bot_user`` holds in ``tenant``.

    Idempotent: a second call finds nothing active and returns a result
    with ``changed=False``.

    Raises:
        OwnerRevokeRefused — the person is this salon's active owner.
    """

    from apps.audit.services import write_audit
    from apps.events.vocabulary import STAFF_ACCESS_REVOKED
    from apps.tenancy.context import tenant_scope

    reason = (reason or "").strip()[:MAX_REASON_LEN]
    now = timezone.now()

    with transaction.atomic():
        # No `select_related` under `select_for_update`: `created_by` is
        # nullable, and Postgres refuses FOR UPDATE over the nullable side
        # of an outer join (DRF-1160, learned the hard way).
        rows = list(
            TenantStaff.all_tenants.select_for_update().filter(
                tenant_id=tenant.id,
                bot_user=bot_user,
                deactivated_at__isnull=True,
            )
        )

        if any(row.role == TenantStaff.Role.OWNER for row in rows):
            logger.info(
                "identity.staff_revoke.owner_refused tenant=%s person=%s",
                tenant.slug,
                bot_user.id,
            )
            raise OwnerRevokeRefused("the salon owner's access cannot be revoked here")

        roles_revoked = tuple(sorted(row.role for row in rows))
        if rows:
            TenantStaff.all_tenants.filter(pk__in=[row.pk for row in rows]).update(
                deactivated_at=now
            )

        # The master link is a second, independent grant — a person can
        # hold one, both, or neither.
        master = (
            CatalogMaster.all_tenants.select_for_update()
            .filter(tenant_id=tenant.id, linked_bot_user=bot_user)
            .first()
        )
        master_unlinked = master is not None
        if master is not None:
            master.linked_bot_user = None
            master.save(update_fields=["linked_bot_user"])

        result = RevokeResult(roles_revoked=roles_revoked, master_unlinked=master_unlinked)

        if result.changed:
            # Inside the transaction on purpose: an audit row describing a
            # revoke that rolled back would be a lie. write_audit reads
            # current_tenant(), and this service is also callable from a
            # command or a consumer with no tenant set.
            with tenant_scope(tenant):
                write_audit(
                    STAFF_ACCESS_REVOKED,
                    target="identity.BotUser",
                    target_id=bot_user.id,
                    payload={
                        "roles_revoked": list(roles_revoked),
                        "master_unlinked": master_unlinked,
                        "master_id": str(master.id) if master is not None else None,
                        "actor_id": str(actor.id) if actor is not None else None,
                        "reason": reason,
                    },
                    actor_id=actor.id if actor is not None else None,
                )

    logger.info(
        "identity.staff_revoke.done tenant=%s person=%s roles=%s master=%s changed=%s",
        tenant.slug,
        bot_user.id,
        ",".join(roles_revoked) or "-",
        master_unlinked,
        result.changed,
    )
    return result
