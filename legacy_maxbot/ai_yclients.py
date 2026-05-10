"""Enrichment action_data через YClients API — для show_slots и show_my_bookings.

Phase 2.3 пост-T11. ai_tool_handlers — side-effect-free валидаторы, реальный
fetch slots/records делается здесь на пути render → клиент.

Вызывается из ai_concierge.send_message ПОСЛЕ dispatch_tool_call,
ДО сохранения Message в БД (чтобы action_data в БД сразу содержал slots).

Если YClients staff_id или ServiceOption.yclients_service_id не настроены —
graceful degradation: action_data['slots']=[] → render показывает «слотов нет,
попробуйте другой день?». Менеджер должен заполнить mapping через /admin/.
"""
from __future__ import annotations

import logging
from typing import Any

from asgiref.sync import sync_to_async

from services_app.models import BotUser, Master, Service, ServiceOption


logger = logging.getLogger("maxbot.ai_yclients")


@sync_to_async
def _load_masters_yclients_info(master_ids: list[int]) -> dict[int, tuple[str, str | None]]:
    """Returns {master_id: (yclients_staff_id, yclients_service_id)} для availability check.

    yclients_service_id берём первый попавшийся из любой услуги мастера —
    нужен только чтобы спросить YClients «есть ли вообще время».
    Мастера без yclients_staff_id пропускаем (не можем проверить → оставляем).
    """
    result: dict[int, tuple[str, str | None]] = {}
    masters = (
        Master.objects
        .filter(id__in=master_ids, is_active=True)
        .prefetch_related("services")
    )
    for m in masters:
        if not m.yclients_staff_id:
            continue
        service_ids = list(m.services.values_list("id", flat=True))
        yc_svc_id = (
            ServiceOption.objects
            .filter(service_id__in=service_ids, yclients_service_id__isnull=False)
            .exclude(yclients_service_id="")
            .values_list("yclients_service_id", flat=True)
            .first()
        )
        result[m.id] = (str(m.yclients_staff_id), str(yc_svc_id) if yc_svc_id else None)
    return result


@sync_to_async
def _load_master_and_service(master_id: int, service_id: int):
    """Возвращает (master, service, yclients_service_id из ServiceOption или None)."""
    master = Master.objects.filter(id=master_id, is_active=True).first()
    service = Service.objects.filter(id=service_id, is_active=True).first()
    yc_svc_id = (
        ServiceOption.objects
        .filter(service_id=service_id, yclients_service_id__isnull=False)
        .exclude(yclients_service_id="")
        .order_by("order", "id")
        .values_list("yclients_service_id", flat=True)
        .first()
    )
    return master, service, yc_svc_id


async def enrich_show_slots(action_data: dict[str, Any], *, yclients_api=None) -> dict[str, Any]:
    """Mutates action_data — добавляет master_name, service_name, slots.

    Возвращает мутированный dict (для chain-like usage). На любом fail
    устанавливает slots=[] чтобы render показал «нет слотов».
    """
    master_id = action_data.get("master_id")
    service_id = action_data.get("service_id")
    date_str = action_data.get("date")

    if not master_id or not service_id or not date_str:
        action_data["slots"] = []
        return action_data

    master, service, yc_svc_id = await _load_master_and_service(master_id, service_id)
    action_data["master_name"] = master.name if master else "—"
    action_data["service_name"] = service.name if service else "—"

    # Без mapping native booking невозможен → empty slots, render покажет
    # «нет слотов», потом user через ask_clarification спросит другую дату/услугу.
    if not master or not master.yclients_staff_id or not yc_svc_id:
        logger.info(
            "enrich_show_slots: no yclients mapping, master_id=%s service_id=%s",
            master_id, service_id,
        )
        action_data["slots"] = []
        return action_data

    if yclients_api is None:
        from services_app.yclients_api import get_yclients_api
        yclients_api = get_yclients_api()

    try:
        # YClients get_available_times → List[str] в формате "HH:MM"
        times = await sync_to_async(yclients_api.get_available_times)(
            staff_id=int(master.yclients_staff_id),
            date=date_str,
            service_ids=[int(yc_svc_id)],
        )
        slot_strs = [str(t) for t in (times or [])]

        time_pref = action_data.get("time_preference")
        if time_pref in ("morning", "afternoon", "evening"):
            ranges = {
                "morning": (6, 12),
                "afternoon": (12, 17),
                "evening": (17, 22),
            }
            lo, hi = ranges[time_pref]

            def _hour_in_range(s: str) -> bool:
                # Поддерживаем "HH:MM" и "HH:MM:SS" одинаково — берём первую часть.
                try:
                    return lo <= int(s.split(":")[0]) < hi
                except (ValueError, IndexError):
                    return False

            slot_strs = [s for s in slot_strs if _hour_in_range(s)]

        # Берём top-12 чтобы UI не уходил в стену кнопок (3 кнопки/ряд × 4 ряда)
        action_data["slots"] = slot_strs[:12]
        logger.info(
            "enrich_show_slots: %d slots for master=%s service=%s date=%s",
            len(action_data["slots"]), master_id, service_id, date_str,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("enrich_show_slots: YClients fetch failed: %s", exc)
        action_data["slots"] = []

    return action_data


@sync_to_async
def _master_name_by_yclients_staff(staff_id) -> str:
    """Резолвит yclients staff_id → наше имя (для записи)."""
    if not staff_id:
        return "—"
    m = Master.objects.filter(yclients_staff_id=str(staff_id)).first()
    return m.name if m else f"staff#{staff_id}"


async def enrich_show_my_bookings(
    action_data: dict[str, Any],
    bot_user: BotUser,
    *,
    yclients_api=None,
) -> dict[str, Any]:
    """Mutates action_data — добавляет bookings (list[dict]) для рендера.

    Если у bot_user нет client_phone — empty list (render: «нет записей»).
    Filter из action_data (upcoming/past/all) применяется на сервере YClients
    параметром start/end дат если возможно, иначе локально.
    """
    filter_value = action_data.get("filter") or "upcoming"
    phone = (bot_user.client_phone or "").strip()
    if not phone:
        action_data["bookings"] = []
        return action_data

    if yclients_api is None:
        from services_app.yclients_api import get_yclients_api
        yclients_api = get_yclients_api()

    try:
        # YClients get_records принимает client_phone (или client_id)
        records = await sync_to_async(yclients_api.get_records)(
            client_phone=phone,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("enrich_show_my_bookings: YClients fetch failed: %s", exc)
        action_data["bookings"] = []
        return action_data

    bookings: list[dict[str, Any]] = []
    for rec in records or []:
        # YClients record shape: {datetime, services: [{title}], staff: {name}, ...}
        services = rec.get("services") or []
        staff = rec.get("staff") or {}
        bookings.append({
            "datetime": rec.get("datetime") or rec.get("date") or "",
            "master_name": staff.get("name") or "—",
            "service_name": services[0].get("title") if services else "—",
        })

    # Filter upcoming/past локально (YClients API не всегда умеет фильтр).
    # YClients может возвращать datetime в naive формате ("2026-04-30T14:00:00")
    # или с offset ("...+03:00"). Приводим обе стороны к Moscow-aware (Europe/Moscow)
    # перед сравнением — иначе TypeError "naive vs aware" → пустой список записей.
    if filter_value in ("upcoming", "past"):
        from datetime import datetime as dt_cls
        try:
            from zoneinfo import ZoneInfo
            MSK = ZoneInfo("Europe/Moscow")
        except Exception:  # noqa: BLE001
            from datetime import timezone, timedelta
            MSK = timezone(timedelta(hours=3))
        now = dt_cls.now(tz=MSK)
        filtered: list[dict[str, Any]] = []
        for b in bookings:
            try:
                bdt = dt_cls.fromisoformat(b["datetime"])
                if bdt.tzinfo is None:
                    bdt = bdt.replace(tzinfo=MSK)
                if filter_value == "upcoming" and bdt >= now:
                    filtered.append(b)
                elif filter_value == "past" and bdt < now:
                    filtered.append(b)
            except (ValueError, TypeError):
                continue
        bookings = filtered

    action_data["bookings"] = bookings[:10]
    logger.info(
        "enrich_show_my_bookings: %d bookings filter=%s phone=%s",
        len(bookings), filter_value, phone[:4] + "...",
    )
    return action_data


async def enrich_show_masters(action_data: dict[str, Any], *, yclients_api=None) -> dict[str, Any]:
    """Фильтрует мастеров по доступности в YClients на запрошенную дату.

    Если date не передана или YClients не настроен — возвращает как есть.
    Если на дату нет ни одного доступного мастера — оставляет masters=[] и
    устанавливает all_busy_date чтобы render показал кнопки «другой день».
    """
    date_str = action_data.get("date") or ""
    masters = action_data.get("masters") or []

    if not date_str or not masters:
        return action_data

    master_ids = [
        item.get("master", {}).get("id")
        for item in masters
        if item.get("master", {}).get("id") is not None
    ]
    if not master_ids:
        return action_data

    yclients_info = await _load_masters_yclients_info(master_ids)
    # master_id → (staff_id, service_id). Мастера без mapping проверить нельзя → оставляем.
    unmappable_ids = {mid for mid in master_ids if mid not in yclients_info}

    if yclients_api is None:
        from services_app.yclients_api import get_yclients_api
        yclients_api = get_yclients_api()

    busy_ids: set[int] = set()
    for master_id, (staff_id, yc_svc_id) in yclients_info.items():
        if not yc_svc_id:
            # Нет маппинга услуги — не можем проверить, оставляем
            continue
        try:
            times = await sync_to_async(yclients_api.get_available_times)(
                staff_id=int(staff_id),
                date=date_str,
                service_ids=[int(yc_svc_id)],
            )
            if not times:
                busy_ids.add(master_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "enrich_show_masters: YClients check failed master=%s date=%s: %s",
                master_id, date_str, exc,
            )

    if not busy_ids:
        # Все доступны или не удалось проверить — ничего не меняем
        return action_data

    available_masters = [
        item for item in masters
        if item.get("master", {}).get("id") not in busy_ids
        or item.get("master", {}).get("id") in unmappable_ids
    ]

    if not available_masters:
        # Все заняты → пустой список + сигнал для рендера
        action_data["masters"] = []
        action_data["all_busy_date"] = date_str
        logger.info(
            "enrich_show_masters: all %d masters busy on %s → show date fallback",
            len(masters), date_str,
        )
    else:
        removed = len(masters) - len(available_masters)
        action_data["masters"] = available_masters
        logger.info(
            "enrich_show_masters: filtered %d busy master(s) on %s, %d remain",
            removed, date_str, len(available_masters),
        )

    return action_data
