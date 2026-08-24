"""DRF-1353 — mirror Ayla's resolved (master×service) health-check flag.

Nullable ``ADD COLUMN`` with no default: Postgres 11+ rewrites nothing and
takes only a brief ACCESS EXCLUSIVE lock, so this is safe to run online on
the pilot.

The NULL is deliberate. Existing rows become "unknown", which the booking
gate reads as "screening required" — identical to the behaviour before this
column existed. Backfilling ``False`` would have opened a medical gate for
every already-mirrored edge in one migration.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0011_masterservice_ayla_edge_provenance"),
    ]

    operations = [
        migrations.AddField(
            model_name="masterservice",
            name="resolved_requires_health_check",
            field=models.BooleanField(
                blank=True,
                default=None,
                help_text="Mirrored Ayla SpecialistService.resolved_requires_health_check. NULL = unknown (never synced); the booking health-check gate treats NULL as 'screening required'.",
                null=True,
            ),
        ),
    ]
