"""Исключения, недоступность и закрытия салона — показ (DRF-1240, чтение).

``GET /api/v1/admin/masters/<id>/exceptions/?from=&to=``

# Что это и чего здесь нет

Замороженный UX DRF-1240 требует, среди прочего, «отображение уже
существующих исключений». Сегодня салон не видит их **нигде**: день мастера
(срез A1) показывает сегодняшнюю рамку, кадр салона (A2) — сегодняшние
смены, а перечня «что уже назначено на ближайшие дни» нет ни на одном
экране.

**Записи здесь нет, и это не задел на будущее, а граница по факту.** Все
восемь записывающих маршрутов салонной поверхности объявлены
``SERVICE_READ_ONLY`` (``apps/integrations/ayla/salon_surface.py``). Из них
weekly-шаблон закрыт **дважды** — сервисным креденшелом и открытым пробелом
weekly-guard (В-4 row D), — а остальные шесть только креденшелом. По §117
владелец разрешил использовать credential path **условно**: сначала три
проверки (object authorization, tenant scope, audit attribution), потом
использование, «иначе мы просто обменяем 8 SP разработки на скрытую
privilege escalation». Проверки не сделаны, значит записи нет.

# Почему мягкий разбор, а не строгий как в кадре

``master_api.services.schedule_frame`` на нераспознанной строке **бросает**
``SalonUnavailable``. Там это верно: испорченная строка искажает расчёт
доступности, а неверная доступность хуже отсутствующей.

Здесь предмет другой — показ списка. Спрятать все исключения салона из-за
одной странной строки хуже, чем показать остальные и назвать проблему,
поэтому каждый список отвечает одним из четырёх состояний
(``services/wire_lists``). Разная строгость на разных предметах — не
непоследовательность, а разная цена ошибки.

# Что известно про форму строк, а что нет

``date`` / ``is_working_day`` / ``start_time`` / ``end_time`` у исключений и
``start_at`` / ``end_at`` у отгулов — **проверены живьём**: ровно эти имена
разбирает ``schedule_frame`` на пилоте под включённым флагом.

Форма строки закрытия известна только из докстринга ``list_closures``, то
есть это пересказ, а не провод. Поэтому закрытия читаются тем же мягким
разбором: неопознанная строка даст ``unreadable`` с именами полей, и
следующий читатель узнает форму из ответа, а не из выезда на пилот.

# Кого пускаем

``require_admin_role``, как и день мастера: это график конкретного человека,
а не день салона. Вправе ли ресепшн видеть его — вопрос владельцу
(**DRF-1640**), и отвечать на него выбором декоратора значило бы принять
продуктовое решение молча.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.admin_api.services.wire_lists import UNREADABLE, read_rows
from apps.catalog.models import CatalogMaster
from apps.integrations.ayla.salon_client import (
    SalonNotConfigured,
    SalonUnavailable,
    get_salon_client,
)
from apps.integrations.ayla.user_proxy import external_user_id_for
from apps.master_api.services.schedule_frame import _ayla_read_actor

logger = logging.getLogger(__name__)

LIST_KEYS = ("exceptions", "time_off", "closures")


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def _master_or_none(master_id: str) -> CatalogMaster | None:
    """Мастер ЭТОГО салона; чужой и кривой id отвечают одинаковым «нет»."""

    try:
        mid = uuid.UUID(master_id)
    except (TypeError, ValueError):
        return None
    return CatalogMaster.objects.filter(id=mid).first()


def _iso_date(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date_cls.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _hhmm(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.strptime(value[:5], "%H:%M")
    except ValueError:
        return None
    return parsed.strftime("%H:%M")


def _exception_row(item: dict[str, Any]) -> dict[str, Any] | None:
    """Исключение по дате. Без разобранной даты строка бесполезна."""

    day = _iso_date(item.get("date"))
    if day is None:
        return None
    working = bool(item.get("is_working_day"))
    return {
        "id": str(item.get("id") or f"exc-{day}"),
        "date": day,
        "is_working_day": working,
        # Часы имеют смысл только у рабочего дня: «не работаю» с часами —
        # противоречие, и показывать их значило бы предложить время, которое
        # тем же ответом объявлено нерабочим.
        "start": _hhmm(item.get("start_time")) if working else None,
        "end": _hhmm(item.get("end_time")) if working else None,
    }


def _time_off_row(item: dict[str, Any]) -> dict[str, Any] | None:
    start = item.get("start_at")
    end = item.get("end_at")
    if not isinstance(start, str) or not isinstance(end, str) or not start or not end:
        return None
    return {
        "id": str(item.get("id") or ""),
        "start_at": start,
        "end_at": end,
        "reason": item.get("reason") or "",
    }


def _closure_row(item: dict[str, Any]) -> dict[str, Any] | None:
    day = _iso_date(item.get("date"))
    if day is None:
        return None
    return {
        "id": str(item.get("id") or f"closed-{day}"),
        "date": day,
        "start": _hhmm(item.get("start_time")),
        "end": _hhmm(item.get("end_time")),
        "reason": item.get("reason") or "",
    }


def _parse_range(request: HttpRequest) -> tuple[date_cls, date_cls] | JsonResponse:
    from apps.master_api.services.schedule import DEFAULT_RANGE_DAYS, MAX_RANGE_DAYS

    def _one(name: str) -> date_cls | None:
        raw = request.GET.get(name)
        if not raw:
            return None
        try:
            return date_cls.fromisoformat(raw)
        except ValueError:
            return None

    from_date = _one("from")
    if from_date is None and request.GET.get("from"):
        return _error("bad_request", "'from' must be YYYY-MM-DD", 400)
    if from_date is None:
        from_date = timezone.localdate()

    to_date = _one("to")
    if to_date is None and request.GET.get("to"):
        return _error("bad_request", "'to' must be YYYY-MM-DD", 400)
    if to_date is None:
        to_date = from_date + timedelta(days=DEFAULT_RANGE_DAYS - 1)

    # Пределы берутся из того же модуля, что и расчёт, а не объявляются здесь
    # своими числами: два предела на один диапазон разъехались бы молча.
    if from_date > to_date:
        return _error("bad_request", "'from' must be <= 'to'", 400)
    if (to_date - from_date).days >= MAX_RANGE_DAYS:
        return _error("bad_request", f"range exceeds {MAX_RANGE_DAYS} days", 400)
    return from_date, to_date


@require_http_methods(["GET"])
@require_admin_role
def master_exceptions(request: HttpRequest, master_id: str) -> HttpResponse:
    """Что уже назначено мастеру: исключения, недоступность, закрытия салона."""

    master = _master_or_none(master_id)
    if master is None:
        return _error("not_found", "master not found", 404)

    parsed = _parse_range(request)
    if isinstance(parsed, JsonResponse):
        return parsed
    from_date, to_date = parsed

    if not getattr(settings, "BOOKING_VIA_AYLA_REST", False):
        # Флаг выключен — салон правит локальные таблицы, и исключения Ayla
        # его расписания не описывают. Показать их значило бы выдать чужие
        # отмены за свои.
        return _error(
            "frame_source_local_unsupported",
            "BOOKING_VIA_AYLA_REST is off: the salon edits local tables and "
            "Ayla's exceptions do not describe them; this view reads Ayla only",
            503,
        )

    tenant = request.tenant  # type: ignore[attr-defined]
    actor_user = _ayla_read_actor(tenant)
    if actor_user is None:
        return _error(
            "schedule_source_not_configured",
            f"no active owner/admin staff to read the schedule for {tenant.slug}",
            503,
        )

    actor = external_user_id_for(actor_user)
    client = get_salon_client()
    window = {"date_from": from_date.isoformat(), "date_to": to_date.isoformat()}

    try:
        exceptions = client.list_schedule_exceptions(
            actor_external_id=actor,
            tenant_slug=tenant.slug,
            specialist_id=str(master.id),
            **window,
        )
        time_off = client.list_time_off(
            actor_external_id=actor,
            tenant_slug=tenant.slug,
            specialist_id=str(master.id),
            **window,
        )
        closures = client.list_closures(
            actor_external_id=actor,
            tenant_slug=tenant.slug,
            **window,
        )
    except SalonNotConfigured as exc:
        return _error("schedule_source_not_configured", str(exc), 503)
    except SalonUnavailable as exc:
        # Названная причина, а не пустые списки: пустота читается как «ничего
        # не назначено», и салон спланировал бы день поверх отгула.
        return _error("schedule_unavailable", str(exc), 503)

    payload: dict[str, Any] = {
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "exceptions": read_rows(exceptions, _exception_row),
        "time_off": read_rows(time_off, _time_off_row),
        "closures": read_rows(closures, _closure_row),
    }
    unreadable = sorted(k for k in LIST_KEYS if payload[k]["state"] == UNREADABLE)
    if unreadable:
        logger.warning(
            "admin_api.master_exceptions.unreadable tenant=%s master=%s keys=%s",
            tenant.slug,
            master.pk,
            ",".join(unreadable),
        )
    payload["unreadable_lists"] = unreadable

    # Записи здесь нет по факту границы, а не по забывчивости — см. докстринг
    # модуля. Поле говорит это экрану, чтобы он не рисовал действий, которых
    # сервер не примет.
    payload["writable"] = False
    return JsonResponse(payload)


__all__ = ["master_exceptions"]
