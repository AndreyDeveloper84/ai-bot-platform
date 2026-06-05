"""Food-scanner feature-specific 152-ФЗ consent — ``BotUser.food_scanner_consent_at``.

Wellness MVP scaled pilot (memory ``project_wellness_mvp_scaled_pilot``):
the food scanner / nutrition diary surface needs its OWN explicit
acknowledgement, separate from the broad welcome-S2 ``consent_at``. The
W1 frontend F0 gate (#962) persists this client-side via DeviceStorage;
this column is the server-side mirror used by the food_scanner skill +
miniapp_api endpoints to enforce consent on every cross-channel turn
(server consent audit trail per #956).

### Why nullable + no default

* ``null=True`` — backfill-free. Existing BotUsers get NULL, meaning
  «not yet consented» — correct for legacy users from before this PR.
  Skill / endpoints refuse cross-border photo processing + local
  food-log writes until set.
* No default — explicit «not yet» state is meaningful;
  ``auto_now_add`` would hide the never-shown state.
* No db_index — lookup is per-turn O(1) by primary key; no scan
  query motivates an index for this column.

### Gating contract

The food_scanner skill (apps/skills/food_scanner/skill.py) reads this
column in concert with ``settings.NUTRITION_ENABLED`` +
``settings.FOOD_PHOTO_SCAN_ENABLED``:

* ``NUTRITION_ENABLED=False`` → skill returns «feature off» reply.
* ``FOOD_PHOTO_SCAN_ENABLED=False`` → photo turns refused; manual
  food-log path still works when consent is granted.
* ``food_scanner_consent_at IS NULL`` → skill returns redirect-to-
  Mini-App reply. No Ayla call is made.

### Coordination

Founder verdict 2026-06-02 — feature-specific column over generic
``consent_at`` reuse, for explicit feature acknowledgement + audit
trail. Frontend Mini App owns the consent CTA (F0 in #962); the
miniapp_api ``POST /customer/food/consent`` endpoint (Веха 2) sets
this column to ``now()`` on tap.

### Deploy note (адверсариальный обзор PRE_PILOT #5)

Postgres 11+ ``ALTER TABLE … ADD COLUMN … NULL`` is a metadata-only
operation (no table rewrite) — fast, but still takes a brief
``ACCESS EXCLUSIVE`` lock. On the hot BotUser table this lock
contends with inflight queries: under pilot load expect a sub-second
latency spike during the migration. Run the migration during the
low-traffic window or pre-set ``lock_timeout`` so a stuck statement
doesn't pile up. No down-migration risk — the column is nullable +
unindexed, ``REMOVE COLUMN`` is symmetrically fast.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0012_alter_userpreferences_managers_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="botuser",
            name="food_scanner_consent_at",
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text=(
                    "Feature-specific 152-ФЗ consent for the food scanner "
                    "/ nutrition diary surface. NULL = not yet consented; "
                    "the food_scanner skill refuses cross-border photo "
                    "processing and local food-log writes until set. "
                    "Distinct from the broad ``consent_at`` (welcome S2)."
                ),
            ),
        ),
    ]
