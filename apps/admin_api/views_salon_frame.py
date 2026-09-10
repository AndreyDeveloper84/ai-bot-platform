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
from datetime import time as time_cls
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_or_reception_read
from apps.integrations.ayla.salon_client import (
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


def _read_list(row: dict[str, Any], key: str) -> dict[str, Any]:
    """Один список интервалов мастера — с состоянием, а не только строками."""

    if key not in row:
        return {"state": "absent", "rows": [], "seen_fields": []}

    raw = row.get(key)
    if not isinstance(raw, list) or not raw:
        # Пустой список и не-список отвечают одинаково: строк нет. Разница
        # между ними интересна только логу — на экране обе значат «нечего
        # показать», и придумывать для них разные слова значило бы просить
        # администратора различать то, чего он не видит.
        return {"state": "none", "rows": [], "seen_fields": []}

    parsed: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        seen |= set(item)
        for start_field, end_field in _INTERVAL_FIELDS:
            start = _hhmm(item.get(start_field))
            end = _hhmm(item.get(end_field))
            if start is not None and end is not None:
                parsed.append({"start": start, "end": end})
                break

    if not parsed:
        # Строки БЫЛИ. Молчаливый ноль здесь и есть тот дефект, ради которого
        # состояний четыре: пустой ответ прочитался бы как «перерывов нет».
        return {"state": "unreadable", "rows": [], "seen_fields": sorted(seen)}

    return {"state": "parsed", "rows": parsed, "seen_fields": sorted(seen)}


def _master_frame(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "specialist_id": str(row.get("specialist_id") or ""),
        "display_name": str(row.get("display_name") or ""),
        "is_working_day": bool(row.get("is_working_day")),
        "schedule_note": row.get("schedule_note") or None,
        "schedule_source": row.get("schedule_source") or None,
        **{key: _read_list(row, key) for key in LIST_KEYS},
    }


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

    masters = payload.get("masters")
    if not isinstance(masters, list):
        # Тот же довод, что в сверке зеркала: день без ``masters`` не смеет
        # прочитаться как «в салоне никто не работает».
        return _error(
            "schedule_unavailable",
            "upstream returned an unrecognised day payload",
            503,
        )

    frames = [_master_frame(m) for m in masters if isinstance(m, dict)]
    unreadable = sorted(
        {key for f in frames for key in LIST_KEYS if f[key]["state"] == "unreadable"}
    )
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
            "masters": frames,
            # Сводка для экрана: что именно он не вправе показать полным.
            "unreadable_lists": unreadable,
        }
    )


__all__ = ["salon_day_frame"]
