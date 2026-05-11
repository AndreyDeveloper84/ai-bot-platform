"""DRF-459 / Sprint 3 / B1 — Event canonical envelope.

Adds the F0.7 canonical fields (event_name + distinct_id + dialog_id +
properties) alongside the legacy ones (event_type + payload). Both
shapes coexist for one sprint so the ~60 emit() call sites keep
working. Sprint 4 drops event_type + payload once all callers migrate.

Existing rows pick up empty defaults (event_name="", distinct_id="",
dialog_id=NULL, properties={}) — safe on Postgres because all new
columns are NULL-able or carry an explicit default.

Index added: (tenant, event_name, -created_at) — the canonical
per-tenant query path for replay tooling.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0001_initial"),
        ("tenancy", "0002_tenant_features_tenant_locale_tenant_plan_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="dialog_id",
            field=models.UUIDField(
                blank=True,
                db_index=True,
                help_text="Conversation.id for events attached to a turn. Null for system/background events (webhook ingestion, breaker rotation, etc.).",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="event",
            name="distinct_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="Cross-channel user identity. Stringified BotUser.id (UUID) for now; cross-channel composite arrives in Sprint 4+.",
                max_length=128,
            ),
        ),
        migrations.AddField(
            model_name="event",
            name="event_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Canonical event name (F0.7). Namespaced verb, e.g. 'consent_granted', 'webhook_received'. New code reads this field; old subscribers fall back to event_type during the back-compat window.",
                max_length=80,
            ),
        ),
        migrations.AddField(
            model_name="event",
            name="properties",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Canonical structured props (F0.7). Never raw PII — IDs, hashes, and consent-type/reason strings only.",
            ),
        ),
        migrations.AlterField(
            model_name="event",
            name="event_type",
            field=models.CharField(
                help_text="DEPRECATED — legacy event identifier. Use event_name. Mirrored to event_name by emit() during the Sprint 3 → Sprint 4 back-compat window; removed in Sprint 4.",
                max_length=120,
            ),
        ),
        migrations.AlterField(
            model_name="event",
            name="payload",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="DEPRECATED — legacy structured payload. Use 'properties'. Mirrored from properties during the back-compat window; removed in Sprint 4.",
            ),
        ),
        migrations.AddIndex(
            model_name="event",
            index=models.Index(
                fields=["tenant", "event_name", "-created_at"],
                name="events_even_tenant__5a043c_idx",
            ),
        ),
    ]
