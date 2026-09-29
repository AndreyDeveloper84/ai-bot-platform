"""Салонная готовность поимённо — каталог + зеркало (DRF-2117, §50 п.4).

Владелец жмёт «Проверить готовность» и слышит не «всё хорошо», а список:
«Салон пока не готов: Анна — не настроен график; Иван — не назначены услуги;
Мария — не связана с каталогом.» Или — «Салон готов принимать записи.»

### Два слоя, и почему их два

Каталог (``GET /api/v1/internal/salons/<slug>/readiness/``, контракт §2b
``docs/CATALOG_INTERNAL_API_CONTRACT.md``) знает публикацию, рабочие дни,
продаваемые предложения, привязку MAX-личности и свободные окна. Он **не
знает** того, что живёт только в зеркале бота: подтверждения графика
владельцем (§83, ``schedule_confirmed_at``), ``catalog_specialist_id``,
статуса ``SoloIdentityLink``, ``ayla_user_id``. Эти факты — здесь, через
:func:`apps.catalog.master_state.sale_block` — единственное место, где
бот отвечает «почему мастер не продаётся»; вторую копию условий этот модуль
не заводит. Гейты (``MASTER_SCHEDULE_CONFIRMATION_REQUIRED``,
``MASTER_CATALOG_IDENTITY_REQUIRED``) действуют ровно так, как в
``sale_block``: ``schedule_unconfirmed`` появляется только при включённом
гейте (решение главного окна 20.09). Исключение одно и названо:
``catalog_unlinked`` без гейта — строка без ``catalog_specialist_id`` не
продаётся по гейту, но записать к ней из админки нельзя
(``apps.catalog.specialist_ref.catalog_specialist_id`` бросает), поэтому
для готовности это препятствие всегда.

### Что считается

* каталог не ответил / отказал / не настроен → **UNKNOWN = проблема**:
  ``ready=False``, одна строка о салоне целиком, класс отказа — в лог; «готов»
  при неизвестном состоянии не печатается никогда;
* каждая проблема каталога — по коду; тексты — константы :data:`TEXTS` по
  тем же кодам, незнакомый код — текст каталога;
* проблемы уровня салона (``no_masters``, ``master: null`` в контракте) —
  строкой без имени; каталог сказал ``ready=false`` без единой строки —
  ``unknown`` (форма, которую он не производит, но «готов» из неё не выводится);
* поверх — зеркало: строки тенанта не в архиве, с принятым приглашением и
  не снятые владельцем (``pending`` / ``revoked`` по ``sale_block`` — решения
  владельца о составе, не препятствия; ``is_active=False`` — тот же фильтр,
  что у дня салона ``staff_actions.salon_day``); ``profile_incomplete`` /
  ``ayla_unlinked`` / ``catalog_unlinked`` / ``schedule_unconfirmed`` — по
  коду ``sale_block``; строка с ``catalog_specialist_id``, которого нет в
  ответе каталога, — ``catalog_missing``;
* отказ зеркала или поиска актора (БД) — тот же UNKNOWN, что и отказ
  каталога: кнопка и прокси не падают, а говорят «не удалось проверить»;
* «готов» — только при пустом списке и без ``unknown`` ни у каталога, ни у нас.

**Названный предел (DRF-1540/1541):** мастер из приглашения и та же мастер
из синхронизации живут двумя строками зеркала; путь приглашения
``catalog_specialist_id`` не пишет. Пока это так, инвайт-строка без ключа даёт
«не связана с каталогом» даже для человека, которого каталог знает по
синхронизированной строке, и считается отдельно в ``masters_total``.
Склейка — контракт DRF-1541, не этот модуль.

Актор чтения — владелец, иначе администратор (``_ayla_read_actor``, то же
правило, что у кадра дня DRF-1237): каталог подтверждает slug по его TUR.
Имена без склонений («Анна — …»): морфологии в репо нет
(``marketplace/discovery.py``), предел назван.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone

logger = logging.getLogger(__name__)

# ─── коды и тексты ────────────────────────────────────────────────────────

#: Коды каталога (контракт §2b) и коды зеркала (``sale_block`` + свои).
SCHEDULE_MISSING = "schedule_missing"
SERVICES_MISSING = "services_missing"
IDENTITY_NOT_LINKED = "identity_not_linked"
NOT_PUBLISHED = "not_published"
HIDDEN_FROM_CATALOG = "hidden_from_catalog"
BOOKING_PAUSED = "booking_paused"
NO_FREE_SLOTS = "no_free_slots"
SLOTS_UNKNOWN = "slots_unknown"

CATALOG_UNLINKED = "catalog_unlinked"
AYLA_UNLINKED = "ayla_unlinked"
SCHEDULE_UNCONFIRMED = "schedule_unconfirmed"
PROFILE_INCOMPLETE = "profile_incomplete"
CATALOG_MISSING = "catalog_missing"
#: Уровень салона (каталог, ``master: null``).
NO_MASTERS = "no_masters"

#: Отказы источника целиком — одна строка о салоне.
SOURCE_UNAVAILABLE = "source_unavailable"
SOURCE_REFUSED = "source_refused"
SOURCE_NOT_CONFIGURED = "source_not_configured"

#: Тексты причин — константы по коду, «{name} — …».
TEXTS: dict[str, str] = {
    SCHEDULE_MISSING: "{name} — не настроен график",
    SERVICES_MISSING: "{name} — не назначены услуги",
    IDENTITY_NOT_LINKED: "{name} — не привязана личность MAX",
    NOT_PUBLISHED: "{name} — профиль не опубликован",
    HIDDEN_FROM_CATALOG: "{name} — скрыт из каталога",
    BOOKING_PAUSED: "{name} — приём записей на паузе",
    NO_FREE_SLOTS: "{name} — нет свободных окон на ближайшие {days} дней",
    SLOTS_UNKNOWN: "{name} — не удалось проверить свободные окна",
    CATALOG_UNLINKED: "{name} — не связана с каталогом",
    # DRF-2378: кем подтверждается — снято, потому что роли «оператор» в
    # системе нет. Факт остаётся, выдуманный исполнитель уходит; своего
    # адресата сюда не подставляем — на этом экране его не называли.
    AYLA_UNLINKED: "{name} — личность не подтверждена",
    SCHEDULE_UNCONFIRMED: "{name} — график не подтверждён",
    PROFILE_INCOMPLETE: "{name} — профиль не заполнен",
    CATALOG_MISSING: "{name} — не найдена в каталоге",
}

#: Тексты проблем уровня салона — без имени.
SALON_TEXTS: dict[str, str] = {
    NO_MASTERS: "В салоне нет ни одного мастера",
}

SOURCE_TEXTS: dict[str, str] = {
    SOURCE_UNAVAILABLE: "Не удалось проверить готовность: каталог не ответил. Попробуйте ещё раз.",
    SOURCE_REFUSED: (
        "Не удалось проверить готовность: каталог не подтвердил доступ салона. "
        "Нужна связь администратора с каталогом — обратитесь в поддержку."
    ),
    SOURCE_NOT_CONFIGURED: "Не удалось проверить готовность: связь с каталогом не настроена.",
}

READY_TEXT = "Салон готов принимать записи."
NOT_READY_HEAD = "Салон пока не готов:"

#: Коды ``sale_block``, которые читаются как препятствия готовности.
#: ``pending`` / ``revoked`` — не здесь: это решения владельца о составе.
_MIRROR_BLOCKS = frozenset(
    {
        PROFILE_INCOMPLETE,
        AYLA_UNLINKED,
        CATALOG_UNLINKED,
        SCHEDULE_UNCONFIRMED,
    }
)


# ─── результат ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Problem:
    master_id: str | None
    master_name: str
    code: str
    text: str
    #: ``catalog`` | ``mirror`` | ``source`` — откуда факт.
    origin: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "master": {"id": self.master_id, "name": self.master_name},
            "code": self.code,
            "text": self.text,
            "origin": self.origin,
        }


@dataclass(frozen=True)
class Readiness:
    """Итог: ``ready`` только при пустом списке и без ``unknown``."""

    problems: tuple[Problem, ...] = ()
    unknown: bool = False
    #: Код отказа источника целиком (``SOURCE_*``) или ``None``.
    source_problem: str | None = None
    checked_at: str = ""
    masters_total: int = 0
    limits: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        return not self.problems and not self.unknown

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "unknown": self.unknown,
            "source_problem": self.source_problem,
            "checked_at": self.checked_at,
            "masters_total": self.masters_total,
            "problems": [p.as_dict() for p in self.problems],
            "limits": list(self.limits),
        }


# ─── сбор ─────────────────────────────────────────────────────────────────


def _first_name(raw: str) -> str:
    name = (raw or "").strip()
    return name.split()[0] if name else "мастер"


def _text(code: str, name: str, *, fallback: str = "", days: int = 7) -> str:
    template = TEXTS.get(code)
    if template is None:
        return fallback or f"{name} — {code}"
    return template.format(name=name, days=days)


def _source_failure(code: str) -> Readiness:
    return Readiness(
        problems=(Problem(None, "", code, SOURCE_TEXTS[code], "source"),),
        unknown=True,
        source_problem=code,
        checked_at=timezone.now().isoformat(),
    )


def _catalog_readiness(tenant: Any, actor_external_id: str):
    from apps.catalog.services.http_client import CatalogHttpClient

    with CatalogHttpClient() as http:
        return http.fetch_salon_readiness(
            tenant_slug=tenant.slug,
            actor_external_id=actor_external_id,
        )


def _mirror_rows(tenant: Any) -> list[Any]:
    """Строки зеркала, о которых есть смысл спрашивать: не в архиве, приглашение
    принято, не сняты владельцем (``is_active`` — как у дня салона)."""
    from apps.catalog.master_state import ACCEPTED
    from apps.catalog.models import CatalogMaster
    from apps.tenancy.context import tenant_scope

    with tenant_scope(tenant):
        return list(
            CatalogMaster.objects.filter(
                archived_at__isnull=True, invite_status=ACCEPTED, is_active=True
            )
            .select_related("identity_link")
            .order_by("name", "id")
        )


def _mirror_problems(rows: list[Any], catalog_ids: set[str], *, days: int) -> list[Problem]:
    from apps.catalog.master_state import sale_block

    out: list[Problem] = []
    for row in rows:
        name = _first_name(getattr(row, "name", ""))
        catalog_id = getattr(row, "catalog_specialist_id", None)
        codes: list[str] = []
        block = sale_block(row)
        if block is not None and block not in _MIRROR_BLOCKS:
            # ``pending`` / ``revoked`` — решение владельца о составе; строку
            # не спрашиваем ни о связи, ни о каталоге (фильтр выше держит
            # это же, здесь — на случай гонки со сменой статуса).
            continue
        if block is not None:
            codes.append(block)
        if catalog_id is None:
            # Без гейта ``sale_block`` молчит, а запись к такой строке из
            # админки невозможна — препятствие всегда (докстринг модуля).
            if CATALOG_UNLINKED not in codes:
                codes.append(CATALOG_UNLINKED)
        elif str(catalog_id) not in catalog_ids:
            codes.append(CATALOG_MISSING)
        for code in codes:
            out.append(
                Problem(
                    str(catalog_id) if catalog_id is not None else None,
                    name,
                    code,
                    _text(code, name, days=days),
                    "mirror",
                )
            )
    return out


def check_salon_readiness(tenant: Any) -> Readiness:
    """Каталог + зеркало; любой отказ источника — UNKNOWN = проблема.

    Наружу не бросает: и кнопка в чате, и прокsi admin Mini App отвечают
    «не удалось проверить», а не 500 / проглоченный тап (ключ идемпотентности
    консьюмера на исключении не освобождается).
    """
    try:
        return _check_salon_readiness(tenant)
    except Exception:  # noqa: BLE001 — UNKNOWN = проблема; класс и трасса — в лог
        logger.warning(
            "admin_api.salon_readiness.failed tenant=%s",
            getattr(tenant, "slug", "?"),
            exc_info=True,
        )
        return _source_failure(SOURCE_UNAVAILABLE)


def _check_salon_readiness(tenant: Any) -> Readiness:
    from apps.catalog.services.http_client import (
        CatalogError,
        CatalogNotConfigured,
        CatalogReadinessRefused,
        CatalogTransportError,
    )
    from apps.integrations.ayla.user_proxy import external_user_id_for
    from apps.master_api.services.schedule_frame import _ayla_read_actor

    actor = _ayla_read_actor(tenant)
    if actor is None:
        logger.warning("admin_api.salon_readiness.no_actor tenant=%s", tenant.slug)
        return _source_failure(SOURCE_NOT_CONFIGURED)

    try:
        catalog = _catalog_readiness(tenant, external_user_id_for(actor))
    except CatalogReadinessRefused as exc:
        logger.warning(
            "admin_api.salon_readiness.refused tenant=%s reason=%s status=%s",
            tenant.slug,
            exc.reason,
            exc.status_code,
        )
        return _source_failure(SOURCE_REFUSED)
    except CatalogNotConfigured as exc:
        logger.warning(
            "admin_api.salon_readiness.not_configured tenant=%s err=%s", tenant.slug, exc
        )
        return _source_failure(SOURCE_NOT_CONFIGURED)
    except (CatalogTransportError, CatalogError) as exc:
        logger.warning(
            "admin_api.salon_readiness.unavailable tenant=%s class=%s err=%s",
            tenant.slug,
            type(exc).__name__,
            exc,
        )
        return _source_failure(SOURCE_UNAVAILABLE)

    days = catalog.horizon_days or 7
    problems: list[Problem] = []
    unknown = False
    for p in catalog.salon_problems:
        # Уровень салона (``no_masters``): строка без имени, каталожный текст
        # — только для незнакомого кода.
        problems.append(
            Problem(None, "", p["code"], SALON_TEXTS.get(p["code"], p["text"]), "catalog")
        )
    for master in catalog.masters:
        name = _first_name(master.name)
        if "unknown" in master.checks.values():
            unknown = True
        for p in master.problems:
            problems.append(
                Problem(
                    master.id,
                    name,
                    p["code"],
                    _text(p["code"], name, fallback=p["text"], days=days),
                    "catalog",
                )
            )

    if not catalog.ready and not problems:
        # Каталог отказал в «готов», не назвав причины, — форма, которой контракт
        # не производит; читать её как «готов» нельзя.
        unknown = True

    rows = _mirror_rows(tenant)
    catalog_ids = {m.id for m in catalog.masters}
    problems.extend(_mirror_problems(rows, catalog_ids, days=days))

    # Состав — объединение двух списков: мастера каталога ∪ строки зеркала с
    # ключом каталога (в том числе те, кого каталог не вернул — они названы
    # ``catalog_missing``) + строки без ключа (``catalog_unlinked``).
    linked_ids = {
        str(r.catalog_specialist_id) for r in rows if getattr(r, "catalog_specialist_id", None)
    }
    unlinked_rows = sum(1 for r in rows if getattr(r, "catalog_specialist_id", None) is None)
    return Readiness(
        problems=tuple(problems),
        unknown=unknown,
        checked_at=catalog.checked_at or timezone.now().isoformat(),
        masters_total=len(catalog_ids | linked_ids) + unlinked_rows,
        limits=catalog.limits,
    )


# ─── рендер ───────────────────────────────────────────────────────────────


def render(readiness: Readiness) -> str:
    """Текст владельцу: «готов» / «пока не готов:» + строки поимённо / отказ источника."""
    if readiness.source_problem is not None:
        return SOURCE_TEXTS[readiness.source_problem]
    if readiness.ready:
        return READY_TEXT
    lines = [p.text for p in readiness.problems]
    if not lines:
        # ``unknown`` без единой строки — форма, которую каталог не производит
        # (``slots_unknown`` всегда приходит строкой); на всякий случай — честно.
        return "Не удалось проверить готовность полностью. Попробуйте ещё раз."
    body = [f"{line};" for line in lines[:-1]] + [f"{lines[-1]}."]
    return "\n".join([NOT_READY_HEAD, *body])


__all__ = [
    "AYLA_UNLINKED",
    "CATALOG_MISSING",
    "CATALOG_UNLINKED",
    "NOT_READY_HEAD",
    "NO_MASTERS",
    "PROFILE_INCOMPLETE",
    "READY_TEXT",
    "SALON_TEXTS",
    "SCHEDULE_UNCONFIRMED",
    "SOURCE_NOT_CONFIGURED",
    "SOURCE_REFUSED",
    "SOURCE_TEXTS",
    "SOURCE_UNAVAILABLE",
    "TEXTS",
    "Problem",
    "Readiness",
    "check_salon_readiness",
    "render",
]
