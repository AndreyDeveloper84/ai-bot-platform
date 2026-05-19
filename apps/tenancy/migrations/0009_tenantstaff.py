"""TenantStaff model — admin-side role assignment (PR 1.5 / ADR-0008).

Creates the ``tenancy_tenantstaff`` table that holds Owner / Admin /
Receptionist role assignments per tenant. Master detection stays on
``CatalogMaster.linked_bot_user`` (PR #203); customer is the implicit
baseline for every BotUser.

The migration adds:

- The model + foreign keys to ``tenancy.Tenant`` and ``identity.BotUser``
  (both ``PROTECT`` so role history survives accidental drops).
- ``unique_together(tenant, bot_user, role)`` — same person can hold
  multiple roles, but never the same role twice in the same tenant.
- Partial unique constraint ``unique_active_owner_per_tenant`` — at most
  one active Owner row per tenant. Lets Owner handover work as a
  deactivate-then-create flow.
- Two indexes for resolver / admin filter hot paths.

No backfill: existing tenants get zero ``TenantStaff`` rows on apply.
The first Owner per tenant is created by operator action during the
Phase 4c onboarding flow (separate PR). For tests, fabricate the row
directly.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0004_userpreferences"),
        ("tenancy", "0008_tenant_cost_caps"),
    ]

    operations = [
        migrations.CreateModel(
            name="TenantStaff",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("receptionist", "Receptionist"),
                            ("admin", "Admin"),
                            ("owner", "Owner"),
                        ],
                        db_index=True,
                        help_text=(
                            "One of owner / admin / receptionist (ADR-0008). "
                            "Owner uniqueness is enforced per-tenant via "
                            "partial unique constraint below."
                        ),
                        max_length=16,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        help_text="When the staff row was created.",
                    ),
                ),
                (
                    "deactivated_at",
                    models.DateTimeField(
                        blank=True,
                        help_text=(
                            "When the role was revoked. NULL = active. "
                            "Soft deactivation preserves the historical "
                            "assignment for audit; the role resolver "
                            "ignores rows with this set, and the partial "
                            "unique-Owner constraint only counts active "
                            "rows so an Owner handover can deactivate the "
                            "old row then create the new one."
                        ),
                        null=True,
                    ),
                ),
                (
                    "bot_user",
                    models.ForeignKey(
                        help_text=(
                            "The person holding this role, identified by "
                            "their channel-scoped BotUser. PROTECT mirrors "
                            "the tenant FK — a delete must not silently "
                            "drop role history."
                        ),
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="staff_assignments",
                        to="identity.botuser",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        help_text=(
                            "BotUser who created this assignment — audit "
                            "trail for operator-led promotions. NULL "
                            "allowed for system-created rows (seed data, "
                            "management commands, migrations)."
                        ),
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="identity.botuser",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        help_text=(
                            "Owning tenant. PROTECT — dropping a tenant "
                            "must not silently nuke the audit trail of "
                            "who held what role."
                        ),
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="staff",
                        to="tenancy.tenant",
                    ),
                ),
            ],
            options={
                "verbose_name": "Tenant staff",
                "verbose_name_plural": "Tenant staff",
                "indexes": [
                    models.Index(
                        fields=["tenant", "role", "deactivated_at"],
                        name="tenancy_ten_tenant__42973d_idx",
                    ),
                    models.Index(
                        fields=["bot_user"],
                        name="tenancy_ten_bot_use_10345d_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(
                            ("deactivated_at__isnull", True),
                            ("role", "owner"),
                        ),
                        fields=("tenant",),
                        name="unique_active_owner_per_tenant",
                    ),
                ],
                "unique_together": {("tenant", "bot_user", "role")},
            },
        ),
    ]
