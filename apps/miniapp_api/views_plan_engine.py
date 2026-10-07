"""Plan Engine — прокси Mini App к сборке плана в каталоге (DRF-2879, WP7 часть 1).

    POST /customer/plan/decision  → собрать эфемерный план по действующей цели

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
    "customer_plan_decision",
    "plan_decision_payload",
    "plan_engine_enabled",
    "plan_safety_input",
]
