"""Role resolver — single source of truth for "what is this BotUser?".

Per ADR-0008, the platform has 5 roles per BotUser per tenant:

- ``customer`` — implicit baseline, always available.
- ``master`` — has ``CatalogMaster.linked_bot_user`` pointing at this BotUser.
- ``receptionist`` / ``admin`` / ``owner`` — active row in
  :class:`apps.tenancy.models.TenantStaff` with the matching ``role``.

A BotUser can hold multiple roles simultaneously (an owner-master, an
admin-customer, etc.) — see ADR-0008 decision 3 for the additive-roles
contract. The resolver returns a :class:`RoleContext` carrying:

- ``primary_role`` — highest privilege (used for landing-screen routing,
  the role chip on the frontend, the bot DM greeting).
- Five booleans (``is_customer``, ``is_master``, ``is_receptionist``,
  ``is_admin``, ``is_owner``) — useful for "does this user qualify for
  this view at all" checks.
- ``master_id`` — the :class:`CatalogMaster` UUID when the user is a
  master, else ``None``.
- ``landing_path`` — convenience for the Mini App.

Capability checks go through :func:`has_capability`, which consults a
hard-coded matrix that mirrors
``docs/design/policies/conversation-ownership-policy.md §4``. The
frontend uses the capabilities for menu visibility; every privileged
endpoint must re-check server-side (ADR-0008 decision 4).

Performance: ``resolve_role`` does at most two queries (one for the
master link via reverse OneToOne lookup, one bulk select for active
``TenantStaff`` rows). Both are indexed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal
from uuid import UUID

if TYPE_CHECKING:
    from apps.identity.models import BotUser


# --- types ----------------------------------------------------------------


PrimaryRole = Literal["customer", "master", "receptionist", "admin", "owner"]


# Increasing privilege per ADR-0008. Used for the "highest wins"
# tie-breaker that picks ``primary_role``. Customer is always present
# (implicit baseline) and so always at rank 0.
_ROLE_PRIVILEGE: dict[PrimaryRole, int] = {
    "customer": 0,
    "master": 1,
    "receptionist": 2,
    "admin": 3,
    "owner": 4,
}


# Mini App landing destination per role. Convenience only — the frontend
# is free to override based on its own state (e.g. deep-link target).
# Server-side capability checks are still authoritative for any action.
_LANDING_PATH: dict[PrimaryRole, str] = {
    "owner": "/admin/dashboard",
    "admin": "/admin/dashboard",
    "receptionist": "/admin/conversations",
    "master": "/master/dashboard",
    "customer": "/",
}


@dataclass(frozen=True)
class RoleContext:
    """Effective role context for a BotUser within a tenant.

    Resolved server-side from the two source tables
    (:class:`apps.catalog.models.CatalogMaster` and
    :class:`apps.tenancy.models.TenantStaff`). Frontend uses for
    convenience UI; capability checks must always go through
    :func:`has_capability` — never frontend-only.

    The dataclass is frozen so a resolved context can be cached on the
    request without risk of mutation downstream.
    """

    bot_user_id: UUID
    tenant_id: UUID
    primary_role: PrimaryRole
    is_customer: bool  # always True per ADR-0008 decision 6
    is_master: bool
    is_receptionist: bool
    is_admin: bool
    is_owner: bool
    master_id: UUID | None  # CatalogMaster.id if is_master, else None
    landing_path: str
    # The full capability set the user qualifies for under ANY of their
    # roles. Computed in resolve_role so callers don't have to iterate
    # all five booleans themselves.
    capabilities: frozenset[str] = field(default_factory=frozenset)


# --- capability matrix ---------------------------------------------------


# Source of truth: docs/design/policies/conversation-ownership-policy.md §4
# (the permissions matrix retained as authoritative even though the
# surrounding tier doc was deprecated 2026-05-19 — see ADR-0008 context).
#
# The slugs below mirror the matrix row-by-row. Where the matrix says
# "own only" for master / receptionist, the slug has the ``_own``
# suffix; "all" surfaces use ``_all``. The split lets endpoints query
# the right scope for the right caller without role-name branching.
#
# Where the matrix shows "✅ (with audit log)" for receptionist phone
# reveal, we encode the capability as True and rely on the endpoint to
# write the audit row. The audit-on-access requirement lands in a
# separate PR — see the TODO immediately below this block.
#
# TODO(role-audit-PR): receptionists with ``view_customer_phone_audited``
# must trigger an audit row at the call site. Until that PR lands the
# gate passes but no audit is written. Tracked separately because the
# audit slug + receiver wiring is a meaningful chunk of code.
CAPABILITIES: dict[PrimaryRole, frozenset[str]] = {
    "owner": frozenset(
        {
            # All-scope conversation visibility
            "view_conversation_list_all",
            "view_customer_phone",
            "view_customer_medical",
            "view_customer_ltv",
            # Reply / lifecycle
            "send_reply_all",
            "promote_human_locked",
            "demote_human_locked",
            "approve_resume_after_locked",
            # KB + catalog
            "add_response_to_kb",
            "add_service_to_catalog",
            # Conversation ops
            "snooze_conversation_all",
            "escalate_csm",
            "block_customer",
            # Owner-only privileges
            "manage_tenant_roles",
            "export_data",
            "tune_assistant",
            "change_assistant_persona",
            "override_sla",
        }
    ),
    "admin": frozenset(
        {
            "view_conversation_list_all",
            "view_customer_phone",
            # view_customer_medical: Admin gets it only with
            # ``medical_role`` tenant-flag per §4 (encoded as conditional
            # at the call site, NOT here; default-no in the matrix).
            "view_customer_ltv",
            "send_reply_all",
            "promote_human_locked",
            "demote_human_locked",
            "approve_resume_after_locked",
            "add_response_to_kb",
            "add_service_to_catalog",
            "snooze_conversation_all",
            "escalate_csm",
            "block_customer",
        }
    ),
    "receptionist": frozenset(
        {
            "view_conversation_list_all",
            # Audited phone reveal — see TODO above. The capability
            # gate passes; the audit-on-access requirement lands in a
            # follow-up PR.
            "view_customer_phone_audited",
            "send_reply_all",
            "promote_human_locked",
            "snooze_conversation_all",
            "escalate_csm",
        }
    ),
    "master": frozenset(
        {
            "view_conversation_list_own",
            # Master does NOT see customer phone numbers per §4.
            "send_reply_own",
            "promote_human_locked",
            "snooze_conversation_own",
            "escalate_csm",
        }
    ),
    # Customer baseline is the empty set — every "customer" capability
    # is implicit (browse catalog, book, message the bot), gated by
    # init-data verification at the endpoint, not by this matrix.
    "customer": frozenset(),
}


def has_capability(role_ctx: RoleContext, capability: str) -> bool:
    """Server-side capability gate. Authoritative per ADR-0008 decision 4.

    Returns True iff ``capability`` is in the role context's combined
    capability set. The set is computed in :func:`resolve_role` as the
    union of every role the user qualifies for, so an owner-master
    holding both an Owner ``TenantStaff`` row and a
    ``CatalogMaster.linked_bot_user`` link gets owner-scoped capabilities
    AND the master ``view_conversation_list_own`` superset (mostly
    redundant — owner already has ``_all`` — but the union covers
    edge cases at zero cost).

    Always re-check capabilities at the endpoint. The frontend role chip
    is convenience only; a forged request that claims a role it doesn't
    hold MUST fail here.
    """

    return capability in role_ctx.capabilities


# --- resolver ------------------------------------------------------------


def _compute_landing_path(primary_role: PrimaryRole) -> str:
    """Convenience: Mini App landing destination for the highest role."""

    return _LANDING_PATH[primary_role]


def resolve_role(bot_user: "BotUser") -> RoleContext:
    """Resolve the effective :class:`RoleContext` for ``bot_user``.

    Reads two source tables:

    - :class:`apps.catalog.models.CatalogMaster` — reverse OneToOne via
      ``linked_bot_user``. If the linked master is ACCEPTED and not
      archived, the user is a master.
    - :class:`apps.tenancy.models.TenantStaff` — active rows
      (``deactivated_at IS NULL``) within ``bot_user.tenant``.

    Per ADR-0008, all rows return ``is_customer=True``. Primary role is
    the highest privilege the user qualifies for. Capabilities are the
    union of every role's capability set.

    Cross-tenant: the resolver only looks at the BotUser's own tenant.
    A BotUser is tenant-scoped by uniqueness; the same person at two
    salons has two BotUsers and resolves separately.
    """

    # Local import to avoid models/services import-time cycle. (BotUser
    # imports TenantScopedManager from tenancy, which doesn't import
    # this module — but ``apps.identity.models`` is loaded before
    # services on AppConfig.ready, so a top-level import here would
    # work today; keeping it local is defensive against future imports
    # from this service in models.)
    from apps.catalog.models import CatalogMaster
    from apps.tenancy.models import TenantStaff

    # --- master detection ---
    # The reverse OneToOne attribute name from CatalogMaster.linked_bot_user
    # is ``master_identity`` per the related_name on the FK. Use
    # CatalogMaster.all_tenants for tenant-safety (the BotUser already
    # carries its tenant; we re-filter explicitly as defence-in-depth).
    master_row = (
        CatalogMaster.all_tenants.filter(
            tenant=bot_user.tenant_id,
            linked_bot_user=bot_user,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            archived_at__isnull=True,
        )
        .only("id")
        .first()
    )
    is_master = master_row is not None
    master_id: UUID | None = master_row.id if master_row is not None else None

    # --- staff detection ---
    # Active rows only. The composite index on (tenant, role,
    # deactivated_at) covers this filter exactly. Use all_tenants
    # because resolve_role is called from auth decorators that haven't
    # entered tenant_scope yet — defence-in-depth.
    staff_roles: set[str] = set(
        TenantStaff.all_tenants.filter(
            tenant=bot_user.tenant_id,
            bot_user=bot_user,
            deactivated_at__isnull=True,
        ).values_list("role", flat=True)
    )
    is_owner = TenantStaff.Role.OWNER in staff_roles
    is_admin = TenantStaff.Role.ADMIN in staff_roles
    is_receptionist = TenantStaff.Role.RECEPTIONIST in staff_roles

    # --- primary role: highest privilege wins ---
    primary_role: PrimaryRole = "customer"
    if is_owner:
        primary_role = "owner"
    elif is_admin:
        primary_role = "admin"
    elif is_receptionist:
        primary_role = "receptionist"
    elif is_master:
        primary_role = "master"

    # --- combined capabilities: union of every role the user holds ---
    held_roles: list[PrimaryRole] = ["customer"]
    if is_master:
        held_roles.append("master")
    if is_receptionist:
        held_roles.append("receptionist")
    if is_admin:
        held_roles.append("admin")
    if is_owner:
        held_roles.append("owner")

    combined: set[str] = set()
    for role in held_roles:
        combined.update(CAPABILITIES[role])

    return RoleContext(
        bot_user_id=bot_user.id,
        tenant_id=bot_user.tenant_id,
        primary_role=primary_role,
        is_customer=True,
        is_master=is_master,
        is_receptionist=is_receptionist,
        is_admin=is_admin,
        is_owner=is_owner,
        master_id=master_id,
        landing_path=_compute_landing_path(primary_role),
        capabilities=frozenset(combined),
    )


__all__ = [
    "CAPABILITIES",
    "PrimaryRole",
    "RoleContext",
    "has_capability",
    "resolve_role",
]
