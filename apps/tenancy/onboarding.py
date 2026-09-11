"""Подключение салона одним путём, с проверкой исхода (DRF-1525).

До этой задачи салон подключали ручным вызовом
``apps/tenancy/management/commands/create_tenant.py``, и на этом всё:
никто не проверял, что за строкой последовали услуги, мастера, город и
бронируемость. Ошибка в любом из трёх шагов давала тихий отказ:

* неверный ``--id`` — синхронизация ходит в Ayla с ``?tenant=<UUID>``,
  поэтому строка со случайным ключом зеркалит **ноль строк**, а прогон
  рапортует «обработано 5, ошибок 0, создано 0»;
* пустой ``--city`` — салона нет ни в одном городском ответе;
* пропущенная синхронизация — строка есть, каталога нет.

Здесь один путь (:func:`connect_salon`), который делает все шаги и
**проверяет исход**: салон считается подключённым тогда, когда он виден
клиенту, а не когда появилась строка в таблице (тот же критерий, что в
DRF-1511).

### Закрытый перечень причин невидимости

Перечень ведёт DRF-1511; этот модуль — окно в него, а не второй список.
Карточка одного салона отвечает на вопрос «почему его не видно»
названной причиной из :data:`REASON_LABELS`. Новая причина добавляется
в перечень явно — неучтённого «просто не видно» быть не должно.

Одна строка перечня DRF-1511 здесь не проверяется по определению: «нет
строки Tenant в боте» — у карточки салона строка всегда есть. «Синхро-
низация падает» читается из ``last_catalog_sync_ok_at``: NULL («ни разу
не проходила») — названная причина; устаревание и аларм — это часы
здоровья DRF-1494 и сводка DRF-1500, не эта карточка.

У NULL есть подслучай, который локальными полями от него неотличим:
идентификатор салона не существует в Ayla, и тогда синхронизация вернёт
ноль не «пока», а всегда. Различает их :func:`_never_synced_reason` —
запросом к Ayla (:func:`probe_backend`). С DRF-1525 (11.09.2026) сам
:func:`connect_salon` идентификатор не проверяет, а ПОЛУЧАЕТ из Ayla по
slug: человек UUID не вводит и не видит, и класс «случайный ключ» на
этом пути закрыт по построению; подслучай остаётся у строк, заведённых
мимо экрана.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.core.exceptions import ValidationError

from apps.catalog.master_state import AWAITING_VERIFICATION
from apps.catalog.models import CatalogMaster, CatalogService
from apps.catalog.services.verification import VerificationOutcome
from apps.catalog.services.verification import verify_masters as _verify_masters
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Закрытый перечень причин невидимости (DRF-1511)
# ---------------------------------------------------------------------------

REASON_TENANT_INACTIVE = "tenant_inactive"
REASON_CITY_MISSING = "city_missing"
REASON_NEVER_SYNCED = "never_synced"
REASON_ID_NOT_IN_AYLA = "id_not_in_ayla"
REASON_NO_ACTIVE_SERVICES = "no_active_services"
REASON_NO_BOOKABLE_MASTERS = "no_bookable_masters"
REASON_MASTERS_AWAIT_VERIFICATION = "masters_await_verification"

#: Код причины → объяснение для экрана. Ключи этого словаря и есть
#: закрытый перечень: причина, которой здесь нет, не может быть показана.
REASON_LABELS: dict[str, str] = {
    REASON_TENANT_INACTIVE: "салон выключен (is_active=False) — скрыт из всех выдач",
    REASON_CITY_MISSING: "не задан город — городской поиск салона не найдёт",
    REASON_NEVER_SYNCED: "синхронизация каталога ни разу не проходила — витрины нет",
    # Подслучай «ни разу не проходила», а не второй код на всякий
    # случай. Замер 09.09.2026: салон `testovuy-salin`, заведённый
    # штатной формой Django, получил СЛУЧАЙНЫЙ первичный ключ — такого
    # UUID в Ayla нет. Прежняя подпись говорила ему «синхронизация ни
    # разу не проходила», и это читалось как «запустите синхронизацию».
    # Правда другая: она вернёт ноль и завтра, и через год. Первичный
    # ключ строки не меняется, поэтому названо и то, что с этим делать —
    # иначе причина сообщала бы о безвыходности и молчала о выходе.
    REASON_ID_NOT_IN_AYLA: (
        "идентификатора салона нет в Ayla — по нему синхронизация вернёт ноль строк "
        "и сегодня, и всегда; первичный ключ строки не меняется, поэтому салон "
        "нужно удалить и подключить заново через экран «Подключить салон» — "
        "UUID он возьмёт из Ayla сам"
    ),
    REASON_NO_ACTIVE_SERVICES: "нет активных услуг — записываться не на что",
    REASON_NO_BOOKABLE_MASTERS: (
        "нет бронируемых мастеров (is_active, не в архиве, приглашение принято) — "
        "в поиске салон не появляется вовсе; это безопасно, но не сбой"
    ),
    # Формулировка владельца (OPEN_DECISIONS §51.1) плюс «всё остальное
    # в порядке» — половина, которая и отличает подслучай от архива и
    # снятой активности. Обещание «салон появится в поиске» сюда НЕ
    # входит: его печатает экран рядом с кнопкой, и второй раз в причине
    # оно читалось бы заиканием. Причину видит и карточка салона, где
    # кнопки нет и обещать нечего.
    REASON_MASTERS_AWAIT_VERIFICATION: (
        "мастера приехали синхронизацией и ждут верификации — приглашения "
        "им не отправлялись; всё остальное у них в порядке"
    ),
}


class ConnectError(RuntimeError):
    """Отказ подключения с объяснением — вместо тихого «успеха»."""


#: Причины ``SETUP_PENDING`` экрана подключения (DRF-1525). Два кода на
#: «токен не настроен», потому что чинятся они в РАЗНЫХ контейнерах:
#: первый — в окружении бота, второй — в окружении каталога (или значения
#: не совпадают). Одно имя на двоих отправило бы оператора искать не там.
PENDING_PROVISIONING_TOKEN_MISSING = "provisioning_token_missing"
PENDING_PROVISIONING_REFUSED = "provisioning_refused"

PENDING_LABELS: dict[str, str] = {
    PENDING_PROVISIONING_TOKEN_MISSING: (
        "в окружении бота не задан AYLA_IDENTITY_PROVISIONING_TOKEN — без него "
        "бот не может завести салон в Ayla; задайте его (значение должно "
        "совпадать с каталогом и отличаться от AYLA_INTERNAL_API_TOKEN) и "
        "нажмите «Подключить» ещё раз"
    ),
    PENDING_PROVISIONING_REFUSED: (
        "Ayla отвергла провижининг-токен бота (HTTP 403): на стороне каталога "
        "AYLA_IDENTITY_PROVISIONING_TOKEN пуст или не совпадает с нашим; "
        "приведите значения к одному и нажмите «Подключить» ещё раз"
    ),
}


class ConnectPending(Exception):
    """Подключение не состоялось, потому что контур не донастроен.

    Не :class:`ConnectError`: тот — отказ по данным (slug занят, Ayla не
    знает салона), этот — ``SETUP_PENDING`` по решению владельца (§11
    свода: «отсутствие токена должно давать понятный SETUP_PENDING, а не
    ложный успех»). Строка бота не создаётся: без UUID из каталога её не
    из чего создать, а случайный ключ запрещает DRF-1510.
    """

    def __init__(self, blocked_by: str) -> None:
        if blocked_by not in PENDING_LABELS:
            raise ValueError(f"unknown pending reason {blocked_by!r}")
        super().__init__(PENDING_LABELS[blocked_by])
        self.blocked_by = blocked_by


@dataclass(frozen=True)
class SalonAssessment:
    """Состояние одного салона глазами клиента."""

    tenant: Tenant
    last_sync_ok_at: datetime | None
    active_services: int
    bookable_masters: int
    reasons: tuple[str, ...]
    #: Всего строк мастеров у салона — знаменатель к «бронируемых N из M».
    #:
    #: DRF-1553. Одно число («бронируемых 0») не говорит ничего: ноль из
    #: нуля — это салон без мастеров, ноль из девяти — девять мастеров,
    #: которых что-то держит. Тот же принцип, что в сводке DRF-1500.
    masters_total: int = 0
    #: Сколько мастеров стали бы бронируемыми, если их верифицировать.
    #:
    #: Считается по :data:`apps.catalog.master_state.AWAITING_VERIFICATION`
    #: — это ``AVAILABLE`` с перевёрнутым приглашением, поэтому число
    #: одновременно и подпись кнопки, и обещание: столько мастеров
    #: прибавится к бронируемым. Мастер в архиве или с ``is_active=False``
    #: сюда не входит: верификация его бронируемым не сделает.
    masters_awaiting_verification: int = 0

    @property
    def is_visible(self) -> bool:
        """Виден ли салон клиенту: ни одной названной причины невидимости."""
        return not self.reasons

    @property
    def can_verify_masters(self) -> bool:
        """Есть ли на экране что предложить кнопкой (DRF-1553).

        Не «есть ли непринятые приглашения», а «названа ли причина
        ожидания верификации»: у салона, невидимого по другой причине,
        верификация ничего не решает, и кнопка обещала бы исход, которого
        не будет.
        """
        return REASON_MASTERS_AWAIT_VERIFICATION in self.reasons


@dataclass(frozen=True)
class BackendProbe:
    """Что отвечает Ayla по идентификатору тенанта до сохранения строки."""

    services: int
    specialists: int

    @property
    def found_anything(self) -> bool:
        return self.services > 0 or self.specialists > 0


@dataclass(frozen=True)
class ConnectResult:
    """Итог подключения: строка, прогон синхронизации и проверка исхода.

    ``created_in_ayla`` — салон заведён в каталоге этим же нажатием (201)
    или уже был там (200). Экран говорит человеку разное: у только что
    заведённого салона мастеров нет по определению, и «нет бронируемых
    мастеров» для него — не тревога, а следующий шаг.
    """

    tenant: Tenant
    created_in_ayla: bool
    sync_error: str | None
    sync_skipped: bool
    assessment: SalonAssessment


# ---------------------------------------------------------------------------
# Проверка исхода
# ---------------------------------------------------------------------------


def _never_synced_reason(tenant: Tenant, *, http_client: Any | None) -> str:
    """Который из двух: «ещё не синхронизировалась» или «UUID мёртвый».

    Различить их локальными полями нельзя: у обоих
    ``last_catalog_sync_ok_at IS NULL``, и оба молчат одинаково. Разница
    живёт в Ayla, поэтому спрашивают Ayla — той же проверкой, что
    :func:`connect_salon` делает ДО сохранения строки
    (:func:`probe_backend`). Мёртвый салон 09.09.2026 отличается от
    здорового только этим ответом.

    Цена запроса — та же, что у проверки при подключении: две выборки
    по одному салону. Он уходит только при ``last_catalog_sync_ok_at IS
    NULL``, то есть у салона без витрины; у мёртвого идентификатора обе
    выборки пусты и обрываются на первой странице.

    Без клиента и на упавшем запросе возвращается прежняя причина.
    Сказать «идентификатора нет в Ayla», не спросив Ayla, значило бы
    назвать состояние источника по молчанию потребителя — то есть
    обвинить его замером, которого никто не делал. Молчание об
    отказе тоже не годится: он уходит в лог, где его видно.
    """
    if http_client is None:
        return REASON_NEVER_SYNCED
    try:
        probe = probe_backend(str(tenant.id), http_client=http_client)
    except Exception as exc:
        logger.warning(
            "tenancy.assess.probe_failed tenant=%s error=%s: причина остаётся "
            "«ни разу не проходила» — замера нет",
            tenant.slug,
            exc.__class__.__name__,
        )
        return REASON_NEVER_SYNCED
    return REASON_NEVER_SYNCED if probe.found_anything else REASON_ID_NOT_IN_AYLA


def assess_salon(tenant: Tenant, *, http_client: Any | None = None) -> SalonAssessment:
    """Ответить на «почему салона не видно» названными причинами.

    Перечень закрыт: возвращаются только коды из :data:`REASON_LABELS`.
    «Нет активных услуг» и «нет бронируемых мастеров» проверяются только
    после хотя бы одной успешной синхронизации — до неё обе пустоты уже
    названы причиной ``never_synced``, и дублировать их значило бы
    отвечать на вопрос дважды.

    Чтение каталога — строго скопленное, через ``tenant_scope`` и
    тенантные менеджеры: кросс-тенантный ``all_tenants`` вне
    ``apps/marketplace/`` запрещает контракт MKT1 (#1018), а карточке
    нужен ровно один салон. Предикат бронируемости тот же, что читает
    клиент: ``_MasterManager.bookable()`` → ``master_state.AVAILABLE``.

    ``http_client`` — необязательный: с ним «синхронизация ни разу не
    проходила» уточняется до «идентификатора нет в Ayla» (см.
    :func:`_never_synced_reason`), без него остаётся прежней. Сетевой
    запрос делается ТОЛЬКО когда успешной синхронизации не было ни разу:
    у салона с витриной спрашивать нечего.
    """
    with tenant_scope(tenant):
        active_services = CatalogService.objects.filter(is_active=True).count()
        bookable_masters = CatalogMaster.objects.bookable().count()
        masters_total = CatalogMaster.objects.count()
        awaiting = CatalogMaster.objects.filter(AWAITING_VERIFICATION).count()

    reasons: list[str] = []
    if not tenant.is_active:
        reasons.append(REASON_TENANT_INACTIVE)
    if not (tenant.city or "").strip():
        reasons.append(REASON_CITY_MISSING)
    if tenant.last_catalog_sync_ok_at is None:
        reasons.append(_never_synced_reason(tenant, http_client=http_client))
    else:
        if active_services == 0:
            reasons.append(REASON_NO_ACTIVE_SERVICES)
        if bookable_masters == 0:
            # DRF-1553. Подслучай, а не второй код на всякий случай:
            # после DRF-1496 «мастера приехали синхронизацией и ждут
            # верификации» стало исходом по умолчанию КАЖДОГО
            # подключения, и от текста требуется назвать действие, а не
            # симптом. «Архив / is_active=False» остаётся прежней
            # причиной — там предлагать нечего.
            reasons.append(
                REASON_MASTERS_AWAIT_VERIFICATION if awaiting else REASON_NO_BOOKABLE_MASTERS
            )

    return SalonAssessment(
        tenant=tenant,
        last_sync_ok_at=tenant.last_catalog_sync_ok_at,
        active_services=active_services,
        bookable_masters=bookable_masters,
        reasons=tuple(reasons),
        masters_total=masters_total,
        masters_awaiting_verification=awaiting,
    )


def verify_salon_masters(tenant: Tenant, *, user) -> VerificationOutcome:  # type: ignore[no-untyped-def]
    """Верифицировать мастеров салона — тем же сервисом, что админка каталога.

    DRF-1553, решение владельца (OPEN_DECISIONS §51.1): действие даётся
    кнопкой на экране подключения, а не называется словами. Тело
    верификации при этом одно на оба экрана —
    :func:`apps.catalog.services.verification.verify_masters`; здесь
    только выбор строк и скоупинг.

    Берутся ровно те мастера, которых считает
    :data:`~apps.catalog.master_state.AWAITING_VERIFICATION` — не все
    непринятые. Мастер в архиве или снятый с активности тоже «не
    принят», но верификация его бронируемым не сделает: кнопка
    посчитала бы его в своей подписи и молча изменила бы строку, ничего
    не решив. Обещание кнопки и её работа обязаны совпадать.

    Выборка — под ``tenant_scope``: карточке нужен ровно один салон, а
    кросс-тенантный ``all_tenants`` вне ``apps/marketplace/`` запрещает
    контракт MKT1 (#1018). **Сама верификация идёт ВНЕ скоупа**, и это
    не небрежность, а условие паритета: ``adminconsole.journal``
    разворачивает ``LogEntry`` в ``AuditLog`` через ``write_audit``, а
    тот берёт тенанта из ``current_tenant()``. Под скоупом строка
    журнала получила бы ``tenant`` салона, а та же строка от действия в
    админке каталога — ``NULL``, потому что там выборка кросс-тенантная
    и одного салона у неё нет. Два экрана писали бы в журнал РАЗНОЕ —
    ровно то расхождение, которого задача избегает. Измерено, а не
    предположено: ``test_same_masters_same_journal_as_catalog_admin_action``
    сравнивает и ``LogEntry``, и ``AuditLog``.

    **Автоверификации здесь нет и быть не может.** Функция вызывается
    только из обработчика нажатия оператором; ни ``connect_salon``, ни
    ``assess_salon`` её не зовут — DRF-1496 этой задачей не ослабляется
    (граница §51, подтверждена §51.1).
    """

    with tenant_scope(tenant):
        awaiting = list(CatalogMaster.objects.filter(AWAITING_VERIFICATION))
    return _verify_masters(awaiting, user=user)


# ---------------------------------------------------------------------------
# Проверка идентификатора до сохранения
# ---------------------------------------------------------------------------


def probe_backend(tenant_uuid: str, *, http_client: Any) -> BackendProbe:
    """Спросить у Ayla, есть ли что-нибудь по этому Tenant UUID.

    Читатель — :func:`_never_synced_reason`: у строки без единой удачной
    синхронизации она различает «ещё не проходила» и «такого UUID в Ayla
    нет». Спрос достаточно ограничить двумя выборками из трёх — услуги и
    мастера: связи без них всё равно не существуют.
    """
    with http_client as http:
        services = http.fetch_salon_services(tenant_id=tenant_uuid)
        specialists = http.fetch_specialists(tenant_id=tenant_uuid)
    return BackendProbe(services=len(services), specialists=len(specialists))


# ---------------------------------------------------------------------------
# Один путь подключения
# ---------------------------------------------------------------------------


def connect_salon(
    *,
    slug: str,
    name: str,
    city: str,
    http_client: Any | None = None,
    sync_service: Any | None = None,
) -> ConnectResult:
    """Подключить салон: найти или завести в Ayla, строка, синхронизация, исход.

    UUID салона человек не вводит и не видит (владелец, 11.09.2026): он
    приходит из каталога — ``CatalogHttpClient.ensure_tenant`` находит
    салон по slug или заводит его (``POST /api/v1/internal/tenants/``,
    DRF-1525). Так снимается целый класс отказов прежней формы — «не
    UUID», «по UUID ничего нет»: ключ больше не вводится на глаз.

    Отказывает с :class:`ConnectError` — с объяснением, не тихо — когда:

    * slug / название / город не проходят модельную валидацию;
    * slug уже подключён здесь (существующий салон настраивается, а не
      подключается заново);
    * в Ayla этот slug занят салоном с ДРУГИМ названием (409) — опечатка
      или второй салон, решает оператор, а не код;
    * UUID, который вернула Ayla, уже занят здесь другим slug;
    * Ayla недоступна или ответила неразборчиво.

    Останавливается с :class:`ConnectPending` (``SETUP_PENDING``, не
    отказ), когда контур не донастроен: провижининг-токен пуст у нас или
    отвергнут каталогом. Строка не создаётся ни в одном из случаев.

    Синхронизация запускается сразу после создания строки. Её сбой —
    в том числе HTTP 429 ограничителя частоты Ayla — НЕ откатывает
    строку и НЕ отменяет подключение: «каталог не доехал» не то же
    самое, что «салон не подключён». Сбой возвращается в
    ``ConnectResult.sync_error``, а карточка честно покажет причину
    невидимости (``never_synced``) при следующем чтении.

    ``http_client`` / ``sync_service`` инъецируются тестами; в бою оба
    создаются настоящими.
    """
    from apps.catalog.services.http_client import (
        CatalogProvisioningRefused,
        CatalogProvisioningTokenMissing,
        CatalogSlugTaken,
    )
    from apps.catalog.services.sync import CatalogSyncService

    city = (city or "").strip()
    candidate = Tenant(slug=slug, name=name, city=city)
    try:
        candidate.full_clean(exclude=["id"], validate_unique=False)
    except ValidationError as exc:
        raise ConnectError(f"Некорректные данные салона: {exc.message_dict}") from exc

    if Tenant.all_objects.filter(slug=slug).exists():
        raise ConnectError(
            f"Салон {slug!r} уже подключён. Откройте его карточку — город, "
            "активность и лимиты меняются там, а не повторным подключением."
        )

    # Локальные проверки — ДО похода в Ayla: заводить салон в источнике
    # ради того, чтобы тут же отказать по локальной причине, нельзя.
    if http_client is None:
        from apps.catalog.services.http_client import CatalogHttpClient

        http_client = CatalogHttpClient()
    try:
        with http_client as http:
            ensured = http.ensure_tenant(slug=slug, name=name, city=city)
    except CatalogProvisioningTokenMissing as exc:
        logger.warning(
            "tenancy.connect.pending reason=%s slug=%s", PENDING_PROVISIONING_TOKEN_MISSING, slug
        )
        raise ConnectPending(PENDING_PROVISIONING_TOKEN_MISSING) from exc
    except CatalogProvisioningRefused as exc:
        logger.warning(
            "tenancy.connect.pending reason=%s slug=%s", PENDING_PROVISIONING_REFUSED, slug
        )
        raise ConnectPending(PENDING_PROVISIONING_REFUSED) from exc
    except CatalogSlugTaken as exc:
        raise ConnectError(
            f"В Ayla slug {exc.slug!r} уже занят салоном {exc.existing_name!r}, а вы "
            f"подключаете {exc.requested_name!r}. Если это тот же салон — введите его "
            "название так, как оно записано в Ayla; если другой — выберите другой slug. "
            "Строка не создана."
        ) from exc
    except Exception as exc:
        raise ConnectError(
            f"Не удалось найти или завести салон в Ayla "
            f"({exc.__class__.__name__}): строка не создана. Повторите, "
            "когда бэкенд отвечает, — подключать вслепую нельзя."
        ) from exc

    clash = Tenant.all_objects.filter(id=ensured.id).exclude(slug=slug).first()
    if clash is not None:
        raise ConnectError(
            f"Ayla вернула для {slug!r} идентификатор {ensured.id}, но он уже занят "
            f"салоном {clash.slug!r}. Два салона не могут делить один Ayla Tenant "
            "UUID — их каталоги сольются. Строка не создана."
        )

    tenant = Tenant.objects.create(
        id=ensured.id,
        slug=slug,
        name=name,
        city=city,
    )
    logger.info(
        "tenancy.connect.%s slug=%s tenant=%s",
        "created_in_ayla" if ensured.created else "found_in_ayla",
        slug,
        tenant.id,
    )

    if sync_service is None:
        sync_service = CatalogSyncService(http_client=http_client)
    result = sync_service.run(tenant)
    # ``SyncResult.error`` на успешном прогоне — пустая строка, не None.
    sync_error = (result.error or None) if result.ran else None

    tenant.refresh_from_db()
    return ConnectResult(
        tenant=tenant,
        created_in_ayla=ensured.created,
        sync_error=sync_error,
        sync_skipped=result.skipped,
        assessment=assess_salon(tenant),
    )
