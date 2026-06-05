"""S1 onboarding idempotency — ``BotUser.welcomed_at`` (task #85, W2/Epsilon).

Customer first-time experience handoff (Tau's customer-onboarding-flow.md
S1 step) requires bot to auto-trigger welcome message on FIRST contact —
not just on explicit ``/start``. Without a per-BotUser timestamp the
welcome would re-fire on every subsequent message ("я просто хотела
спросить" → bot спамит menu снова).

``welcomed_at`` records the timestamp of the first successful welcome
delivery. ``WelcomeSkill.matches`` short-circuits when this field is
non-NULL, falling back to the existing routing logic
(``/start`` + ``cb:welcome:*``) plus dispatcher's normal flow for other
texts.

### Why nullable + db_index

* ``null=True`` — backfill-free deploy. Existing BotUsers (who already
  saw welcome via /start before this PR) get NULL, which means the
  next message MAY re-trigger welcome once — acceptable trade-off
  vs an offline backfill job.
* ``db_index=True`` — the welcome auto-trigger path checks this field
  on every inbound message; an index keeps the ``is None`` evaluation
  trivially cheap. Single-column index, no composite.

### Coordination note (tech-lead handoff 2026-05-26)

W4 has parallel task #86 adding ``consent_at`` for S2 phase. Tech-lead
explicitly OK'd adding ``welcomed_at`` here in this PR rather than
batching with W4 — single-field migration is low risk; W4's PR will
add ``consent_at`` separately. If W4 ends up bundling ``welcomed_at``
in their PR by accident, merge resolution is trivial (one of the two
migrations becomes a no-op).
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0009_clientprofile_review_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="botuser",
            name="welcomed_at",
            field=models.DateTimeField(
                null=True,
                blank=True,
                db_index=True,
                help_text=(
                    "Timestamp первого успешного welcome (S1 onboarding) "
                    "delivery. NULL для BotUsers, существовавших до "
                    "task #85 deploy — для них welcome MAY re-fire one "
                    "more time на следующем message (acceptable). Set "
                    "by WelcomeSkill.handle() at first trigger; never "
                    "reset."
                ),
            ),
        ),
    ]
