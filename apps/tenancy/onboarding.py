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
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.core.exceptions import ValidationError

from apps.catalog.master_state import AVAILABLE
from apps.catalog.models import CatalogMaster, CatalogService
from apps.tenancy.models import Tenant

# ---------------------------------------------------------------------------
# Закрытый перечень причин невидимости (DRF-1511)
# ---------------------------------------------------------------------------

REASON_TENANT_INACTIVE = "tenant_inactive"
REASON_CITY_MISSING = "city_missing"
REASON_NEVER_SYNCED = "never_synced"
REASON_NO_ACTIVE_SERVICES = "no_active_services"
REASON_NO_BOOKABLE_MASTERS = "no_bookable_masters"

#: Код причины → объяснение для экрана. Ключи этого словаря и есть
#: закрытый перечень: причина, которой здесь нет, не может быть показана.
REASON_LABELS: dict[str, str] = {
    REASON_TENANT_INACTIVE: "салон выключен (is_active=False) — скрыт из всех выдач",
    REASON_CITY_MISSING: "не задан город — городской поиск салона не найдёт",
    REASON_NEVER_SYNCED: "синхронизация каталога ни разу не проходила — витрины нет",
    REASON_NO_ACTIVE_SERVICES: "нет активных услуг — записываться не на что",
    REASON_NO_BOOKABLE_MASTERS: (
        "нет бронируемых мастеров (is_active, не в архиве, приглашение принято) — "
        "в поиске салон не появляется вовсе; это безопасно, но не сбой"
    ),
}


class ConnectError(RuntimeError):
    """Отказ подключения с объяснением — вместо тихого «успеха»."""


@dataclass(frozen=True)
class SalonAssessment:
    """Состояние одного салона глазами клиента."""

    tenant: Tenant
    last_sync_ok_at: datetime | None
    active_services: int
    bookable_masters: int
    reasons: tuple[str, ...]

    @property
    def is_visible(self) -> bool:
        """Виден ли салон клиенту: ни одной названной причины невидимости."""
        return not self.reasons


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
    """Итог подключения: строка, прогон синхронизации и проверка исхода."""

    tenant: Tenant
    probe: BackendProbe
    sync_error: str | None
    sync_skipped: bool
    assessment: SalonAssessment


# ---------------------------------------------------------------------------
# Проверка исхода
# ---------------------------------------------------------------------------


def assess_salon(tenant: Tenant) -> SalonAssessment:
    """Ответить на «почему салона не видно» названными причинами.

    Перечень закрыт: возвращаются только коды из :data:`REASON_LABELS`.
    «Нет активных услуг» и «нет бронируемых мастеров» проверяются только
    после хотя бы одной успешной синхронизации — до неё обе пустоты уже
    названы причиной ``never_synced``, и дублировать их значило бы
    отвечать на вопрос дважды.
    """
    active_services = CatalogService.all_tenants.filter(tenant=tenant, is_active=True).count()
    bookable_masters = CatalogMaster.all_tenants.filter(AVAILABLE, tenant=tenant).count()

    reasons: list[str] = []
    if not tenant.is_active:
        reasons.append(REASON_TENANT_INACTIVE)
    if not (tenant.city or "").strip():
        reasons.append(REASON_CITY_MISSING)
    if tenant.last_catalog_sync_ok_at is None:
        reasons.append(REASON_NEVER_SYNCED)
    else:
        if active_services == 0:
            reasons.append(REASON_NO_ACTIVE_SERVICES)
        if bookable_masters == 0:
            reasons.append(REASON_NO_BOOKABLE_MASTERS)

    return SalonAssessment(
        tenant=tenant,
        last_sync_ok_at=tenant.last_catalog_sync_ok_at,
        active_services=active_services,
        bookable_masters=bookable_masters,
        reasons=tuple(reasons),
    )


# ---------------------------------------------------------------------------
# Проверка идентификатора до сохранения
# ---------------------------------------------------------------------------


def probe_backend(tenant_uuid: str, *, http_client: Any) -> BackendProbe:
    """Спросить у Ayla, есть ли что-нибудь по этому Tenant UUID.

    Идентификатор нельзя ввести на глаз: прежде чем строка будет создана,
    экран обязан убедиться, что по ней что-то есть. Спрос достаточно
    ограничить двумя выборками из трёх — услуги и мастера: связи без них
    всё равно не существуют.
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
    tenant_id: str | None,
    city: str,
    http_client: Any | None = None,
    sync_service: Any | None = None,
) -> ConnectResult:
    """Подключить салон: строка, идентификатор, город, синхронизация, исход.

    Отказывает с :class:`ConnectError` — с объяснением, не тихо — когда:

    * идентификатор не задан или не является UUID (случайный ключ
      зазеркалит ноль строк — класс из DRF-1510);
    * slug не проходит модельную валидацию;
    * идентификатор уже занят другим салоном;
    * slug уже существует (существующий салон настраивается, а не
      подключается заново);
    * Ayla недоступна для проверки идентификатора;
    * по идентификатору в Ayla нет ни одной услуги и ни одного мастера.

    Синхронизация запускается сразу после создания строки. Её сбой —
    в том числе HTTP 429 ограничителя частоты Ayla — НЕ откатывает
    строку и НЕ отменяет подключение: «каталог не доехал» не то же
    самое, что «салон не подключён». Сбой возвращается в
    ``ConnectResult.sync_error``, а карточка честно покажет причину
    невидимости (``never_synced``) при следующем чтении.

    ``http_client`` / ``sync_service`` инъецируются тестами; в бою оба
    создаются настоящими.
    """
    from apps.catalog.services.sync import CatalogSyncService

    raw_id = (tenant_id or "").strip()
    if not raw_id:
        raise ConnectError(
            "Идентификатор Ayla Tenant UUID не задан. Синхронизация ходит в "
            "Ayla с ?tenant=<UUID>: салон, созданный со случайным ключом, "
            "зазеркалит ноль услуг и ноль мастеров, а прогон отрапортует "
            "успех (DRF-1510). Возьмите UUID салона из бэкенда Ayla."
        )
    try:
        pk = uuid.UUID(raw_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ConnectError(
            f"Идентификатор {raw_id!r} не является UUID. Нужен Ayla Tenant "
            "UUID салона из бэкенда — slug здесь не подходит."
        ) from exc

    candidate = Tenant(slug=slug, name=name, city=(city or "").strip())
    try:
        candidate.full_clean(exclude=["id"], validate_unique=False)
    except ValidationError as exc:
        raise ConnectError(f"Некорректные данные салона: {exc.message_dict}") from exc

    clash = Tenant.all_objects.filter(id=pk).exclude(slug=slug).first()
    if clash is not None:
        raise ConnectError(
            f"Идентификатор {pk} уже занят салоном {clash.slug!r}. Два салона "
            "не могут делить один Ayla Tenant UUID — их каталоги сольются."
        )
    if Tenant.all_objects.filter(slug=slug).exists():
        raise ConnectError(
            f"Салон {slug!r} уже подключён. Откройте его карточку — город, "
            "активность и лимиты меняются там, а не повторным подключением."
        )

    # Проверка идентификатора ДО сохранения: по нему должно что-то быть.
    if http_client is None:
        from apps.catalog.services.http_client import CatalogHttpClient

        http_client = CatalogHttpClient()
    try:
        probe = probe_backend(str(pk), http_client=http_client)
    except Exception as exc:
        raise ConnectError(
            f"Не удалось проверить идентификатор в Ayla "
            f"({exc.__class__.__name__}): строка не создана. Повторите, "
            "когда бэкенд отвечает, — подключать вслепую нельзя."
        ) from exc
    if not probe.found_anything:
        raise ConnectError(
            f"По идентификатору {pk} в Ayla нет ни одной услуги и ни одного "
            "мастера. Скорее всего это не тот UUID: с ним салон зазеркалил "
            "бы пустую витрину. Строка не создана."
        )

    tenant = Tenant.objects.create(
        id=pk,
        slug=slug,
        name=name,
        city=(city or "").strip(),
    )

    if sync_service is None:
        sync_service = CatalogSyncService(http_client=http_client)
    result = sync_service.run(tenant)
    # ``SyncResult.error`` на успешном прогоне — пустая строка, не None.
    sync_error = (result.error or None) if result.ran else None

    tenant.refresh_from_db()
    return ConnectResult(
        tenant=tenant,
        probe=probe,
        sync_error=sync_error,
        sync_skipped=result.skipped,
        assessment=assess_salon(tenant),
    )
