"""Экран здоровья контура: то, что сломалось молча (DRF-1500, эпик DRF-75).

04.09.2026 каталог не синхронизировался двенадцать дней: в бэкенде было
265 услуг и 14 маникюров, в зеркале бота — 94 и ноль, а бот честно
отвечал клиентам «такого у наших мастеров нет». Нашлось это не сигналом,
а потому что владелец вспомнил про маникюр. Этот модуль собирает пять
молчаливых сбоёв на один экран:

1. **Свежесть каталога** — возраст последней успешной синхронизации по
   каждому салону. Сам сигнал заведён в DRF-1494
   (``apps.catalog.staleness`` поверх ``Tenant.last_catalog_sync_ok_at``);
   здесь он только показывается, второй не заводится.
2. **Расхождение зеркала с источником** — число услуг и мастеров в
   зеркале против чисел бэкенда Ayla. Одно число само по себе ничего не
   говорит; расхождение говорит всё. Источниковых чисел в базе нет (sync
   их не хранит), поэтому они запрашиваются у Ayla REST-ом: первая
   страница пагинации несёт полный ``count`` при ``page_size=1`` — один
   лёгкий запрос на ресурс на салон, с коротким таймаутом и кэшем. Если
   бэкенд не опрошен (нет токена/URL, сеть лежит), экран честно помечает
   источник чисел, а не рисует нули.
3. **Очередь handoff** — сколько открыто и возраст самой старой. Детали
   задач — соседний экран (DRF-1499), здесь только сводка.
4. **Спящие поверхности** — какие флаги выключены и что из-за этого
   недоступно. На 04.09 ``NUTRITION_ENABLED`` был не задан вовсе, и это
   было неочевидно ниоткуда, кроме ``printenv`` в контейнере. Отсюда
   флаги не переключаются (правило заморозки) — показываются.
5. **Салон, невидимый клиенту целиком** — мастера приняты и активны, а
   продаётся ноль. Сигнал заведён в ``apps.catalog.visibility`` поверх
   построчного ``sale_block``; здесь он только показывается. Пункт 2
   этого состояния не ловит и не мог бы: зеркало сходится с бэкендом
   строка в строку, гейт продажи стоит после зеркала.

Персональных данных здесь нет: только агрегаты — возрасты, числа,
состояния флагов, счётчики строк каталога. Значения флагов не показываются, только
«задан/не задан» и «включено/выключено»; имена на секрет-паттерн
проверяются, чтобы сюда случайно не притащили токен.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import httpx
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.catalog.models import CatalogMaster, CatalogService
from apps.catalog.staleness import TenantSyncAge, stale_after_seconds, sync_ages
from apps.catalog.visibility import TenantVisibility, tenant_visibilities
from apps.handoff.models import AdminTask
from apps.integrations.ayla.url_builder import AylaUrlBuilder, AylaUrlError
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant
from apps.integrations.ayla.request_id import with_request_id

logger = logging.getLogger(__name__)

#: Имя, попавшее под этот паттерн, на экран не выводится никогда — даже
#: состояние «задан/не задан». Защита от того, что кто-то добавит в
#: реестр поверхностей переменную с токеном в имени.
_SECRET_NAME_RE = re.compile(r"TOKEN|SECRET|PASSWORD|KEY")

#: Таймаут одного опроса Ayla за числом. Экран админки не должен ждать
#: дольше, чем оператор готов смотреть на спиннер.
DEFAULT_UPSTREAM_TIMEOUT_SECONDS = 5

#: Сколько держать опрошенное число в кэше. Свежесть «до пяти минут»
#: достаточна, чтобы увидеть расхождение в дни — и не бить бэкенд на
#: каждое обновление страницы.
DEFAULT_UPSTREAM_CACHE_SECONDS = 300


# ---------------------------------------------------------------------------
# Спящие поверхности
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SurfaceFlag:
    """Один флаг-поверхность: состояние и следствие его тишины."""

    name: str
    consequence: str
    env_present: bool
    enabled: bool

    @property
    def state_label(self) -> str:
        if self.enabled:
            return "включена"
        if not self.env_present:
            return "не задана (по умолчанию выключена)"
        return "выключена"


#: Реестр поверхностей. Каждая запись — (флаг, что недоступно, пока он
#: спит). Список курируется руками: автоматический обход ``settings``
#: притащил бы на экран всё подряд, включая внутренние ручки, а экран
#: отвечает на вопрос «чего клиент не получает».
_SURFACE_FLAGS: tuple[tuple[str, str], ...] = (
    (
        "NUTRITION_ENABLED",
        "Скиллы питания (дневник, скан еды) отвечают клиенту отказом «функция отключена».",
    ),
    (
        "FOOD_PHOTO_SCAN_ENABLED",
        "Фото еды не уходит в распознавание, даже если NUTRITION_ENABLED включён.",
    ),
    (
        "CONCIERGE_NUTRITION_CONTEXT_ENABLED",
        "Консьерж не получает контекст питания клиента в системный промпт.",
    ),
    (
        "NUTRITION_PROACTIVE_ENABLED",
        "Проактивные напоминания о питании не отправляются.",
    ),
    (
        "WELLNESS_PROACTIVE_ENABLED",
        "Проактивные wellness-сообщения не отправляются.",
    ),
    (
        "POST_VISIT_FOLLOWUP_ENABLED",
        "Сообщения после визита клиентам не отправляются.",
    ),
    (
        "CERTIFICATE_PAYMENT_ENABLED",
        "Оплата подарочных сертификатов недоступна.",
    ),
    (
        "ORCHESTRATOR_SHADOW_ENABLED",
        "Теневой прогон оркестратора не пишет сравнение ответов — регрессии промптов не видны.",
    ),
)


def surface_flags() -> list[SurfaceFlag]:
    """Состояние всех поверхностей из реестра.

    «Не задана» отличается от «выключена» по присутствию переменной в
    окружении — именно это было неочевидно 04.09. Значение переменной
    не читается и не показывается: проверяется только ``in os.environ``.
    """
    flags: list[SurfaceFlag] = []
    for name, consequence in _SURFACE_FLAGS:
        if _SECRET_NAME_RE.search(name):
            logger.error("adminconsole.health: имя поверхности похоже на секрет: %r", name)
            continue
        flags.append(
            SurfaceFlag(
                name=name,
                consequence=consequence,
                env_present=name in os.environ,
                enabled=bool(getattr(settings, name, False)),
            )
        )
    return flags


# ---------------------------------------------------------------------------
# Расхождение зеркала с источником
# ---------------------------------------------------------------------------


class UpstreamCounter(Protocol):
    """Кто угодно, умеющий спросить у бэкенда полное число записей."""

    def count(self, *, tenant_id: str, resource: str) -> int | None:
        """Полное число ``resource`` (``services``/``masters``) в бэкенде.

        ``None`` — бэкенд не ответил; экран обязан это показать, а не
        подставить ноль.
        """
        ...


class AylaUpstreamCounter:
    """Опрос Ayla REST: первая страница пагинации несёт полный ``count``.

    Запрашивается ``page_size=1`` — тело ответа минимально, а ``count``
    тот же, что видит синхронизация, когда идёт за строками. Результат
    кэшируется; любая ошибка (нет URL/токена, сеть, не-200) превращается
    в ``None`` и пишется в лог — экран здоровья не имеет права падать
    из-за того же сбоя, который он показывает.
    """

    _PATHS = {
        "services": "internal/catalog/salon-services/",
        "masters": "internal/specialists/",
    }

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self._base_url = (
            base_url if base_url is not None else getattr(settings, "AYLA_BASE_URL", "")
        )
        self._token = (
            token if token is not None else getattr(settings, "AYLA_INTERNAL_API_TOKEN", "")
        )
        self._timeout = (
            timeout
            if timeout is not None
            else int(
                getattr(
                    settings,
                    "CONTOUR_HEALTH_UPSTREAM_TIMEOUT_SECONDS",
                    DEFAULT_UPSTREAM_TIMEOUT_SECONDS,
                )
            )
        )

    def count(self, *, tenant_id: str, resource: str) -> int | None:
        cache_key = f"contour-health:upstream:{resource}:{tenant_id}"
        cached = cache.get(cache_key)
        if cached is not None:
            return int(cached)

        total = self._fetch(tenant_id=tenant_id, resource=resource)
        if total is not None:
            ttl = int(
                getattr(
                    settings,
                    "CONTOUR_HEALTH_UPSTREAM_CACHE_SECONDS",
                    DEFAULT_UPSTREAM_CACHE_SECONDS,
                )
            )
            cache.set(cache_key, total, timeout=ttl)
        return total

    def _fetch(self, *, tenant_id: str, resource: str) -> int | None:
        path = self._PATHS[resource]
        if not self._token:
            logger.warning("adminconsole.health: AYLA_INTERNAL_API_TOKEN не задан")
            return None
        try:
            url = AylaUrlBuilder(self._base_url).build(path)
        except AylaUrlError:
            logger.warning("adminconsole.health: AYLA_BASE_URL не задан или битый")
            return None
        try:
            response = httpx.get(
                url,
                params={"tenant": tenant_id, "page_size": 1},
                headers=with_request_id(
                    {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
                ),
                timeout=self._timeout,
            )
            response.raise_for_status()
            total = response.json().get("count")
        except Exception:  # noqa: BLE001 — граница экрана: любой сбой = «не опрошен»
            logger.exception(
                "adminconsole.health: бэкенд не ответил resource=%s tenant_id=%s",
                resource,
                tenant_id,
            )
            return None
        return int(total) if isinstance(total, int) else None


def build_default_counter() -> UpstreamCounter:
    """Фабрика, которую тесты подменяют фейком без сети."""
    return AylaUpstreamCounter()


@dataclass(frozen=True)
class MirrorDivergence:
    """Зеркало против источника по одному салону — числами рядом."""

    slug: str
    mirror_services: int
    mirror_masters: int
    upstream_services: int | None
    upstream_masters: int | None

    @property
    def services_gap(self) -> int | None:
        if self.upstream_services is None:
            return None
        return self.upstream_services - self.mirror_services

    @property
    def masters_gap(self) -> int | None:
        if self.upstream_masters is None:
            return None
        return self.upstream_masters - self.mirror_masters

    @property
    def upstream_seen(self) -> bool:
        """Бэкенд опрошен хотя бы по одному ресурсу."""
        return self.upstream_services is not None or self.upstream_masters is not None

    @property
    def diverged(self) -> bool:
        """Расхождение есть, если хоть один зазор не равен нулю."""
        return bool(self.services_gap) or bool(self.masters_gap)


# ---------------------------------------------------------------------------
# Очередь handoff (сводка; детали — экран DRF-1499)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HandoffSummary:
    open_count: int
    in_progress_count: int
    oldest_open_age_seconds: float | None

    @property
    def oldest_age_human(self) -> str:
        if self.oldest_open_age_seconds is None:
            return "—"
        minutes = int(self.oldest_open_age_seconds // 60)
        if minutes < 60:
            return f"{minutes}м"
        hours, minutes = divmod(minutes, 60)
        if hours < 24:
            return f"{hours}ч{minutes:02d}м"
        days, hours = divmod(hours, 24)
        return f"{days}д{hours:02d}ч"


def handoff_summary(*, now: datetime | None = None) -> HandoffSummary:
    """Сколько обращений ждёт человека и как долго — без содержимого."""
    moment = now or timezone.now()
    waiting = AdminTask.all_tenants.filter(
        status__in=(AdminTask.Status.OPEN, AdminTask.Status.IN_PROGRESS)
    )
    oldest = waiting.order_by("created_at").values_list("created_at", flat=True).first()
    return HandoffSummary(
        open_count=waiting.filter(status=AdminTask.Status.OPEN).count(),
        in_progress_count=waiting.filter(status=AdminTask.Status.IN_PROGRESS).count(),
        oldest_open_age_seconds=(None if oldest is None else (moment - oldest).total_seconds()),
    )


# ---------------------------------------------------------------------------
# Сводный отчёт
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContourHealthReport:
    catalog_ages: list[TenantSyncAge]
    divergences: list[MirrorDivergence]
    visibility: list[TenantVisibility]
    handoff: HandoffSummary
    flags: list[SurfaceFlag]
    threshold_seconds: int
    upstream_queried: bool
    problems: list[str] = field(default_factory=list)

    @property
    def has_problems(self) -> bool:
        return bool(self.problems)


def collect_report(
    *,
    now: datetime | None = None,
    counter: UpstreamCounter | None = None,
) -> ContourHealthReport:
    """Собрать все пять молчаливых сбоёв в один отчёт.

    ``problems`` — готовый список криков для красной плашки экрана.
    Экран «кричит», если список не пуст; проверка задачи — что на
    состоянии 04.09 (12 дней отставания, 94 против 265) он не пуст.
    """
    moment = now or timezone.now()
    counter = counter if counter is not None else build_default_counter()

    ages = sync_ages(now=moment)
    divergences = _divergences(ages, counter)
    visibility = tenant_visibilities()
    handoff = handoff_summary(now=moment)
    flags = surface_flags()

    problems: list[str] = []
    for age in ages:
        if age.is_stale:
            problems.append(f"{age.slug}: каталог не синхронизировался {age.age_human}")
    for div in divergences:
        if div.diverged:
            problems.append(
                f"{div.slug}: зеркало расходится с бэкендом — услуги "
                f"{div.mirror_services} против {div.upstream_services}, "
                f"мастера {div.mirror_masters} против {div.upstream_masters}"
            )
    for vis in visibility:
        if vis.invisible_whole:
            # Причина в тексте крика, а не только в таблице: без неё
            # оператор идёт искать «почему пусто» с нуля, а слово уже
            # посчитано построчным гейтом.
            problems.append(
                f"{vis.slug}: клиент не увидит никого — принято "
                f"{vis.admitted}, продаётся 0 ({vis.blocks_human})"
            )

    return ContourHealthReport(
        catalog_ages=ages,
        divergences=divergences,
        visibility=visibility,
        handoff=handoff,
        flags=flags,
        threshold_seconds=stale_after_seconds(),
        upstream_queried=any(d.upstream_seen for d in divergences),
        problems=problems,
    )


def _divergences(ages: list[TenantSyncAge], counter: UpstreamCounter) -> list[MirrorDivergence]:
    """По каждому синхронизируемому салону — зеркало против бэкенда.

    Чтение зеркала скоплено через ``tenant_scope`` — тот же приём, что
    ``sync_catalog._mirror_count``: кросс-тенантный менеджер каталога
    зарезервирован за marketplace discovery (import_boundaries MKT1,
    #1018), а число через скоп — это ровно то, что бот реально видит.
    """
    divergences: list[MirrorDivergence] = []
    for age in ages:
        tenant = Tenant.objects.get(slug=age.slug)
        with tenant_scope(tenant):
            mirror_services = CatalogService.objects.count()
            mirror_masters = CatalogMaster.objects.count()
        divergences.append(
            MirrorDivergence(
                slug=age.slug,
                mirror_services=mirror_services,
                mirror_masters=mirror_masters,
                upstream_services=counter.count(tenant_id=age.tenant_id, resource="services"),
                upstream_masters=counter.count(tenant_id=age.tenant_id, resource="masters"),
            )
        )
    return divergences
