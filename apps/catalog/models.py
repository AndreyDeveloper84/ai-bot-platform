"""Catalog mirror models (DRF-572 / Sprint 7 / C1).

Read-only mirrors of the canonical `mysite/services_app/` rows. The
catalog-sync service (C-track DRF-573..579) fetches via
``/api/v1/catalog/*`` every 15 min and upserts into these tables. The
KB ingester (K-track DRF-558..568) reads from here to build chunks.

### Why mirror at all?

We could query mysite live on every retrieval. We don't because:

* **Latency**: HTTP round-trip to mysite from the platform process pool
  is 50-200ms per FAQ turn. A local Postgres index is sub-ms.
* **Resilience**: mysite outages must not silently degrade the platform.
  A 24-hour-old mirror is still useful for FAQ retrieval; a 5xx
  pipe-through is not.
* **Schema decoupling**: when mysite reshapes its `Service` table
  (Phase 1 multi-salon), the mirror is the contract surface — only the
  sync adapter (C3) has to change, not every consumer.

### Fields shared by all four mirrors

* ``tenant`` — CASCADE. Catalog mirrors are derived data; tenant
  removal drops them. Re-sync re-populates on first beat.
* ``external_id`` — mysite primary key (IntegerField). Sync stable.
* ``external_updated_at`` — ``Service.updated_at`` (etc.) at last sync.
  Used as the `?since=` cursor for incremental pulls AND as the
  last-writer-wins tiebreak when two beats race (C3 / DRF-574).
* ``synced_at`` — `auto_now=True`. When the platform last touched
  this row. Differs from ``external_updated_at`` (upstream timestamp)
  and from row ``updated_at`` semantics on other apps.

### Sprint 7 scope

Sprint 7 ships **read-only** mirrors — admin write paths are blocked
(see ``apps/catalog/admin.py``). Sprint 8 may add operator-write
overrides for staged content; that's out of scope here.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone

from apps.catalog.master_state import AVAILABLE
from apps.tenancy.managers import TenantScopedManager


class _MirrorBase(models.Model):
    """Shared fields + Meta for every catalog mirror.

    Abstract base — concrete mirrors set their own
    ``verbose_name``/``indexes`` if they need more. Indexes that every
    mirror wants live here.
    """

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False, verbose_name="Идентификатор"
    )
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        help_text="Owning tenant. CASCADE — mirrors are derived from "
        "mysite via catalog sync; tenant delete also drops them.",
        verbose_name="Салон",
    )
    external_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="Legacy mysite integer PK. Nullable since S3B re-keyed "
        "the mirror onto the Ayla stable-id (UUID): Ayla-fed rows leave this "
        "NULL and key on (tenant, ayla_service_id / ayla_user_id). Kept for "
        "legacy rows — unique_together (tenant, external_id) stays NULL-safe.",
        verbose_name="Идентификатор в источнике",
    )
    external_updated_at = models.DateTimeField(
        help_text="Upstream `updated_at` at last sync. Drives the "
        "`?since=` cursor (C2) and last-writer-wins on concurrent beats "
        "(C4 Risk #5).",
        verbose_name="Изменено в источнике",
    )
    synced_at = models.DateTimeField(
        auto_now=True,
        help_text="When the platform last touched this row. Differs "
        "from `external_updated_at` (upstream's timestamp).",
        verbose_name="Синхронизировано",
    )

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        abstract = True


class CatalogService(_MirrorBase):
    """Mirror of `mysite/services_app.Service`.

    Fields preserve mysite shape with `seo_*` collapsed into
    `seo_title`/`seo_description`. M2M relations (`related_services`,
    `options`) intentionally NOT mirrored — Sprint 7 retrieval only
    needs scalar fields.

    ### Ayla event-driven update path (#444)

    ``ayla_service_id`` and ``cache_version`` were added in migration
    ``0006_catalogservice_ayla_service_id_cache_version`` so the
    ``service.updated`` cross-service event consumer in
    ``apps/eventbus/consumers/catalog.py`` can find mirror rows by
    Ayla's canonical UUID and signal cache invalidation to downstream
    readers. ADR-0009 hard rule #1: bot-platform mirror is a
    read-replica, never the source of truth.
    """

    slug = models.SlugField(max_length=100, verbose_name="Код услуги")
    name = models.CharField(max_length=200, verbose_name="Название услуги")
    short_description = models.TextField(blank=True, default="", verbose_name="Краткое описание")
    description = models.TextField(blank=True, default="", verbose_name="Описание")
    price_from = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Цена от, ₽"
    )
    duration_min = models.IntegerField(null=True, blank=True, verbose_name="Длительность, мин")
    is_active = models.BooleanField(default=True, verbose_name="Продаётся")
    is_popular = models.BooleanField(default=False, verbose_name="Популярная")
    seo_title = models.CharField(
        max_length=255, blank=True, default="", verbose_name="SEO-заголовок"
    )
    seo_description = models.TextField(blank=True, default="", verbose_name="SEO-описание")
    goals = models.JSONField(default=list, blank=True, verbose_name="Цели клиента (теги)")
    requires_health_check = models.BooleanField(
        default=False, verbose_name="Требует проверки здоровья"
    )
    contraindications = models.TextField(blank=True, default="", verbose_name="Противопоказания")
    raw = models.JSONField(default=dict, blank=True, verbose_name="Сырой ответ источника")

    # #444 — link to Ayla's canonical Service.id (UUID). Coexists with
    # the legacy mysite integer ``external_id``: mysite-synced rows set
    # external_id + leave this nullable; Ayla-event-fed rows set this
    # + may leave external_id null. Lookup key for the
    # service.updated consumer.
    ayla_service_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Canonical Ayla Service.id (UUID). Lookup key for the "
            "apps/eventbus service.updated consumer. Coexists with "
            "legacy integer external_id while mysite sync still runs; "
            "a separate cleanup PR removes external_id once mysite is "
            "fully retired."
        ),
        verbose_name="Идентификатор услуги в Ayla",
    )

    # #444 — mirror-staleness signal. Bumped on every service.updated
    # event. Future cache layers (Redis, in-memory) include cache_version
    # in their key so a version bump invalidates the old cache
    # transparently. No active cache layer reads this today — it is a
    # forward-compatible signal so the consumer can land before the
    # cache layer ships.
    cache_version = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Mirror-staleness counter, incremented on every "
            "service.updated event. Downstream cache layers include "
            "it in their cache key so increments transparently "
            "invalidate stale entries. No active cache reads it today."
        ),
        verbose_name="Версия кеша",
    )

    class Meta:
        verbose_name = "Услуга каталога"
        verbose_name_plural = "Услуги каталога"
        ordering = ["name"]
        unique_together = (("tenant", "external_id"),)
        constraints = [
            # S3B re-key: Ayla-fed rows are keyed on the stable UUID. Partial
            # (WHERE ayla_service_id IS NOT NULL) so legacy NULL rows are
            # exempt. DB-level backstop over the per-tenant sync lock +
            # update_or_create (ADR-0011 §3.5 — constraints are the unkillable
            # line over app-level convention). Applied while the column is
            # all-NULL → instant validate, zero conflict.
            models.UniqueConstraint(
                fields=["tenant", "ayla_service_id"],
                condition=models.Q(ayla_service_id__isnull=False),
                name="uq_catalog_service_tenant_ayla_service_id",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "-external_updated_at"]),
            models.Index(fields=["tenant", "slug"]),
        ]

    def __str__(self) -> str:
        # Ayla-fed rows have no slug / integer external_id — fall back to the
        # stable UUID + name so admin/log output stays readable.
        label = self.slug or self.name
        key = self.ayla_service_id or self.external_id
        return f"CatalogService[{label}@{key}]"


class _MasterManager(TenantScopedManager):
    """Tenant-scoped + ``bookable()`` filter for customer-facing reads.

    Per master-management handoff §3 line 164 — only ``is_active=True``
    AND ``invite_status='accepted'`` masters are bookable.

    DRF-1506 — the predicate now comes from
    :data:`apps.catalog.master_state.AVAILABLE`, the single definition
    the four other sites also read. That adds ``archived_at IS NULL``,
    which this filter used to omit: a master archived while still
    ``is_active`` stayed on sale. It deliberately does NOT add
    ``linked_bot_user IS NOT NULL`` — see the module docstring there for
    the nine pilot masters that requirement would take off sale.

    DRF-1506 — предикат приезжает из
    :data:`apps.catalog.master_state.AVAILABLE`, единственного
    определения на все места. DRF-1540 добавил в него
    ``ayla_user_id IS NOT NULL``: строка без канонического ключа
    продавалась бы, а уведомления о записи до человека не доходили бы —
    решение владельца 06.09.2026 меняет молчаливый отказ на видимый.
    Условие живёт ТАМ, а не здесь: следующая задача (DRF-1521) добавляет
    своё в то же место, и «почему не продаётся» обязано иметь один ответ
    на витрину и на ростер владелицы.
    """

    def bookable(self):
        return self.filter(AVAILABLE)


class CatalogMaster(_MirrorBase):
    """Master record — originally a mysite mirror, now first-class with
    platform-side state (invite flow, soft-archive, MAX handle).

    See ``docs/design/handoffs/2026-05-18-master-management-handoff.md``.

    Sync (``upserter.upsert_specialists``) overwrites: name, specialization,
    bio, experience, rating, is_active, yclients_staff_id, raw, and — since
    DRF-1588 — address, location_lat, location_lng.
    Platform fields NEVER touched by sync: invite_status, mode,
    photo_url, archived_at, invited_at, accepted_at, max_handle,
    linked_bot_user and — since §83 — the schedule-confirmation trio
    (schedule_confirmed_at, schedule_confirmed_by, schedule_fingerprint).
    That list is why the pilot's nine active masters
    all carry ``linked_bot_user IS NULL`` — they arrived by sync, which
    has no platform side to fill in (DRF-1506).
    """

    class InviteStatus(models.TextChoices):
        PENDING = "pending", "Pending invite"
        ACCEPTED = "accepted", "Accepted"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    class Mode(models.TextChoices):
        INVITE = "invite", "Invite-based access"
        CATALOG_ONLY = "catalog_only", "Catalog only (no login)"

    name = models.CharField(max_length=200, verbose_name="Имя мастера")
    specialization = models.CharField(
        max_length=255, blank=True, default="", verbose_name="Специализация"
    )
    bio = models.TextField(blank=True, default="", verbose_name="О себе")
    experience = models.CharField(max_length=255, blank=True, default="", verbose_name="Опыт")
    # DRF-1535 / DRF-1224 — the rating domain is 1..5. A stored ``0.00`` is
    # therefore not a low rating, it is the ABSENCE of one, and the pilot's
    # nine zero rows are exactly that: no master on the contour has a single
    # review. Readers must treat 0.00 as "no data" — never render it (see
    # ``apps.orchestrator.discovery._render_master_cards``) and never let it
    # push a master down a list.
    rating = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Mirrored from the source feed. Domain is 1..5, so a stored "
            "0.00 means «no rating yet», not «rated zero» — it is never "
            "rendered (DRF-1224) and takes no part in discovery ordering "
            "(DRF-1535)."
        ),
        verbose_name="Рейтинг",
    )
    # DRF-1535 — this help_text used to promise a discovery Bayesian
    # trust-score (#1060) «so a 5.0 from 1 review can't outrank a 4.8 from
    # 200». That score was never written, and nothing outside tests has ever
    # read this column. A docstring describing code that does not exist is
    # worse than no docstring: it is read as a guarantee, and the next author
    # builds on it. The promise is gone; the field stays, because sync fills
    # it and removing it would drop data we will want when reviews land.
    review_count = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Number of reviews backing ``rating``, mirrored from Ayla's "
            "``reviews_count``. Written by catalog sync "
            "(``catalog/services/upserter._master_fields``) and read by "
            "nobody: discovery neither ranks nor filters on it, and there is "
            "no trust-score behind it. Collecting reviews is DRF-1527; until "
            "that lands this is 0 on every pilot row."
        ),
        verbose_name="Отзывов",
    )
    is_active = models.BooleanField(default=True, verbose_name="Активна по данным синхронизации")
    yclients_staff_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="YClients staff id — pre-populated from mysite so the "
        "Phase 1 booking flow can dispatch without a second lookup.",
        verbose_name="Идентификатор в YClients",
    )
    ayla_user_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Canonical Ayla user_id (UUID) for this master. Bridge for "
            "event-payload master_user_id → CatalogMaster ORM JOIN. "
            "Nullable because legacy mysite-synced rows lack this — "
            "back-filled by catalog-sync service or master event consumer."
        ),
        verbose_name="Идентификатор пользователя в Ayla",
    )
    raw = models.JSONField(default=dict, blank=True, verbose_name="Сырой ответ источника")

    # DRF-1588 — адрес и координаты мастера. Данные приезжали и раньше, но
    # только внутрь ``raw``: замер на пилоте 08.09.2026 нашёл гео-ключи в
    # слепке у 31 строки из 34, а колонок под них не было ни одной. Пока
    # значение живёт в JSON, по нему нельзя ни искать, ни фильтровать, ни
    # сортировать — данные есть, доступа к ним нет. Эти три колонки и есть
    # доступ; читателя они себе не назначают.
    #
    # Источник — ``users_specialistprofile.address / location_lat /
    # location_lng`` в самой Ayla (OPEN_DECISIONS §45): адрес физически
    # хранится у СПЕЦИАЛИСТА, а не у салона. Правило старшинства «салон
    # против мастера» — DRF-1589, и здесь его нет.
    #
    # ``NULL`` против ``""`` — разница смысловая, и она единственное, что
    # стоит между нами и повтором дефекта §65:
    #
    # * ``address is None`` — ключа ``address`` в слепке НЕ БЫЛО. Мы не
    #   знаем. Три пилотные строки из 34 именно такие.
    # * ``address == ""``   — ключ был и нёс пустую строку. Источник
    #   ответил «адреса нет». Это ответ, а не молчание.
    #
    # Координаты по той же причине ``null=True`` и НИКОГДА не ``0.0``:
    # на пилоте они ``None`` у всех 31 строки, а нулевая пара — это точка
    # в Гвинейском заливе, которая выглядит как настоящее значение и
    # уедет на карту как настоящее. Отсутствие координаты обязано остаться
    # отсутствием.
    address = models.CharField(
        max_length=500,
        null=True,
        blank=True,
        default=None,
        help_text=(
            "Адрес мастера, зеркалится из ключа ``address`` фида "
            "специалистов. NULL — ключа в слепке не было (не знаем); "
            '"" — ключ был и нёс пустое (источник ответил «нет»). '
            "Разница обязательна: подставленное значение неотличимо от "
            "настоящего."
        ),
        verbose_name="Адрес приёма",
    )
    location_lat = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        default=None,
        help_text=(
            "Широта из ключа ``location_lat``. NULL — координаты нет "
            "(ключ отсутствовал либо нёс null). НИКОГДА не 0.0: пара "
            "нулей — точка в Гвинейском заливе, неотличимая от настоящей."
        ),
        verbose_name="Широта",
    )
    location_lng = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        default=None,
        help_text=(
            "Долгота из ключа ``location_lng``. NULL — координаты нет "
            "(ключ отсутствовал либо нёс null). НИКОГДА не 0.0 — см. "
            "``location_lat``."
        ),
        verbose_name="Долгота",
    )

    # #445 — slot cache staleness counter. Bumped on every
    # master.schedule.updated event. Forward-compatible signal for
    # downstream slot cache layers (apps/scheduling has no active
    # cache today); incrementing this invalidates cache keys
    # transparently once a cache layer reads it. Same pattern as
    # CatalogService.cache_version (#444).
    cache_version = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Slot-cache staleness counter, incremented on every "
            "master.schedule.updated event. Future cache layers "
            "(Redis, in-memory) include this in their key so "
            "increments transparently invalidate stale slot lookups. "
            "No active cache layer reads this today — it's a "
            "forward-compatible signal."
        ),
        verbose_name="Версия кеша",
    )

    invite_status = models.CharField(
        max_length=16,
        choices=InviteStatus.choices,
        default=InviteStatus.PENDING,
        db_index=True,
        help_text=(
            "Default PENDING (DRF-1496): a master born by sync was never "
            "invited, so she is not bookable until an operator verifies "
            "her by hand (journaled admin action). The pre-DRF-1496 "
            "default was ACCEPTED; rows that existed on 04.09.2026 were "
            "grandfathered as ACCEPTED by migration 0017 so the pilot's "
            "booking pick-list did not silently collapse. Invite "
            "create-path writes PENDING explicitly, as before."
        ),
        verbose_name="Состояние приглашения",
    )
    mode = models.CharField(
        max_length=16,
        choices=Mode.choices,
        default=Mode.CATALOG_ONLY,
        verbose_name="Режим работы с ботом",
    )
    photo_url = models.URLField(
        max_length=500, blank=True, default="", verbose_name="Ссылка на фото"
    )
    archived_at = models.DateTimeField(null=True, blank=True, verbose_name="В архиве с")
    archive_reason = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Owner-provided rationale for the most recent deactivation "
            "(MM5 Step 3, «Причина»). Persisted as a forensic note next "
            "to the AuditLog row; cleared on reactivate so the next "
            "deactivation starts fresh."
        ),
        verbose_name="Причина архива",
    )
    invited_at = models.DateTimeField(null=True, blank=True, verbose_name="Приглашение выписано")
    accepted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Момент, когда мастер впервые оказалась связанной с BotUser "
            "и принявшей приглашение — состояние M1 из "
            "docs/design/policies/master-onboarding-m0-m7.md §4.1. "
            "Ставит его ``save()`` (см. ниже), поэтому забыть его на "
            "новом пути приземления нельзя. Не стирается: это дата "
            "события, а не флаг. NULL у синхронизированных мастеров — "
            "они в бот не приземлялись."
        ),
        verbose_name="Приглашение принято",
    )
    max_handle = models.CharField(max_length=64, blank=True, default="", verbose_name="Ник в MAX")

    # M0 onboarding (master mobile handoff §M0 + master-management MM2).
    # invite_token: opaque UUID emitted by MM2 POST /api/v1/masters/invite,
    # carried in the Mini App deeplink as ?token=<uuid>. UUID format (not
    # HMAC) per the MM2 response contract; cleared on accept (one-shot).
    # invite_expires_at: invited_at + 7d; checked on every onboarding/*
    # endpoint. After expiry, the row stays PENDING but the token can no
    # longer be claimed — operator must re-issue.
    # linked_bot_user: OneToOne to BotUser. SET_NULL on BotUser delete so
    # the master row survives (audit trail). A master has exactly one
    # MAX/Telegram identity in Phase 1; multi-device is later.
    invite_token = models.UUIDField(
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text="One-shot opaque token from MM2 invite-create. Cleared "
        "on accept; uniqueness enforced so a stale token can't collide.",
        verbose_name="Одноразовый ключ приглашения",
    )
    invite_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="invited_at + 7d (Q-MM2). Past-expiry tokens 410.",
        verbose_name="Приглашение действует до",
    )
    linked_bot_user = models.OneToOneField(
        "identity.BotUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="master_identity",
        help_text="MAX/Telegram BotUser this master signs in with. "
        "SET_NULL on BotUser delete preserves the master audit trail.",
        verbose_name="Связанный аккаунт в мессенджере",
    )

    # ── подтверждение расписания (§83, DRF-1521 п. 6) ─────────────────────
    #
    # Три столбца, а не булево, и живут они ЗДЕСЬ, а не на
    # ``scheduling.WorkingHours``, — решение владельца от 09.09.2026:
    # подтверждение это «состояние допуска мастера к продаже», а не
    # свойство часов. Часы говорят, когда мастер работает; подтверждение
    # говорит, что этого мастера можно показывать клиентам.
    #
    # Столбцы платформенные: синхронизация их не трогает — она пишет
    # только перечисленный список зеркальных полей через ``update_fields``
    # (``apps/catalog/services/upserter.py``). Тот же класс, что
    # ``invite_status`` и ``accepted_at``.
    #
    # Умолчание — НЕ подтверждено, и заполнять их миграцией запрещено
    # (правило 6 решения): массовая простановка выглядит как «включили
    # функцию», а означает «объявили проверенным непроверенное».
    schedule_confirmed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Когда владелец салона подтвердил рабочие часы этого "
        "мастера. NULL — не подтверждены; по умолчанию так у всех.",
        verbose_name="Расписание подтверждено",
    )
    schedule_confirmed_by = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Кто подтвердил. SET_NULL сохраняет след подтверждения, "
        "даже если аккаунт удалён: «подтверждено» и «известно кем» — "
        "разные утверждения, и первое не должно исчезать со вторым.",
        verbose_name="Кем подтверждено",
    )
    schedule_fingerprint = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Отпечаток ИМЕННО ТЕХ часов, которые подтвердили. Без "
        "него «подтверждено» означает «кто-то когда-то нажал»: нечем "
        "отличить подтверждённую версию часов от нынешней. Формат и "
        "состав — apps/catalog/services/schedule_confirmation.py.",
        verbose_name="Отпечаток подтверждённых часов",
    )

    objects = _MasterManager()  # type: ignore[misc]
    all_tenants = models.Manager()  # type: ignore[misc]

    class Meta:
        verbose_name = "Мастер"
        verbose_name_plural = "Мастера"
        ordering = ["name"]
        unique_together = (("tenant", "external_id"),)
        indexes = [
            models.Index(fields=["tenant", "-external_updated_at"]),
            models.Index(fields=["tenant", "yclients_staff_id"]),
            models.Index(fields=["tenant", "is_active", "invite_status"]),
        ]
        # DRF-1507 — ключ склейки. До него единственным ограничением было
        # ``unique_together (tenant, external_id)``, которое не покрывает ни
        # ``ayla_user_id``, ни ``max_handle``: приглашение заводит строку с
        # ``uuid4`` первичным ключом и синтетическим ``external_id``, а
        # синхронизация — вторую, с канонической ``SpecialistProfile.id``.
        # Две строки на одного человека — это ``resolve_master``
        # (``apps/booking/master_notify.py``) ищет по ``id`` ИЛИ
        # ``ayla_user_id`` и не находит инвайт-строку с ``ayla_user_id=NULL``:
        # мастер не получает ни одного уведомления о записи.
        #
        # Частичное, а не ``unique_together``: столбец пуст у каждой
        # инвайт-строки, и наивная уникальность столкнула бы NULL с NULL и
        # запретила бы второго приглашённого мастера в салоне. В Postgres
        # NULL и так не конфликтуют, но условие оставлено явным: оно
        # читается как правило, а не как побочный эффект трёхзначной логики.
        #
        # ``(tenant, max_handle)`` здесь НЕТ, и после DRF-1507 п.3-4 это
        # уже не «файл занят», а решение. Причина, по которой ключ не
        # добавлен, сменилась, и старую формулировку («ограничение уедет к
        # тому, кто починит писателя») читать больше нельзя: писателя
        # починили — ``master_invite_create`` на протухшем приглашении
        # перевыпускает токен на существующей строке вместо второй, а
        # ``onboarding_accept`` доносит handle на склеенную строку. Вторая
        # строка с тем же handle этими путями больше не заводится.
        #
        # Ограничение всё равно не поставлено, и вот почему:
        #
        # 1. Столбец хранит свободный текст, набранный человеком.
        #    Уникальность по сырому значению слишком СЛАБА для своей цели —
        #    ``@anna`` и ``anna`` её не нарушают, а это один аккаунт MAX, —
        #    и одновременно слишком СТРОГА для живых данных: что именно
        #    лежит в столбце у пяти салонов, приехавших DRF-1510, отсюда
        #    не проверяется, а миграция, упавшая на выкладке за день до
        #    запуска, стоит дороже дубля, который путь больше не создаёт.
        # 2. Гарантию держит писатель, и она сильнее индекса: сравнение
        #    идёт по ``apps/catalog/handles.normalize_handle``, то есть без
        #    учёта регистра и ведущей ``@``. Индекс по сырому столбцу этого
        #    бы не дал.
        # 3. Приземление теперь ПИШЕТ ``max_handle`` на строку склейки, а
        #    погашенная инвайт-строка своё значение сохраняет как след.
        #    С ограничением пришлось бы стирать handle у погашенной строки
        #    ради того, чтобы вставка прошла, — то есть менять поведение
        #    приземления в угоду индексу и получить ровно тот 500 на
        #    основном пути, ради которого задача существует.
        #
        # Миграция ``0016_master_dedup_keys`` по-прежнему ЛОГИРУЕТ дубли по
        # ``max_handle``: они не должны стать невидимыми оттого, что ключа
        # нет. Если лог на боевых данных покажет дубли, которых писатель
        # больше не создаёт, — это существующие дубли, и их слияние
        # отдельный обоснованный шаг с замером, а не побочный эффект.
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "ayla_user_id"],
                condition=models.Q(ayla_user_id__isnull=False),
                name="uq_catalog_master_tenant_ayla_user_id",
            ),
        ]

    def save(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        """Штампует ``accepted_at`` в момент приземления. Один раз.

        DRF-1506. Приземление пишут три места — ``onboarding_accept``
        (принятие инвайта в Mini App), ``staff_invites._link_master``
        (код доступа) и ``solo_onboarding`` (соло-провайдер, DRF-1507).
        Просить каждое из них не забыть про столбец — это ровно тот
        способ, которым определений стало пять. Здесь модель отвечает
        за свой собственный инвариант, и четвёртый путь приземления
        получит его даром.

        Не стирается при обратном переходе: ``accepted_at`` отвечает
        «когда она приняла», а не «принята ли сейчас» — на второй
        вопрос отвечает ``invite_status``.

        ``update_fields`` дополняется, а не игнорируется: вызывающие
        пишут узкие списки полей (``onboarding_accept`` перечисляет
        пять), и без этого штамп молча не доехал бы до базы.
        """

        if (
            self.accepted_at is None
            and self.linked_bot_user_id is not None
            and self.invite_status == self.InviteStatus.ACCEPTED
        ):
            self.accepted_at = timezone.now()
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = list(update_fields) + ["accepted_at"]
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"CatalogMaster[{self.name}@{self.external_id}]"


class MasterServiceQuerySet(models.QuerySet):
    """QuerySet that closes the one hole ``pre_save`` cannot see.

    ``bulk_create`` does not send ``pre_save``/``post_save`` — Django says so
    explicitly. So the signal in :mod:`apps.catalog.signals` covers ``.save()``,
    ``.create()``, ``.get_or_create()``, ``.update_or_create()`` and
    ``loaddata``, and this override covers the remaining one. That matters
    here and not in theory: the invite seeder (``views_invite._seed_services``)
    is a ``bulk_create``, and a bulk insert is the exact shape of the
    2026-07-22 incident.

    Both managers below are built from this class, so ``.objects`` and
    ``.all_tenants`` are equally covered — an escape hatch that skipped the
    check would be the first thing found and used.
    """

    def bulk_create(self, objs, *args, **kwargs):  # type: ignore[no-untyped-def]
        from apps.catalog.provenance import require_master_service_write
        from apps.catalog.signals import audit_master_service_created, stamp_provenance

        objs = list(objs)
        if not objs:
            # Nothing is being written, so nothing needs an author. Refusing
            # here would make "clear the list and call anyway" crash callers
            # for no forensic gain.
            return super().bulk_create(objs, *args, **kwargs)

        ctx = require_master_service_write("bulk_create")
        for obj in objs:
            stamp_provenance(obj, ctx)
        created = super().bulk_create(objs, *args, **kwargs)
        for obj in created:
            audit_master_service_created(obj, ctx)
        return created


class MasterService(models.Model):
    """Master ↔ Service M2M (which services this master performs).

    Per master-management handoff §MM4 — admin maintains via matrix UI.
    Customer booking endpoints MUST check this mapping before assigning
    ``BookingRequest.master_id``.

    ### Dual ownership (DRF-945 / P1 service discovery)

    This table has **two writers** and they must not fight:

    * **Operator** — the MM4 matrix (``apps/admin_api/views_services_mapping.py``)
      and the invite seeder (``views_invite.py``). Rows they create leave
      ``ayla_specialist_service_id`` NULL.
    * **Catalog sync** — mirrors Ayla's canonical ``SpecialistService`` edge
      (``GET /internal/catalog/specialist-services/``). Rows it owns carry a
      non-NULL ``ayla_specialist_service_id``.

    Sync only ever touches rows it created, so an operator-maintained mapping
    can never be removed by a sync beat. Operator rows are deliberately NOT
    adopted — adoption would hand an operator-authored row to reconciliation.

    One deliberate exception to "non-NULL ⇒ an Ayla id" (DRF-967): the dev
    fixture command ``seed_dev_formula_tela`` stamps its own edges with
    synthetic uuid5 values from its ``SEED_EDGE_NAMESPACE``, precisely so sync
    owns and can later reconcile them — an unstamped fixture row is immortal,
    which is how a pilot tenant ended up with 232 unreconcilable edges. On a
    tenant that does sync, such a stamp is replaced by the real edge id on the
    first beat that publishes the pair. Note the consequence for repair work:
    ``cleanup_orphan_master_services`` targets NULL rows only, so seeded rows on
    a dev tenant where sync never runs are outside its reach by design.

    **Row existence is the contract.** Every reader — booking create and
    reschedule, slot serving, the miniapp/master catalogs, the MM4 matrix —
    treats the presence of a row as "this master performs this service", and
    none of them filters a status column. So there is no ``is_active`` flag
    here on purpose: an edge upstream marks inactive leaves no row, and a
    vanished edge is deleted. A tombstone would read as "offered" everywhere
    and would make a non-bookable service bookable. Delete also matches the
    table's existing lifecycle — the MM4 matrix deletes the row when an
    operator unchecks a cell.
    """

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False, verbose_name="Идентификатор"
    )
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="master_services",
        verbose_name="Салон",
    )
    master = models.ForeignKey(
        "catalog.CatalogMaster",
        on_delete=models.CASCADE,
        related_name="services_offered",
        verbose_name="Мастер",
    )
    service = models.ForeignKey(
        "catalog.CatalogService",
        on_delete=models.CASCADE,
        related_name="masters_offering",
        verbose_name="Услуга",
    )
    # DEAD SINCE 0002 (DRF-975 finding). No writer has ever populated this —
    # not the MM4 matrix, not the invite seeder, not sync, not the dev seed.
    # It is also the wrong type: actors in this platform are
    # ``identity.BotUser``, not ``auth.User``, so it could not have held the
    # MM4 operator even if someone had tried. Its permanent NULL is why the
    # 232 pilot rows looked authorless — they are not special, *every* row is
    # NULL here. ``created_by_actor_id`` below is the field that actually
    # carries the who. Left in place rather than dropped: removing a column is
    # a separate, independently reviewable migration.
    created_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Кем заведена связь",
    )

    # DRF-975 — mandatory write provenance. Both columns are stamped by the
    # ``pre_save`` signal / ``bulk_create`` override from the
    # ``apps.catalog.provenance`` context; neither is a caller kwarg.
    #
    # NULLABLE, and the NULL is load-bearing exactly like
    # ``resolved_requires_health_check`` above: NULL means "written before
    # this column existed — author unrecoverable". Every row created after the
    # migration is non-NULL by construction, because the write is refused
    # otherwise. Backfilling the pre-existing NULLs to some sentinel is a
    # product decision about the 232 pilot rows, NOT a schema decision, and is
    # deliberately not made here (see DRF-975 report / DRF-967).
    #
    # Why store it on the row at all when AuditLog already has it: AuditLog is
    # swept at ``AUDIT_LOG_RETENTION_DAYS`` (default 90). An edge outlives its
    # audit row by years. After the sweep, this column is the only surviving
    # answer to "who created this relation".
    source = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Which writer created this edge (apps.catalog.provenance."
            "MasterServiceSource). NULL = created before DRF-975 shipped; "
            "author unrecoverable."
        ),
        verbose_name="Откуда взялась связь",
    )
    created_by_actor_id = models.UUIDField(
        null=True,
        blank=True,
        help_text=(
            "identity.BotUser.id of the human who caused this edge, when there "
            "was one. NULL for machine writers (catalog sync) and for rows "
            "predating DRF-975. Not an FK on purpose: this is a forensic "
            "stamp that must survive the BotUser row being deleted."
        ),
        verbose_name="Автор (идентификатор в Ayla)",
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Заведена")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Изменена")

    # DRF-945 — provenance + Ayla's canonical bookable-edge id
    # (``SpecialistService.id``). NULL ⇒ operator-owned (MM4 matrix / invite
    # seeder); non-NULL ⇒ catalog-sync owns this row and may reconcile it.
    # This is the ONLY discriminator sync uses to decide what it may touch.
    ayla_specialist_service_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Canonical Ayla SpecialistService.id (UUID) — the bookable "
            "master↔service edge. NULL means the row was created by an "
            "operator (MM4 matrix / invite seeding) and catalog sync must "
            "never reconcile it away."
        ),
        verbose_name="Идентификатор связи в Ayla",
    )

    # DRF-1353 — the RESOLVED (master×service) health-check flag, mirrored
    # from Ayla's ``SpecialistService.resolved_requires_health_check``
    # (escalate-only OR across template floor → salon service → specialist,
    # Ayla ``services/models.py``). This is the source #1034/#1121 called
    # missing and the booking gate's fail-closed stub was standing in for.
    #
    # TRI-STATE, and the NULL is load-bearing:
    #   True  → this master×service needs a human health check → gate closed.
    #   False → Ayla explicitly says no screening → gate open.
    #   NULL  → never synced (operator-owned MM4 row, pre-migration row, or an
    #           upstream that does not send the key) → UNKNOWN → gate stays
    #           closed, exactly as before this column existed.
    # A non-null default would have made every pre-existing row read as
    # "no screening needed" the moment the migration ran — a fail-OPEN
    # backfill of a medical gate. Hence null=True with no default.
    resolved_requires_health_check = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        help_text=(
            "Mirrored Ayla SpecialistService.resolved_requires_health_check. "
            "NULL = unknown (never synced); the booking health-check gate "
            "treats NULL as 'screening required'."
        ),
        verbose_name="Требует проверки здоровья (итог)",
    )

    # DRF-975 — both managers carry the provenance-checking ``bulk_create``.
    objects = TenantScopedManager.from_queryset(MasterServiceQuerySet)()  # type: ignore[misc]
    all_tenants = models.Manager.from_queryset(MasterServiceQuerySet)()  # type: ignore[misc]

    class Meta:
        verbose_name = "Связь мастера с услугой"
        verbose_name_plural = "Связи мастеров с услугами"
        ordering = ["master_id", "service_id"]
        unique_together = (("master", "service"),)
        constraints = [
            # One local row per Ayla edge, per tenant. Partial (WHERE NOT
            # NULL) so operator rows — which are all NULL — are exempt. Same
            # pattern as uq_catalog_service_tenant_ayla_service_id; applied
            # while the column is all-NULL → instant validate, zero conflict.
            models.UniqueConstraint(
                fields=["tenant", "ayla_specialist_service_id"],
                condition=models.Q(ayla_specialist_service_id__isnull=False),
                name="uq_master_service_tenant_ayla_specialist_service_id",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "master"]),
            models.Index(fields=["tenant", "service"]),
        ]

    def __str__(self) -> str:
        return f"MasterService[{self.master_id} → {self.service_id}]"


class CatalogFaq(_MirrorBase):
    """Mirror of `mysite/services_app.FAQ`."""

    question = models.CharField(max_length=500, verbose_name="Вопрос")
    answer = models.TextField(verbose_name="Ответ")
    category_slug = models.SlugField(
        max_length=100, blank=True, default="", verbose_name="Код раздела"
    )
    raw = models.JSONField(default=dict, blank=True, verbose_name="Сырой ответ источника")

    class Meta:
        verbose_name = "Вопрос и ответ"
        verbose_name_plural = "Вопросы и ответы"
        ordering = ["question"]
        unique_together = (("tenant", "external_id"),)
        indexes = [
            models.Index(fields=["tenant", "-external_updated_at"]),
            models.Index(fields=["tenant", "category_slug"]),
        ]

    def __str__(self) -> str:
        return f"CatalogFaq[{self.question[:40]}@{self.external_id}]"


class CatalogHelpArticle(_MirrorBase):
    """Mirror of `mysite/services_app.HelpArticle`.

    Bot-side help articles (KB-style — "как записаться", "что взять с
    собой", "цены"). Different from `CatalogFaq` which is the per-service
    FAQ on the public site.
    """

    question = models.CharField(max_length=500, verbose_name="Заголовок")
    answer = models.TextField(verbose_name="Текст")
    order = models.IntegerField(default=0, verbose_name="Порядок")
    is_active = models.BooleanField(default=True, verbose_name="Показывается")
    raw = models.JSONField(default=dict, blank=True, verbose_name="Сырой ответ источника")

    class Meta:
        verbose_name = "Статья справки"
        verbose_name_plural = "Статьи справки"
        ordering = ["order", "question"]
        unique_together = (("tenant", "external_id"),)
        indexes = [
            models.Index(fields=["tenant", "-external_updated_at"]),
            models.Index(fields=["tenant", "is_active", "order"]),
        ]

    def __str__(self) -> str:
        return f"CatalogHelpArticle[{self.question[:40]}@{self.external_id}]"
