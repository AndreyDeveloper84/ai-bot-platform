"""UserPreferences model — Phase 3 / F4 customer preferences."""

import django.db.models.deletion
from django.db import migrations, models

import apps.tenancy.managers


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0003_botuser_deleted_at"),
        ("tenancy", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserPreferences",
            fields=[
                (
                    "bot_user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="preferences",
                        serialize=False,
                        to="identity.botuser",
                    ),
                ),
                (
                    "notify_reminders",
                    models.BooleanField(
                        default=True,
                        help_text=(
                            "T-24h / T-2h / T-15m reminders for confirmed bookings. "
                            "Even when False, transactional confirmations still fire — only "
                            "soft reminders mute."
                        ),
                    ),
                ),
                (
                    "notify_retention",
                    models.BooleanField(
                        default=True,
                        help_text=("Proactive «время обновить» nudge ~6 weeks after last visit."),
                    ),
                ),
                (
                    "notify_promo",
                    models.BooleanField(
                        default=False,
                        help_text="Promo / marketing campaigns. Default OFF — opt-in.",
                    ),
                ),
                (
                    "notify_birthday",
                    models.BooleanField(
                        default=True,
                        help_text=(
                            "Birthday greeting + gift offer. Requires birthday_date "
                            "to actually trigger."
                        ),
                    ),
                ),
                (
                    "birthday_date",
                    models.DateField(
                        blank=True,
                        null=True,
                        help_text=(
                            "Optional birthday for the birthday greeting flow. Year "
                            "is preserved for age-conditional offers but never displayed back."
                        ),
                    ),
                ),
                (
                    "allergies",
                    models.TextField(
                        blank=True,
                        default="",
                        help_text=(
                            "Free-text contraindications / allergies surfaced to the "
                            "master before each booking. Read by the booking confirm view."
                        ),
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="user_preferences",
                        to="tenancy.tenant",
                        help_text=(
                            "Owning tenant. CASCADE — preferences die with the "
                            "tenant, no value in keeping them otherwise."
                        ),
                    ),
                ),
            ],
            options={
                "verbose_name": "User preferences",
                "verbose_name_plural": "User preferences",
                "indexes": [
                    models.Index(
                        fields=["tenant", "notify_promo"],
                        name="identity_us_tenant__de5c0f_idx",
                    ),
                    models.Index(
                        fields=["tenant", "notify_birthday", "birthday_date"],
                        name="identity_us_tenant__c3e9d8_idx",
                    ),
                ],
                "managers": [
                    ("objects", apps.tenancy.managers.TenantScopedManager()),
                ],
            },
        ),
    ]
