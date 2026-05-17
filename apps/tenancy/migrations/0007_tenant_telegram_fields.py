# Phase 1 / CH1 (DRF-848) — per-tenant Telegram bot credentials.
#
# Adds ``telegram_bot_token`` and ``telegram_webhook_secret`` to the
# Tenant table. Both default to empty strings so the migration is
# backward-compatible: existing tenants stay valid and the Telegram
# channel adapter treats an empty token pair as "Telegram not
# configured for this tenant" (the webhook view returns 404).
#
# SECURITY note: the operator-facing flow lives in
# ``docs/runbooks/telegram-bot-onboarding.md``. Tokens are credentials —
# the admin masks them in list_display and ``Tenant.__repr__`` masks
# them in any debug output.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenancy", "0006_tenant_is_system"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="telegram_bot_token",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "BotFather access token for this tenant's Telegram bot. "
                    "NEVER log this value — masked in __repr__ and admin "
                    "list views. See docs/runbooks/telegram-bot-onboarding.md."
                ),
                max_length=128,
            ),
        ),
        migrations.AddField(
            model_name="tenant",
            name="telegram_webhook_secret",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Per-tenant secret the operator generates (e.g. "
                    "secrets.token_urlsafe(32)) and registers with "
                    "Telegram's setWebhook ?secret_token=…. Telegram "
                    "sends it back in X-Telegram-Bot-Api-Secret-Token on "
                    "every webhook POST; the webhook view verifies with "
                    "hmac.compare_digest."
                ),
                max_length=64,
            ),
        ),
    ]
