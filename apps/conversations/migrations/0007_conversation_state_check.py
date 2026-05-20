# Generated for Conversations retro Y6.

from django.db import migrations, models


def _normalise_unknown_states(apps, schema_editor):
    """Pre-flight data-safety: remap any out-of-enum ``state`` value to
    ``idle`` BEFORE the CHECK constraint is added.

    Reviewer Y-2: a Postgres ``ALTER TABLE … ADD CONSTRAINT … CHECK`` is
    validated against existing rows. A single ``state='zombie'`` row
    (hand-edited / corrupted / left over from a deprecated state) would
    abort the migration — exactly the prod-deploy-halt incident this
    migration is supposed to PREVENT. Sweep unknowns first, log the
    distinct offenders so ops can post-investigate, then add the
    constraint on a clean dataset.
    """

    Conversation = apps.get_model("conversations", "Conversation")
    AuditLog = apps.get_model("audit", "AuditLog")

    known = {"idle", "consulting", "escalated", "human_handoff"}
    offenders_qs = Conversation.objects.exclude(state__in=known)
    distinct_states = list(offenders_qs.values_list("state", flat=True).distinct())
    offender_count = offenders_qs.count()
    if offender_count == 0:
        return

    # Stamp an audit row so the remap is forensically recoverable.
    AuditLog.objects.create(
        action="conversations.state_check.unknowns_remapped",
        target="Conversation",
        payload={
            "remapped_count": offender_count,
            "distinct_unknown_states": sorted(set(distinct_states)),
            "remap_target": "idle",
        },
    )
    offenders_qs.update(state="idle")


def _noop_reverse(apps, schema_editor):
    # The remap is data-cleanup; reverse is a no-op (we can't reconstruct
    # the original unknown values, and the constraint removal in the
    # reverse direction makes the historical state irrelevant).
    pass


class Migration(migrations.Migration):
    """Conversations retro Y6: add CHECK constraint on Conversation.state.

    ``state`` is a CharField with TextChoices, which Django does NOT
    enforce at the DB layer. A hand-edited row with ``state='zombie'``
    would load silently and the D4 dispatcher's HUMAN_HANDOFF short-
    circuit would pass through, running the bot on undefined state.
    The CHECK constraint keeps the model and DB invariants aligned.

    Pre-flight sweep (closes reviewer Y-2): remap unknown values to
    ``idle`` with an AuditLog row before the constraint adds, so a
    single corrupted row can't halt the prod deploy.

    New states must be added here AND to the model's State enum.
    """

    dependencies = [
        ("conversations", "0006_conversation_skill_state"),
        # Audit migration prerequisite so the data-safety RunPython can
        # write its remap audit row.
        ("audit", "0002_auditlog_soft_delete"),
    ]

    operations = [
        migrations.RunPython(_normalise_unknown_states, _noop_reverse),
        migrations.AddConstraint(
            model_name="conversation",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("state__in", ["idle", "consulting", "escalated", "human_handoff"])
                ),
                name="conversation_state_known_value",
            ),
        ),
    ]
