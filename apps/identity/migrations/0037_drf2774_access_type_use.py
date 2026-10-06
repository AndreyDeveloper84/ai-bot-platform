# DRF-2774 — значение «use» журнала красной зоны (продление срока использованием).

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0036_drf2544_source_tenant_origin_help_text"),
    ]

    operations = [
        migrations.AlterField(
            model_name="redzoneaccesslog",
            name="access_type",
            field=models.CharField(
                choices=[
                    ("read", "Read"),
                    ("write", "Write"),
                    ("purge", "Purge"),
                    ("delete", "Delete — subject-requested soft-delete (tombstone)"),
                    ("withdrawal", "Withdrawal — explicit consent revocation"),
                    (
                        "write_rejected_dob_lookup",
                        "Write rejected — DOB lookup failed (Ayla REST outage)",
                    ),
                    (
                        "write_rejected_no_consent",
                        "Write rejected — yellow/red without consent (DB CHECK)",
                    ),
                    ("use", "Use — cited in an answer / recommendation, term extended"),
                ],
                help_text="What kind of access. Round-2 AS2 + ADR-0011 §11.3 added 'withdrawal' + 'write_rejected_dob_lookup' values; DRF-2133 added 'delete' (subject-requested soft-delete from the Mini App).",
                max_length=32,
            ),
        ),
    ]
