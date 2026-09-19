"""Сколько живых записей вытеснит закрытие времени мастера — один читатель (§142, DRF-2118).

До DRF-2118 этот вопрос умела задавать только вьюха
``views_schedule_impact.py`` (Admin Mini App, срез В §150) — с полным
разбором отказов каталога в HTTP-коды. Уведомление-решение «мастер просит
изменить график» обязано назвать «затронуто N» тем же чтением, а не своим:
два читателя одного вопроса разошлись бы в ответе в первый же день.

Здесь — само чтение и его исход; вьюха и рендерер уведомления только
переводят исход на свой язык (JSON / строка текста). Отказ каталога
**никогда не превращается в ноль**: пустота читается как «никого не
затронет», и салон закрыл бы время поверх живых записей.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from apps.admin_api.services.wire_lists import UNREADABLE, read_rows
from apps.catalog.specialist_ref import CatalogSpecialistUnresolved, catalog_specialist_id
from apps.integrations.ayla.salon_client import (
    SalonAPIError,
    SalonForbidden,
    SalonNotConfigured,
    SalonUnavailable,
    SalonValidationError,
    get_salon_client,
)
from apps.integrations.ayla.user_proxy import external_user_id_for

logger = logging.getLogger(__name__)

#: Исходы чтения. ``ok`` — список прочитан (``affected`` — число);
#: остальные — названные причины, при которых ``affected`` — ``None``.
OK = "ok"
UNAVAILABLE = "unavailable"  # сеть / 5xx / отказ каталога
NOT_CONFIGURED = "not_configured"  # нет читающего актора, REST выключен, каталог не настроен
FORBIDDEN = "forbidden"  # каталог отказал актору салона (DRF-2087)
BAD_WINDOW = "bad_window"  # окно отвергнуто каталогом
UNRESOLVED = "unresolved"  # у зеркала нет id профиля в каталоге (DRF-1933)


@dataclass(frozen=True)
class Impact:
    """Исход чтения impact. ``affected`` есть только при ``state == OK``."""

    state: str
    affected: int | None = None
    #: Разобранные строки записей и состояние списка — для вьюхи (JSON).
    bookings: dict[str, Any] = field(default_factory=dict)
    timezone: str | None = None
    #: Slug и подробность отказа — для HTTP-ответа вьюхи и для лога.
    error_slug: str = ""
    error_detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == OK


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


def impact_for_window(tenant: Any, master: Any, *, start_at: str, end_at: str) -> Impact:
    """Какие живые записи вытеснит отсутствие мастера над ``[start_at, end_at)``.

    Ayla читается от имени активного владельца, иначе активного админа
    салона (``_ayla_read_actor``) — тот же порядок, что у остальных чтений
    салонной поверхности. Границы — ISO-строки со смещением салона, как их
    строит вьюха (``_parse_window``) или рендерер уведомления.
    """

    from apps.master_api.services.schedule_frame import _ayla_read_actor

    if not getattr(settings, "BOOKING_VIA_AYLA_REST", False):
        return Impact(
            NOT_CONFIGURED,
            error_slug="frame_source_local_unsupported",
            error_detail=(
                "BOOKING_VIA_AYLA_REST is off: the salon edits local tables and "
                "Ayla's bookings do not describe them; this view reads Ayla only"
            ),
        )

    actor_user = _ayla_read_actor(tenant)
    if actor_user is None:
        return Impact(
            NOT_CONFIGURED,
            error_slug="schedule_source_not_configured",
            error_detail=f"no active owner/admin staff to read the schedule for {tenant.slug}",
        )

    # DRF-1933: у строки зеркала нет id профиля в каталоге — звать каталог
    # не с чем; первичный ключ зеркала туда не уходит.
    try:
        specialist_id = catalog_specialist_id(master)
    except CatalogSpecialistUnresolved:
        return Impact(
            UNRESOLVED,
            error_slug="catalog_profile_unresolved",
            error_detail="master is not set up in the catalog yet",
        )

    try:
        raw = get_salon_client().get_schedule_impact(
            actor_external_id=external_user_id_for(actor_user),
            tenant_slug=tenant.slug,
            specialist_id=specialist_id,
            start_at=start_at,
            end_at=end_at,
        )
    except SalonValidationError as exc:
        return Impact(BAD_WINDOW, error_slug="bad_window", error_detail=str(exc))
    except SalonNotConfigured as exc:
        return Impact(
            NOT_CONFIGURED, error_slug="schedule_source_not_configured", error_detail=str(exc)
        )
    except SalonUnavailable as exc:
        # Названная причина, а не пустой список: пустота читается как «никого
        # не затронет», и салон закрыл бы время поверх живых записей.
        return Impact(UNAVAILABLE, error_slug="schedule_unavailable", error_detail=str(exc))
    except SalonForbidden as exc:
        # DRF-2087 — 403 каталога по имени, как у действий (отмена, поиск
        # клиентов). Это не сбой, а факт настройки: человек, от чьего имени
        # салон читает (владелец/админ), в каталоге не администратор этого
        # салона. У 403 есть ход оператора, и он назван.
        logger.warning(
            "admin_api.schedule_impact.forbidden actor=%s tenant=%s err=%s",
            actor_user.pk,
            tenant.slug,
            exc,
        )
        return Impact(
            FORBIDDEN,
            error_slug="salon_forbidden",
            error_detail=(
                f"catalog refused the salon actor for {tenant.slug}: the owner/admin "
                "this read is named to is not an administrator of this salon in the "
                "catalog — relink them there (provision_salon_admin) and retry; "
                "nothing was read"
            ),
        )
    except SalonAPIError as exc:
        # DRF-2087 — страховочная сеть: следующий подкласс, которого сегодня
        # нет, и 401/404, которых здесь не ждали — без сети каждый был бы
        # 500 без имени.
        logger.warning(
            "admin_api.schedule_impact.salon_error class=%s tenant=%s err=%s",
            type(exc).__name__,
            tenant.slug,
            exc,
        )
        return Impact(
            UNAVAILABLE,
            error_slug="schedule_unavailable",
            error_detail=(
                f"catalog refused to read the schedule impact ({type(exc).__name__}); "
                "nothing was read"
            ),
        )

    raw_rows = raw.get("bookings")
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
    # Нечитаемые строки — тоже люди: считаются в «затронуто», а не выпадают.
    affected = len(bookings["rows"]) + bookings["unreadable_rows"]
    return Impact(
        OK, affected=affected, bookings=bookings, timezone=_str_or_none(raw.get("timezone"))
    )


def affected_phrase(impact: Impact) -> str:
    """«Затронута одна запись» / «Затронуто записей: N» / названное отсутствие.

    Ноль — только когда список прочитан и пуст; любой отказ каталога —
    словами, чтобы владелица не приняла «не смогли прочитать» за «никого».
    """

    if impact.ok and impact.affected is not None:
        n = impact.affected
        if n == 0:
            return "Записей не затронуто"
        if n == 1:
            return "Затронута одна запись"
        return f"Затронуто записей: {n}"
    if impact.state == NOT_CONFIGURED:
        return "Затронутые записи: не удалось прочитать (источник не настроен)"
    if impact.state == FORBIDDEN:
        return "Затронутые записи: не удалось прочитать (каталог отказал салону)"
    if impact.state == UNRESOLVED:
        return "Затронутые записи: не удалось прочитать (мастер не заведён в каталоге)"
    return "Затронутые записи: источник не ответил"


__all__ = [
    "BAD_WINDOW",
    "FORBIDDEN",
    "Impact",
    "NOT_CONFIGURED",
    "OK",
    "UNAVAILABLE",
    "UNRESOLVED",
    "affected_phrase",
    "impact_for_window",
]
