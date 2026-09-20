"""Записи мастера: детали с временным состоянием и поиск клиента (DRF-2154, М-2).

Ручки под личностью мастера (``request.master``); мастер — актор, параметра
``master_id`` в путях и телах нет по построению. Создание и слоты идут
через :mod:`apps.admin_api.services.booking` — тот же код, что у салонной
стойки, — а здесь то, чего у админа нет:

* :func:`booking_detail` — своя запись из зеркала (``RemoteBookingProxy``,
  ``specialist_id`` ∈ :func:`specialist_keys` — pk строки синка или
  каталожный id соло/склеенного мастера) с ``temporal_state`` по часам
  сервера; чужая запись неотличима от несуществующей — вызывающий
  отвечает одним 404;
* :func:`temporal_state` — пять состояний макета DRF-1185;
* :func:`enrich_customer_rows` — к строкам поиска Ayla добавить дату
  последнего визита у ЭТОГО мастера (решение владельца 20.09: одноимённые
  различаются именем с инициалом и датой последнего визита);
* :func:`name_initial` — «Анна П.».

**Телефон клиента сюда не попадает ни в каком виде** (DRF-1039 / OD-W2-2):
``BotUser`` читается через ``.only()`` без колонки ``phone``, строки Ayla
телефона не несут по контракту поиска, а :func:`looks_like_phone` закрывает
поиск по номеру до слова владельца.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable
from uuid import UUID

from django.db.models import Max

from apps.booking.models import RemoteBookingProxy
from apps.catalog.models import CatalogMaster, CatalogService
from apps.catalog.specialist_ref import specialist_keys
from apps.identity.models import BotUser
from apps.master_api.services.dashboard import get_tenant_tz
from apps.master_api.services.visit_source import GUEST_NAME

logger = logging.getLogger(__name__)

#: DRF-1185: после окончания назначенного времени — трёхчасовое окно, в
#: котором система может получить сведения об исключительной ситуации.
#: «Если всё прошло как запланировано, ничего делать не нужно.» Дольше —
#: результата всё ещё нет: «Проверяем результат».
AFTER_WINDOW = timedelta(hours=3)

#: Закрытый словарь статуса наружу. Зеркало хранит сырое значение Ayla
#: (``awaiting_payment`` — её слово, которого нет в choices модели);
#: незнакомое значение уходит как ``unknown``, а не как случайная строка.
BOOKING_STATUSES: tuple[str, ...] = (
    "confirmed",
    "awaiting_payment",
    "pending_payment",
    "completed",
    "cancelled",
    "no_show",
)

TEMPORAL_STATES: tuple[str, ...] = ("upcoming", "now", "after", "completed", "unknown")

#: Сколько ближайших окон предлагать, когда время занято (DRF-1184: «другие
#: варианты», не весь день).
ALTERNATIVES_LIMIT = 5

#: Запрос, состоящий только из цифр и телефонной пунктуации. Ayla ищет по
#: номеру ТОЧНО — и это вход, а не выход; но для мастера поиск по номеру
#: закрыт до слова владельца (DRF-1039 закрывает, макет DRF-1184 допускал).
_PHONE_SHAPED_RE = re.compile(r"^[\d\s()+\-]+$")
_PHONE_MIN_DIGITS = 5


def looks_like_phone(query: str) -> bool:
    """Запрос выглядит как номер телефона, а не как имя."""

    q = (query or "").strip()
    if not q or not _PHONE_SHAPED_RE.fullmatch(q):
        return False
    return sum(ch.isdigit() for ch in q) >= _PHONE_MIN_DIGITS


def name_initial(full_name: str | None) -> str:
    """«Анна Петрова» → «Анна П.»; одно слово — как есть; пусто — «Гость».

    Ровно то, что показывает салонная стойка (``DayVisit``: имя + инициал
    фамилии) — фамилия целиком мастеру не уходит (``client_last_name`` в
    списке запрещённых ключей).
    """

    parts = (full_name or "").strip().split()
    if not parts:
        return GUEST_NAME
    if len(parts) < 2 or not parts[1]:
        return parts[0]
    return f"{parts[0]} {parts[1][:1]}."


def public_status(raw: str | None) -> str:
    return raw if raw in BOOKING_STATUSES else "unknown"


def temporal_state(
    *, start_at: datetime | None, end_at: datetime | None, status: str, now: datetime
) -> str:
    """Пять состояний DRF-1185 по часам СЕРВЕРА.

    ``completed`` — только по статусу: «приложение само не должно считать
    запись завершённой только потому, что прошло время». Дальше по часам:
    до начала — ``upcoming``; внутри промежутка — ``now``; после конца в
    трёхчасовом окне — ``after``; дольше без результата — ``unknown``
    («Проверяем результат»). Ветка «нет времени» — защита типа: зеркало
    хранит ``start_at``/``end_at`` NOT NULL.

    Отменённая запись (``cancelled`` / ``no_show``) тоже получает состояние
    по часам: врать ``unknown`` значило бы крутить «Проверить снова» на
    записи, про которую всё известно. Что показывать — решает экран по
    ``status`` ДО ``temporal_state`` (М-4; вид «Отменена» — у владельца).
    """

    if status == "completed":
        return "completed"
    if start_at is None or end_at is None:
        return "unknown"
    if now < start_at:
        return "upcoming"
    if now < end_at:
        return "now"
    if now < end_at + AFTER_WINDOW:
        return "after"
    return "unknown"


@dataclass(frozen=True)
class BookingDetail:
    id: str
    client_name_initial: str
    client_last_visit_date: date | None
    service_id: str | None
    service_name: str
    start_at: datetime | None
    end_at: datetime | None
    duration_min: int
    status: str
    temporal_state: str
    minutes_until: int | None
    checked_at: datetime

    def to_dict(self, tz) -> dict[str, Any]:
        def _iso(value: datetime | None) -> str | None:
            return value.astimezone(tz).isoformat() if value else None

        return {
            "id": self.id,
            "client": {
                "name_initial": self.client_name_initial,
                "last_visit_date": (
                    self.client_last_visit_date.isoformat() if self.client_last_visit_date else None
                ),
            },
            "service": {"id": self.service_id, "name": self.service_name},
            "start_at": _iso(self.start_at),
            "end_at": _iso(self.end_at),
            "duration_min": self.duration_min,
            "status": self.status,
            "temporal_state": self.temporal_state,
            "minutes_until": self.minutes_until,
            "checked_at": _iso(self.checked_at),
        }


def own_booking(master: CatalogMaster, appointment_id: UUID | str) -> RemoteBookingProxy | None:
    """Своя запись мастера из зеркала, или None.

    Чужая запись того же салона, запись другого салона и несуществующий id
    дают один и тот же None: форма отказа не должна подтверждать, что
    запись существует.
    """

    return RemoteBookingProxy.all_tenants.filter(
        tenant_id=master.tenant_id,
        specialist_id__in=specialist_keys(master),
        appointment_id=appointment_id,
    ).first()


def _last_visit_dates(
    master: CatalogMaster,
    bot_user_ids: Iterable[UUID],
    *,
    before: datetime | None = None,
) -> dict[UUID, date]:
    """Дата последнего СОСТОЯВШЕГОСЯ визита каждого клиента у этого мастера.

    Визит — строка зеркала со статусом ``completed`` (DRF-1146: визиты, не
    записи). ``before`` — для деталей записи: «была 12.05» считается до
    этой записи, а не включая её.
    """

    ids = [i for i in bot_user_ids if i is not None]
    if not ids:
        return {}
    qs = RemoteBookingProxy.all_tenants.filter(
        tenant_id=master.tenant_id,
        specialist_id__in=specialist_keys(master),
        status="completed",
        bot_user_id__in=ids,
    )
    if before is not None:
        qs = qs.filter(start_at__lt=before)
    tz = get_tenant_tz(master.tenant)
    out: dict[UUID, date] = {}
    for row in qs.values("bot_user_id").annotate(last=Max("start_at")):
        if row["last"] is not None:
            out[row["bot_user_id"]] = row["last"].astimezone(tz).date()
    return out


def booking_detail(
    master: CatalogMaster, appointment_id: UUID | str, *, now: datetime
) -> BookingDetail | None:
    """Детали своей записи для экрана DRF-1185, или None (→ 404)."""

    proxy = own_booking(master, appointment_id)
    if proxy is None:
        return None

    # Имя клиента — только имя и инициал; ``phone`` в .only() отсутствует
    # намеренно (DRF-1360): номер не должен даже войти в процесс.
    client_name = GUEST_NAME
    last_visit: date | None = None
    if proxy.bot_user_id is not None:
        bu = (
            BotUser.all_tenants.filter(tenant_id=master.tenant_id, id=proxy.bot_user_id)
            .only("id", "client_name", "display_name")
            .first()
        )
        if bu is not None:
            client_name = name_initial(bu.client_name or bu.display_name)
        last_visit = _last_visit_dates(master, [proxy.bot_user_id], before=proxy.start_at).get(
            proxy.bot_user_id
        )

    service_id: str | None = None
    service_name = ""
    if proxy.service_id is not None:
        # Под tenant_scope вьюхи; tenant_id — второй замок, как в visit_source.
        svc = (
            CatalogService.objects.filter(
                tenant_id=master.tenant_id, ayla_service_id=proxy.service_id
            )
            .values("id", "name")
            .first()
        )
        if svc is not None:
            service_id, service_name = str(svc["id"]), svc["name"]

    duration = 0
    if proxy.start_at and proxy.end_at:
        duration = max(int((proxy.end_at - proxy.start_at).total_seconds() // 60), 0)

    status = public_status(proxy.status)
    state = temporal_state(start_at=proxy.start_at, end_at=proxy.end_at, status=status, now=now)
    minutes_until = None
    if state == "upcoming" and proxy.start_at is not None:
        minutes_until = int((proxy.start_at - now).total_seconds() // 60)

    return BookingDetail(
        id=str(proxy.appointment_id),
        client_name_initial=client_name,
        client_last_visit_date=last_visit,
        service_id=service_id,
        service_name=service_name,
        start_at=proxy.start_at,
        end_at=proxy.end_at,
        duration_min=duration,
        status=status,
        temporal_state=state,
        minutes_until=minutes_until,
        checked_at=now,
    )


def enrich_customer_rows(master: CatalogMaster, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Строки поиска Ayla → ``{id, name «Анна П.», last_visit_date, named}``.

    ``id`` в ответе Ayla — её id пользователя; у ``BotUser`` он лежит в
    ``ayla_user_id`` (так же его находит потребитель событий записи). Через
    него — к строкам зеркала этого мастера и дате последнего визита. Клиент
    без визитов у мастера — ``null`` («новый клиент» на экране).
    """

    ayla_ids: list[UUID] = []
    for row in rows:
        try:
            ayla_ids.append(UUID(str(row.get("id") or "")))
        except ValueError:
            continue

    bot_users_by_ayla: dict[UUID, list[UUID]] = {}
    if ayla_ids:
        # Один Ayla-пользователь может иметь несколько BotUser (по каналу):
        # дата визита сводится по всем его строкам, не по первой попавшейся.
        for row_id, ayla_id in BotUser.all_tenants.filter(
            tenant_id=master.tenant_id, ayla_user_id__in=ayla_ids
        ).values_list("id", "ayla_user_id"):
            if ayla_id is not None:
                bot_users_by_ayla.setdefault(ayla_id, []).append(row_id)
    last_by_bot_user = _last_visit_dates(
        master, [i for ids in bot_users_by_ayla.values() for i in ids]
    )

    out: list[dict[str, Any]] = []
    for row in rows:
        raw_name = str(row.get("name") or "").strip()
        if ":" in raw_name and raw_name.split(":", 1)[0].isalnum() and " " not in raw_name:
            # Хэндл канала (`bot:max:123`) — не имя; та же поправка, что у стойки.
            raw_name = ""
        raw_id = str(row.get("id") or "")
        last: date | None = None
        try:
            dates = [
                d
                for d in (last_by_bot_user.get(i) for i in bot_users_by_ayla.get(UUID(raw_id), []))
                if d is not None
            ]
            last = max(dates) if dates else None
        except ValueError:
            pass
        out.append(
            {
                "id": raw_id,
                "name": name_initial(raw_name) if raw_name else "Без имени",
                "named": bool(raw_name),
                "last_visit_date": last.isoformat() if last else None,
            }
        )
    return out


__all__ = [
    "AFTER_WINDOW",
    "ALTERNATIVES_LIMIT",
    "BOOKING_STATUSES",
    "TEMPORAL_STATES",
    "BookingDetail",
    "booking_detail",
    "enrich_customer_rows",
    "looks_like_phone",
    "name_initial",
    "own_booking",
    "public_status",
    "temporal_state",
]
