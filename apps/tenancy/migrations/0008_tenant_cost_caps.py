# Phase 1 / PI9 (DRF-860) — per-tenant daily LLM cost ceiling.
#
# Adds two operational guardrail fields to Tenant:
#   * daily_token_cap     — BigIntegerField, default 1_000_000 tokens.
#   * daily_cost_cap_usd  — DecimalField(8,2), default $50.00.
#
# Both are enforced at the LLM call boundary by
# apps.llm.cost_tracker.enforce_caps. Existing tenants get the defaults
# on migrate; admin operators can raise / lower per-tenant via the
# "Cost Controls" fieldset in TenantAdmin.

from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenancy", "0007_tenant_telegram_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="daily_token_cap",
            field=models.BigIntegerField(
                default=1_000_000,
                help_text=(
                    "Daily LLM token budget (input + output, completion + "
                    "embedding). Reset at 00:00 UTC. The pre-call gate in "
                    "apps.llm.cost_tracker rejects with TenantQuotaExceeded "
                    "once the counter reaches this value."
                ),
            ),
        ),
        migrations.AddField(
            model_name="tenant",
            name="daily_cost_cap_usd",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("50.00"),
                help_text=(
                    "Daily USD spend budget. Cost per call is computed from "
                    "apps.llm.pricing.compute_cost; once the day's "
                    "accumulator crosses this value the cost-tracker rejects "
                    "further LLM calls via TenantQuotaExceeded. Reset at "
                    "00:00 UTC."
                ),
                max_digits=8,
            ),
        ),
    ]
