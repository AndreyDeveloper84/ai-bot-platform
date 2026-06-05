"""S2 privacy consent (152-ФЗ) — ``BotUser.consent_at`` (task #85 part 2).

Customer onboarding flow S2 step gates downstream actions on explicit
152-ФЗ consent. ``consent_at`` records the timestamp when customer
tapped «Да, продолжим» (или «Понятно, продолжим» из S2a fold).
NULL value means consent never granted — customer либо refused («Не
сейчас») либо ещё не дошёл до S2 после welcome.

### Tau's spec §12 (customer-onboarding-flow.md)

* ``NULL`` → customer never went through onboarding consent step,
  либо refused. Distinguish from welcomed_at: bot welcomed (showed S1)
  но user либо tapped refuse либо ушёл из chat.
* ``datetime`` → consent given. Downstream actions могут proceed.

### State 4 returning user query semantics

Returning user detection (per Tau's State 4 в §11):
``BotUser.last_interaction_at < now - 30d AND consent_at IS NOT NULL``.
``consent_at`` distinguishes «вернулась со старой согласия» (soft
re-welcome) от «вернулась после refused / прерванного flow» (full
onboarding restart). Query lives в otherwise-unrelated PR per Tau §12
Alpha scope — этот PR ships только field.

### Why nullable + no default

* ``null=True`` — backfill-free. Existing BotUsers получают NULL,
  что означает «не давала согласия в текущем onboarding» — корректно
  для legacy users которые пользовались бот-ом до этого PR. Если
  legacy user пишет после deploy, welcome S1 re-fires (per
  welcomed_at semantics) и она проходит S2 нормально.
* No default — explicit «not yet» state имеет смысл; ``auto_now_add``
  бы скрыл refused / never-shown статус.
* No db_index — query usage пока низкое (welcome skill check на ANY
  inbound message), хотя если monitoring покажет slow query —
  легко add в follow-up. Keep migration minimal по pre-flip rubric.

### Coordination

Изначально task #86 W4 ownership; tech-lead 2026-05-26 reassigned to W2
(absorb scope) — single PR = single review cycle, faster ship. Same
pattern что welcomed_at в #754 — precedent set.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0010_botuser_welcomed_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="botuser",
            name="consent_at",
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text=(
                    "152-ФЗ consent timestamp. NULL = not yet "
                    "consented, не ходит дальше welcome S1. Set when "
                    "user actions S2 consent CTA («Да, продолжим» или "
                    "«Понятно, продолжим» из S2a fold). Used to gate "
                    "downstream actions + distinguish returning-user "
                    "states (per Tau's customer-onboarding-flow.md §11)."
                ),
            ),
        ),
    ]
