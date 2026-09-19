"""Кнопки уведомлений-решений салонного бота — что делает тап (DRF-2118).

Принимает ``cb:salon:n:<kind>:<ref>:<action>`` из
:mod:`apps.channels.max.salon_notify` и возвращает текст ответа. Решения
идут **теми же сервисами, что у Admin Mini App** — логика решения здесь не
дублируется, только выбирается:

* ``schedule`` — ``approve`` → :func:`apps.channels.max.staff_actions.approve_request`
  (→ ``approve_availability_request``); ``reject`` →
  ``reject_availability_request`` с причиной-константой
  :data:`REJECT_REASON_BY_CODE`; ``details`` — диф «Было/Станет» и impact §142;
* ``handoff`` — ``return`` → :func:`apps.handoff.services.release_conversation_to_bot`;
  ``open`` — дверь в кабинет (без настроенной ссылки — словами);
* ``sync`` — ``retry`` → задача ``sync_catalog_for_tenant``; ``details`` —
  возраст и последний успех синхронизации.

Решает **только админ-сторона** (владелец / админ / ресепшн по правилам
меню); мастер, нажавший кнопку из чужого сообщения, получает не отказ, а
объяснение — и ничего не меняется.

**Предел причины отказа:** в чате причина — константа по коду, читается
мастером по-человечески («Отклонено владельцем в чате; подробности
спросите у администратора»). Причина словами — только из Mini App, где её
можно набрать и увидеть, что отклоняешь.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from apps.channels.max import salon_notify

logger = logging.getLogger(__name__)

#: Причины отказа по коду — единственный источник текста, который мастер
#: увидит в DM решения (``dispatch_master_decision_dm``).
REJECT_REASON_BY_CODE: dict[str, str] = {
    "chat_declined": "Отклонено владельцем в чате; подробности спросите у администратора",
}

NOT_ALLOWED = "Решение по этому уведомлению принимает администратор салона."
UNKNOWN = "Эта кнопка больше не действует."


def handle_action(*, payload: str, tenant: Any, bot_user: Any, role_ctx: Any) -> str:
    """Выполнить тап по кнопке уведомления. Никогда не бросает."""

    parsed = salon_notify.parse_callback(payload)
    if parsed is None:
        return UNKNOWN
    kind, ref, action = parsed
    is_admin_side = bool(
        getattr(role_ctx, "is_owner", False)
        or getattr(role_ctx, "is_admin", False)
        or getattr(role_ctx, "is_receptionist", False)
    )
    if not is_admin_side:
        logger.info(
            "channels.max.salon_notify.action_refused kind=%s action=%s bot_user=%s",
            kind,
            action,
            getattr(bot_user, "id", None),
        )
        return NOT_ALLOWED

    try:
        if kind == "schedule":
            return _schedule(action, ref, tenant, bot_user)
        if kind == "handoff":
            return _handoff(action, ref, tenant)
        if kind == "sync":
            return _sync(action, tenant)
    except Exception:  # noqa: BLE001 — тап по кнопке не должен ронять обработчик
        logger.exception(
            "channels.max.salon_notify.action_crashed kind=%s action=%s ref=%s", kind, action, ref
        )
        return "Не получилось выполнить действие. Попробуйте из кабинета салона."
    return UNKNOWN


# ── тип 2 ────────────────────────────────────────────────────────────


def _schedule(action: str, ref: str, tenant: Any, bot_user: Any) -> str:
    from apps.channels.max import staff_actions

    if action == "approve":
        return staff_actions.approve_request(tenant=tenant, request_id=ref, actor=bot_user)
    if action == "reject":
        return _reject(ref, tenant, bot_user)
    if action == "details":
        return _schedule_details(ref, tenant)
    return UNKNOWN


def _reject(ref: str, tenant: Any, bot_user: Any) -> str:
    from apps.admin_api.services.availability import (
        AvailabilityDecisionError,
        reject_availability_request,
    )

    try:
        parsed = UUID(str(ref))
    except (ValueError, AttributeError):
        return "Заявка не найдена."
    try:
        reject_availability_request(
            request_id=parsed,
            tenant_id=tenant.id,
            actor=None,
            actor_bot_user_id=getattr(bot_user, "id", None),
            actor_role="admin",
            rejection_reason=REJECT_REASON_BY_CODE["chat_declined"],
        )
    except AvailabilityDecisionError as exc:
        slug = getattr(exc, "slug", "")
        if slug == "already_decided":
            return "Эту заявку уже рассмотрели."
        if slug == "not_found":
            return "Заявка не найдена."
        logger.warning("channels.max.salon_notify.reject_failed slug=%s request=%s", slug, ref)
        return "Не получилось отклонить заявку. Попробуйте из кабинета салона."
    return "Заявка отклонена. Мастер получит уведомление."


def _schedule_details(ref: str, tenant: Any) -> str:
    from apps.admin_api.services import schedule_impact as si
    from apps.scheduling.models import ScheduleChangeRequest

    try:
        parsed = UUID(str(ref))
    except (ValueError, AttributeError):
        return "Заявка не найдена."
    request = (
        ScheduleChangeRequest.all_tenants.filter(id=parsed, tenant=tenant)
        .select_related("master", "tenant")
        .first()
    )
    if request is None:
        return "Заявка не найдена."
    day_labels, was, becomes = salon_notify.schedule_diff(request, request.master, tenant)
    impact = salon_notify.schedule_request_impact(request)
    status = getattr(request, "status", "")
    status_line = {
        "pending": "Решение ещё не принято.",
        "approved": "Заявка одобрена.",
        "rejected": "Заявка отклонена.",
    }.get(str(status), "")
    lines = [
        f"{request.master.name} — {', '.join(day_labels)}",
        f"Было: {was}",
        f"Станет: {becomes}",
        si.affected_phrase(si.Impact(state=impact.state, affected=impact.affected)),
    ]
    reason_class = getattr(request, "reason_class", "") or ""
    if reason_class:
        lines.append(f"Класс причины: {reason_class}")  # свободный текст причины не показываем
    if status_line:
        lines.append(status_line)
    return "\n".join(lines)


# ── тип 1 ────────────────────────────────────────────────────────────


def _handoff(action: str, ref: str, tenant: Any) -> str:
    from apps.handoff.models import AdminTask
    from apps.handoff.notify import admin_task_url

    try:
        parsed = UUID(str(ref))
    except (ValueError, AttributeError):
        return "Диалог не найден."
    task = AdminTask.all_tenants.filter(id=parsed, tenant=tenant).first()
    if task is None:
        return "Диалог не найден."
    if action == "return":
        from apps.handoff.services import release_conversation_to_bot

        if release_conversation_to_bot(task):
            return "Диалог возвращён Ayla."
        return "Этот диалог уже закрыт."
    if action == "open":
        url = admin_task_url(task.id)
        return f"Открыть диалог: {url}" if url else "Диалог — в кабинете салона."
    return UNKNOWN


# ── тип 3 ────────────────────────────────────────────────────────────


def _sync(action: str, tenant: Any) -> str:
    if action == "retry":
        from apps.catalog.tasks import sync_catalog_for_tenant

        sync_catalog_for_tenant.delay(str(tenant.id))
        return "Запустила синхронизацию каталога. Сообщу, если не пройдёт снова."
    if action == "details":
        from apps.catalog.staleness import sync_ages

        for age in sync_ages():
            if age.slug == tenant.slug:
                last = age.last_ok_at.isoformat(timespec="minutes") if age.last_ok_at else "никогда"
                return (
                    f"Последняя удачная синхронизация: {last}. "
                    f"Порог: {age.threshold_seconds // 60} мин. "
                    "Причину ищет оператор в логах воркера; повторить можно кнопкой."
                )
        return "Сведений о синхронизации для этого салона нет."
    return UNKNOWN


__all__ = ["NOT_ALLOWED", "REJECT_REASON_BY_CODE", "UNKNOWN", "handle_action"]
