# DRF-2984 PR-B2 — ожидающая смена тарифа в зеркале подписки. Только новые
# столбцы с безопасными умолчаниями.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenancy", "0022_drf2984_subscription_mirror"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="pending_plan",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Mirror: confirmed tariff change waiting for pending_plan_from; "
                "empty — none.",
                max_length=32,
                verbose_name="Тариф со следующего периода",
            ),
        ),
        migrations.AddField(
            model_name="tenant",
            name="pending_plan_from",
            field=models.DateField(
                blank=True,
                help_text="Mirror: the day pending_plan takes effect.",
                null=True,
                verbose_name="Новый тариф с",
            ),
        ),
    ]
