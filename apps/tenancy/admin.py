"""Admin registration for Tenant (DRF-419 / Sprint 1 / A2).

Ported from Ayla ``origin/dev:tenants/admin.py``. Stripped of the
Unfold theming dependency — the platform admin is plain Django for
Sprint 1; we can layer Unfold in later if/when an admin polish sprint
arrives.

Key behaviour:
  * ``get_queryset`` uses ``Tenant.all_objects`` so deactivated tenants
    remain visible in admin (default ``Tenant.objects`` manager hides
    ``is_active=False`` rows from app code).
  * ``id``, ``created_at``, ``updated_at`` are read-only — the UUID is
    auto-generated and the timestamps are managed by ``auto_now*``.
  * Rows with ``is_system=True`` are protected from deletion (KB-RAG Sub-1,
    GH #114) — the ``global_kb`` corpus tenant must not vanish on a stray
    admin click. ``has_delete_permission`` hides the per-row delete button,
    ``delete_model`` and ``delete_queryset`` enforce the same rule
    server-side in case a custom action bypasses the UI.
  * Секреты Telegram (``telegram_bot_token``, ``telegram_webhook_secret``)
    в форму не отдаются — см. :class:`TenantAdminForm` (DRF-1495).
"""

from __future__ import annotations

from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied

from apps.tenancy.models import Tenant


class TenantAdminForm(forms.ModelForm):
    """Форма тенанта, которая не показывает секреты (DRF-1495).

    До этой правки маскировалась только колонка списка
    (``telegram_bot_token_masked``), а форма изменения отдавала и токен
    бота, и вебхук-секрет обычными текстовыми полями: полные значения
    уезжали в HTML страницы каждому, кто её открыл, и оставались в
    кеше браузера, в скриншоте, в «сохранить страницу». Границы эпика
    DRF-75 говорят: секреты и токены в интерфейсе не показывать никак.

    Механика простая и без своего состояния: поле рендерится
    ``PasswordInput(render_value=False)`` — значение в разметку не
    попадает вовсе, — и пустая отправка означает «не менять». Задать
    новое значение по-прежнему можно, стереть — нет; стирание секрета
    это редкая осознанная операция, и для неё есть shell.

    Что именно сейчас настроено, видно по read-only полям
    ``telegram_bot_token_state`` / ``telegram_webhook_secret_state``:
    они отвечают «задан / не задан» (для токена — плюс последние 4
    символа, чтобы отличить два бота друг от друга, не раскрывая
    ключа).
    """

    telegram_bot_token = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label="Новый токен бота",
        help_text=(
            "Пусто — оставить текущий. Значение не показывается: это "
            "credential BotFather. Текущее состояние — в поле выше."
        ),
    )
    telegram_webhook_secret = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label="Новый вебхук-секрет",
        help_text=(
            "Пусто — оставить текущий. Значение не показывается. "
            "Генерируется оператором через secrets.token_urlsafe(32)."
        ),
    )

    class Meta:
        model = Tenant
        fields = "__all__"

    def clean_telegram_bot_token(self) -> str:
        submitted = (self.cleaned_data.get("telegram_bot_token") or "").strip()
        return submitted or (self.instance.telegram_bot_token or "")

    def clean_telegram_webhook_secret(self) -> str:
        submitted = (self.cleaned_data.get("telegram_webhook_secret") or "").strip()
        return submitted or (self.instance.telegram_webhook_secret or "")


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    form = TenantAdminForm
    list_display = (
        "name",
        "slug",
        "is_active",
        "is_system",
        "shadow_mode",
        "telegram_bot_token_masked",
        "daily_token_cap",
        "daily_cost_cap_usd",
        "created_at",
    )
    list_filter = ("is_active", "is_system", "shadow_mode")
    list_editable = ("shadow_mode",)
    search_fields = ("name", "slug")
    readonly_fields = (
        "id",
        "is_system",
        "created_at",
        "updated_at",
        "telegram_bot_token_state",
        "telegram_webhook_secret_state",
    )
    fieldsets = (
        (None, {"fields": ("id", "slug", "name", "is_active", "is_system")}),
        (
            "Sprint 8 shadow-mode",
            {
                "fields": ("shadow_mode",),
                "description": (
                    "When checked, the orchestrator writes shadow rows but "
                    "does NOT send outbound messages to the user. See "
                    "docs/runbooks/shadow-mode-launch.md before flipping in prod."
                ),
            },
        ),
        (
            "Phase 1 — Telegram channel",
            {
                "fields": (
                    "telegram_bot_token_state",
                    "telegram_bot_token",
                    "telegram_webhook_secret_state",
                    "telegram_webhook_secret",
                ),
                "description": (
                    "Per-tenant Telegram bot credentials. Token is from "
                    "@BotFather; webhook_secret is operator-generated via "
                    "secrets.token_urlsafe(32) and registered with "
                    "Telegram's setWebhook. "
                    "DRF-1495: ни одно из двух значений в эту форму не "
                    "отдаётся. Поля ввода пустые всегда; пустая отправка "
                    "означает «оставить как есть». Что настроено сейчас — "
                    "в строках состояния над каждым полем. "
                    "См. docs/runbooks/telegram-bot-onboarding.md."
                ),
            },
        ),
        (
            "Cost Controls",
            {
                "fields": ("daily_token_cap", "daily_cost_cap_usd"),
                "description": (
                    "Per-tenant daily LLM budget (Phase 1 / PI9 / DRF-860). "
                    "Either cap can trip independently and the bot serves a "
                    "static 'лимит исчерпан' fallback once exhausted; reset "
                    "at 00:00 UTC. The 80% threshold also pings the salon "
                    "manager's MAX chat (manager_chat_id) once per day."
                ),
            },
        ),
        (
            "Системное",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Telegram token (last 4)")
    def telegram_bot_token_masked(self, obj: Tenant) -> str:
        """Admin column: never expose the full Telegram bot token.

        Delegates to :meth:`Tenant._mask_telegram_token` so the masking
        rule lives in one place (model + admin can't drift).
        """
        return obj._mask_telegram_token() or "—"

    @admin.display(description="Токен бота сейчас")
    def telegram_bot_token_state(self, obj: Tenant) -> str:
        """Состояние токена без самого токена (DRF-1495).

        Последние 4 символа — тот же приём, что в списке и в
        ``__repr__``: их хватает, чтобы отличить два бота друг от друга,
        и не хватает, чтобы воспользоваться ключом.
        """
        masked = obj._mask_telegram_token()
        return f"задан ({masked})" if masked else "не задан"

    @admin.display(description="Вебхук-секрет сейчас")
    def telegram_webhook_secret_state(self, obj: Tenant) -> str:
        """«Задан / не задан». Хвост не показываем: секрет сравнивается
        целиком через ``hmac.compare_digest``, и любая его часть — подсказка.
        """
        return "задан" if (obj.telegram_webhook_secret or "") else "не задан"

    def get_queryset(self, request):
        # Admin must see deactivated tenants too — use all_objects manager.
        return Tenant.all_objects.all()

    def has_delete_permission(self, request, obj=None):
        # When obj is None Django asks "may this user delete *anything* here?"
        # to decide whether to render the changelist's "Delete selected" action.
        # We return True so the dropdown still appears for regular tenants;
        # the per-row enforcement happens in delete_model / delete_queryset.
        if obj is not None and getattr(obj, "is_system", False):
            return False
        return super().has_delete_permission(request, obj)

    def delete_model(self, request, obj):
        if getattr(obj, "is_system", False):
            raise PermissionDenied(
                "System tenants cannot be deleted from admin. "
                "Clear `is_system` first or remove the row via shell with explicit intent."
            )
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        # Refuse the whole batch if any row is a system tenant — partial
        # deletes are worse than full refusal here (operator gets a clear
        # error instead of mixed success).
        if queryset.filter(is_system=True).exists():
            raise PermissionDenied(
                "Selection includes system tenants. Remove them from the "
                "selection or clear `is_system` first."
            )
        super().delete_queryset(request, queryset)
