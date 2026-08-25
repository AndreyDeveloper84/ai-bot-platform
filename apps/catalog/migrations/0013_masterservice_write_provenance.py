"""Write-provenance columns for ``MasterService`` (DRF-975).

Schema only — **no data migration, deliberately.** Both columns are added
NULLABLE with no default, so every pre-existing row (including the 232
unattributable ``formula-tela`` edges from 2026-07-22) keeps ``source IS NULL``
and is left exactly as it was.

That is not an oversight. Backfilling those rows to a sentinel such as
``legacy_operator_unknown`` is a *product* decision, not a schema one: it is
only useful in combination with a change to the catalog-sync ownership
contract (``apps.catalog.services.upserter``), which today keys "may I
reconcile this row?" strictly off ``ayla_specialist_service_id IS NULL`` and
would still not touch a re-labelled row. Making sync honour a new sentinel
means letting sync delete rows an operator may have authored by hand — which
is the risk the ownership contract was written to prevent. That trade belongs
to the product owner, and it is written up in the DRF-975 report rather than
smuggled in here.

ALTER TABLE ADD COLUMN with a NULL default is metadata-only on PostgreSQL 11+
— no table rewrite, no long lock, safe on the pilot.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0012_masterservice_resolved_health_check"),
    ]

    operations = [
        migrations.AddField(
            model_name="masterservice",
            name="created_by_actor_id",
            field=models.UUIDField(
                blank=True,
                help_text="identity.BotUser.id of the human who caused this edge, when there was one. NULL for machine writers (catalog sync) and for rows predating DRF-975. Not an FK on purpose: this is a forensic stamp that must survive the BotUser row being deleted.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="masterservice",
            name="source",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Which writer created this edge (apps.catalog.provenance.MasterServiceSource). NULL = created before DRF-975 shipped; author unrecoverable.",
                max_length=32,
                null=True,
            ),
        ),
    ]
