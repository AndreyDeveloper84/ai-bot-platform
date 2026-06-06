"""Seed the ``global_bot`` sentinel tenant (#1019 / EPIC #1014).

DATA migration (no schema change). Provisions the system Tenant that owns the
global, tenant-less bot identity so ``BotUser.unique_together (tenant, channel,
channel_user_id)`` is satisfied for global users without altering the schema.

The runtime helper ``apps.identity.services.global_tenant.get_global_bot_tenant``
also ``get_or_create``s this row as an idempotent safety net; this migration is
the deterministic, ops-visible seed.
"""

from __future__ import annotations

from django.db import migrations

GLOBAL_BOT_TENANT_SLUG = "global_bot"


def _seed_global_bot_tenant(apps, schema_editor):
    Tenant = apps.get_model("tenancy", "Tenant")
    # Historical model in a migration carries a vanilla manager (custom
    # TenantScoped managers are stripped) — this get_or_create is unscoped.
    Tenant.objects.get_or_create(
        slug=GLOBAL_BOT_TENANT_SLUG,
        defaults={"name": "Global Bot Identity", "is_system": True},
    )


def _unseed_global_bot_tenant(apps, schema_editor):
    Tenant = apps.get_model("tenancy", "Tenant")
    BotUser = apps.get_model("identity", "BotUser")
    tenant = Tenant.objects.filter(slug=GLOBAL_BOT_TENANT_SLUG).first()
    if tenant is None:
        return
    # Never strand global BotUsers: only remove the sentinel when nothing
    # references it.
    if BotUser.objects.filter(tenant=tenant).exists():
        return
    tenant.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0013_botuser_food_scanner_consent_at"),
        ("tenancy", "0009_tenantstaff"),
    ]

    operations = [
        migrations.RunPython(_seed_global_bot_tenant, _unseed_global_bot_tenant),
    ]
