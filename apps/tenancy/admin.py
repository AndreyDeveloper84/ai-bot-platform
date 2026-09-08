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
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.html import format_html, format_html_join

from apps.tenancy.models import Tenant
from apps.tenancy.onboarding import (
    REASON_LABELS,
    ConnectError,
    assess_salon,
    connect_salon,
    verify_salon_masters,
)


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


class SalonConnectForm(forms.Form):
    """Поля экрана «подключить салон» (DRF-1525).

    Идентификатор — обязательное поле: его нельзя ввести «на глаз»,
    :func:`connect_salon` проверяет по Ayla, что по нему что-то есть,
    до сохранения строки.
    """

    slug = forms.SlugField(
        max_length=50,
        label="Slug",
        help_text="Строчные буквы, цифры, дефис/подчёркивание, 2–50 знаков.",
    )
    name = forms.CharField(max_length=200, label="Название салона")
    tenant_id = forms.CharField(
        label="Ayla Tenant UUID",
        help_text=(
            "UUID салона из бэкенда Ayla. Синхронизация ходит с "
            "?tenant=<UUID>: без настоящего идентификатора салон "
            "зазеркалит ноль строк при «успешном» прогоне. Перед "
            "сохранением экран проверит, что по нему что-то есть."
        ),
    )
    city = forms.CharField(
        max_length=120,
        label="Город",
        help_text="Например, «Пенза». Без города салон отсутствует во всех городских ответах.",
    )


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    form = TenantAdminForm
    change_list_template = "admin/tenancy/tenant/change_list.html"
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
        "salon_visibility_state",
        "last_catalog_sync_at",
        "last_catalog_sync_ok_at",
    )
    fieldsets = (
        (
            None,
            {
                "fields": ("id", "slug", "name", "city", "address", "is_active", "is_system"),
                "description": (
                    "Город участвует в городском поиске: без него салон "
                    "отсутствует во всех городских ответах (DRF-1510). "
                    "Адрес (DRF-1588) синхронизация заполнит сама, когда "
                    "источник начнёт его отдавать (DRF-1587); пустое поле "
                    "означает «источник ничего не сказал», а не «адреса нет»."
                ),
            },
        ),
        (
            "Видимость для клиентов (DRF-1525)",
            {
                "fields": (
                    "salon_visibility_state",
                    "last_catalog_sync_ok_at",
                    "last_catalog_sync_at",
                ),
                "description": (
                    "Проверка исхода: салон заведён тогда, когда виден "
                    "клиенту, а не когда появилась строка. Причины "
                    "невидимости — закрытый перечень DRF-1511."
                ),
            },
        ),
        (
            "Адрес менеджера в MAX (DRF-1559)",
            {
                "fields": ("manager_user_id", "manager_chat_id"),
                "description": (
                    "Сюда уходят эскалации напоминаний, оповещения о записях, "
                    "просьбы изменить расписание и предупреждения о лимитах. "
                    "<b>Заполняйте manager_user_id.</b> chat_id — это "
                    "идентификатор ДИАЛОГА: он верен только для того бота, из "
                    "переписки с которым его скопировали, и салонный бот, "
                    "отправляя туда, получает 404 dialog.not.found (замер "
                    "07.09.2026, OPEN_DECISIONS §55). user_id — это человек, и "
                    "он верен для любого нашего бота. "
                    "Пересчитать одно в другое нельзя: MAX отдаёт "
                    "recipient.user_id в ответе на успешную отправку — оттуда "
                    "и берут. Пока user_id пуст, читается chat_id, то есть "
                    "поведение остаётся вчерашним."
                ),
            },
        ),
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
                    "manager's MAX address once per day — см. раздел «Адрес "
                    "менеджера в MAX»."
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

    @admin.display(description="Видимость и состояние каталога")
    def salon_visibility_state(self, obj: Tenant) -> str:
        """Карточка одного салона: синхронизация, витрина, причины (DRF-1525).

        Не сводка по контуру (это DRF-1500), а ответ про этот салон:
        когда последний раз синхронизировался, сколько услуг и
        бронируемых мастеров, и — если клиент его не видит — почему,
        названной причиной из закрытого перечня DRF-1511.
        """
        assessed = assess_salon(obj)
        last_ok = (
            assessed.last_sync_ok_at.strftime("%d.%m.%Y %H:%M UTC")
            if assessed.last_sync_ok_at
            else "никогда"
        )
        head = format_html(
            "Последняя успешная синхронизация: {}. Активных услуг: {}. Бронируемых мастеров: {}.",
            last_ok,
            assessed.active_services,
            assessed.bookable_masters,
        )
        if assessed.is_visible:
            return format_html("{}<br><strong>Салон виден клиентам.</strong>", head)
        rows = format_html_join(
            "",
            "<li>{}</li>",
            ((REASON_LABELS[code],) for code in assessed.reasons),
        )
        return format_html(
            "{}<br><strong>Клиентам не виден. Причины:</strong><ul>{}</ul>",
            head,
            rows,
        )

    # ------------------------------------------------------------------
    # Экран подключения салона (DRF-1525)
    # ------------------------------------------------------------------

    def get_urls(self):
        custom = [
            path(
                "connect/",
                self.admin_site.admin_view(self.connect_view),
                name="tenancy_tenant_connect",
            ),
        ]
        return custom + super().get_urls()

    #: Имя поля кнопки верификации: в нём же приезжает id салона.
    #:
    #: DRF-1553. Отдельного URL у действия нет намеренно: оно живёт на
    #: экране подключения и нигде больше, а отдельный адрес пришлось бы
    #: защищать вторым разрешением — то самое «смешение двух прав»,
    #: которое владелец снял, оставив ОДИН уровень доступа (§51.1).
    VERIFY_FIELD = "verify_masters_of"

    def connect_view(self, request: HttpRequest) -> HttpResponse:
        """Форма «подключить салон» и действие верификации на ней же.

        Настройки тенанта меняет только суперпользователь (OPEN_DECISIONS
        §27 п.2, роли DRF-1495) — подключение тенанта тем более. Экран
        не дублирует ``create_tenant``: он вызывает :func:`connect_salon`,
        который проверяет идентификатор по Ayla ДО сохранения, запускает
        синхронизацию и показывает исход глазами клиента.

        DRF-1553 добавил сюда второе действие — верификацию мастеров.
        После DRF-1496 мастер, приехавший синхронизацией, рождается
        ``pending``, поэтому сразу после подключения бронируемых ноль и
        критерий успеха этого экрана («салон виден клиенту») без ручного
        шага не достигался НИКОГДА. Владелец выбрал дать шаг кнопкой, а
        не назвать его словами (§51.1), и уровень доступа оставил один —
        тот же суперпользователь; проверка прав здесь одна на оба
        действия, и кнопка ничьих прав не расширяет.

        Верификация идёт тем же сервисом, что действие ``verify_masters``
        в админке каталога (:func:`verify_salon_masters` →
        ``apps.catalog.services.verification``), поэтому и состояние, и
        строка журнала у двух экранов одинаковые.
        """
        if not request.user.is_superuser:
            raise PermissionDenied(
                "Подключение салона — действие владельца контура "
                "(суперпользователя), а не ролей админки."
            )

        verify_of = request.POST.get(self.VERIFY_FIELD) if request.method == "POST" else None

        form = SalonConnectForm(request.POST if verify_of is None else None)
        result = None
        error = None
        salon = None
        verified = None

        if verify_of is not None:
            try:
                salon = Tenant.all_objects.filter(pk=verify_of).first()
            except (ValidationError, ValueError):
                # Не UUID вовсе. ``UUIDField`` отвечает на это исключением,
                # а не пустой выборкой, и без перехвата экран отдал бы 500
                # на подделанной форме.
                salon = None
            if salon is None:
                # Не «верифицировано: 0», а отказ: салон, которого нет,
                # обязан выглядеть ошибкой, а не пустым успехом — тот же
                # класс тихого «успеха», ради которого заведён DRF-1525.
                error = (
                    f"Салон {verify_of!r} не найден — верификация не выполнена. "
                    "Подключите салон заново или верифицируйте мастеров "
                    "действием в админке каталога."
                )
            else:
                verified = verify_salon_masters(salon, user=request.user)
        elif request.method == "POST" and form.is_valid():
            try:
                result = connect_salon(
                    slug=form.cleaned_data["slug"],
                    name=form.cleaned_data["name"],
                    tenant_id=form.cleaned_data["tenant_id"],
                    city=form.cleaned_data["city"],
                )
                salon = result.tenant
            except ConnectError as exc:
                error = str(exc)

        # Оценка после действия, а не до: кнопка обещает «салон появится
        # в поиске», и экран обязан показать, случилось ли это на самом
        # деле, а не повторить обещание.
        if result is not None:
            # ``connect_salon`` уже посчитал исход — второй проход по тем
            # же таблицам дал бы то же самое за лишние запросы.
            assessment = result.assessment
        elif salon is not None:
            assessment = assess_salon(salon)
        else:
            assessment = None

        context = {
            **self.admin_site.each_context(request),
            "title": "Подключить салон",
            "form": form,
            "result": result,
            "salon": salon,
            "assessment": assessment,
            "assessment_reasons": (
                [REASON_LABELS[code] for code in assessment.reasons]
                if assessment is not None
                else []
            ),
            "verified": verified,
            "verify_field": self.VERIFY_FIELD,
            "error": error,
            "opts": self.model._meta,  # noqa: SLF001 — admin chrome API
        }
        return TemplateResponse(request, "admin/tenancy/tenant/connect.html", context)

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
