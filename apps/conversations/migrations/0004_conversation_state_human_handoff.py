"""DRF-466 / Sprint 3 / C3 — add HUMAN_HANDOFF to Conversation.State.

ADR-0007 "alter_choices recipe" only:
  - schema delta = a Django CharField choices tuple change
  - no data migration (existing rows untouched; new value is opt-in
    write from C2 services and D3 HandoffSkill)
  - reversible by re-running makemigrations on the old choices

When the bot triggers handoff, ``apps/handoff/services.py::create_admin_task``
flips the active Conversation to ``HUMAN_HANDOFF``. The D4 dispatcher
(Sprint 3) short-circuits skill execution when state == HUMAN_HANDOFF so
the bot doesn't replay over the operator. ``resolve_admin_task`` flips
back to IDLE to resume autonomy.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0003_alter_conversation_bot_user"),
    ]

    operations = [
        migrations.AlterField(
            model_name="conversation",
            name="state",
            field=models.CharField(
                choices=[
                    ("idle", "IDLE"),
                    ("consulting", "CONSULTING"),
                    ("escalated", "ESCALATED"),
                    ("human_handoff", "HUMAN_HANDOFF"),
                ],
                default="idle",
                help_text="Lifecycle state. Minimal per ADR-0007; new states land with their writer code in Sprint 3+.",
                max_length=16,
            ),
        ),
    ]
