"""BotUser admin (DRF-434 / Sprint 2 / A2).

Admin surface for support staff who need to look up a user across all
tenants (cross-tenant access is the whole point — admin is the escape
hatch). Read-mostly: edits to ``client_name`` / ``phone`` / ``context``
are allowed for manual data fixes, everything else is read-only.

# Red-zone safety via DB role separation (#572 / Sprint 1 Track A AS6)

Any red-zone ``MemoryEntry`` read that becomes reachable through the
Django admin relies on the Postgres RLS policy + the 5-role separation
created in migration ``0008_red_zone_db_security`` — NOT on the AST lint
``tools/lint/red_zone_guard.py``, which deliberately allowlists this
module (spec §11). The only thing between an admin page and an un-gated
red-zone read is whether the connection role is **RLS-subject**: a role
that is neither ``SUPERUSER`` nor ``BYPASSRLS`` cannot see red rows
without the ``ayla.red_zone_access_context`` GUC, whereas a superuser /
``BYPASSRLS`` role bypasses RLS entirely — regardless of its *name*.

``assert_admin_db_role()`` enforces this **capability** (not a role-name
match — a role called ``ayla_app`` carrying ``BYPASSRLS`` would still
leak). ``RedZoneGuardedAdminMixin`` is the mandatory base for any future
ModelAdmin that reads red-zone rows; it runs the assertion on the
serving path (``get_queryset``) so a misconfigured deployment fails loud
rather than silently exposing red rows. Do **NOT** bypass it via raw SQL
or ``manage.py dbshell`` — those authenticate as whatever role the
operator supplies and never pass through this guard.

NB: no red-zone admin is registered today — red-zone reads go through
``apps.identity.services.red_zone_reader.RedZoneReader`` (audited). The
mixin is deliberately **not** applied to ``BotUserAdmin`` /
``ClientProfileAdmin`` because neither reads red-zone ``MemoryEntry``
content; applying it there would be misplaced (false assurance).
"""

from __future__ import annotations

import logging

from django.contrib import admin
from django.db import connection

from apps.identity.models import BotUser, ClientProfile

logger = logging.getLogger(__name__)


class RedZoneAdminRoleError(RuntimeError):
    """Admin is serving while connected as a DB role that bypasses RLS.

    Raised by :func:`assert_admin_db_role` on the admin-serving path when the
    Postgres connection role is ``SUPERUSER`` or has ``BYPASSRLS`` — either of
    which defeats the red-zone RLS policy. Surfaces a deployment
    misconfiguration loudly instead of silently exposing 152-ФЗ red-zone rows.
    """


def assert_admin_db_role() -> None:
    """Runtime assertion (#572 / AS6): the admin must serve as an RLS-subject role.

    The security property is a **capability**, not a name: red-zone RLS only
    protects red rows when the connection role is neither ``SUPERUSER`` nor
    ``BYPASSRLS``. A role *named* ``ayla_app`` that was granted ``BYPASSRLS``
    would still leak — so we check ``pg_roles.rolsuper`` / ``rolbypassrls`` for
    the effective ``current_user``, never the role name (S5 finding).

    Fires on the admin-serving path (``get_queryset`` of
    :class:`RedZoneGuardedAdminMixin`), NOT at import time:
    ``django.contrib.admin`` autodiscovers this module during ``django.setup``
    for *every* management command, so an import-time DB assertion would break
    ``migrate``, ``dbshell``, and the test suite (superuser test role).

    No-op on non-Postgres engines (local SQLite has no roles/RLS;
    application-layer defence via ``RedZoneReader`` covers that flow).

    Raises:
        RedZoneAdminRoleError: on Postgres when the effective connection role
            is ``SUPERUSER`` or ``BYPASSRLS`` (or cannot be found in
            ``pg_roles`` — fail closed).
    """
    if connection.vendor != "postgresql":
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_user, rolsuper, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
            row = cursor.fetchone()
    except Exception:  # pragma: no cover - DB unreachable at this point
        # Don't convert a transient DB hiccup into a misleading role error;
        # the admin request will fail on its own query if the DB is truly down.
        logger.warning("Could not verify admin DB role capabilities", exc_info=True)
        return

    if row is None:
        # current_user not present in pg_roles should never happen; fail closed.
        raise RedZoneAdminRoleError(
            "Could not resolve the admin connection role in pg_roles; refusing "
            "to serve red-zone-capable admin without a verified RLS-subject role."
        )

    role, rolsuper, rolbypassrls = row
    if rolsuper or rolbypassrls:
        raise RedZoneAdminRoleError(
            f"Django admin is connected to Postgres as role {role!r} with "
            f"rolsuper={rolsuper}, rolbypassrls={rolbypassrls} — it BYPASSES "
            "red-zone RLS. The admin must serve as an RLS-subject role (neither "
            "SUPERUSER nor BYPASSRLS), e.g. ayla_app. Fix the deployment DB "
            "credentials (see docs/specs/memory-entry-schema.md §11) instead of "
            "bypassing this check."
        )


class RedZoneGuardedAdminMixin:
    """Mandatory base for any ModelAdmin that can read RLS-gated red-zone rows.

    Runs :func:`assert_admin_db_role` before every queryset read so the admin
    fails loud if the serving connection could bypass red-zone RLS. A future
    ``MemoryEntryAdmin`` (or any admin exposing red-zone ``MemoryEntry``
    content) MUST inherit this mixin **before** ``admin.ModelAdmin`` so the
    guard runs ahead of the real queryset:

        class MemoryEntryAdmin(RedZoneGuardedAdminMixin, admin.ModelAdmin):
            ...

    It is intentionally NOT mixed into ``BotUserAdmin`` / ``ClientProfileAdmin``
    — those do not read red-zone content, so guarding them would be misplaced.
    """

    def get_queryset(self, request):
        assert_admin_db_role()
        return super().get_queryset(request)


@admin.register(BotUser)
class BotUserAdmin(admin.ModelAdmin):
    list_display = ("channel", "channel_user_id", "display_name", "tenant", "last_seen")
    list_filter = ("channel", "tenant")
    search_fields = ("channel_user_id", "phone", "display_name", "client_name")
    readonly_fields = (
        "id",
        "tenant",
        "channel",
        "channel_user_id",
        "first_seen",
        "last_seen",
    )
    fieldsets = (
        (None, {"fields": ("id", "tenant", "channel", "channel_user_id")}),
        (
            "Identity (editable for manual fixes)",
            {"fields": ("display_name", "client_name", "phone", "chat_id")},
        ),
        (
            "Personalisation",
            {"fields": ("timezone", "context"), "classes": ("collapse",)},
        ),
        (
            "Системное",
            {"fields": ("first_seen", "last_seen"), "classes": ("collapse",)},
        ),
    )

    def get_queryset(self, request):
        # Admin must see all tenants for support cases; the
        # TenantScopedManager filter would hide rows otherwise.
        return BotUser.all_tenants.all()


@admin.register(ClientProfile)
class ClientProfileAdmin(admin.ModelAdmin):
    """Read-only forensic view of computed RFM/LTV/tier per bot_user.

    All fields are derived by `apps.identity.services` — no manual edits.
    Recompute via Celery beat (P7) or `booking_completed` signal (P8).
    """

    list_display = (
        "bot_user",
        "tenant",
        "rfm_segment",
        "loyalty_tier",
        "lifecycle_stage",
        "recency_days",
        "frequency_visits",
        "monetary_total",
        "last_recomputed_at",
    )
    list_filter = ("rfm_segment", "loyalty_tier", "lifecycle_stage", "tenant")
    search_fields = (
        "bot_user__channel_user_id",
        "bot_user__phone",
        "bot_user__client_name",
    )
    readonly_fields = (
        "bot_user",
        "tenant",
        "recency_days",
        "frequency_visits",
        "monetary_total",
        "rfm_segment",
        "ltv",
        "predicted_ltv_12m",
        "churn_risk",
        "lifecycle_stage",
        "avg_visit_interval_days",
        "favorite_service_id",
        "favorite_category_id",
        "preferred_master_id",
        "loyalty_tier",
        "last_recomputed_at",
    )

    def get_queryset(self, request):
        return ClientProfile.all_tenants.all()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
