"""Read-only Catalog mirror admin (DRF-572 / Sprint 7 / C1).

Catalog mirrors are derived state — the source of truth lives in mysite,
the platform writes only through the catalog sync (C-track). Manual
edits would silently rot on the next sync overwrite. Admin is view-only
for forensics; controlled mutation (force resync) lands in C6 (DRF-576)
as an admin action.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.http import HttpRequest
from django.shortcuts import render
from django.utils import timezone

from apps.catalog.master_state import is_available, sale_block
from apps.catalog.models import (
    CatalogFaq,
    CatalogHelpArticle,
    CatalogMaster,
    CatalogService,
    MasterService,
)
from apps.catalog.provenance import MasterServiceSource, master_service_write
from apps.catalog.services import verification


class _MirrorAdminBase(admin.ModelAdmin):
    list_filter = ("tenant", "external_updated_at")
    date_hierarchy = "external_updated_at"
    ordering = ("-external_updated_at",)

    def get_queryset(self, request):
        return self.model.all_tenants.all()

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


@admin.register(CatalogService)
class CatalogServiceAdmin(_MirrorAdminBase):
    list_display = ("slug", "name", "tenant", "is_active", "is_popular", "synced_at")
    search_fields = ("slug", "name", "external_id")


class MasterArchivedFilter(admin.SimpleListFilter):
    """«В архиве / не в архиве» — по NULL в ``archived_at`` (DRF-1496)."""

    title = "архив"
    parameter_name = "archived"

    def lookups(self, request, model_admin):  # type: ignore[no-untyped-def]
        return (("live", "Не в архиве"), ("archived", "В архиве"))

    def queryset(self, request, queryset):  # type: ignore[no-untyped-def]
        if self.value() == "live":
            return queryset.filter(archived_at__isnull=True)
        if self.value() == "archived":
            return queryset.filter(archived_at__isnull=False)
        return queryset


#: Поля, которые перезаписывает синхронизация каталога. Править их руками
#: бессмысленно — затрёт следующий прогон, — поэтому карточка отдаёт их
#: только на чтение, а не «временно, с предупреждением».
_SYNC_MANAGED_FIELDS = (
    "name",
    "specialization",
    "bio",
    "experience",
    "rating",
    "review_count",
    "is_active",
    "yclients_staff_id",
    "ayla_user_id",
    "external_id",
    "external_updated_at",
    "synced_at",
    "cache_version",
)

#: Платформенные поля. Меняются только действиями со страницы списка —
#: каждое действие пишет в журнал с автором, поэтому свободной правки
#: этих полей в карточке тоже нет: правка через форму обязана иметь
#: причину (архив) или быть осознанным жестом (верификация).
_PLATFORM_FIELDS = (
    "tenant",
    "invite_status",
    "mode",
    "photo_url",
    "max_handle",
    "linked_bot_user",
    "invited_at",
    "invite_expires_at",
    "accepted_at",
    "archived_at",
    "archive_reason",
)


@admin.register(CatalogMaster)
class CatalogMasterAdmin(_MirrorAdminBase):
    """Мастера: карточка, верификация приглашения, архив (DRF-1496).

    В отличие от остальных зеркал каталога, этот экран не полностью
    read-only: у мастера есть платформенные поля, которых синхронизация
    никогда не касается (см. докстринг ``CatalogMaster``). Ими управляет
    оператор — но только действиями, потому что:

    * верификация (перевод ``invite_status`` вручную) обязана оставить
      след с автором — ``log_change`` пишет ``LogEntry``, который
      ``apps.adminconsole.journal`` разворачивает в ``AuditLog``;
    * архив обязан иметь причину — «архивировал молча» не допускается,
      поэтому действие ведёт через промежуточную страницу с обязательным
      полем причины, а поля ``archived_at``/``archive_reason`` в карточке
      только для чтения.

    ``invite_token`` и ``raw`` в карточку не попадают вовсе: первый —
    одноразовый секрет привязки, второй — сырой зеркальный JSON, в
    котором могут лежать контакты мастера.

    Право на действия — ``catalog.change_catalogmaster``: оно есть у
    владельца и у роли «правящий», у «смотрящего» его нет, и Django сам
    не отдаст ему действия (``permissions=("change",)``).
    """

    list_display = (
        "name",
        "tenant",
        "invite_status",
        "mode",
        "bookable",
        "bookable_note",
        "synced_at",
    )
    list_filter = (  # type: ignore[assignment]
        "tenant",
        "is_active",
        "invite_status",
        "mode",
        MasterArchivedFilter,
        "external_updated_at",
    )
    search_fields = ("name", "specialization", "external_id", "max_handle")
    # DRF-1515: invite_token — действующий одноразовый ключ привязки мастера
    # (apps/master_api/auth.py → validate_invite_token). Экран read-only для
    # всех, значит значение печаталось бы каждому открывшему форму. Поле не
    # попадает в форму вовсе: показывать его здесь некому и незачем.
    exclude = ("invite_token",)
    actions = (
        "verify_masters",
        "revoke_invite_masters",
        "archive_masters",
        "unarchive_masters",
    )
    fieldsets = (
        (
            "Данные синхронизации",
            {
                "description": (
                    "Эти поля перезаписывает каждый прогон синхронизации "
                    "каталога — правка здесь была бы временной, поэтому "
                    "они только для чтения."
                ),
                "fields": _SYNC_MANAGED_FIELDS,
            },
        ),
        (
            "Платформенное состояние",
            {
                "description": (
                    "Меняется действиями со страницы списка — верификацией "
                    "и архивом. Каждое действие пишется в журнал с автором "
                    "и причиной."
                ),
                "fields": _PLATFORM_FIELDS,
            },
        ),
    )
    readonly_fields = _SYNC_MANAGED_FIELDS + _PLATFORM_FIELDS

    def get_queryset(self, request):  # type: ignore[no-untyped-def]
        return self.model.all_tenants.select_related("tenant")

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        # Единственный из зеркал, где право на change живое: без него
        # Django не отдаст действия даже владельцу. Форма при этом
        # пустая — все поля readonly, изменить строку можно только
        # журналируемым действием.
        return request.user.has_perm("catalog.change_catalogmaster")

    @admin.display(description="Бронируется", boolean=True)
    def bookable(self, obj: CatalogMaster) -> bool:
        """Тот же предикат, что ``CatalogMaster.objects.bookable()``."""
        return is_available(obj)

    #: Причина отказа словами оператора — по коду из ``sale_block``.
    #:
    #: Ключи ровно из :data:`apps.catalog.master_state.SaleBlock`, и
    #: полнота держится тестом: гейт продажи расширяется (DRF-1521
    #: добавляет ``profile_incomplete``), и забытый ключ печатал бы
    #: оператору «нет данных» вместо причины.
    _BOOKABLE_NOTES = {
        "revoked": "в архиве или снята синхронизацией",
        "pending": "приглашение не принято",
        "ayla_unlinked": "не удалось связать профиль с Ayla",
    }

    @admin.display(description="Почему не бронируется")
    def bookable_note(self, obj: CatalogMaster) -> str:
        """Причина, а не только факт — требование задачи к списку.

        Спрашивает ``sale_block``, а не пересобирает лестницу условий
        заново. Своя копия здесь была бы четвёртой (докстринг
        ``apps/catalog/master_state.py`` заводить новые запрещает) и уже
        врала бы: после DRF-1540 гейт спрашивает про ``ayla_user_id``, и
        несвязанный мастер по прежней лестнице подписывался как
        «неактивна по данным синхронизации» — оператор шёл бы чинить
        активность, которая в порядке.
        """
        block = sale_block(obj)
        if block is None:
            return "—"
        if block == "revoked" and obj.archived_at is not None:
            # Архив и снятая активность — одно значение гейта, но разные
            # действия оператора: из архива достают отсюда, активность
            # чинят в источнике синхронизации.
            return "в архиве"
        return self._BOOKABLE_NOTES.get(block, block)

    @admin.action(
        permissions=["change"],
        description="Верифицировать: приглашение принято (вручную)",
    )
    def verify_masters(self, request, queryset) -> None:  # type: ignore[no-untyped-def]
        """Тонкая обёртка над сервисом верификации (DRF-1553).

        Тело действия переехало в
        ``apps.catalog.services.verification.verify_masters``: то же
        действие понадобилось экрану подключения салона (DRF-1553,
        OPEN_DECISIONS §51.1), а позвать метод админки с ``request`` и
        ``queryset`` оттуда нельзя. Копия была бы второй реализацией
        верификации — и вторым текстом в журнале.

        Здесь осталось ровно то, что принадлежит админке: какие строки
        выбраны и что сказать оператору. Журнал пишет сервис — см. его
        докстринг, почему не вызывающий.
        """
        outcome = verification.verify_masters(queryset, user=request.user)
        self.message_user(
            request,
            f"Верифицировано: {outcome.verified}. Уже принятых пропущено: {outcome.skipped}.",
            level=messages.SUCCESS,
        )

    @admin.action(
        permissions=["change"],
        description="Отозвать приглашение (cancelled)",
    )
    def revoke_invite_masters(self, request, queryset) -> None:  # type: ignore[no-untyped-def]
        changed = 0
        for master in queryset:
            if master.invite_status == CatalogMaster.InviteStatus.CANCELLED:
                continue
            old = master.get_invite_status_display()
            master.invite_status = CatalogMaster.InviteStatus.CANCELLED
            master.save(update_fields=["invite_status"])
            self.log_change(
                request,
                master,
                f"Приглашение отозвано вручную: «{old}» → «отменено».",
            )
            changed += 1
        self.message_user(
            request,
            f"Отозвано: {changed}. Уже отменённых пропущено: {len(queryset) - changed}.",
            level=messages.SUCCESS,
        )

    @admin.action(
        permissions=["change"],
        description="Отправить в архив (причина обязательна)",
    )
    def archive_masters(self, request, queryset):  # type: ignore[no-untyped-def]
        """Архив только с причиной — промежуточная страница обязательна.

        Пустая причина не «проставляет пустую строку», а возвращает
        оператора на форму: без причины архив не случается вовсе.
        """
        if request.POST.get("apply"):
            reason = request.POST.get("archive_reason", "").strip()
            if reason:
                archived = 0
                for master in queryset:
                    if master.archived_at is not None:
                        continue
                    master.archived_at = timezone.now()
                    master.archive_reason = reason
                    master.save(update_fields=["archived_at", "archive_reason"])
                    self.log_change(request, master, f"Архив. Причина: {reason}")
                    archived += 1
                self.message_user(
                    request,
                    f"В архив отправлено: {archived}. Уже в архиве "
                    f"пропущено: {len(queryset) - archived}.",
                    level=messages.SUCCESS,
                )
                return None
            self.message_user(
                request,
                "Архив без причины не допускается — укажите причину.",
                level=messages.ERROR,
            )
        return render(
            request,
            "admin/catalog/master_archive.html",
            context={
                "title": "Отправить в архив",
                "masters": queryset,
                "action_checkbox_name": ACTION_CHECKBOX_NAME,
                "opts": self.model._meta,  # noqa: SLF001
            },
        )

    @admin.action(
        permissions=["change"],
        description="Извлечь из архива",
    )
    def unarchive_masters(self, request, queryset) -> None:  # type: ignore[no-untyped-def]
        restored = 0
        for master in queryset:
            if master.archived_at is None:
                continue
            master.archived_at = None
            master.archive_reason = ""
            master.save(update_fields=["archived_at", "archive_reason"])
            self.log_change(request, master, "Извлечён из архива.")
            restored += 1
        self.message_user(
            request,
            f"Извлечено из архива: {restored}. Не были в архиве: {len(queryset) - restored}.",
            level=messages.SUCCESS,
        )


@admin.register(MasterService)
class MasterServiceAdmin(admin.ModelAdmin):
    """The one catalog admin that is NOT read-only -- and, until DRF-975, the
    second unaudited write path into ``catalog_masterservice``.

    Every other mirror here extends ``_MirrorAdminBase``, which denies add /
    change / delete outright. This one does not, and it never wrote an audit
    row either: a superuser could create master-service edges through
    ``/admin/`` and leave exactly the same forensic hole as the 2026-07-22
    script. That is not hypothetical on a pilot host where operators have
    admin accounts.

    It is wired rather than locked down, because the ability to fix one edge by
    hand is genuinely useful during a pilot. What changed is that it now has to
    say who it is: ``save_model`` / ``delete_*`` enter the provenance context,
    so the row is stamped ``source="django_admin"`` and the signals in
    ``apps.catalog.signals`` emit ``master.service_edge_created`` /
    ``master.service_edge_deleted``. Without the context the model gate would
    refuse the write and the admin would 500 -- correct, but a worse
    experience than an audited success.

    ``created_by_actor_id`` stays NULL here on purpose: the admin actor is an
    ``auth.User`` (integer pk), not the ``identity.BotUser`` UUID that column
    holds. The acting username goes into the audit payload's ``reason``
    instead, which is honest about what we actually know.
    """

    list_display = ("master", "service", "tenant", "source", "created_at")
    list_filter = ("tenant", "source")
    search_fields = ("master__name", "service__name")
    raw_id_fields = ("master", "service", "created_by")
    # Provenance is written by the platform, never typed by a human -- an
    # editable ``source`` would let an admin relabel a hand-made row as
    # ``catalog_sync`` and undo the whole point.
    readonly_fields = ("source", "created_by_actor_id", "created_at", "updated_at")

    def get_queryset(self, request):
        return self.model.all_tenants.all().select_related("master", "service", "tenant")

    def _ctx(self, request):
        return master_service_write(
            MasterServiceSource.DJANGO_ADMIN,
            reason=f"django admin user={getattr(request.user, 'username', '?')}",
        )

    def save_model(self, request, obj, form, change):
        with self._ctx(request):
            super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        with self._ctx(request):
            super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        with self._ctx(request):
            super().delete_queryset(request, queryset)


@admin.register(CatalogFaq)
class CatalogFaqAdmin(_MirrorAdminBase):
    list_display = ("question", "category_slug", "tenant", "synced_at")
    search_fields = ("question", "answer", "external_id")


@admin.register(CatalogHelpArticle)
class CatalogHelpArticleAdmin(_MirrorAdminBase):
    list_display = ("question", "tenant", "is_active", "order", "synced_at")
    search_fields = ("question", "answer", "external_id")
