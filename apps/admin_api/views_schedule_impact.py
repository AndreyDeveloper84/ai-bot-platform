"""Что вытеснит закрытие времени мастера — предпросмотр (§142, срез В).

``GET /api/v1/admin/masters/<id>/schedule/impact/?date=&from=&to=``

# Решение владельца и что из него здесь

§142: салон закрывает время мастера **отдельным действием**, обязан
**показать затронутые записи** и **отдельно решить перенос или отмену каждой**.
Реестр §150 разделил это на срезы: **В** — показать, не применять; **Б** —
применять от названного человека.

Это срез В. Вид показывает, какие живые записи закрытие вытеснит, — и
останавливается. Решение по записям и само закрытие — в Pro App, где
администратор вошёл под своим именем.

# Почему граница ровно здесь, а не «пока не сделали»

Ayla уже умеет остальное: ``POST tenants/me/masters/{id}/time-off/`` с
``resolutions`` применяет отсутствие и решение по каждой записи одной
транзакцией, с автором. Бот туда **не дотягивается нарочно**: сервисный
Bearer на этой поверхности только читает (``IsAylaReadOnly``, DRF-1297 B-1),
а внутренний маршрут поштучные решения не выставляет — «cancelling somebody's
appointment is a decision with money and a message attached, and it belongs
on a surface where a named human is the actor, not a shared service token».
Замер: ``docs/SALON_BLOCK_MEASUREMENT_142.md``.

# Что НЕ отдаётся экрану, и почему

``impact_token``. Ayla отдаёт его вместе со списком — отпечаток набора,
который ``POST … time-off/`` потребует обратно, чтобы отказать
``IMPACT_CHANGED``, если записи изменились, пока администратор решал. Здесь
его **потребить некому**: единственный потребитель — запись, а запись отсюда
не идёт. Отдать токен экрану значило бы показать намерение, которое нельзя
подтвердить, — кнопку без сервера за ней.

# Кто читает

Тот же порядок, что у остальных чтений салонной поверхности: Ayla называется
активный владелец, иначе активный администратор салона
(``_ayla_read_actor``). Это **названный предел**: человек, нажавший кнопку,
и человек, названный Ayla, могут не совпадать. Нажавший пишется в наш лог
рядом.

Персональных данных в ответе нет по построению: Ayla отдаёт услугу, время,
цену и статус оплаты — не клиента.

# Окно приходит датой и часами САЛОНА

``date=YYYY-MM-DD&from=HH:MM&to=HH:MM``, смещение к ним прикладывает сервер
по часовому поясу салона. Не ISO со смещением от браузера: экран не знает
пояса салона иначе как строкой IANA, а вычислять смещение в браузере —
ровно та ошибка, из-за которой хронология салона показывала «11:00» вместо
«14:00» (11.09.2026). Администратор думает «15-го с 10 до 14», и запрос
говорит то же самое.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date as date_cls
from datetime import datetime, time
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_or_reception_read
from apps.admin_api.services.salon_day import tenant_tz
from apps.admin_api.services.schedule_impact import impact_for_window
from apps.catalog.models import CatalogMaster

logger = logging.getLogger(__name__)

#: Куда идёт решение по записям. Строка для экрана, не для ветвления.
NEXT_STEP = "pro_app"


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def _master_or_none(master_id: str) -> CatalogMaster | None:
    try:
        mid = uuid.UUID(master_id)
    except (TypeError, ValueError):
        return None
    return CatalogMaster.objects.filter(id=mid).first()


def _date_or_none(value: str | None) -> date_cls | None:
    try:
        return date_cls.fromisoformat(value) if value else None
    except ValueError:
        return None


def _time_or_none(value: str | None) -> time | None:
    try:
        return time.fromisoformat(value) if value else None
    except ValueError:
        return None


def _parse_window(request: HttpRequest, tenant: Any) -> tuple[str, str] | JsonResponse:
    """``date`` + ``from`` + ``to`` в часах салона → два ISO со смещением салона."""

    day = _date_or_none(request.GET.get("date"))
    start = _time_or_none(request.GET.get("from"))
    end = _time_or_none(request.GET.get("to"))
    if day is None or start is None or end is None:
        return _error(
            "bad_window",
            "date (YYYY-MM-DD), from (HH:MM) and to (HH:MM) are required",
            400,
        )
    if end <= start:
        return _error("bad_window", "to must be after from", 400)
    tz = tenant_tz(tenant)
    return (
        datetime.combine(day, start, tzinfo=tz).isoformat(),
        datetime.combine(day, end, tzinfo=tz).isoformat(),
    )


@require_http_methods(["GET"])
@require_admin_or_reception_read
def master_schedule_impact(request: HttpRequest, master_id: str) -> HttpResponse:
    """Какие записи вытеснит закрытие времени мастера — показ, не действие."""

    master = _master_or_none(master_id)
    if master is None:
        return _error("not_found", "master not found", 404)

    tenant = request.tenant  # type: ignore[attr-defined]
    parsed = _parse_window(request, tenant)
    if isinstance(parsed, JsonResponse):
        return parsed
    start_at, end_at = parsed

    requester = getattr(request, "bot_user", None)
    logger.info(
        "admin_api.schedule_impact.preview tenant=%s master=%s requester=%s",
        tenant.slug,
        master.pk,
        getattr(requester, "pk", None),
    )

    # DRF-2118 — само чтение живёт в сервисе: тот же ответ читает
    # уведомление-решение «мастер просит изменить график». Здесь — только
    # перевод исхода в HTTP.
    impact = impact_for_window(tenant, master, start_at=start_at, end_at=end_at)
    if not impact.ok:
        return _error(impact.error_slug, impact.error_detail, _STATUS_BY_SLUG[impact.error_slug])

    return JsonResponse(
        {
            "start_at": start_at,
            "end_at": end_at,
            "timezone": impact.timezone,
            "bookings": impact.bookings,
            # Записи отсюда нет — см. докстринг модуля. Экран рисует показ и
            # подсказку, а не кнопку, которую сервер не примет.
            "writable": False,
            "next_step": NEXT_STEP,
        }
    )


#: HTTP-код по slug отказа сервиса — те же коды, что вьюха отдавала до DRF-2118.
_STATUS_BY_SLUG = {
    "frame_source_local_unsupported": 503,
    "schedule_source_not_configured": 503,
    "catalog_profile_unresolved": 409,
    "bad_window": 400,
    "schedule_unavailable": 503,
    "salon_forbidden": 403,
}


__all__ = ["master_schedule_impact"]
