"""Soft-delete column for BotUser — Phase 3 / F4 data-deletion."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0002_clientprofile"),
    ]

    operations = [
        migrations.AddField(
            model_name="botuser",
            name="deleted_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text=(
                    "When the customer requested data deletion. Non-null "
                    "means PII has been scrubbed and the user should be "
                    "invisible to default queries."
                ),
                null=True,
            ),
        ),
    ]
