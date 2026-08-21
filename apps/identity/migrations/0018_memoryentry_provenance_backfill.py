# Data migration — canonical provenance backfill (Memory Domain Contract
# §3.1, Migration Plan Step 3B; owner/architect ruling: legacy `source` is
# NOT re-purposed as canonical provenance).
#
# Mapping (deliberately narrow):
#   source == "explicit"          -> provenance = "user_stated"
#   source == "inferred"/"signal" -> provenance stays NULL.
#     user_confirmed_inference exists ONLY after explicit user confirmation
#     through the proposal flow — silent promotion of inferred/signal
#     legacy rows is forbidden; they are reported as anomalies instead.
#   any other legacy source value -> provenance stays NULL (anomaly).
#
# Idempotent: only rows with provenance IS NULL are written; nothing else
# (confirmation evidence, derivation_method, evidence_refs, consent_scope)
# is fabricated. Historical models via apps.get_model; bulk .update() only.
#
# Backwards semantics (documented choice): NULL provenance exactly where
# THIS rule could have written it — source="explicit" AND
# provenance="user_stated". A later writer that legitimately sets
# user_stated on an explicit row is indistinguishable from the backfill;
# rolling back a data migration is a return-to-pre-Step-3B state, so those
# rows return to NULL as well. Inferred/signal/unknown rows are never
# touched in either direction.

from django.db import migrations
from django.db.models import Q


def backfill_provenance(apps, schema_editor):
    MemoryEntry = apps.get_model("identity", "MemoryEntry")
    MemoryEntry.objects.filter(provenance__isnull=True, source="explicit").update(
        provenance="user_stated"
    )
    # inferred / signal / unknown-source rows: intentionally left NULL —
    # anomaly reporting happens in the Step-3B runbook/report, not here.


def revert_provenance(apps, schema_editor):
    MemoryEntry = apps.get_model("identity", "MemoryEntry")
    MemoryEntry.objects.filter(Q(source="explicit") & Q(provenance="user_stated")).update(
        provenance=None
    )


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0017_memoryentry_provenance"),
    ]

    operations = [
        migrations.RunPython(backfill_provenance, revert_provenance),
    ]
