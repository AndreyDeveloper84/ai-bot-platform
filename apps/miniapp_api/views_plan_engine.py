"""Plan Engine — прокси Mini App к плану в каталоге (DRF-2879, DRF-2876).

    POST /customer/plan/decision  → собрать эфемерный план по действующей цели
    GET  /customer/plan/current   → сохранённый план (см. :func:`customer_plan_current`)

Каталог собирает план, но не вычисляет два входа — их приносит бот:

* **реестр планировочных правил** — вендоренная копия из ayla-knowledge,
  читается :func:`apps.planning_rules.registry.load_registry` на каждый запрос
  и уходит в теле в форме :func:`apps.planning_rules.wire.registry_wire_body`.
  Реестр не загрузился — запрос в каталог НЕ уходит, экран получает 503
  ``plan_rules_unavailable``. Пустой или частичный реестр не подставляется:
  каталог принял бы его за «правил нет», а это другое утверждение;
* **состояние безопасности** — см. :func:`plan_safety_input`.

Тело от экрана — только ``excluded_capability_refs`` (способности, которые
человек убрал; перечень держит экран, каталог между вызовами ничего не
помнит). Субъект — ``external_user_id_for(bot_user)`` из подписанных данных
Mini App: чужого идентификатора в запросе не существует как понятия.

Ответ каталога уходит экрану как есть: ``outcome`` и, только при ``PLAN``,
``decision``. Исходы без плана (``SAFETY_BLOCKED``, ``NO_GOAL``,
``NO_CURATED_DECOMPOSITION``, ``PLAN_NOT_JUSTIFIED``) — штатные ответы 200,
не ошибки.

Флаг ``PLAN_ENGINE_ENABLED`` выключен → 404 ``plan_engine_disabled`` ДО
чтения реестра и ДО каталога. Тела в лог не идут — только исход и класс.
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
from apps.integrations.ayla.plan_engine_client import (
    PlanEngineAuthError,
    PlanEngineConfigError,
    PlanEngineContractError,
    PlanEngineDisabledError,
    PlanEngineHttpClient,
)
from apps.miniapp_api.views import _error, require_init_data
from apps.planning_rules.registry import PlanningRegistryError, load_registry
from apps.planning_rules.wire import registry_wire_body

logger = logging.getLogger(__name__)

#: Состояние безопасности, когда бот его НЕ ОЦЕНИВАЛ. Каталог читает
#: ``UNKNOWN`` как запрет (контракт Plan Engine §12) и отвечает
#: ``SAFETY_BLOCKED`` — план не строится.
SAFETY_STATE_NOT_EVALUATED = "UNKNOWN"
#: Версия политики при неоценённом состоянии: названо, что оценки не было, —
#: версию политики, которая не применялась, выдумывать нельзя.
SAFETY_POLICY_NOT_EVALUATED = "bot:not-evaluated"

#: Сколько убранных способностей экран может назвать за раз.
MAX_EXCLUDED_REFS = 50
MAX_REF_LEN = 128


def plan_engine_enabled() -> bool:
    return bool(getattr(settings, "PLAN_ENGINE_ENABLED", False))


def plan_safety_input(bot_user: BotUser) -> tuple[str, str]:
    """Состояние безопасности и версия политики для сборки плана.

    Сегодня — всегда «не оценивалось». Движок безопасности бота судит о
    ТЕКСТЕ входящего сообщения; у экрана текста нет, а вердикта о человеке
    вне сообщения в боте не существует. Слать ``NORMAL`` без оценки значило
    бы объявить безопасность проверенной; ``NOT_APPLICABLE`` каталог
    отвергает — сборка плана чувствительна к безопасности.

    Поэтому до появления настоящего источника сборка честно отвечает
    ``SAFETY_BLOCKED``. Источник подключается ЗДЕСЬ — одно место, и узел
    ``test_s1`` держит, что до тех пор уходит именно «не оценивалось».
    """
    return SAFETY_STATE_NOT_EVALUATED, SAFETY_POLICY_NOT_EVALUATED


def plan_decision_payload(document: dict[str, Any]) -> dict[str, Any]:
    """Ответ каталога → JSON экрана: имя исхода и решение, только когда оно есть."""
    return {
        "outcome": document["outcome"],
        "decision": document.get("decision") if document["outcome"] == "PLAN" else None,
    }


def _excluded_refs(request: HttpRequest) -> list[str] | JsonResponse:
    """Убранные способности из тела; пустое тело — «ничего не убрано»."""
    if not request.body:
        return []
    try:
        body = json.loads(request.body)
    except ValueError:
        return _error("malformed", "body is not valid JSON", 400)
    if not isinstance(body, dict):
        return _error("malformed", "body must be a JSON object", 400)
    refs = body.get("excluded_capability_refs", [])
    if (
        not isinstance(refs, list)
        or len(refs) > MAX_EXCLUDED_REFS
        or not all(isinstance(r, str) and r.strip() and len(r) <= MAX_REF_LEN for r in refs)
    ):
        return _error(
            "malformed", "excluded_capability_refs must be a list of capability keys", 400
        )
    return [r.strip() for r in refs]


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
def customer_plan_decision(request: HttpRequest) -> HttpResponse:
    """POST — собрать эфемерный план. Ничего не сохраняет."""
    from apps.integrations.ayla import external_user_id_for

    if not plan_engine_enabled():
        return _error("plan_engine_disabled", "plan engine is not enabled", 404)

    excluded = _excluded_refs(request)
    if isinstance(excluded, JsonResponse):
        return excluded

    try:
        rules_registry = registry_wire_body(load_registry())
    except PlanningRegistryError as exc:
        # Fail-closed: без реестра каталог не спрашивается. Пустой реестр
        # вместо настоящего был бы утверждением «правил нет».
        logger.error("customer_plan_decision.rules_unavailable class=%s", type(exc).__name__)
        return _error("plan_rules_unavailable", "planning rules registry is not available", 503)

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    safety_state, safety_policy_version = plan_safety_input(bot_user)
    try:
        document = PlanEngineHttpClient().compose_decision(
            external_user_id=external_user_id_for(bot_user),
            safety_state=safety_state,
            safety_policy_version=safety_policy_version,
            rules_registry=rules_registry,
            excluded_capability_refs=excluded,
        )
    except Exception as exc:  # noqa: BLE001 — каждый класс назван в _refusal
        return _refusal(exc)

    logger.info(
        "customer_plan_decision.done bot_user=%s outcome=%s excluded=%d",
        bot_user.pk,
        document["outcome"],
        len(excluded),
    )
    return JsonResponse(plan_decision_payload(document))


def saved_plan_payload(plan: dict[str, Any], labels: dict[str, str]) -> dict[str, Any] | None:
    """Сохранённый план → JSON экрана: идентификаторы и ПОДПИСИ шагов.

    Ключ способности экрану не уходит — человеку его показывать нельзя, а
    экрану он для показа не нужен. ``None`` — план показать нельзя: шагов нет
    или у какого-то шага нет подтверждённой подписи. Частичный список не
    отдаётся: план без одного шага выглядел бы целым планом.
    """
    raw_revision = plan.get("revision")
    revision: dict[str, Any] = raw_revision if isinstance(raw_revision, dict) else {}
    steps = [s for s in revision.get("steps") or [] if isinstance(s, dict)]
    out: list[dict[str, str]] = []
    for step in steps:
        label = labels.get(str(step.get("capability_ref") or ""))
        step_id = step.get("step_id")
        if not label or not isinstance(step_id, str) or not step_id:
            return None
        out.append({"step_id": step_id, "label": label})
    if not out:
        return None
    return {"plan_id": str(plan.get("plan_id") or ""), "steps": out}


@require_http_methods(["GET"])
@require_init_data
def customer_plan_current(request: HttpRequest) -> HttpResponse:
    """GET — сохранённый план нового механизма подписями каталога.

    Решение владельца (лист 07.10, п.9): раздел «Мой план» один; сохранённый
    новый план — основной. Экран спрашивает эту ручку первой и показывает
    прежний план, только когда здесь ``plan: null``.

    Просмотр вердикта безопасности не требует и ограничениями не закрыт.

    * флаг выключен → 404 ``plan_engine_disabled`` до каталога (экран
      показывает прежний план, как сегодня);
    * плана нет → 200 ``{"plan": null}``;
    * у шага нет подтверждённой подписи → 502 ``plan_step_unlabelled``: это
      пробел данных куратора, громко в лог; экран не подставляет прежний
      план вместо сохранённого нового.
    """
    from apps.integrations.ayla import external_user_id_for

    if not plan_engine_enabled():
        return _error("plan_engine_disabled", "plan engine is not enabled", 404)

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    external_user_id = external_user_id_for(bot_user)
    try:
        client = PlanEngineHttpClient()
        plan = client.get_plan(external_user_id=external_user_id)
        if plan is None:
            return JsonResponse({"plan": None})
        raw_revision = plan.get("revision")
        steps = raw_revision.get("steps") if isinstance(raw_revision, dict) else None
        keys = [
            str(s.get("capability_ref") or "")
            for s in (steps if isinstance(steps, list) else [])
            if isinstance(s, dict)
        ]
        labels = (
            client.capability_labels(external_user_id=external_user_id, keys=keys) if keys else {}
        )
    except Exception as exc:  # noqa: BLE001 — каждый класс назван в _refusal
        return _refusal(exc)

    payload = saved_plan_payload(plan, labels)
    if payload is None:
        logger.error(
            "customer_plan_current.unlabelled bot_user=%s steps=%d labelled=%d",
            bot_user.pk,
            len(keys),
            len(labels),
        )
        return _error("plan_step_unlabelled", "a plan step has no confirmed label", 502)
    logger.info("customer_plan_current.done bot_user=%s steps=%d", bot_user.pk, len(keys))
    return JsonResponse({"plan": payload})


def _refusal(exc: Exception) -> JsonResponse:
    if isinstance(exc, PlanEngineDisabledError):
        return _error("plan_engine_disabled", "plan engine is not enabled", 404)
    if isinstance(exc, PlanEngineContractError):
        # Каталог отверг НАШ запрос — это наш дефект, а не ошибка человека:
        # громко в лог (имя нарушения — не данные), экрану — недоступность.
        logger.error("customer_plan_decision.contract_violation reason=%s", exc.reason)
        return _error("ayla_unavailable", "plan engine rejected the request", 502)
    if isinstance(exc, PlanEngineConfigError):
        logger.error("customer_plan_decision.config_error class=%s", type(exc).__name__)
        return _error("not_configured", "ayla plan engine not configured", 503)
    if isinstance(exc, PlanEngineAuthError):
        logger.error("customer_plan_decision.auth_error class=%s", type(exc).__name__)
        return _error("ayla_unavailable", "ayla plan engine unavailable", 502)
    logger.warning("customer_plan_decision.unavailable class=%s", type(exc).__name__)
    return _error("ayla_unavailable", "ayla plan engine unavailable", 502)


__all__ = [
    "SAFETY_POLICY_NOT_EVALUATED",
    "SAFETY_STATE_NOT_EVALUATED",
    "customer_plan_current",
    "customer_plan_decision",
    "plan_decision_payload",
    "plan_engine_enabled",
    "plan_safety_input",
    "saved_plan_payload",
]
