"""Tool-call handlers — валидация LLM args + формирование action_data.

Phase 2.3 §T03. Адаптация Ayla/djangoproject/ai/tools_handlers.py.

Handlers SIDE-EFFECT-FREE:
- Не делают network calls (YClients fetch — в render layer ai_ui.py / T07)
- Не пишут в БД (создание BookingRequest — в ai_action_service.py / T09)
- Только validate args + формируют action_data для UI

Anti-hallucination: каждый handler фильтрует ID через
`context.candidate_ids` / `context.candidate_service_ids` (frozenset из
реальной БД). Если LLM выдумал ID — `_fallback_clarification` возвращает
ask_clarification вместо raise. Клиент НИКОГДА не видит сломанную карточку.

dispatch_tool_call(tc, context) — главный диспетчер по tool name.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from maxbot.ai_context import MasterContext
from maxbot.ai_tools import ActionType
from services_app.models import Master, Service


logger = logging.getLogger("maxbot.ai_tools")


@dataclass(frozen=True)
class ToolResult:
    """Что handler возвращает в AIConcierge для записи в Message.action_*"""

    action_type: str
    action_data: dict[str, Any]


# ─── Helpers ──────────────────────────────────────────────────────────────


def _safe_int(value: Any) -> int | None:
    """Defensive int cast. None / "abc" / [] → None."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _fallback_clarification(reason: str, *, question: str = "") -> ToolResult:
    """LLM выдал bad args — bounce на clarification вместо raise.

    Reason — для логов/audit. Question — опциональный override (по умолчанию
    общий «уточните»).
    """
    logger.warning("ai.tool_call.fallback reason=%s", reason)
    return ToolResult(
        action_type=ActionType.ASK_CLARIFICATION,
        action_data={
            "question": question or "Уточните, пожалуйста, что вы хотели бы найти?",
            "options": [],
        },
    )


# ─── handle_show_masters ──────────────────────────────────────────────────


def handle_show_masters(args: dict[str, Any], context: MasterContext) -> ToolResult:
    """Validate master_ids в context.candidate_ids, drop invalid silently.

    Anti-hallucination: LLM выдумал ID — фильтруем. Если все невалидные →
    fallback. Partial result (часть валидных) лучше чем dead chat turn.
    """
    raw_ids = args.get("master_ids") or []
    scores = args.get("match_scores") or []
    reasons = args.get("match_reasons") or []
    explanation = args.get("explanation") or ""
    date_str = args.get("date") or ""

    valid_ids = [_safe_int(rid) for rid in raw_ids]
    valid_ids = [vid for vid in valid_ids if vid is not None and vid in context.candidate_ids]

    if not valid_ids:
        return _fallback_clarification("show_masters_no_valid_ids")

    by_id = {c.id: c for c in context.candidates}
    masters: list[dict[str, Any]] = []
    for idx, mid in enumerate(valid_ids):
        c = by_id[mid]
        # Preview для UI — направления (категории), не отдельные услуги.
        # Категории информативнее «услуга_X, услуга_Y» — клиент видит
        # «Лазерная эпиляция, Уходы для лица, RF-лифтинг», а не случайный
        # набор из 5 услуг внутри этих же категорий.
        seen: set[str] = set()
        categories: list[str] = []
        for _id, _name, cat, _hc in c.services:
            if cat and cat not in seen:
                seen.add(cat)
                categories.append(cat)
        masters.append({
            "master": {
                "id": c.id,
                "name": c.name,
                "specialization": c.specialization,
                "categories_preview": categories[:5],
            },
            "match_score": scores[idx] if idx < len(scores) else None,
            "match_reasons": reasons[idx] if idx < len(reasons) else [],
        })

    return ToolResult(
        action_type=ActionType.SHOW_MASTERS,
        action_data={"masters": masters, "explanation": explanation, "date": date_str},
    )


# ─── handle_show_slots ────────────────────────────────────────────────────


def handle_show_slots(args: dict[str, Any], context: MasterContext) -> ToolResult:
    """Validate (master_id, service_id) в context + ISO date.

    Реальный fetch слотов из YClients — в render layer (T07), здесь только
    валидация args + базовая проверка что master может оказывать услугу.
    """
    master_id = _safe_int(args.get("master_id"))
    service_id = _safe_int(args.get("service_id"))
    date_str = args.get("date") or ""

    if master_id is None or master_id not in context.candidate_ids:
        return _fallback_clarification("show_slots_invalid_master_id")
    if service_id is None or service_id not in context.candidate_service_ids:
        return _fallback_clarification("show_slots_invalid_service_id")
    try:
        target_date = date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return _fallback_clarification("show_slots_invalid_date")

    # Проверим что master.services включает service (cross-validation)
    master = next((c for c in context.candidates if c.id == master_id), None)
    if master is None:
        return _fallback_clarification("show_slots_master_lost")
    master_service_ids = {t[0] for t in master.services}
    if service_id not in master_service_ids:
        return _fallback_clarification(
            "show_slots_master_does_not_offer_service",
            question=f"Этот мастер не оказывает данную услугу. Подобрать другого?",
        )

    action_data: dict[str, Any] = {
        "master_id": master_id,
        "service_id": service_id,
        "date": target_date.isoformat(),
    }
    time_preference = args.get("time_preference")
    if time_preference in ("morning", "afternoon", "evening"):
        action_data["time_preference"] = time_preference

    return ToolResult(
        action_type=ActionType.SHOW_SLOTS,
        action_data=action_data,
    )


# ─── handle_confirm_booking ───────────────────────────────────────────────


def handle_confirm_booking(args: dict[str, Any], context: MasterContext) -> ToolResult:
    """Validate (master_id, service_id) + ISO datetime, load имена для UI.

    Не создаёт BookingRequest — это в ai_action_service.execute_confirm_booking
    после клика клиента «✅ Да».
    """
    master_id = _safe_int(args.get("master_id"))
    service_id = _safe_int(args.get("service_id"))
    datetime_str = args.get("datetime") or ""

    if master_id is None or master_id not in context.candidate_ids:
        return _fallback_clarification("confirm_booking_invalid_master_id")
    if service_id is None or service_id not in context.candidate_service_ids:
        return _fallback_clarification("confirm_booking_invalid_service_id")
    try:
        slot = datetime.fromisoformat(datetime_str)
    except (ValueError, TypeError):
        return _fallback_clarification("confirm_booking_invalid_datetime")

    # Load Master + Service для UI рендера (имя, цена, длительность)
    master = Master.objects.filter(id=master_id, is_active=True).first()
    service = Service.objects.filter(id=service_id, is_active=True).first()
    if master is None or service is None:
        return _fallback_clarification("confirm_booking_orm_load_failed")

    return ToolResult(
        action_type=ActionType.CONFIRM_BOOKING,
        action_data={
            "master_id": master_id,
            "service_id": service_id,
            "datetime": slot.isoformat(),
            "master_name": master.name,
            "service_name": service.name,
            # price_from / duration_min — для UI карточки
            "price_from": str(service.price_from) if service.price_from else None,
            "duration_min": service.duration_min if hasattr(service, "duration_min") else None,
        },
    )


# ─── handle_show_my_bookings ──────────────────────────────────────────────


def handle_show_my_bookings(args: dict[str, Any]) -> ToolResult:
    """Validate filter enum, default upcoming. Fetch — в render layer (T07)."""
    raw_filter = args.get("filter") or "upcoming"
    if raw_filter not in {"upcoming", "past", "all"}:
        raw_filter = "upcoming"
    return ToolResult(
        action_type=ActionType.SHOW_MY_BOOKINGS,
        action_data={"filter": raw_filter},
    )


# ─── handle_recommend_services (Phase 2.4 T02) ────────────────────────────


def handle_recommend_services(args: dict[str, Any]) -> ToolResult:
    """Подобрать топ-4 услуги по goals + ранжировать по is_popular.

    Валидация: 1-3 goal'а из SERVICE_GOAL_VALUES. Если LLM передал что-то
    несуществующее — фильтруем; если пусто после фильтра — fallback ask_clarification.

    Каждая услуга в action_data.services содержит id, name, short_description,
    price_from, duration_min, goals — render layer показывает карточки.
    """
    from services_app.models import SERVICE_GOAL_VALUES, Service

    raw_goals = args.get("goals") or []
    explanation = (args.get("explanation") or "").strip()

    valid_goals = [g for g in raw_goals if g in SERVICE_GOAL_VALUES]
    if not valid_goals:
        return _fallback_clarification(
            "recommend_services_no_valid_goals",
            question="Уточните, пожалуйста, что для вас важнее всего — расслабиться, "
                     "снять боль, тонизировать или что-то другое?",
        )

    qs = (
        Service.objects.active()
        .with_any_goal(valid_goals)
        .with_category()
        .order_by("-is_popular", "order", "name")[:4]
    )
    services_data = []
    for svc in qs:
        services_data.append({
            "id": svc.id,
            "name": svc.name,
            "short_description": svc.short_description or "",
            "price_from": str(svc.price_from) if svc.price_from else None,
            "duration_min": svc.duration_min,
            "category": svc.category.name if svc.category else "",
            "goals": list(svc.goals or []),
            "slug": svc.slug or "",
            "requires_health_check": bool(svc.requires_health_check),
            "contraindications": svc.contraindications or "",
        })

    if not services_data:
        # Нет услуг под эту цель — graceful: ask_clarification с fallback
        return _fallback_clarification(
            "recommend_services_no_match",
            question="Мы не нашли подходящих услуг под этот запрос. "
                     "Хотите посмотреть популярные услуги или связаться с менеджером?",
        )

    return ToolResult(
        action_type=ActionType.RECOMMEND_SERVICES,
        action_data={
            "goals": valid_goals,
            "explanation": explanation,
            "services": services_data,
        },
    )


# ─── handle_ask_clarification ─────────────────────────────────────────────


def handle_ask_clarification(args: dict[str, Any]) -> ToolResult:
    """Pass-through. Если LLM эмиттит пустой question → fallback с дефолтом."""
    question = (args.get("question") or "").strip()
    options = args.get("options") or []
    if not question:
        return _fallback_clarification("ask_clarification_empty_question")
    return ToolResult(
        action_type=ActionType.ASK_CLARIFICATION,
        action_data={"question": question, "options": options},
    )


# ─── dispatch_tool_call ───────────────────────────────────────────────────


def dispatch_tool_call(tool_call, context: MasterContext) -> ToolResult:
    """Главный диспетчер. Принимает OpenAI tool_call object, возвращает ToolResult.

    Безопасность: невалидный JSON в arguments / unknown tool name →
    fallback на ask_clarification, не raise.
    """
    name = getattr(tool_call.function, "name", "") or ""
    raw_args = getattr(tool_call.function, "arguments", "") or "{}"

    try:
        args = json.loads(raw_args)
    except (json.JSONDecodeError, ValueError):
        return _fallback_clarification(f"invalid_json_arguments_in_{name}")

    if name == ActionType.SHOW_MASTERS:
        return handle_show_masters(args, context)
    if name == ActionType.SHOW_SLOTS:
        return handle_show_slots(args, context)
    if name == ActionType.CONFIRM_BOOKING:
        return handle_confirm_booking(args, context)
    if name == ActionType.SHOW_MY_BOOKINGS:
        return handle_show_my_bookings(args)
    if name == ActionType.RECOMMEND_SERVICES:
        return handle_recommend_services(args)
    if name == ActionType.ASK_CLARIFICATION:
        return handle_ask_clarification(args)

    return _fallback_clarification(f"unknown_tool_{name}")
