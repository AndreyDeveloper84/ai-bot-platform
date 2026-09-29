"""Другие подходы, показанные с карточкой C04 (DRF-1770, К-3 N4)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("recommendation", "0002_booking_provenance")]

    operations = [
        migrations.AddField(
            model_name="recommendation",
            name="alternatives",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
