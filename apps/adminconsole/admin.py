"""Экран журнала действий админки (DRF-1495).

``django.contrib.admin.models.LogEntry`` Django пишет сам, но по
умолчанию не регистрирует — и в этом репозитории его никто не
регистрировал, так что след правок был, а посмотреть его было негде.

Экран строго на чтение. Журнал, который можно править, — не журнал;
``apps.audit.admin.AuditLogAdmin`` держит ту же линию с DRF-426.
Хронология в ``AuditLog`` шире (туда пишут ещё и сервисы), а этот
экран — быстрый ответ на «что делали руками через админку».

DRF-1514 добавил сюда вторую пару экранов — доступ к данным клиента и
журнал этого доступа. Они отвечают на другой вопрос: не «кто что
правил», а «кто что видел». Просмотр чужой переписки не меняет ни
строки, поэтому в ``LogEntry`` его нет и быть не может.
"""

from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.models import LogEntry
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import NoReverseMatch, reverse

from apps.adminconsole.client_access import is_unrestricted, open_access
from apps.adminconsole.models import ClientDataAccessGrant, ClientDataAccessLog


@admin.register(LogEntry)
class AdminActionLogAdmin(admin.ModelAdmin):
    list_display = (
        "action_time",
        "user",
        "action_label",
        "content_type",
        "object_repr",
        "change_message_short",
    )
    list_filter = ("action_flag", "content_type", "user")
    search_fields = ("object_repr", "change_message", "user__username")
    date_hierarchy = "action_time"
    ordering = ("-action_time",)

    @admin.display(description="Действие", ordering="action_flag")
    def action_label(self, obj: LogEntry) -> str:
        if obj.is_addition():
            return "создано"
        if obj.is_change():
            return "изменено"
        if obj.is_deletion():
            return "удалено"
        return "—"

    @admin.display(description="Что менялось")
    def change_message_short(self, obj: LogEntry) -> str:
        """Перечень изменённых полей. Значений здесь нет — только имена."""
        message = obj.get_change_message()
        return message if len(message) <= 160 else message[:157] + "…"

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False


#: Что показывает карточка уже выданного пропуска. Форма заведения
#: короче — там вводятся только обращение и причина, остальное
#: проставляет ``open_access``.
_GRANT_VIEW_FIELDS = (
    "admin_task",
    "actor_username",
    "client_label",
    "tenant_slug",
    "reason",
    "created_at",
    "expires_at",
)


@admin.register(ClientDataAccessGrant)
class ClientDataAccessGrantAdmin(admin.ModelAdmin):
    """Экран «указать причину и открыть доступ» (DRF-1514).

    Единственная дверь к переписке и профилю клиента. Здесь заводится
    пропуск, а не правится строка, поэтому изменение и удаление
    закрыты: отредактированная задним числом причина — это причина,
    которой не было.

    ``has_add_permission`` отвечает «да» любому сотруднику с доступом в
    админку, хотя права ``add_clientdataaccessgrant`` роли не выдаются
    (``adminconsole`` лежит в ``EDITOR_DENIED_APP_LABELS``). Это не
    дыра: открыть пропуск — не «править данные», а объявить намерение,
    и запретить это смотрящему значило бы запретить ему работать.
    """

    autocomplete_fields = ("admin_task",)
    list_display = (
        "created_at",
        "actor_username",
        "client_label",
        "tenant_slug",
        "expires_at",
        "reason_short",
    )
    list_filter = ("tenant_slug",)
    search_fields = ("actor_username", "client_label", "reason")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    @admin.display(description="Причина")
    def reason_short(self, obj: ClientDataAccessGrant) -> str:
        return obj.reason if len(obj.reason) <= 120 else obj.reason[:117] + "…"

    def get_fields(self, request: HttpRequest, obj: object = None):  # type: ignore[no-untyped-def]
        return ("admin_task", "reason") if obj is None else _GRANT_VIEW_FIELDS

    def get_readonly_fields(self, request: HttpRequest, obj: object = None):  # type: ignore[no-untyped-def]
        return () if obj is None else _GRANT_VIEW_FIELDS

    def has_add_permission(self, request: HttpRequest) -> bool:
        user = getattr(request, "user", None)
        return bool(getattr(user, "is_active", False) and getattr(user, "is_staff", False))

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False

    def get_queryset(self, request: HttpRequest):  # type: ignore[no-untyped-def]
        """Свои пропуска. Чужие — владельцу.

        В строке пропуска лежит подпись клиента; общий список превратил
        бы этот экран в справочник клиентов всех салонов, то есть в то
        самое, что задача закрывает.
        """
        queryset = super().get_queryset(request)
        if is_unrestricted(getattr(request, "user", None)):
            return queryset
        return queryset.filter(actor_id=getattr(request.user, "pk", None))

    def formfield_for_foreignkey(self, db_field, request, **kwargs):  # type: ignore[no-untyped-def]
        """Поле обращения собирается здесь целиком, а не через ``db_field``.

        ``ForeignKey.formfield()`` берёт ``AdminTask._default_manager``,
        то есть ``TenantScopedManager``. В админке тенанта в контексте
        нет: менеджер вернул бы пустой список **и** написал бы в аудит
        «запрос без тенанта» на каждый показ формы. Поэтому поле
        строится напрямую поверх ``all_tenants`` — очередь обращений
        кросс-тенантная по построению.
        """
        if db_field.name == "admin_task":
            from django.contrib.admin.widgets import AutocompleteSelect

            from apps.handoff.models import AdminTask

            return forms.ModelChoiceField(
                queryset=AdminTask.all_tenants.all(),
                widget=AutocompleteSelect(db_field, self.admin_site),
                label="Обращение",
                help_text=db_field.help_text,
                required=True,
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request: HttpRequest, obj, form, change) -> None:  # type: ignore[no-untyped-def]
        """Поля проставляет служба, а не форма — см. ``open_access``."""
        open_access(
            actor=request.user,
            admin_task=obj.admin_task,
            reason=obj.reason,
            grant=obj,
        )

    def response_add(self, request: HttpRequest, obj, post_url_continue=None):  # type: ignore[no-untyped-def]
        """Вернуть человека туда, откуда он пришёл, — на обращение."""
        try:
            url = reverse("admin:handoff_admintask_change", args=[obj.admin_task_id])
        except NoReverseMatch:  # pragma: no cover — экран очереди снят
            return super().response_add(request, obj, post_url_continue)
        self.message_user(
            request,
            f"Доступ к данным клиента открыт до {obj.expires_at:%H:%M %d.%m}. "
            "Каждый открытый экран попадёт в журнал доступа.",
            level=messages.SUCCESS,
        )
        return HttpResponseRedirect(url)


@admin.register(ClientDataAccessLog)
class ClientDataAccessLogAdmin(admin.ModelAdmin):
    """Журнал доступа — кто что видел (DRF-1514).

    Соседний экран ``/admin/admin/logentry/`` отвечает на другой вопрос:
    кто что **правил**. Просмотр чужой переписки не меняет ни строки,
    поэтому в журнале изменений его нет и быть не может — отсюда вторая
    таблица, а не колонка в первой.

    Строго на чтение, как и все журналы в этом репозитории.
    Не-суперпользователь видит только свой след: в строках лежат
    подписи клиентов, и общий список снова стал бы справочником клиентов
    всех салонов.
    """

    list_display = (
        "occurred_at",
        "actor_username",
        "outcome",
        "screen",
        "client_label",
        "tenant_slug",
        "reason_short",
        "detail",
    )
    list_filter = ("outcome", "screen", "tenant_slug")
    search_fields = ("actor_username", "client_label", "reason", "object_id")
    date_hierarchy = "occurred_at"
    ordering = ("-occurred_at",)

    @admin.display(description="Причина")
    def reason_short(self, obj: ClientDataAccessLog) -> str:
        return obj.reason if len(obj.reason) <= 120 else obj.reason[:117] + "…"

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False

    def get_queryset(self, request: HttpRequest):  # type: ignore[no-untyped-def]
        queryset = super().get_queryset(request)
        if is_unrestricted(getattr(request, "user", None)):
            return queryset
        return queryset.filter(actor_pk=str(getattr(request.user, "pk", "") or ""))
