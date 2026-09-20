"""Plan Lite — прокси Mini App к каталогу под субъектом (DRF-2101, §49).

    GET    /customer/plan-lite           → wellness-context.plan_lite
    POST   /customer/plan-lite           → создать план {goal_id?, actions[1..3], template_version?}
    DELETE /customer/plan-lite           → закрыть план (append-only)
    GET    /customer/plan-lite/proposal  → предложение из шаблона цели (DRF-2123, План-A)

План-A (DRF-2123): предложение — ``{goal_key, why, template_version, actions}``
из шаблона активной цели, ничего не создаёт; экран подтверждает его тем же
POST с ``template_version`` (провенанс плана в каталоге). Отказы предложения —
своими слагами: 404 ``no_active_goal`` / ``no_template`` (второй — не ошибка
для экрана: конструктор без блока).

Первый живой вызывающий ``wellness_context_client`` (до этого — только
запертая проактивность). Форму тела проверяет каталог (400 → ``ayla_bad_request``);
этот прыжок — только JSON-объект и субъект: ``external_user_id_for(bot_user)``
из подписанного initData, чужой id в запросе не существует как понятие.

Флаг ``PLAN_LITE_ENABLED`` выключен → 404 ``plan_lite_disabled`` ДО вызова
каталога (штатно, не 5xx). Отказы каталога — по имени: 409 ``already_active``,
404 ``not_found`` (цель не у человека / нет активного плана) и
``plan_lite_disabled`` (каталог выключен при включённом боте), 502
``ayla_unavailable``, 503 ``not_configured``. Тела ответов в логах нет —
только статус и класс (DRF-2009).

В-5: экран получает ровно то, что несёт DTO — форму обязательства и факт
``done_count``; процентов и «достигнуто» у DTO нет полей. DRF-2124: у
``log_food`` — ещё факт ``within_target_count`` (целое или ``null`` —
«ориентира нет», не 0, §103); у воды и брони ключа нет, как в каталоге.

Отдельный модуль, а не :mod:`apps.miniapp_api.views`: тот сегодня в работе
у соседей; ворота и отказ импортированы оттуда.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.identity.models import BotUser
from apps.integrations.ayla.wellness_context_client import (
    PlanLite,
    PlanLiteAction,
    PlanLiteAlreadyActiveError,
    PlanLiteDisabledError,
    PlanLiteGoalNotFoundError,
    PlanLiteNoTemplateError,
    PlanLiteProposal,
    WellnessContextAuthError,
    WellnessContextClientError,
    WellnessContextConfigError,
    WellnessContextHttpClient,
    WellnessContextUnavailableError,
)
from apps.miniapp_api.views import _error, require_init_data

logger = logging.getLogger(__name__)


def plan_lite_enabled() -> bool:
    return bool(getattr(settings, "PLAN_LITE_ENABLED", False))


def plan_lite_payload(plan: PlanLite | None) -> dict[str, Any] | None:
    """DTO → JSON экрана; ``bucket`` собирается обратно в объект контракта."""
    if plan is None:
        return None
    return {
        "plan_id": plan.plan_id,
        "goal_key": plan.goal_key,
        "actions": [_action_payload(a) for a in plan.actions],
    }


def _action_payload(a: PlanLiteAction) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action_type": a.action_type,
        "cadence": a.cadence,
        "target_count": a.target_count,
        "done_count": a.done_count,
        "bucket": {"start": a.bucket_start, "end": a.bucket_end},
    }
    if a.action_type == "log_food":
        # DRF-2124: ключ есть всегда — ``null`` и «0 дней в ориентире» разные факты.
        payload["within_target_count"] = a.within_target_count
    return payload


def plan_lite_proposal_payload(proposal: PlanLiteProposal) -> dict[str, Any]:
    """DTO предложения → JSON экрана: ключ цели, «почему», версия шаблона и
    форма действий. Фактов и результата у предложения нет по построению."""
    return {
        "goal_key": proposal.goal_key,
        "why": proposal.why,
        "template_version": proposal.template_version,
        "actions": [
            {
                "action_type": a.action_type,
                "cadence": a.cadence,
                "target_count": a.target_count,
            }
            for a in proposal.actions
        ],
    }


def _refusal(exc: Exception, *, step: str) -> JsonResponse:
    if isinstance(exc, PlanLiteDisabledError):
        return _error("plan_lite_disabled", "plan lite is not enabled", 404)
    if isinstance(exc, PlanLiteAlreadyActiveError):
        return _error("already_active", "an active plan already exists; close it first", 409)
    if isinstance(exc, PlanLiteGoalNotFoundError):
        # У предложения нет активной цели — своим слагом (DRF-2123); у
        # писателей — прежний not_found (цель не у человека / нет плана).
        if step == "proposal":
            return _error("no_active_goal", "no active goal to propose a plan for", 404)
        return _error("not_found", "goal or active plan not found", 404)
    if isinstance(exc, PlanLiteNoTemplateError):
        return _error("no_template", "no plan template for the active goal", 404)
    if isinstance(exc, WellnessContextConfigError):
        logger.error("customer_plan_lite.%s.config_error class=%s", step, type(exc).__name__)
        return _error("not_configured", "ayla wellness not configured", 503)
    if isinstance(exc, WellnessContextAuthError):
        logger.error("customer_plan_lite.%s.auth_error class=%s", step, type(exc).__name__)
        return _error("not_configured", "ayla wellness auth failed", 503)
    if isinstance(exc, WellnessContextClientError):
        return _error("ayla_bad_request", "ayla rejected the request", 400)
    if isinstance(exc, WellnessContextUnavailableError):
        logger.warning("customer_plan_lite.%s.unavailable class=%s", step, type(exc).__name__)
        return _error("ayla_unavailable", "ayla wellness unavailable", 502)
    raise exc


@csrf_exempt
@require_http_methods(["GET", "POST", "DELETE"])
@require_init_data
def customer_plan_lite(request: HttpRequest) -> HttpResponse:
    from apps.integrations.ayla import external_user_id_for

    if not plan_lite_enabled():
        return _error("plan_lite_disabled", "plan lite is not enabled", 404)

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    external_id = external_user_id_for(bot_user)
    client = WellnessContextHttpClient()

    if request.method == "GET":
        try:
            ctx = client.get_wellness_context(external_user_id=external_id)
        except Exception as exc:  # noqa: BLE001 — каждый класс назван в _refusal
            return _refusal(exc, step="get")
        return JsonResponse({"plan_lite": plan_lite_payload(ctx.plan_lite)})

    if request.method == "DELETE":
        try:
            client.close_plan_lite(external_user_id=external_id)
        except Exception as exc:  # noqa: BLE001
            return _refusal(exc, step="close")
        return JsonResponse({"closed": True})

    content_type = (request.content_type or "").split(";")[0].strip().lower()
    if content_type != "application/json" or not request.body:
        return _error("malformed", "expected a JSON body", 400)
    try:
        body = json.loads(request.body)
    except ValueError:
        return _error("malformed", "body is not valid JSON", 400)
    if not isinstance(body, dict):
        return _error("malformed", "body must be a JSON object", 400)
    goal_id = body.get("goal_id")
    actions = body.get("actions")
    # goal_id необязателен (PR-2b): экран его не знает — активную цель берёт
    # каталог; передан — прокидывается как есть, форму проверяет каталог.
    if goal_id is not None and (not isinstance(goal_id, str) or not goal_id.strip()):
        return _error("malformed", "goal_id must be a non-empty string when given", 400)
    if not isinstance(actions, list):
        return _error("malformed", "actions must be a list", 400)
    # template_version необязателен (DRF-2123): дан — целое ≥ 1 (bool — не
    # целое), прокидывается как есть; существование версии проверяет каталог.
    template_version = body.get("template_version")
    if template_version is not None and (
        not isinstance(template_version, int)
        or isinstance(template_version, bool)
        or template_version < 1
    ):
        return _error("malformed", "template_version must be an integer >= 1 when given", 400)
    try:
        plan = client.create_plan_lite(
            external_user_id=external_id,
            actions=actions,
            goal_id=goal_id.strip() if isinstance(goal_id, str) else None,
            template_version=template_version,
        )
    except Exception as exc:  # noqa: BLE001
        return _refusal(exc, step="create")
    logger.info(
        "customer_plan_lite.created bot_user=%s actions=%d template_version=%s",
        bot_user.pk,
        len(plan.actions),
        template_version,
    )
    return JsonResponse({"plan_lite": plan_lite_payload(plan)}, status=201)


@csrf_exempt
@require_http_methods(["GET"])
@require_init_data
def customer_plan_lite_proposal(request: HttpRequest) -> HttpResponse:
    """GET — предложение Plan Lite из шаблона активной цели (DRF-2123, План-A).

    Ничего не создаёт: экран показывает его и подтверждает POST'ом с
    ``template_version``. Флаг выключен → 404 ``plan_lite_disabled`` ДО
    каталога; 404 ``no_active_goal`` / ``no_template`` — по имени.
    """
    from apps.integrations.ayla import external_user_id_for

    if not plan_lite_enabled():
        return _error("plan_lite_disabled", "plan lite is not enabled", 404)

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    external_id = external_user_id_for(bot_user)
    client = WellnessContextHttpClient()
    try:
        proposal = client.get_plan_lite_proposal(external_user_id=external_id)
    except Exception as exc:  # noqa: BLE001 — каждый класс назван в _refusal
        return _refusal(exc, step="proposal")
    return JsonResponse({"proposal": plan_lite_proposal_payload(proposal)})


__all__ = [
    "customer_plan_lite",
    "customer_plan_lite_proposal",
    "plan_lite_enabled",
    "plan_lite_payload",
    "plan_lite_proposal_payload",
]
