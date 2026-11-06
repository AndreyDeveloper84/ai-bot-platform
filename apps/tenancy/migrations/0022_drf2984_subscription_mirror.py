# DRF-2984 — зеркало подписки каталога на тенанте (PR-B1). Только новые
# столбцы с безопасными умолчаниями: существующие строки получают "none"/NULL.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenancy", "0021_drf2595_timezone_help_text_strict_empty"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tenant",
            name="plan",
            field=models.CharField(
                default="free",
                help_text="Mirror of the catalog tariff (solo / salon). Written ONLY by "
                "apps.tenancy.billing_mirror from catalog billing events (DRF-2984); "
                "'free' until the first event. The catalog owns the tariff and the price.",
                max_length=32,
                verbose_name="Тариф",
            ),
        ),
        migrations.AddField(
            model_name="tenant",
            name="subscription_status",
            field=models.CharField(
                choices=[
                    ("none", "Нет подписки"),
                    ("trial", "Пробный период"),
                    ("active", "Активна"),
                    ("past_due", "Долг"),
                    ("frozen", "Заморожена"),
                    ("canceled", "Отменена"),
                ],
                default="none",
                help_text="Mirror of the catalog subscription status. 'none' — no "
                "billing event seen yet.",
                max_length=16,
                verbose_name="Статус подписки",
            ),
        ),
        migrations.AddField(
            model_name="tenant",
            name="subscription_status_at",
            field=models.DateTimeField(
                blank=True,
                help_text="occurred_at of the event the mirror was written from; an "
                "older event never overwrites a newer one.",
                null=True,
                verbose_name="Статус подписки на момент",
            ),
        ),
        migrations.AddField(
            model_name="tenant",
            name="trial_ends_at",
            field=models.DateField(
                blank=True,
                help_text="Mirror: last day of the free trial.",
                null=True,
                verbose_name="Пробный период до",
            ),
        ),
        migrations.AddField(
            model_name="tenant",
            name="current_period_end",
            field=models.DateField(
                blank=True,
                help_text="Mirror: last day of the paid period.",
                null=True,
                verbose_name="Оплачено до",
            ),
        ),
    ]
