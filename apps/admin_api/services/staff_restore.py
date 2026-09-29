"""What «Вернуть доступ» may give back — one rule for the roster and the view (DRF-2274).

«Вернуть» is narrower than «was once held»: it is «held, and taken away by
a REVOKE, and nothing has replaced it since». Two readers ask it — the
roster (which rows get the button) and ``staff/restore/`` (which requests
are accepted) — and they must never disagree, so both call this module.

### Staff roles

``TenantStaff.deactivated_at`` is written by revoke AND by a role change
(``change_staff_role`` closes the old row too), so a deactivated row alone
proves nothing: after «admin → receptionist» the admin row is closed, and
restoring it would leave the person with two roles — exactly what the role
change removed. The proof is the latest ``staff.access_revoked`` row for
the person: its ``roles_revoked`` are what the revoke took. And only while
the person holds NO live staff role: a live one means somebody has decided
their role since, and a second one would be a grant, not a return.

### The master link

Revoking clears ``CatalogMaster.linked_bot_user`` and records the holder
nowhere but the ``staff.access_revoked`` row naming the card. That row goes
stale the moment the card is linked to anybody else — and not every path
that links a card writes an audit row naming it. So «superseded» is read
from every trace a later link leaves, compared with the revoke's time:

* ``invited_at`` / ``accepted_at`` on the card — the master-invite flow;
* a staff code for this card redeemed later (``StaffInvite.used_at``);
* an operator onboarding onto this card (``staff.specialist_onboarded``,
  written against the card).

A card linked later and unlinked WITHOUT a revoke (the holder's account
deleted — ``SET_NULL``; an operator's hand) therefore stays unrestorable:
the stale revoke row does not hand it back to the earlier holder.

Archived cards are never restorable: ``link_master_to_person`` refuses them.

### The journal outlives people, on purpose

«Забудь всё» does not touch the audit log, account deletion keeps
``AuditLog.target_id`` (``account_reset.KEPT_BY_DESIGN``), and retention
only soft-archives rows — read here through ``all_tenants``. So a revoke
row can name somebody who no longer exists; the view answers that as
``person_gone``, never as «not held».
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID

#: Staff roles a revoke can take and a restore can give back. Not owner.
RESTORABLE_STAFF_ROLES: frozenset[str] = frozenset({"admin", "receptionist"})


def revokes(tenant_id: Any) -> Any:
    from apps.audit.models import AuditLog
    from apps.events.vocabulary import STAFF_ACCESS_REVOKED

    # `all_tenants` on purpose: a soft-archived row is still the record.
    return AuditLog.all_tenants.filter(tenant_id=tenant_id, action=STAFF_ACCESS_REVOKED)


def restorable_staff_roles(tenant_id: Any, person_ids: Iterable[Any]) -> dict[UUID, set[str]]:
    """Per person: the staff roles the latest revoke took, if nothing replaced them."""

    from apps.tenancy.models import TenantStaff

    ids = list(person_ids)
    if not ids:
        return {}
    live = set(
        TenantStaff.all_tenants.filter(
            tenant_id=tenant_id, bot_user_id__in=ids, deactivated_at__isnull=True
        ).values_list("bot_user_id", flat=True)
    )
    candidates = [pid for pid in ids if pid not in live]
    out: dict[UUID, set[str]] = {}
    if not candidates:
        return out
    seen: set[Any] = set()
    for target_id, payload in (
        revokes(tenant_id)
        .filter(target_id__in=candidates)
        .order_by("target_id", "-created_at")
        .values_list("target_id", "payload")
    ):
        if target_id in seen:
            continue  # only the latest revoke speaks
        seen.add(target_id)
        roles = set((payload or {}).get("roles_revoked") or []) & RESTORABLE_STAFF_ROLES
        if roles:
            out[target_id] = roles
    return out


def latest_revoke_names(tenant_id: Any, person_id: Any, role: str) -> bool:
    """Did the latest revoke of this person take ``role``? (Idempotent-repeat check.)"""

    row = revokes(tenant_id).filter(target_id=person_id).order_by("-created_at").first()
    return row is not None and role in set((row.payload or {}).get("roles_revoked") or [])


def restorable_masters(tenant_id: Any, masters: Iterable[Any]) -> dict[str, UUID]:
    """Per unlinked, unarchived card: who the latest revoke took it from — unless superseded.

    ``masters`` are rows with ``id``, ``linked_bot_user_id``, ``archived_at``,
    ``invited_at``, ``accepted_at`` (model instances or ``.values()`` dicts).
    """

    from apps.audit.models import AuditLog
    from apps.events.vocabulary import STAFF_SPECIALIST_ONBOARDED
    from apps.tenancy.models import StaffInvite

    def col(row: Any, name: str) -> Any:
        return row[name] if isinstance(row, dict) else getattr(row, name)

    cards = {
        str(col(r, "id")): r
        for r in masters
        if col(r, "linked_bot_user_id") is None and col(r, "archived_at") is None
    }
    if not cards:
        return {}

    # A key lookup, not containment: `contains` on JSON is not portable
    # across the backends the suite runs on, and at salon scale the btree
    # indexes on (tenant, created_at) / (action, created_at) carry it.
    latest: dict[str, tuple[datetime, Any]] = {}
    for created_at, target_id, payload in (
        revokes(tenant_id)
        .filter(payload__master_id__in=list(cards))
        .values_list("created_at", "target_id", "payload")
    ):
        mid = str((payload or {}).get("master_id"))
        if mid in cards and (mid not in latest or created_at > latest[mid][0]):
            latest[mid] = (created_at, target_id)
    if not latest:
        return {}

    redeemed: dict[str, datetime] = {}
    for card_id, used_at in StaffInvite.all_tenants.filter(
        tenant_id=tenant_id, catalog_master_id__in=list(latest), used_at__isnull=False
    ).values_list("catalog_master_id", "used_at"):
        key = str(card_id)
        if used_at is not None and (key not in redeemed or used_at > redeemed[key]):
            redeemed[key] = used_at
    onboarded: dict[str, datetime] = {}
    for onboarded_card, created_at in AuditLog.all_tenants.filter(
        tenant_id=tenant_id,
        action=STAFF_SPECIALIST_ONBOARDED,
        target="catalog.CatalogMaster",
        target_id__in=list(latest),
    ).values_list("target_id", "created_at"):
        key = str(onboarded_card)
        if key not in onboarded or created_at > onboarded[key]:
            onboarded[key] = created_at

    out: dict[str, UUID] = {}
    for mid, (revoked_at, person_id) in latest.items():
        if person_id is None:
            continue
        card = cards[mid]
        later = [
            col(card, "invited_at"),
            col(card, "accepted_at"),
            redeemed.get(mid),
            onboarded.get(mid),
        ]
        if any(t is not None and t > revoked_at for t in later):
            continue  # linked to somebody since — the revoke row is stale
        out[mid] = person_id
    return out


__all__ = [
    "RESTORABLE_STAFF_ROLES",
    "revokes",
    "latest_revoke_names",
    "restorable_masters",
    "restorable_staff_roles",
]
