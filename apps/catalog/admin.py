"""Read-only Catalog mirror admin (DRF-572 / Sprint 7 / C1).

Catalog mirrors are derived state — the source of truth lives in mysite,
the platform writes only through the catalog sync (C-track). Manual
edits would silently rot on the next sync overwrite. Admin is view-only
for forensics; the single controlled mutation is the force-resync admin
action (DRF-1581) on ``CatalogServiceAdmin``, which enqueues
``apps.catalog.tasks.sync_catalog_for_tenant`` per selected tenant.

(The paragraph above used to promise the button "lands in C6 (DRF-576)".
DRF-576 was the read-only admin; the button itself had been specced in
DRF-667, closed as its duplicate — and half the subject left with the
dupe. DRF-1581 is the number that actually shipped it.)
"""

from __future__ import annotations

from typing import ClassVar

from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.http import HttpRequest
from django.shortcuts import render
from django.utils import timezone

from apps.adminconsole.theme import AylaAdminMedia, BadgeMap, absent, badge
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
from apps.catalog.tasks import sync_catalog_for_tenant


#: Как выглядит отсутствие значения на экранах каталога.
#:
#: Не «—» и не «0»: прочерк в колонке чисел читается как ноль, а ноль —
#: как измеренный ноль. Ни то, ни другое не правда, когда значения просто
#: нет (OPEN_DECISIONS §65 — «не подставлять значение вместо отсутствия»).
_ABSENT = "нет данных"


class _MirrorAdminBase(AylaAdminMedia, admin.ModelAdmin):
    list_filter = ("tenant", "external_updated_at")
    date_hierarchy = "external_updated_at"
    ordering = ("-external_updated_at",)
    empty_value_display = _ABSENT

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
    list_display = ("name", "slug", "tenant", "is_active", "is_popular", "synced_at")
    search_fields = ("slug", "name", "external_id")
    search_help_text = "Ищет по названию услуги, её коду и идентификатору в источнике."
    # Экран и так только для чтения (``_MirrorAdminBase`` отказывает в
    # change). Перечисление здесь ничего не открывает и ничего не
    # закрывает — оно позволяет Django нарисовать в карточке поля,
    # которые не редактируемы в принципе (``id``, ``synced_at``,
    # ``external_updated_at``): без явного readonly Django отказывается
    # включать их в fieldsets.
    readonly_fields = tuple(f.name for f in CatalogService._meta.fields)
    actions = ("force_resync_selected_tenants",)
    fieldsets = (
        (
            "Услуга",
            {
                "fields": ("name", "slug", "tenant", "short_description", "description"),
                "description": (
                    "Так услуга называется у клиента. Экран только для "
                    "чтения: каталог — зеркало, правку затрёт следующий "
                    "прогон синхронизации."
                ),
            },
        ),
        (
            "Продажа",
            {
                "fields": (
                    "price_from",
                    "duration_min",
                    "is_active",
                    "is_popular",
                    "goals",
                ),
                "description": (
                    "«Продаётся» — то же поле, по которому услуга попадает клиенту в выдачу."
                ),
            },
        ),
        (
            "Здоровье и противопоказания",
            {"fields": ("requires_health_check", "contraindications")},
        ),
        (
            "Служебное: синхронизация и SEO",
            {
                "classes": ("collapse",),
                "fields": (
                    "id",
                    "external_id",
                    "ayla_service_id",
                    "external_updated_at",
                    "synced_at",
                    "cache_version",
                    "seo_title",
                    "seo_description",
                    "raw",
                ),
                "description": (
                    "Технические поля зеркала. Нужны, когда разбираешься, "
                    "почему услуга приехала не такой, какой ждали."
                ),
            },
        ),
    )

    # DRF-1581, образец — DRF-1495 у зеркала KB: без ``permissions=``
    # Django отдаёт действие всякому, кто открыл экран, включая роль
    # «смотрящий». ``permissions=("change",)`` не годится:
    # ``has_change_permission`` в базовом классе безусловно False, и
    # действие умерло бы вместе с ролями. Отдельный предикат спрашивает
    # право на модель: владелец и «правящий» кнопку получают,
    # «смотрящий» — нет.
    def has_resync_permission(self, request: HttpRequest) -> bool:
        return request.user.has_perm("catalog.change_catalogservice")

    @admin.action(
        permissions=("resync",),
        description="Пересинхронизировать каталог выбранных салонов",
    )
    def force_resync_selected_tenants(self, request: HttpRequest, queryset) -> None:  # type: ignore[no-untyped-def]
        """Enqueue :func:`sync_catalog_for_tenant` per distinct tenant (DRF-1581).

        Дедуп по тенанту, не по строкам: сколько бы услуг салона ни было
        выбрано, задание одно — прогон пересинхронизирует все три зеркала
        (услуги, мастера, связи) этого салона целиком.

        Веера по всем салонам здесь нет нарочно: ставится ровно то, что
        выбрал оператор. Анонимный лимит Ayla (~30/мин, замер окна
        DRF-1595) веер по каждому тенанту разом не пережил бы.

        Защита от двойного запуска — не в этом методе, а в сервисе:
        Redis-замок ``CatalogSyncService`` отвечает повторному прогону
        ``skipped``, поэтому двойное нажатие не запускает двух
        синхронизаций одного салона.

        Синхронно из админ-запроса не гоняем (причина расписана у K9,
        ``apps/kb/admin.py``): фетч из Ayla по сети заблокировал бы
        воркер админки на минуты. Оператор видит принятие в message,
        завершение — по обновившейся колонке ``synced_at``.
        """
        tenants = sorted(
            {
                row.tenant.slug: str(row.tenant_id) for row in queryset.select_related("tenant")
            }.items()
        )
        if not tenants:
            self.message_user(
                request,
                "В выборке нет салонов — пересинхронизация не поставлена.",
                level=messages.WARNING,
            )
            return

        for _slug, tenant_id in tenants:
            # ``.delay`` ставит задание и возвращается сразу; результат
            # (AsyncResult) здесь не ждём — завершение видно по synced_at.
            sync_catalog_for_tenant.delay(tenant_id)

        slugs = ", ".join(slug for slug, _ in tenants)
        self.message_user(
            request,
            f"Пересинхронизация поставлена для {len(tenants)} салон(а/ов): {slugs}. "
            "Прогон стартует в течение минуты; завершение смотрите по колонке "
            "synced_at — она обновится, когда синхронизация закончится. "
            "Повторное нажатие, пока идёт прогон, пропускается замком.",
            level=messages.SUCCESS,
        )


class MasterArchivedFilter(admin.SimpleListFilter):
    """«В архиве / не в архиве» — по NULL в ``archived_at`` (DRF-1496)."""

    title = "архив мастера"
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
    # §83 — подтверждение расписания. Только для чтения, и это не
    # осторожность: правимый отпечаток означал бы, что подтверждение
    # можно ВПИСАТЬ, а не выдать. Ставит его один писатель —
    # ``catalog.services.schedule_confirmation.confirm_schedule``, с
    # живым чтением часов и автором (правило 6).
    "schedule_confirmed_at",
    "schedule_confirmed_by",
    "schedule_fingerprint",
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
        "invite_state",
        "work_mode",
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
    search_help_text = (
        "Ищет по имени мастера, специализации, нику в MAX и идентификатору в источнике."
    )
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
            "Мастер",
            {
                "description": (
                    "Как мастера видит клиент. Эти поля перезаписывает "
                    "каждый прогон синхронизации каталога — правка здесь "
                    "была бы временной, поэтому они только для чтения."
                ),
                "fields": ("name", "specialization", "bio", "experience"),
            },
        ),
        (
            "Репутация и активность",
            {
                "description": (
                    "Тоже приезжает синхронизацией. «Активна по данным "
                    "синхронизации» — это ответ источника, а не решение "
                    "оператора: снятую активность чинят в источнике, не здесь."
                ),
                "fields": ("rating", "review_count", "is_active"),
            },
        ),
        (
            "Приглашение и доступ",
            {
                "description": (
                    "Платформенная часть: её синхронизация не трогает "
                    "никогда. Меняется действиями со страницы списка — "
                    "верификацией и отзывом приглашения. Каждое действие "
                    "пишется в журнал с автором."
                ),
                "fields": (
                    "invite_status",
                    "mode",
                    "max_handle",
                    "linked_bot_user",
                    "invited_at",
                    "invite_expires_at",
                    "accepted_at",
                ),
            },
        ),
        (
            "Подтверждение расписания",
            {
                "description": (
                    "Допуск мастера к продаже (§83). Проставляет владелец "
                    "салона в салонной поверхности, а не форма: отпечаток "
                    "снимается с живых часов в момент подтверждения. Любое "
                    "изменение часов отменяет подтверждение автоматически."
                ),
                "fields": (
                    "schedule_confirmed_at",
                    "schedule_confirmed_by",
                    "schedule_fingerprint",
                ),
            },
        ),
        (
            "Архив",
            {
                "description": (
                    "Архив обязан иметь причину — «архивировал молча» не "
                    "допускается, поэтому поля заполняет действие, а не форма."
                ),
                "fields": ("archived_at", "archive_reason"),
            },
        ),
        (
            "Служебное: связь с источниками",
            {
                "classes": ("collapse",),
                "description": (
                    "Технические идентификаторы и отметки синхронизации. "
                    "Нужны, когда разбираешься, откуда приехало значение."
                ),
                "fields": (
                    "tenant",
                    "photo_url",
                    "yclients_staff_id",
                    "ayla_user_id",
                    "external_id",
                    "external_updated_at",
                    "synced_at",
                    "cache_version",
                ),
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

    #: Состояние приглашения словами — и код рядом.
    #:
    #: Оба имени обязательны. Одного человеческого мало: оператор
    #: прочитает «Ждёт мастера», а спросить о нём не сможет — в задачах,
    #: логах и в разговоре с нами живёт строка ``pending``. Одного
    #: машинного тоже мало — это и есть сегодняшняя болезнь экрана.
    #:
    #: Ключи ровно из :class:`CatalogMaster.InviteStatus`; полноту
    #: держит тест: добавится состояние — забытый ключ напечатает код
    #: без подписи, и тест это поймает.
    _INVITE_BADGES: ClassVar[BadgeMap] = {
        CatalogMaster.InviteStatus.PENDING: ("wait", "Приглашение не принято"),
        CatalogMaster.InviteStatus.ACCEPTED: ("ok", "Приглашение принято"),
        CatalogMaster.InviteStatus.EXPIRED: ("stop", "Приглашение просрочено"),
        CatalogMaster.InviteStatus.CANCELLED: ("stop", "Приглашение отозвано"),
    }

    @admin.display(description="Приглашение", ordering="invite_status")
    def invite_state(self, obj: CatalogMaster):  # type: ignore[no-untyped-def]
        tone, label = self._INVITE_BADGES.get(obj.invite_status, ("off", "Неизвестное состояние"))
        return badge(tone, label, obj.invite_status)

    _MODE_BADGES: ClassVar[BadgeMap] = {
        CatalogMaster.Mode.INVITE: ("ok", "Заходит в приложение"),
        CatalogMaster.Mode.CATALOG_ONLY: ("off", "Только в каталоге, без входа"),
    }

    @admin.display(description="Режим", ordering="mode")
    def work_mode(self, obj: CatalogMaster):  # type: ignore[no-untyped-def]
        tone, label = self._MODE_BADGES.get(obj.mode, ("off", "Неизвестный режим"))
        return badge(tone, label, obj.mode)

    @admin.display(description="Бронируется", boolean=True)
    def bookable(self, obj: CatalogMaster) -> bool:
        """Тот же предикат, что ``CatalogMaster.objects.bookable()``."""
        return is_available(obj)

    #: Причина отказа словами оператора — по коду из ``sale_block``.
    #:
    #: Ключи ровно из :data:`apps.catalog.master_state.SaleBlock`, и
    #: полнота держится тестом: гейт продажи расширяется (DRF-1521
    #: добавила ``profile_incomplete``), и забытый ключ печатал бы
    #: оператору «нет данных» вместо причины.
    _BOOKABLE_NOTES = {
        "revoked": "в архиве или снята синхронизацией",
        "pending": "приглашение не принято",
        "ayla_unlinked": "не удалось связать профиль с Ayla",
        # DRF-1521. Оператору важно ровно одно: чинить это не здесь и не
        # в источнике синхронизации, а у самого мастера — профиль
        # заполняет она. Текст для владелицы салона живёт отдельно, в
        # ``AdminPeopleScreen.tsx``.
        "profile_incomplete": "профиль не заполнен",
        # §83. Чинится не здесь и не в источнике синхронизации: часы
        # подтверждает владелец салона в салонной поверхности.
        "schedule_unconfirmed": "расписание не подтверждено",
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
            # Причины нет — и это факт, а не отсутствие данных. Называем
            # словами, чтобы прочерк не читался как «не посчитали».
            return absent("причин нет, продаётся")
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
        text = f"Верифицировано: {outcome.verified}. Уже принятых пропущено: {outcome.skipped}."
        if outcome.blocked:
            # DRF-1597. Молчать об этих строках нельзя: оператор выбрал их
            # руками и вправе узнать, что действие их НЕ коснулось —
            # иначе «верифицировано: 0» читается как сбой, а не как
            # граница согласия. Причина названа словами, а не кодом:
            # экран читает человек.
            text += (
                f" Не подтверждено — ждут нажатия самого мастера: {outcome.blocked}. "
                "Этим мастерам приглашение реально выписано; принять его "
                "может только сама мастер."
            )
        self.message_user(request, text, level=messages.SUCCESS)

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
class MasterServiceAdmin(AylaAdminMedia, admin.ModelAdmin):
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

    list_display = ("master", "service", "tenant", "origin", "created_at")
    list_filter = ("tenant", "source")
    search_fields = ("master__name", "service__name")
    search_help_text = "Ищет по имени мастера и названию услуги."
    empty_value_display = _ABSENT
    raw_id_fields = ("master", "service", "created_by")
    fieldsets = (
        (
            "Связь",
            {
                "fields": ("master", "service", "tenant"),
                "description": (
                    "Одна строка = «эта мастер делает эту услугу». Именно "
                    "из этих строк собирается выбор мастера у клиента."
                ),
            },
        ),
        (
            "Откуда взялась",
            {
                "fields": ("source", "created_by", "created_by_actor_id", "created_at"),
                "description": (
                    "Происхождение пишет платформа, руками его не набирают: "
                    "иначе рукотворную строку можно было бы переклеить в "
                    "«приехала синхронизацией» и потерять след."
                ),
            },
        ),
        (
            "Служебное",
            {
                "classes": ("collapse",),
                "fields": (
                    "id",
                    "ayla_specialist_service_id",
                    "resolved_requires_health_check",
                    "updated_at",
                ),
            },
        ),
    )

    #: Происхождение связи словами — и код рядом.
    #:
    #: Ключи ровно из :class:`MasterServiceSource`. Тон здесь не «плохо
    #: / хорошо», а «насколько строка объяснима»: приехавшая
    #: синхронизацией объяснима сама собой, рукотворная требует того,
    #: кто её сделал.
    _SOURCE_BADGES: ClassVar[BadgeMap] = {
        MasterServiceSource.CATALOG_SYNC: ("ok", "Синхронизация каталога"),
        MasterServiceSource.MM4_MATRIX: ("ok", "Матрица услуг в приложении"),
        MasterServiceSource.INVITE_SEED: ("off", "Заведена при приглашении"),
        MasterServiceSource.DEV_SEED: ("off", "Тестовые данные"),
        MasterServiceSource.MANUAL_SCRIPT: ("wait", "Скрипт вручную"),
        MasterServiceSource.DJANGO_ADMIN: ("wait", "Заведена в этой админке"),
        MasterServiceSource.ORPHAN_CLEANUP: ("off", "Уборка осиротевших строк"),
        MasterServiceSource.TEST_FIXTURE: ("off", "Фикстура теста"),
    }

    @admin.display(description="Откуда взялась", ordering="source")
    def origin(self, obj: MasterService):  # type: ignore[no-untyped-def]
        if not obj.source:
            # NULL здесь — настоящее отсутствие, а не «прочее»: строка
            # заведена до DRF-975, автор невосстановим (см. help_text
            # поля). Подставить сюда бейдж «неизвестное происхождение»
            # значило бы выдать отсутствие за значение — ровно то, что
            # запрещает OPEN_DECISIONS §65.
            return absent("происхождение не записано")
        tone, label = self._SOURCE_BADGES.get(obj.source, ("off", "Незнакомое происхождение"))
        return badge(tone, label, obj.source)

    # Provenance is written by the platform, never typed by a human -- an
    # editable ``source`` would let an admin relabel a hand-made row as
    # ``catalog_sync`` and undo the whole point.
    # ``id`` добавлен к прежней четвёрке не ради прав, а ради показа:
    # он ``editable=False``, и без явного readonly Django отказывается
    # рисовать его в fieldsets. Ничьих прав это не меняет — поле не было
    # правимым и раньше.
    readonly_fields = ("id", "source", "created_by_actor_id", "created_at", "updated_at")

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
    search_help_text = "Ищет по тексту вопроса и ответа."


@admin.register(CatalogHelpArticle)
class CatalogHelpArticleAdmin(_MirrorAdminBase):
    list_display = ("question", "tenant", "is_active", "order", "synced_at")
    search_fields = ("question", "answer", "external_id")
    search_help_text = "Ищет по заголовку и тексту статьи."
