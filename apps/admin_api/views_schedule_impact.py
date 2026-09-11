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

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_or_reception_read
from apps.admin_api.services.salon_day import tenant_tz
from apps.admin_api.services.wire_lists import UNREADABLE, read_rows
from apps.catalog.models import CatalogMaster
from apps.integrations.ayla.salon_client import (
    SalonNotConfigured,
    SalonUnavailable,
    SalonValidationError,
    get_salon_client,
)
from apps.integrations.ayla.user_proxy import external_user_id_for
from apps.master_api.services.schedule_frame import _ayla_read_actor

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


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _booking_row(item: dict[str, Any]) -> dict[str, Any] | None:
    """Строка предпросмотра.

    Клиента здесь нет — Ayla его не отдаёт, и нам нечего было бы вычистить:
    отсутствие по построению, а не по фильтру.
    """

    appointment_id = _str_or_none(item.get("appointment_id"))
    start = _str_or_none(item.get("start_at_local"))
    end = _str_or_none(item.get("end_at_local"))
    if appointment_id is None or start is None or end is None:
        return None
    refund = item.get("refund_percent_if_cancelled")
    return {
        "appointment_id": appointment_id,
        "start_local": start,
        "end_local": end,
        "service_name": _str_or_none(item.get("service_name")),
        "status": _str_or_none(item.get("status")),
        "payment_status": _str_or_none(item.get("payment_status")),
        "refund_percent_if_cancelled": refund if isinstance(refund, int) else None,
    }


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

    if not getattr(settings, "BOOKING_VIA_AYLA_REST", False):
        return _error(
            "frame_source_local_unsupported",
            "BOOKING_VIA_AYLA_REST is off: the salon edits local tables and "
            "Ayla's bookings do not describe them; this view reads Ayla only",
            503,
        )

    actor_user = _ayla_read_actor(tenant)
    if actor_user is None:
        return _error(
            "schedule_source_not_configured",
            f"no active owner/admin staff to read the schedule for {tenant.slug}",
            503,
        )

    requester = getattr(request, "bot_user", None)
    logger.info(
        "admin_api.schedule_impact.preview tenant=%s master=%s requester=%s named_to_ayla=%s",
        tenant.slug,
        master.pk,
        getattr(requester, "pk", None),
        actor_user.pk,
    )

    try:
        impact = get_salon_client().get_schedule_impact(
            actor_external_id=external_user_id_for(actor_user),
            tenant_slug=tenant.slug,
            specialist_id=str(master.id),
            start_at=start_at,
            end_at=end_at,
        )
    except SalonValidationError as exc:
        return _error("bad_window", str(exc), 400)
    except SalonNotConfigured as exc:
        return _error("schedule_source_not_configured", str(exc), 503)
    except SalonUnavailable as exc:
        # Названная причина, а не пустой список: пустота читается как «никого
        # не затронет», и салон закрыл бы время поверх живых записей.
        return _error("schedule_unavailable", str(exc), 503)

    raw_rows = impact.get("bookings")
    bookings = read_rows(raw_rows, _booking_row)
    # Здесь предмет — СКОЛЬКО людей затронет закрытие, и молча выброшенная
    # строка занижает вред: «затронет одну», когда затронет две. Списки
    # исключений терпят мягкий разбор (см. wire_lists), этот — нет, поэтому
    # выброшенные считаются и называются рядом с разобранными.
    total = len(raw_rows) if isinstance(raw_rows, list) else 0
    bookings["unreadable_rows"] = max(total - len(bookings["rows"]), 0)
    if bookings["state"] == UNREADABLE or bookings["unreadable_rows"]:
        logger.warning(
            "admin_api.schedule_impact.unreadable tenant=%s master=%s dropped=%s state=%s",
            tenant.slug,
            master.pk,
            bookings["unreadable_rows"],
            bookings["state"],
        )

    return JsonResponse(
        {
            "start_at": start_at,
            "end_at": end_at,
            "timezone": _str_or_none(impact.get("timezone")),
            "bookings": bookings,
            # Записи отсюда нет — см. докстринг модуля. Экран рисует показ и
            # подсказку, а не кнопку, которую сервер не примет.
            "writable": False,
            "next_step": NEXT_STEP,
        }
    )


__all__ = ["master_schedule_impact"]
