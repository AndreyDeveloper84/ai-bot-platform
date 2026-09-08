"""Админка расписания — экраны, которые салон правит сам.

Полный CRUD у рабочих часов, исключений, блокировок и настроек сетки.
У заявок на изменение расписания (``ScheduleChangeRequest``) — см.
предупреждение в докстринге :class:`ScheduleChangeRequestAdmin`.

Эта правка касается ТОЛЬКО того, как экран выглядит и как на нём
названы вещи: подписи, группировка полей, подсказка поиска, бейджи
состояний. Ни права, ни действия, ни набор правимых полей не тронуты.
"""

from __future__ import annotations

from typing import ClassVar

from django.contrib import admin

from apps.adminconsole.theme import AylaAdminMedia, BadgeMap, badge
from apps.scheduling.models import (
    ScheduleChangeRequest,
    ScheduleException,
    SlotConfig,
    TimeBlock,
    WorkingHours,
)

#: Отсутствие значения называется словами, а не прочерком и не нулём.
#: Прочерк в колонке времени читается как «ноль часов», ноль — как
#: измеренный ноль (OPEN_DECISIONS §65).
_ABSENT = "нет данных"


class _ScheduleAdminBase(AylaAdminMedia, admin.ModelAdmin):
    empty_value_display = _ABSENT


@admin.register(WorkingHours)
class WorkingHoursAdmin(_ScheduleAdminBase):
    list_display = (
        "master",
        "day_of_week",
        "is_working",
        "start_time",
        "end_time",
        "lunch_start",
        "lunch_end",
        "tenant",
        "updated_at",
    )
    list_filter = ("tenant", "day_of_week", "is_working")
    search_fields = ("master__name",)
    search_help_text = "Ищет по имени мастера."
    ordering = ("master__name", "day_of_week")
    raw_id_fields = ("master", "created_by")
    readonly_fields = ("id", "created_at", "updated_at")
    fieldsets = (
        (
            "Чей день",
            {"fields": ("master", "tenant", "day_of_week", "is_working")},
        ),
        (
            "Смена",
            {
                "fields": ("start_time", "end_time"),
                "description": (
                    "Границы рабочего дня. Из них строится сетка свободных "
                    "часов, которую видит клиент."
                ),
            },
        ),
        (
            "Перерыв",
            {
                "fields": ("lunch_start", "lunch_end"),
                "description": (
                    "Пустые поля означают «перерыв не задан», а не «перерыва нет по нулю минут»."
                ),
            },
        ),
        (
            "Служебное",
            {
                "classes": ("collapse",),
                "fields": ("id", "created_by", "created_at", "updated_at"),
            },
        ),
    )

    def get_queryset(self, request):
        return self.model.all_tenants.all().select_related("master", "tenant")


@admin.register(ScheduleException)
class ScheduleExceptionAdmin(_ScheduleAdminBase):
    list_display = (
        "master",
        "date",
        "exception_kind",
        "start_time",
        "end_time",
        "tenant",
        "updated_at",
    )
    list_filter = ("tenant", "type", "date")
    search_fields = ("master__name", "reason")
    search_help_text = "Ищет по имени мастера и тексту причины."
    date_hierarchy = "date"
    ordering = ("-date", "master__name")
    raw_id_fields = ("master", "created_by")
    readonly_fields = ("id", "created_at", "updated_at")
    fieldsets = (
        (
            "Когда и у кого",
            {"fields": ("master", "tenant", "date", "type")},
        ),
        (
            "Часы",
            {
                "fields": ("start_time", "end_time"),
                "description": (
                    "Заполняются только для «изменённого графика». У "
                    "отпуска, больничного, выходного и корпоратива день "
                    "закрыт целиком, и часы обязаны остаться пустыми."
                ),
            },
        ),
        ("Причина", {"fields": ("reason",)}),
        (
            "Служебное",
            {
                "classes": ("collapse",),
                "fields": ("id", "created_by", "created_at", "updated_at"),
            },
        ),
    )

    #: Вид исключения словами — и код рядом.
    #:
    #: Человеческие подписи у этой модели уже были (``Type`` объявлен
    #: по-русски), но на экране пропадал КОД, а именно им исключение
    #: называется в задачах и логах. Бейдж печатает оба.
    _KIND_BADGES: ClassVar[BadgeMap] = {
        ScheduleException.Type.VACATION: ("off", "Отпуск"),
        ScheduleException.Type.SICK_LEAVE: ("wait", "Больничный"),
        ScheduleException.Type.DAY_OFF: ("off", "Выходной"),
        ScheduleException.Type.CUSTOM_HOURS: ("ok", "Изменён график"),
        ScheduleException.Type.EVENT: ("off", "Корпоратив или обучение"),
    }

    @admin.display(description="Вид исключения", ordering="type")
    def exception_kind(self, obj: ScheduleException):  # type: ignore[no-untyped-def]
        tone, label = self._KIND_BADGES.get(obj.type, ("off", "Неизвестный вид"))
        return badge(tone, label, obj.type)

    def get_queryset(self, request):
        return self.model.all_tenants.all().select_related("master", "tenant")


@admin.register(TimeBlock)
class TimeBlockAdmin(_ScheduleAdminBase):
    list_display = ("master", "start_at", "end_at", "reason", "tenant", "updated_at")
    list_filter = ("tenant",)
    search_fields = ("master__name", "reason")
    search_help_text = "Ищет по имени мастера и тексту причины."
    ordering = ("-start_at",)
    raw_id_fields = ("master", "created_by")
    readonly_fields = ("id", "created_at", "updated_at")
    fieldsets = (
        (
            "Что перекрываем",
            {
                "fields": ("master", "tenant", "start_at", "end_at", "reason"),
                "description": (
                    "Отрезок времени, в который мастера нельзя записать. "
                    "В отличие от исключения в расписании, блокировка не "
                    "привязана к дню целиком."
                ),
            },
        ),
        (
            "Служебное",
            {
                "classes": ("collapse",),
                "fields": ("id", "created_by", "created_at", "updated_at"),
            },
        ),
    )

    def get_queryset(self, request):
        return self.model.all_tenants.all().select_related("master", "tenant")


@admin.register(ScheduleChangeRequest)
class ScheduleChangeRequestAdmin(_ScheduleAdminBase):
    """Заявки мастера на изменение расписания.

    ВНИМАНИЕ, находка этой задачи, а не её работа. Докстринг модуля до
    сегодня утверждал, что экран «read-only for ScheduleChangeRequest
    (resolution is owner-driven via bot DM per Q-M6, not via admin
    form)». Код говорит другое: ``readonly_fields`` содержит ровно
    ``requested_change`` и ``created_at``, а ``status``,
    ``resolution_note`` и ``resolved_at`` в карточке правятся руками.

    Здесь это НЕ чинится и НЕ делается заметнее: группировки полей у
    этого экрана нет нарочно — понятный раздел «Решение» с выпадающим
    списком состояний приглашал бы нажать там, где решение обязано
    приходить из переписки с владельцем. Бейдж состояния добавлен
    только в СПИСОК: список читают, в нём ничего не правится.
    """

    list_display = (
        "master",
        "request_state",
        "created_at",
        "resolved_at",
        "resolved_by",
        "tenant",
    )
    list_filter = ("tenant", "status")
    search_fields = ("master__name", "reason")
    search_help_text = "Ищет по имени мастера и тексту причины."
    ordering = ("-created_at",)
    raw_id_fields = ("master", "resolved_by")
    readonly_fields = ("requested_change", "created_at")

    #: Состояние заявки словами — и код рядом.
    _STATE_BADGES: ClassVar[BadgeMap] = {
        ScheduleChangeRequest.Status.PENDING: ("wait", "Ждёт решения"),
        ScheduleChangeRequest.Status.APPROVED: ("ok", "Согласовано"),
        ScheduleChangeRequest.Status.REJECTED: ("stop", "Отклонено"),
        ScheduleChangeRequest.Status.CANCELLED: ("off", "Мастер отозвала"),
        ScheduleChangeRequest.Status.AUTO_ESCALATED: ("stop", "Ушло на эскалацию по таймауту"),
    }

    @admin.display(description="Состояние заявки", ordering="status")
    def request_state(self, obj: ScheduleChangeRequest):  # type: ignore[no-untyped-def]
        tone, label = self._STATE_BADGES.get(obj.status, ("off", "Неизвестное состояние"))
        return badge(tone, label, obj.status)

    def get_queryset(self, request):
        return self.model.all_tenants.all().select_related("master", "tenant", "resolved_by")


@admin.register(SlotConfig)
class SlotConfigAdmin(_ScheduleAdminBase):
    list_display = (
        "tenant",
        "slot_granularity_min",
        "buffer_min",
        "lead_time_min",
        "max_advance_days",
        "updated_at",
    )
    list_filter = ("tenant",)
    readonly_fields = ("id", "updated_at")
    fieldsets = (
        (
            "Сетка записи",
            {
                "fields": ("tenant", "slot_granularity_min", "buffer_min"),
                "description": (
                    "Шаг сетки — через сколько минут идут предлагаемые "
                    "клиенту начала. Буфер — пауза, которую сетка "
                    "оставляет между двумя записями."
                ),
            },
        ),
        (
            "Насколько вперёд и насколько заранее",
            {
                "fields": ("lead_time_min", "max_advance_days"),
                "description": (
                    "Запас до визита — за сколько минут перестаём "
                    "предлагать ближайшее время. Глубина — как далеко "
                    "вперёд клиент вообще видит свободные дни."
                ),
            },
        ),
        (
            "Служебное",
            {"classes": ("collapse",), "fields": ("id", "updated_at")},
        ),
    )

    def get_queryset(self, request):
        return self.model.all_tenants.all().select_related("tenant")
