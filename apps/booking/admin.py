"""Админка записей: просмотр свободно, изменение — только действиями (DRF-1498).

Read-only on purpose: these tables are written exclusively by the
webhook handler (B2) and the booking-skill tools (B3). Hand-editing
in /admin/ would silently bypass the idempotency contract and the
event-emission side effects.

Правило владельца (DRF-1498): не закрывать визиты и не менять статусы
через Django-админку — это обход машины состояний. Поэтому экран даёт
**действия, а не поля**:

* все поля — ``readonly_fields``, включая ``status`` (запрет механический,
  закреплён тестом ``test_admin_cancel.py``);
* отмена — отдельный экран с обязательной причиной, который зовёт тот
  же сервис машины состояний, что и Mini App API
  (:mod:`apps.booking.services.admin_cancel`).

Массовых операций нет сознательно: массовая отмена — способ испортить
много данных одним нажатием. ``BookingReminderAdmin`` остаётся зеркалом
строго на чтение, как и был.

Телефон клиента на экране не показывается и в поиске не участвует
(DRF-1039): записи ищутся по имени клиента, услуге и комментарию.
"""

from __future__ import annotations

import operator
from typing import TYPE_CHECKING, Any, ClassVar, cast

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from apps.adminconsole.theme import AylaAdminMedia, BadgeMap, badge
from apps.booking.models import BookingReminder, BookingRequest
from apps.booking.services.admin_cancel import AdminCancelError, cancel_booking_from_admin
from apps.booking.services.transitions import InvalidBookingTransition

if TYPE_CHECKING:  # pragma: no cover - только для аннотаций
    from django.contrib.auth.models import AbstractUser
    from django.http import HttpRequest, HttpResponse


class AllTenantsRelatedListFilter(admin.RelatedFieldListFilter):
    """Варианты бокового фильтра по FK берутся из ``all_tenants`` (DRF-1608).

    ПОЧЕМУ ЯВНЫЙ ОБХОД ТЕНАНТНОГО МЕНЕДЖЕРА, А НЕ НЕДОСМОТР.

    Django строит список вариантов бокового фильтра через
    ``field.get_choices()``, а тот ходит в ``rel_model._default_manager``
    — то есть в ``CatalogMaster.objects``, у которого стоит
    :class:`~apps.tenancy.managers.TenantScopedManager`. У запроса в
    ``/admin/`` тенантного контекста нет и быть не должно: админка
    сознательно кросс-тенантная (``get_queryset`` ниже уже берёт
    ``all_tenants``, экраны несут предупреждение про кросс-тенант,
    DRF-1023). Поэтому ``objects`` вне контекста ведёт себя ровно так,
    как задумано **для прикладного кода**, и ровно неверно для админки:

      * ``STRICT_TENANT_SCOPE=strict``  → ``CrossTenantError`` из
        ``filters.py`` ещё до отрисовки: страница отдаёт **500**,
        оператор списка записей не видит вовсе;
      * ``STRICT_TENANT_SCOPE=audit``   → ``.none()``: страница **200**,
        а фильтр «Мастер» молча пуст при непустой таблице мастеров —
        оператор читает это как «мастеров нет».

    Замер 09.09.2026 на ``origin/dev`` @ 92ebc6c, оба режима, зонд
    ``/admin/booking/bookingrequest/`` с двумя мастерами в базе:
    strict → 500 / 0 вариантов, audit → 200 / 0 вариантов.

    Обход именно здесь, а не в менеджере: чинить ``TenantScopedManager``
    под админку значило бы ослаблять сторож ради одного потребителя.
    Сторож прав — неправ был потребитель, который звал ``objects``.

    Ограничение по назначению: фильтр применять только на экранах, где
    кросс-тенантный обзор — заявленное свойство экрана. Для модели без
    ``all_tenants`` поведение падает обратно на штатное Django —
    молчаливой смены семантики не происходит.
    """

    def field_choices(self, field: Any, request: Any, model_admin: Any) -> list:
        rel_model = field.remote_field.model
        manager = getattr(rel_model, "all_tenants", None)
        if manager is None:
            # Модель не тенантная (или escape-hatch не объявлен) —
            # ведём себя как штатный Django-фильтр.
            return super().field_choices(field, request, model_admin)

        remote = field.remote_field
        choice_func = operator.attrgetter(
            remote.get_related_field().attname if hasattr(remote, "get_related_field") else "pk"
        )
        queryset = manager.complex_filter(field.get_limit_choices_to())
        ordering = self.field_admin_ordering(field, request, model_admin)
        if ordering:
            queryset = queryset.order_by(*ordering)
        return [(choice_func(obj), str(obj)) for obj in queryset]


#: Состояния, из которых машина состояний разрешает отмену.
_CANCELLABLE_STATUSES = frozenset(
    {
        BookingRequest.Status.CONFIRMED,
        BookingRequest.Status.RESCHEDULE_REQUESTED,
    }
)


@admin.register(BookingRequest)
class BookingRequestAdmin(AylaAdminMedia, admin.ModelAdmin):
    list_display = (
        "client_name",
        "service_name",
        "master_name",
        "visit_at",
        "visit_state",
        "origin",
        "tenant",
        "cancel_link",
    )
    list_filter = (
        "tenant",
        # DRF-1608: варианты «Мастер» — из ``all_tenants``; см. докстринг
        # :class:`AllTenantsRelatedListFilter`. Голый ``"master"`` роняет
        # эту страницу в 500 при ``STRICT_TENANT_SCOPE=strict``.
        ("master", AllTenantsRelatedListFilter),
        "status",
        "source",
        ("visit_at", admin.DateFieldListFilter),
    )
    search_fields = ("client_name", "service_name", "comment")
    search_help_text = (
        "Ищет по имени клиента, названию услуги и комментарию. "
        "По телефону не ищет и не будет: телефон клиента — не поисковый ключ."
    )
    empty_value_display = "нет данных"
    readonly_fields = tuple(f.name for f in BookingRequest._meta.fields)
    date_hierarchy = "visit_at"
    ordering = ("-created_at",)
    fieldsets = (
        (
            "Визит",
            {
                "fields": (
                    "visit_at",
                    "duration_min",
                    "service_name",
                    "master_name",
                    "category_name",
                    "status",
                ),
                "description": (
                    "Состояние визита показано, но не правится — и не будет: "
                    "закрытие визитов и смена статусов идут только через "
                    "машину состояний. Единственное разрешённое отсюда "
                    "действие — «Отменить…» в списке, и оно зовёт тот же "
                    "сервис, что и приложение."
                ),
            },
        ),
        (
            "Клиент",
            {"fields": ("client_name", "bot_user", "comment")},
        ),
        (
            "Персональные данные клиента",
            {
                "classes": ("collapse",),
                "fields": ("client_phone",),
                "description": (
                    "Раздел свёрнут нарочно: телефон нужен редко, а "
                    "открытый на экране он попадает в скриншот и в кеш "
                    "браузера. Поиск по телефону здесь не работает."
                ),
            },
        ),
        (
            "Откуда пришла запись",
            {"fields": ("source", "booking_source", "is_processed")},
        ),
        (
            "Отмена и перенос",
            {
                "fields": ("cancel_requested_at", "reschedule_candidate", "rescheduled_from"),
                "description": (
                    "Пустые поля означают, что отмены и переноса не было, а не что их «ноль»."
                ),
            },
        ),
        (
            "Завершение визита",
            {
                "fields": ("completed_at", "completed_by"),
                "description": (
                    "Проставляет периодическая задача, когда визит "
                    "закончился. Руками отсюда визит не закрывают."
                ),
            },
        ),
        (
            "Отзыв клиента",
            {
                "fields": (
                    "rating",
                    "feedback_comment",
                    "feedback_at",
                    "feedback_prompt_sent_at",
                ),
            },
        ),
        (
            "Служебное: тарификация и атрибуция",
            {
                "classes": ("collapse",),
                "fields": (
                    "billable",
                    "billing_reason",
                    "ai_assist_score",
                    "commercial_identity_snapshot",
                    "attribution_metadata",
                ),
            },
        ),
        (
            "Служебное: связи и отметки",
            {
                "classes": ("collapse",),
                "fields": (
                    "id",
                    "tenant",
                    "service",
                    "master",
                    "conversation",
                    "original_booking_event",
                    "created_at",
                ),
            },
        ),
    )

    #: Состояние визита словами — и код рядом.
    #:
    #: Оба имени обязательны: подпись читает человек, кодом
    #: (``confirmed``, ``cancel_requested``) визит называется в задачах,
    #: логах и в разговоре с нами.
    _STATE_BADGES: ClassVar[BadgeMap] = {
        BookingRequest.Status.CONFIRMED: ("ok", "Подтверждена"),
        BookingRequest.Status.CANCEL_REQUESTED: ("wait", "Клиент попросил отменить"),
        BookingRequest.Status.RESCHEDULE_REQUESTED: ("wait", "Клиент попросил перенести"),
        BookingRequest.Status.CANCELLED: ("stop", "Отменена"),
        BookingRequest.Status.RESCHEDULED: ("off", "Перенесена, заменена новой"),
    }

    _ORIGIN_BADGES: ClassVar[BadgeMap] = {
        "wizard": ("off", "Форма на сайте"),
        "bot": ("ok", "Диалог с ботом"),
        "yclients_admin": ("off", "Салон завёл в YClients"),
        "import": ("off", "Массовый импорт"),
    }

    @admin.display(description="Состояние визита", ordering="status")
    def visit_state(self, obj: BookingRequest):  # type: ignore[no-untyped-def]
        tone, label = self._STATE_BADGES.get(obj.status, ("off", "Неизвестное состояние"))
        return badge(tone, label, obj.status)

    @admin.display(description="Откуда запись", ordering="source")
    def origin(self, obj: BookingRequest):  # type: ignore[no-untyped-def]
        tone, label = self._ORIGIN_BADGES.get(obj.source, ("off", "Неизвестный источник"))
        return badge(tone, label, obj.source)

    def has_add_permission(self, request) -> bool:  # type: ignore[no-untyped-def]
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # type: ignore[no-untyped-def]
        return False

    def get_queryset(self, request):  # type: ignore[no-untyped-def]
        # Admin must see across tenants — use the escape-hatch manager.
        return BookingRequest.all_tenants.all()

    def get_urls(self) -> list:
        urls = super().get_urls()
        custom = [
            path(
                "<uuid:booking_id>/cancel/",
                self.admin_site.admin_view(self.cancel_view),
                name="booking_bookingrequest_cancel",
            ),
        ]
        return custom + urls

    @admin.display(description="Действие")
    def cancel_link(self, obj: BookingRequest) -> str:
        """Ссылка «Отменить…» — только там, где машина разрешает отмену."""
        if obj.status not in _CANCELLABLE_STATUSES:
            return "—"
        url = reverse("admin:booking_bookingrequest_cancel", args=[obj.pk])
        return format_html('<a href="{}">Отменить…</a>', url)

    def cancel_view(self, request: "HttpRequest", booking_id: str) -> "HttpResponse":
        """Экран отмены: обязательная причина → сервис машины состояний.

        Право — то же, что на «правку» записи: смотрящий (view-only)
        кнопку не получает, правящий получает. Формально правки полей у
        роли нет (все поля readonly), поэтому именно это право и есть
        граница «может совершать действия над записями».
        """
        booking = get_object_or_404(BookingRequest.all_tenants, pk=booking_id)
        if not self.has_change_permission(request, booking):
            raise PermissionDenied

        if request.method == "POST":
            try:
                # Право выше уже проверено — аноним сюда не доходит.
                staff_user = cast("AbstractUser", request.user)
                cancel_booking_from_admin(
                    booking,
                    staff_user=staff_user,
                    reason_text=request.POST.get("reason"),
                )
            except (AdminCancelError, InvalidBookingTransition) as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
            else:
                self.message_user(
                    request,
                    "Запись отменена. Клиент получит уведомление, как при отмене через приложение.",
                    level=messages.SUCCESS,
                )
            return redirect("admin:booking_bookingrequest_changelist")

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,  # noqa: SLF001 — Django's own admin convention
            "original": booking,
            "title": "Отмена записи",
            "cancellable": booking.status in _CANCELLABLE_STATUSES,
        }
        return render(request, "admin/booking/bookingrequest/cancel_form.html", context)


@admin.register(BookingReminder)
class BookingReminderAdmin(AylaAdminMedia, admin.ModelAdmin):
    list_display = (
        "yclients_record_id",
        "reminder_kind",
        "reminder_state",
        "visit_at",
        "scheduled_at",
        "sent_at",
        "master_name",
        "service_name",
        "tenant",
    )
    list_filter = ("status", "kind", "tenant")
    search_fields = ("yclients_record_id", "master_name", "service_name")
    search_help_text = "Ищет по номеру записи в YClients, имени мастера и названию услуги."
    empty_value_display = "нет данных"
    readonly_fields = tuple(f.name for f in BookingReminder._meta.fields)
    ordering = ("scheduled_at",)
    fieldsets = (
        (
            "Напоминание",
            {"fields": ("kind", "status", "scheduled_at", "sent_at", "replied_at")},
        ),
        (
            "О каком визите",
            {"fields": ("visit_at", "master_name", "service_name", "booking_request")},
        ),
        (
            "Кому",
            {"fields": ("bot_user", "chat_id")},
        ),
        (
            "Служебное",
            {
                "classes": ("collapse",),
                "fields": (
                    "id",
                    "tenant",
                    "yclients_record_id",
                    "ayla_appointment_id",
                    "created_at",
                ),
            },
        ),
    )

    _KIND_BADGES: ClassVar[BadgeMap] = {
        BookingReminder.Kind.DAY_BEFORE: ("off", "За сутки"),
        BookingReminder.Kind.TWO_HOURS: ("off", "За два часа"),
    }

    #: Состояние напоминания словами — и код рядом.
    _STATE_BADGES: ClassVar[BadgeMap] = {
        BookingReminder.Status.PENDING: ("wait", "Ждёт отправки"),
        BookingReminder.Status.SENT_NO_REPLY: ("wait", "Отправлено, ответа нет"),
        BookingReminder.Status.SENT: ("ok", "Отправлено"),
        BookingReminder.Status.CONFIRMED: ("ok", "Клиент подтвердил"),
        BookingReminder.Status.RESCHEDULE_REQUESTED: ("wait", "Клиент просит перенести"),
        BookingReminder.Status.CANCELLED: ("stop", "Отменено"),
        BookingReminder.Status.ESCALATED: ("stop", "Ушло к оператору"),
        BookingReminder.Status.FAILED: ("stop", "Ошибка отправки"),
        BookingReminder.Status.STALE_DROPPED: ("off", "Снято: запись изменилась"),
    }

    @admin.display(description="Вид напоминания", ordering="kind")
    def reminder_kind(self, obj: BookingReminder):  # type: ignore[no-untyped-def]
        tone, label = self._KIND_BADGES.get(obj.kind, ("off", "Неизвестный вид"))
        return badge(tone, label, obj.kind)

    @admin.display(description="Состояние", ordering="status")
    def reminder_state(self, obj: BookingReminder):  # type: ignore[no-untyped-def]
        tone, label = self._STATE_BADGES.get(obj.status, ("off", "Неизвестное состояние"))
        return badge(tone, label, obj.status)

    def has_add_permission(self, request) -> bool:  # type: ignore[no-untyped-def]
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # type: ignore[no-untyped-def]
        return False

    def get_queryset(self, request):  # type: ignore[no-untyped-def]
        return BookingReminder.all_tenants.all()
