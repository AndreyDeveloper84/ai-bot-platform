"""Read-only AuditLog admin (DRF-426 / B1).

Audit data is forensic — admin can view + filter + export but never
edit, add, or delete. Retention is handled by
``apps.audit.tasks.cleanup_old_audit_logs`` (Celery task), not by
human cleanup.
"""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from apps.audit.models import AuditLog


class Q12aChainTerminatorFilter(admin.SimpleListFilter):
    """Surface partial-failure reschedule audit rows (issue #530).

    Q12-α tags rows where the reschedule chain TERMINATED outside the
    normal CONFIRMED → RESCHEDULED transition (today: partial-failure
    splits between YClients cancel-success and local-write-fail). These
    rows have ``payload.q12a_chain_terminator == True`` and need
    operator follow-up («manual rebook required» tickets).

    Without this filter, ops would need raw SQL like
    ``SELECT * FROM apps_audit_log WHERE payload @> '{"q12a_chain_terminator": true}'``
    to find them. The filter uses Django's ``has_key`` JSONField
    lookup which compiles to:

    * Postgres: ``payload ? 'q12a_chain_terminator'`` — indexable via
      GIN, efficient at scale
    * SQLite:   ``JSON_TYPE(payload, '$.q12a_chain_terminator') IS NOT NULL``
      — efficient enough for tests

    **Contract**: the writer at ``apps/skills/booking/tools.py`` ALWAYS
    sets ``q12a_chain_terminator: True`` (never ``False``) when the
    chain terminates. Non-terminator rows simply omit the field. So
    «key present» ⇔ «terminator». A test in
    ``test_admin_filters.py::test_q12a_terminator_reason_preserved_for_grouping``
    pins this contract. If a future writer ever stores ``False`` here,
    the filter semantics MUST be re-evaluated AND switched to the
    Postgres-only ``payload__contains={...}`` lookup (gated by backend).
    """

    title = "Q12-α chain terminator"
    parameter_name = "q12a_terminator"

    def lookups(self, request, model_admin):
        return (
            ("yes", "Chain terminators (partial-failure tickets)"),
            ("no", "Non-terminator rows"),
        )

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(payload__has_key="q12a_chain_terminator")
        if self.value() == "no":
            # «Не-терминаторы»: ключа payload.q12a_chain_terminator
            # просто нет (writer ставит его ТОЛЬКО при terminator-событии).
            return queryset.exclude(payload__has_key="q12a_chain_terminator")
        return queryset


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "tenant",
        "action",
        "target",
        "target_id",
        "actor_id",
        "is_archived",
    )
    # is_archived filter exposes soft-deleted rows in admin without
    # losing the default "live rows only" view — operators can flip
    # the filter to inspect retention residue (DRF-851 / PI1).
    # Q12aChainTerminatorFilter (issue #530) surfaces partial-failure
    # tickets that need manual rebook — JSONB containment query.
    list_filter = (
        Q12aChainTerminatorFilter,
        "is_archived",
        "action",
        "target",
        "tenant",
    )
    # Audit retro B2: ``target_id`` / ``actor_id`` are UUIDField.
    # Pre-fix they were in ``search_fields``; Django admin builds
    # ``WHERE target_id ILIKE '%foo%'`` against the UUID column which
    # Postgres rejects with ``invalid input syntax for type uuid`` the
    # moment an operator types anything that isn't a complete UUID.
    # Forensic search was effectively broken. Post-fix only string-
    # typed columns participate in the substring search; admins
    # filtering by id should use the URL query string with an exact
    # UUID value (``?target_id=<uuid>``).
    search_fields = ("action", "target")
    readonly_fields = (
        "id",
        "tenant",
        "actor_id",
        "action",
        "target",
        "target_id",
        "payload",
        "created_at",
        "is_archived",
        "archived_at",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    # Admin must see every tenant's rows for forensic work.
    def get_queryset(self, request):
        return AuditLog.all_tenants.all()

    # Forbid all mutation paths.
    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False
