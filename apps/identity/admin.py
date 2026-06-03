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
red-zone read is the connection role: ``ayla_app`` is RLS-subject (red
rows are invisible without the ``ayla.red_zone_access_context`` GUC),
whereas a superuser / table-owner role bypasses RLS entirely.

``assert_admin_db_role()`` enforces this on the admin-serving path
(``get_queryset``) so a misconfigured deployment fails loud at first
admin use rather than silently exposing red rows. Do **NOT** bypass it
via raw SQL or ``manage.py dbshell`` — those authenticate as whatever
role the operator supplies and never pass through this guard.
"""

from __future__ import annotations

import logging

from django.contrib import admin
from django.db import connection

from apps.identity.models import BotUser, ClientProfile

logger = logging.getLogger(__name__)

# The DB login role the application (and therefore the admin) must run as
# in production so that red-zone RLS + role separation hold. Created by
# migration 0008_red_zone_db_security.
_EXPECTED_ADMIN_DB_ROLE = "ayla_app"


class RedZoneAdminRoleError(RuntimeError):
    """Admin is serving while connected as a DB role other than ``ayla_app``.

    Raised by :func:`assert_admin_db_role` on the admin-serving path when the
    Postgres connection role would bypass red-zone RLS (e.g. a superuser /
    table owner). Surfaces a deployment misconfiguration loudly instead of
    silently exposing 152-ФЗ red-zone rows.
    """


def assert_admin_db_role() -> None:
    """Runtime assertion (#572 / AS6): the admin must serve as ``ayla_app``.

    Fires on the admin-serving path (``get_queryset``), NOT at import time:
    ``django.contrib.admin`` autodiscovers this module during ``django.setup``
    for *every* management command, so an import-time DB assertion would break
    ``migrate`` (runs as ``ayla_migrator``), ``dbshell``, and the test suite
    (superuser test role). Checking at request time keeps those paths working
    while still guaranteeing a live admin connection is RLS-subject.

    No-op on non-Postgres engines (local SQLite has no role separation;
    application-layer defence via ``RedZoneReader`` covers that flow) and when
    the connection role is a known non-serving infra role (``ayla_migrator`` /
    ``ayla_backup``), which never serve admin traffic.

    Raises:
        RedZoneAdminRoleError: on Postgres when the effective connection role
            is neither ``ayla_app`` nor a known infra role.
    """
    if connection.vendor != "postgresql":
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user")
            role = cursor.fetchone()[0]
    except Exception:  # pragma: no cover - DB unreachable at this point
        # Don't convert a transient DB hiccup into a misleading role error;
        # the admin request will fail on its own query if the DB is truly down.
        logger.warning("Could not verify admin DB role", exc_info=True)
        return

    # ayla_migrator / ayla_backup legitimately connect outside the serving
    # path (migrations, physical backup) and must not trip the guard.
    if role in (_EXPECTED_ADMIN_DB_ROLE, "ayla_migrator", "ayla_backup"):
        return

    raise RedZoneAdminRoleError(
        f"Django admin is connected to Postgres as role {role!r}, not "
        f"{_EXPECTED_ADMIN_DB_ROLE!r}. Red-zone RLS + role separation "
        "(migration 0008) only protect 152-ФЗ red rows when the admin runs as "
        f"{_EXPECTED_ADMIN_DB_ROLE!r}; a superuser / owner role bypasses RLS. "
        "Fix the deployment DB credentials (see docs/specs/"
        "memory-entry-schema.md §11) instead of bypassing this check."
    )


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
        # #572 AS6 — fail loud if the admin is not RLS-subject (see module
        # docstring). Runs before any admin DB read on this connection.
        assert_admin_db_role()
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
        # #572 AS6 — see module docstring + BotUserAdmin.get_queryset.
        assert_admin_db_role()
        return ClientProfile.all_tenants.all()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
