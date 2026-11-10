"""Кадр салонного дня: смены, перерывы и отсутствия всех мастеров сразу.

DRF-1237, срез A2 (режим «Все»). Одна ручка —
``GET /api/v1/admin/day/frame/``.

### Почему отдельный источник, а не N раз ``build_schedule``

Экран мастера (срез A1) берёт день через
``master_api.services.schedule.build_schedule``, и это верно для одного
человека. Для всего салона та же дорога стоит непозволительно дорого: под
``BOOKING_VIA_AYLA_REST`` (на пилоте включён) ``load_day_frame`` делает на
**каждого** мастера три последовательных REST-вызова в Ayla —
``get_master_schedule``, ``list_schedule_exceptions``, ``list_time_off``.
Салон из четырёх мастеров — двенадцать удалённых вызовов на одно открытие
экрана, и цена растёт линейно с ростером.

``SalonClient.get_day`` отдаёт то же самое **одним** вызовом. Замер на
пилоте 10.09.2026 (тенант ``formula-tela``, ``scripts/
salon_day_shape_probe.sh``): четыре мастера, у каждого присутствуют ключи
``working_intervals``, ``breaks``, ``absences``, ``bookings``;
``working_intervals`` непуст у всех четверых, поля строки —
``start_local`` / ``end_local``.

### Записей здесь нет, и это правило, а не упущение

Ayla в том же ответе несёт ``bookings``, и соблазн взять их отсюда прямой.
Брать нельзя: ``admin_api.services.salon_day`` читает визиты из зеркала
(``RemoteBookingProxy``) намеренно — «день администратора и день мастера
обязаны совпадать; собранные из разных источников они разойдутся, и изнутри
салона не видно, который врёт». Второй источник записей на том же экране
воскресил бы ровно это расхождение.

Поэтому разделение то же, что у A1: **визиты — из зеркала, кадр — из
Ayla**. Экран складывает два ответа, а не два дня.

### Четыре исхода на каждый список, а не «есть/нет»

``breaks`` и ``absences`` на пилоте присутствуют у всех и пусты у всех —
перерывов не завёл никто (замер каталога 10.09.2026: 63 строки часов у
девяти мастеров, ``break_start`` заполнен у нуля). Значит **форма непустой
строки ``breaks`` не проверена ничем** — это названный предел, а не
доказанное отсутствие.

Из этого следует запрет на молчаливый ноль. Сегодня «строк нет» и «строки
есть, но я их не понял» дали бы на экране одинаковую пустоту, и первый
салон, поставивший мастеру обед, попал бы ровно в эту щель: перерыв был бы
показан рабочим временем. Поэтому каждый список отвечает одним из четырёх
состояний:

* ``absent`` — ключа нет вовсе: контракт разошёлся;
* ``none`` — ключ есть, строк нет: сегодня пусто;
* ``parsed`` — строки разобраны, вот они;
* ``unreadable`` — строки **есть**, но ни одна не опознана. Наружу идут
  имена встреченных полей (не значения), и экран обязан сказать словами, что
  показывает неполный день.

``unreadable`` — не ошибка сервера: день показать можно, нельзя молча
утверждать, что перерывов нет.

### Отсутствия разбираются своим правилом (DRF-2933)

Смены и перерывы каталог отдаёт временем суток (``start_local`` /
``end_local`` = «ЧЧ:ММ»), а отсутствия — моментами: ``start_at_local`` /
``end_at_local``, полные ISO в поясе мастера. Общий разбор интервала их не
узнавал, и любое непустое ``absences`` отвечало ``unreadable``; пилот этого
не показал только потому, что отгулов в нём не было. Здесь отсутствие
обрезается сутками запрошенного дня и отдаётся тем же «ЧЧ:ММ», конец суток —
``24:00``. Причина наружу не идёт: «отпуск» и «больничный» — сведения о
человеке, а кадр читает и ресепшн.

Полнодневный отгул флага ``is_working_day`` не меняет — его считает каталог
по шаблону и изменению на дату. Колонка остаётся рабочей, отсутствие лежит
поверх неё блоком на весь день.

### Закрытия салона и мастер зеркала (DRF-2933)

Каталог несёт ``closures`` в том же ответе; раньше вид их отбрасывал, и
«Расписание» показывало день закрытого салона обычным. Теперь список идёт
наружу тем же правилом четырёх состояний.

``master_id`` у строки мастера — id строки зеркала, под которым того же
человека знает день салона (``admin/day/``). ``specialist_id`` — id каталога,
и у соло-мастера и склеенного приглашения они разные (DRF-1933): без
``master_id`` экран не может приложить смену к записям. Ростер и ключи —
те же, что у дня (``salon_roster``, ``masters_by_specialist_key``). Пары нет
— ``null``, и экран обязан сказать «часы неизвестны», а не угадать по имени.

### Флаг выключен — названный отказ, а не пустой день

При ``BOOKING_VIA_AYLA_REST=false`` салон правит локальные таблицы, и день
Ayla к его расписанию отношения не имеет. Читать его в этом режиме значило
бы показать чужую смену как свою. Локальную ветку этот срез не строит —
предел назван в ответе (``frame_source_local_unsupported``, 503), а не
скрыт пустым списком.

### Кого пускаем

``require_admin_or_reception_read`` — тот же контур, что у дня салона
(§35 п. 1, DRF-1552): это **день салона**, который ресепшн уже читает, а не
личный график мастера. Вопрос о графике конкретного мастера открыт
отдельно (DRF-1640) и здесь не решается.
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime, timedelta
from datetime import time as time_cls
from typing import Any
from uuid import UUID

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_or_reception_read
from apps.admin_api.services.salon_day import masters_by_specialist_key, salon_roster
from apps.admin_api.services.wire_lists import ABSENT, NONE, PARSED, UNREADABLE, read_list
from apps.integrations.ayla.salon_client import (
    SalonAPIError,
    SalonForbidden,
    SalonNotConfigured,
    SalonUnavailable,
    get_salon_client,
)
from apps.integrations.ayla.user_proxy import external_user_id_for

# Приватное имя импортируется намеренно. Правило «кого мы называем Ayla при
# чтении» уже существует в двух копиях (``schedule_frame`` и сверка зеркала),
# и его докстринг обещает свести их при #1288. Третья копия разошлась бы с
# обеими молча — импорт хотя бы падает заметно.
from apps.master_api.services.schedule_frame import _ayla_read_actor

logger = logging.getLogger(__name__)

# Пары полей, которыми провод называет границы интервала. Первая пара —
# единственная, увиденная живьём (``working_intervals`` на пилоте); остальные
# перечислены как встречающиеся в этом же API формы. Список закрытый
# НАРОЧНО: строка, не подошедшая ни под одну пару, обязана дать
# ``unreadable``, а не тихо исчезнуть.
_INTERVAL_FIELDS: tuple[tuple[str, str], ...] = (
    ("start_local", "end_local"),
    ("start", "end"),
    ("start_time", "end_time"),
)

LIST_KEYS = ("working_intervals", "breaks", "absences")

#: Конец суток в ответе: отсутствие, идущее до полуночи и дальше.
END_OF_DAY = "24:00"


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def _hhmm(value: Any) -> str | None:
    """``"10:00:00"`` / ``"10:00"`` -> ``"10:00"``; всё прочее — ``None``.

    ``None`` здесь значит «не разобралось», и вызывающий обязан отличить это
    от «поля не было»: строка с верными именами полей и мусором в значении
    так же непригодна, как строка с чужими именами.
    """

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = time_cls.fromisoformat(value)
    except ValueError:
        return None
    return f"{parsed.hour:02d}:{parsed.minute:02d}"


def _iso_day(value: Any) -> date_cls | None:
    if not isinstance(value, str):
        return None
    try:
        return date_cls.fromisoformat(value)
    except ValueError:
        return None


def _interval_row(item: dict[str, Any]) -> dict[str, str] | None:
    """Строка-интервал в нормальной форме, либо ``None`` — «не опознана».

    Опознание — это И имена полей, И разбор значений: строка с верными
    именами и мусором в значении так же непригодна, как строка с чужими
    именами, и обе обязаны дать ``unreadable``, а не исчезнуть.
    """

    for start_field, end_field in _INTERVAL_FIELDS:
        start = _hhmm(item.get(start_field))
        end = _hhmm(item.get(end_field))
        if start is not None and end is not None:
            return {"start": start, "end": end}
    return None


def _local_wall_clock(value: Any) -> datetime | None:
    """Момент в поясе мастера (ISO со смещением) → его же часы без пояса.

    Смещение не пересчитывается: каталог уже перевёл момент в пояс мастера,
    и «10:00+03:00» значит «10:00 у мастера». Перевод в пояс сервера или
    устройства дал бы другой час — тот самый дефект, что чинил DRF-2589.
    """

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


#: Опознанная строка, которая суток дня не касается, — не строка ответа и не
#: «не понял». Отдельное значение, а не ``None``: эти два исхода разные.
_OUTSIDE: dict[str, Any] = {}


def _absence_row(item: dict[str, Any], day: date_cls | None) -> dict[str, Any] | None:
    """Отсутствие мастера внутри суток ``day`` — «ЧЧ:ММ»–«ЧЧ:ММ» / «24:00».

    ``None`` — строка не опознана. Опознанная строка, которая этих суток не
    касается (каталог режет отсутствия своим окном, и на стыке поясов оно
    шире суток), возвращается как :data:`_OUTSIDE` и в ответ не идёт:
    «не понял» и «не сегодня» — разные исходы, и второй не должен красить
    список в ``unreadable``. Туда же — отсутствие, которое после обрезки до
    минут стало пустым («18:00–18:00»): события длиной ноль экрану незачем.

    Прежняя форма «время суток» (``start_local`` / ``end_local``) принимается
    тоже: её ждали тесты и ждал экран, и она никому не мешает.
    """

    start = _local_wall_clock(item.get("start_at_local"))
    end = _local_wall_clock(item.get("end_at_local"))
    if start is None or end is None:
        return _interval_row(item)
    if day is None or end <= start:
        return None
    day_start = datetime.combine(day, time_cls.min)
    day_end = day_start + timedelta(days=1)
    clipped_start = max(start, day_start)
    clipped_end = min(end, day_end)
    if clipped_end <= clipped_start:
        return _OUTSIDE
    start_hm = f"{clipped_start.hour:02d}:{clipped_start.minute:02d}"
    end_hm = (
        END_OF_DAY if clipped_end == day_end else f"{clipped_end.hour:02d}:{clipped_end.minute:02d}"
    )
    if start_hm == end_hm:
        return _OUTSIDE
    return {"start": start_hm, "end": end_hm}


def _read_absences(row: dict[str, Any], day: date_cls | None) -> dict[str, Any]:
    """Список отсутствий с тем же правилом четырёх состояний, что у остальных.

    Своя петля, а не ``read_list``: тому «опознана, но не про эти сутки»
    неизвестно, и неопознанная строка рядом с такой пропала бы молча —
    список ответил бы «отсутствий нет».
    """

    if "absences" not in row:
        return {"state": ABSENT, "rows": [], "seen_fields": []}
    raw = row.get("absences")
    if not isinstance(raw, list) or not raw:
        return {"state": NONE, "rows": [], "seen_fields": []}

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    unrecognised = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        seen |= set(item)
        parsed = _absence_row(item, day)
        if parsed is None:
            unrecognised += 1
        elif parsed is not _OUTSIDE:
            rows.append(parsed)

    if rows:
        return {"state": PARSED, "rows": rows, "seen_fields": sorted(seen)}
    if unrecognised:
        # Строки БЫЛИ, и хотя бы одну мы не поняли: «отсутствий нет» сказать нельзя.
        return {"state": UNREADABLE, "rows": [], "seen_fields": sorted(seen)}
    return {"state": NONE, "rows": [], "seen_fields": sorted(seen)}


def _closure_row(item: dict[str, Any]) -> dict[str, Any] | None:
    """Закрытие салона на день или его часть.

    Весь день каталог называет сам (``is_full_day``); без флага — весь день
    тогда, когда нет ни начала, ни конца. Названная и не разобранная граница —
    не «весь день», а неопознанная строка: закрыть салону целый день по
    опечатке в одном поле хуже, чем сказать «не разобрал».
    """

    if "start_local" not in item and "end_local" not in item and "is_full_day" not in item:
        return None
    raw_start, raw_end = item.get("start_local"), item.get("end_local")
    start, end = _hhmm(raw_start), _hhmm(raw_end)
    if (raw_start not in (None, "") and start is None) or (
        raw_end not in (None, "") and end is None
    ):
        return None
    full = item.get("is_full_day")
    if not isinstance(full, bool):
        full = start is None and end is None
    return {
        "start": start,
        "end": end,
        "is_full_day": full,
        "reason": str(item.get("reason") or ""),
    }


def _master_frame(
    row: dict[str, Any],
    *,
    day: date_cls | None = None,
    master_id: str | None = None,
) -> dict[str, Any]:
    return {
        "specialist_id": str(row.get("specialist_id") or ""),
        "master_id": master_id,
        "display_name": str(row.get("display_name") or ""),
        "is_working_day": bool(row.get("is_working_day")),
        "schedule_note": row.get("schedule_note") or None,
        "schedule_source": row.get("schedule_source") or None,
        "working_intervals": read_list(row, "working_intervals", _interval_row),
        "breaks": read_list(row, "breaks", _interval_row),
        "absences": _read_absences(row, day),
    }


def _mirror_master_id(index: dict[UUID, Any], specialist_id: Any) -> str | None:
    """Строка зеркала, под которой день салона знает этого мастера каталога."""

    try:
        key = UUID(str(specialist_id))
    except (TypeError, ValueError):
        return None
    master = index.get(key)
    return str(master.id) if master is not None else None


@require_http_methods(["GET"])
@require_admin_or_reception_read
def salon_day_frame(request: HttpRequest) -> HttpResponse:
    """Смены, перерывы и отсутствия всех мастеров салона за один день."""

    tenant = request.tenant  # type: ignore[attr-defined]

    raw_date = (request.GET.get("date") or "").strip()
    day: date_cls | None = None
    if raw_date:
        try:
            day = date_cls.fromisoformat(raw_date)
        except ValueError:
            return _error("bad_request", "date must be YYYY-MM-DD", 400)

    if not getattr(settings, "BOOKING_VIA_AYLA_REST", False):
        return _error(
            "frame_source_local_unsupported",
            "BOOKING_VIA_AYLA_REST is off: the salon edits local tables and "
            "Ayla's day does not describe them; this view reads Ayla only",
            503,
        )

    actor_user = _ayla_read_actor(tenant)
    if actor_user is None:
        # Тенант без активного владельца/админа читать нечем. Это факт
        # настройки, а не сбой, и он обязан назваться: пустой день прочитался
        # бы как «сегодня никто не работает».
        return _error(
            "schedule_source_not_configured",
            f"no active owner/admin staff to read the salon day for {tenant.slug}",
            503,
        )

    try:
        payload = get_salon_client().get_day(
            actor_external_id=external_user_id_for(actor_user),
            tenant_slug=tenant.slug,
            date=day.isoformat() if day is not None else None,
        )
    except SalonNotConfigured as exc:
        return _error("schedule_source_not_configured", str(exc), 503)
    except SalonUnavailable as exc:
        return _error("schedule_unavailable", str(exc), 503)
    except SalonForbidden as exc:
        # DRF-2087 — 403 каталога по имени, как у действий (отмена, поиск
        # клиентов). Это не сбой, а факт настройки: человек, от чьего имени
        # салон читает (владелец/админ, ``_ayla_read_actor``), в каталоге не
        # администратор этого салона. 500 говорило «сломалось» и звало
        # чинить не то; у 403 есть ход оператора, и он назван.
        logger.warning(
            "admin_api.salon_day_frame.forbidden actor=%s tenant=%s err=%s",
            actor_user.pk,
            tenant.slug,
            exc,
        )
        return _error(
            "salon_forbidden",
            f"catalog refused the salon actor for {tenant.slug}: the owner/admin "
            "this read is named to is not an administrator of this salon in the "
            "catalog — relink them there (provision_salon_admin) and retry; "
            "nothing was read",
            403,
        )
    except SalonAPIError as exc:
        # DRF-2087 — страховочная сеть. Узкие ловцы выше — про то, что
        # известно сегодня; этот — про следующий подкласс, которого сегодня
        # нет, и про 401/404, которых здесь не ждали: без сети каждый из них
        # — 500 без имени. Класс отказа уходит в лог, экрану — та же
        # названная причина «источник не ответил».
        logger.warning(
            "admin_api.salon_day_frame.salon_error class=%s tenant=%s err=%s",
            type(exc).__name__,
            tenant.slug,
            exc,
        )
        return _error(
            "schedule_unavailable",
            f"catalog refused to read the salon day ({type(exc).__name__}); nothing was read",
            503,
        )

    masters = payload.get("masters")
    if not isinstance(masters, list):
        # Тот же довод, что в сверке зеркала: день без ``masters`` не смеет
        # прочитаться как «в салоне никто не работает».
        return _error(
            "schedule_unavailable",
            "upstream returned an unrecognised day payload",
            503,
        )

    # Сутки для обрезки отсутствий — те, о которых отвечает каталог; своей
    # даты не подставляем по той же причине, по какой ``date`` ниже берётся
    # из ответа.
    answered = _iso_day(payload.get("date")) or day
    mirror = masters_by_specialist_key(salon_roster(tenant))
    frames = [
        _master_frame(
            m,
            day=answered,
            master_id=_mirror_master_id(mirror, m.get("specialist_id")),
        )
        for m in masters
        if isinstance(m, dict)
    ]
    closures = read_list(payload, "closures", _closure_row)
    unreadable = sorted({key for f in frames for key in LIST_KEYS if f[key]["state"] == UNREADABLE})
    if closures["state"] == UNREADABLE:
        unreadable.append("closures")
    if unreadable:
        logger.warning(
            "admin_api.salon_day_frame.unreadable tenant=%s date=%s keys=%s",
            tenant.slug,
            payload.get("date"),
            ",".join(unreadable),
        )

    return JsonResponse(
        {
            # Дата берётся ИЗ ОТВЕТА: Ayla считает «сегодня» по часовому поясу
            # салона, и подставлять сюда свою значило бы спорить с ней о том,
            # какой сегодня день.
            "date": payload.get("date"),
            "source": "ayla",
            "closures": closures,
            "masters": frames,
            # Сводка для экрана: что именно он не вправе показать полным.
            "unreadable_lists": unreadable,
        }
    )


__all__ = ["salon_day_frame"]
