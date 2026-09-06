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

from typing import TYPE_CHECKING

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from apps.booking.models import BookingReminder, BookingRequest
from apps.booking.services.admin_cancel import AdminCancelError, cancel_booking_from_admin
from apps.booking.services.transitions import InvalidBookingTransition

if TYPE_CHECKING:  # pragma: no cover - только для аннотаций
    from django.http import HttpRequest, HttpResponse

#: Состояния, из которых машина состояний разрешает отмену.
_CANCELLABLE_STATUSES = frozenset(
    {
        BookingRequest.Status.CONFIRMED,
        BookingRequest.Status.RESCHEDULE_REQUESTED,
    }
)


@admin.register(BookingRequest)
class BookingRequestAdmin(admin.ModelAdmin):
    list_display = (
        "client_name",
        "service_name",
        "master_name",
        "visit_at",
        "status",
        "source",
        "tenant",
        "cancel_link",
    )
    list_filter = (
        "tenant",
        "master",
        "status",
        "source",
        ("visit_at", admin.DateFieldListFilter),
    )
    search_fields = ("client_name", "service_name", "comment")
    readonly_fields = tuple(f.name for f in BookingRequest._meta.fields)
    date_hierarchy = "visit_at"
    ordering = ("-created_at",)

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
                cancel_booking_from_admin(
                    booking,
                    staff_user=request.user,
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
class BookingReminderAdmin(admin.ModelAdmin):
    list_display = (
        "yclients_record_id",
        "kind",
        "status",
        "visit_at",
        "scheduled_at",
        "sent_at",
        "master_name",
        "service_name",
        "tenant",
    )
    list_filter = ("status", "kind", "tenant")
    search_fields = ("yclients_record_id", "master_name", "service_name")
    readonly_fields = tuple(f.name for f in BookingReminder._meta.fields)
    ordering = ("scheduled_at",)

    def has_add_permission(self, request) -> bool:  # type: ignore[no-untyped-def]
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # type: ignore[no-untyped-def]
        return False

    def get_queryset(self, request):  # type: ignore[no-untyped-def]
        return BookingReminder.all_tenants.all()
