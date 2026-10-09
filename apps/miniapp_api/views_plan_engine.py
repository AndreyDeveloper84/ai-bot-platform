"""Plan Engine — прокси Mini App к плану в каталоге (DRF-2879, DRF-2876).

    POST /customer/plan/decision  → собрать эфемерный план по действующей цели
    GET  /customer/plan/current   → сохранённый план и предложение рядом с ним
    POST /customer/plan/replace   → заменить действующий план предложением
    POST /customer/plan/keep      → отказаться от предложения, оставить действующий
    POST /customer/plan/save      → сохранить несохранённое предложение из чата
    POST /customer/plan/step      → шаг → услуги → время → запись (см. :func:`customer_plan_step`)

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
from datetime import datetime
import re
import logging
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.identity.models import BotUser
from apps.integrations.ayla.plan_engine_client import (
    PlanConsentRequiredError,
    PlanDeletionInProgressError,
    PlanEngineAuthError,
    PlanEngineConfigError,
    PlanEngineContractError,
    PlanEngineDisabledError,
    PlanEngineHttpClient,
)
from apps.miniapp_api.views import _error, require_init_data
from apps.orchestrator.plan_gate import (
    PLAN_BASIS_UNAVAILABLE,
    PLAN_CONSENT_REQUIRED,
    PLAN_DELETION_REQUESTED,
    plan_consent_basis,
    plan_processing_refusal,
)
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

    # DRF-2967 — основание раньше всего остального: без согласия на
    # хранение или при живой заявке на удаление каталог о человеке не
    # спрашивается вовсе.
    refused = _basis_refusal(request.bot_user)  # type: ignore[attr-defined]
    if refused is not None:
        return refused

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
            consent=plan_consent_basis(bot_user),
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


def saved_plan_payload(
    plan: dict[str, Any], labels: dict[str, str], effects: dict[str, str] | None = None
) -> dict[str, Any] | None:
    """Сохранённый план → JSON экрана: идентификаторы, ПОДПИСИ шагов и «зачем».

    ``why`` — курируемый «ожидаемый эффект» способности из каталога (решение
    владельца, лист 07.10, п.13: «Почему этот шаг?»). Нет текста — ``null``:
    экран ссылку не показывает, текст не сочиняется.

    Ключ способности экрану не уходит — человеку его показывать нельзя, а
    экрану он для показа не нужен. ``None`` — план показать нельзя: шагов нет
    или у какого-то шага нет подтверждённой подписи. Частичный список не
    отдаётся: план без одного шага выглядел бы целым планом.
    """
    raw_revision = plan.get("revision")
    revision: dict[str, Any] = raw_revision if isinstance(raw_revision, dict) else {}
    steps = [s for s in revision.get("steps") or [] if isinstance(s, dict)]
    out: list[dict[str, Any]] = []
    for step in steps:
        key = str(step.get("capability_ref") or "")
        label = labels.get(key)
        step_id = step.get("step_id")
        if not label or not isinstance(step_id, str) or not step_id:
            return None
        out.append(
            {
                "step_id": step_id,
                "label": label,
                "why": (effects or {}).get(key) or None,
                "booked_at": _booked_at(plan, step_id),
            }
        )
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
    * плана нет → 200 ``{"plan": null, "proposal": null}``;
    * рядом с ДЕЙСТВУЮЩИМ планом есть предложение → ``proposal`` с теми же
      полями шагов и ``replaces_plan_id`` — планом, о замене которого экран
      спросит человека. Предложение, которое нечем показать (шаг без
      подписи), не отдаётся — действующий план от этого не страдает;
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
        plan, proposal = client.get_plan_and_proposal(external_user_id=external_user_id)
        if plan is None:
            # Предложение существует только рядом с действующим планом: без
            # него заменять нечего, и спрашивать о замене не о чем.
            return JsonResponse({"plan": None, "proposal": None, "draft": _draft_for(bot_user)})
        keys = _step_keys(plan)
        # Предложение показывается только рядом с ДЕЙСТВУЮЩИМ планом: у
        # приостановленного заменять нечего (каталог заменяет действующий).
        if plan.get("status") != "active":
            proposal = None
        proposal_keys = _step_keys(proposal) if proposal is not None else []
        wanted = list(dict.fromkeys([*keys, *proposal_keys]))
        details = (
            client.capability_details(external_user_id=external_user_id, keys=wanted)
            if wanted
            else {}
        )
    except Exception as exc:  # noqa: BLE001 — каждый класс назван в _refusal
        return _refusal(exc)

    labels = {key: row["label"] for key, row in details.items()}
    effects = {
        key: row["expected_effect"] for key, row in details.items() if row["expected_effect"]
    }
    payload = saved_plan_payload(plan, labels, effects)
    if payload is None:
        logger.error(
            "customer_plan_current.unlabelled bot_user=%s steps=%d labelled=%d",
            bot_user.pk,
            len(keys),
            len(labels),
        )
        return _error("plan_step_unlabelled", "a plan step has no confirmed label", 502)
    # Шаг без «зачем» — пробел знания для куратора: человеку ссылка не
    # показывается, а счёт таких шагов виден в журнале.
    proposal_payload = None
    if proposal is not None:
        proposal_payload = saved_plan_payload(proposal, labels, effects)
        if proposal_payload is None:
            logger.error(
                "customer_plan_current.proposal_unlabelled bot_user=%s steps=%d",
                bot_user.pk,
                len(proposal_keys),
            )
        else:
            proposal_payload["replaces_plan_id"] = payload["plan_id"]
    logger.info(
        "customer_plan_current.done bot_user=%s steps=%d without_why=%d proposal=%s",
        bot_user.pk,
        len(keys),
        sum(1 for key in keys if key not in effects),
        proposal_payload is not None,
    )
    return JsonResponse(
        {"plan": payload, "proposal": proposal_payload, "draft": _draft_for(bot_user)}
    )


#: Статусы записи каталога, при которых запись от шага действует.
_LIVE_BOOKING = frozenset({"pending", "awaiting_payment", "confirmed"})


def _booked_at(plan: dict[str, Any], step_id: str) -> str | None:
    """Время записи, сделанной от этого шага, — или ``None``.

    Каталог хранит у шага только идентификаторы услуги и записи и время
    записи; названий услуги и мастера в состоянии шага нет, и экран их не
    показывает. Считается только действующая запись (:data:`_LIVE_BOOKING`):
    отменённая, прошедшая и запись с неизвестным статусом — нет.

    Каталог отдаёт это время в UTC, а экран берёт часы из строки (DRF-2589),
    поэтому момент переписывается в пояс салона. Салона записи от шага бот
    не знает (его нет в зеркале), и пояс берётся запасной — названный в
    ``apps.tenancy.timezones`` (Москва). Для салона в другом поясе часы будут
    неверны, пока каталог не отдаст пояс записи. Неразобранное время не
    показывается.
    """
    from apps.tenancy.timezones import salon_iso, salon_zone

    states = plan.get("step_state")
    state = states.get(step_id) if isinstance(states, dict) else None
    bookings = state.get("bookings") if isinstance(state, dict) else None
    if not isinstance(bookings, list):
        return None
    for booking in reversed(bookings):
        if (
            isinstance(booking, dict)
            and isinstance(booking.get("start_datetime"), str)
            and booking["start_datetime"]
            and booking.get("status") in _LIVE_BOOKING
        ):
            try:
                moment = datetime.fromisoformat(booking["start_datetime"])
            except ValueError:
                continue
            return salon_iso(moment, salon_zone(None))
    return None


def _step_keys(plan: dict[str, Any]) -> list[str]:
    raw_revision = plan.get("revision")
    steps = raw_revision.get("steps") if isinstance(raw_revision, dict) else None
    return [
        str(s.get("capability_ref") or "")
        for s in (steps if isinstance(steps, list) else [])
        if isinstance(s, dict)
    ]


def _person_conversation(bot_user: BotUser) -> Any:
    """Действующий разговор человека с ботом — или ``None``. Только читается.

    Чат Mini App и бот — один разговор глобальной оболочки MAX; экран его не
    создаёт. Человек не из MAX, оболочки или разговора нет — ``None``.
    """
    from apps.conversations.services import resolve_active_global_conversation
    from apps.identity.services.global_tenant import get_global_bot_tenant

    if (bot_user.channel or "") != "max":
        return None
    person_id = (bot_user.channel_user_id or "").strip()
    if not person_id:
        return None
    shell = BotUser.all_tenants.filter(
        tenant=get_global_bot_tenant(), channel="max", channel_user_id=person_id
    ).first()
    if shell is None:
        return None
    return resolve_active_global_conversation(shell, create_if_missing=False)


def _draft_for(bot_user: BotUser) -> dict[str, Any] | None:
    """Несохранённое предложение, собранное в чате, — для показа на экране.

    Задание владельца (§9): Mini App сохраняет без сообщения «сохрани» в чат.
    Предложение у чата и экрана общее — оно лежит в состоянии разговора.

    Под гейтом согласия (DRF-2967) НЕ отдаётся — решение автора гейта:
    открыт просмотр СОХРАНЁННОГО плана, а несохранённое предложение — это
    результат обработки, который планом ещё не стал. Закрыто молча
    (``null``), а не отказом всей ручки: иначе закрылось бы и чтение своего
    плана. Любой сбой — тоже ``null``: чтение плана из-за черновика не падает.
    """
    from apps.orchestrator.plan_engine_card import pending_proposal_view

    try:
        if plan_processing_refusal(bot_user) is not None:
            return None
        conversation = _person_conversation(bot_user)
        if conversation is None:
            return None
        return pending_proposal_view(conversation)
    except Exception:  # noqa: BLE001 — черновик не должен ронять чтение плана
        logger.warning("customer_plan.draft_unavailable bot_user=%s", bot_user.pk, exc_info=True)
        return None


def last_turn_safety_for(bot_user: BotUser) -> Any:
    """Вердикт ПОСЛЕДНЕГО хода разговора этого человека — или ``None``.

    У нажатия на экране своего вердикта нет: экран несёт вердикт последнего
    хода чата вместе с его ревизией, чтобы «стоп» минуту назад не обходился
    кнопкой на экране (решение главного окна 08.10). Чат Mini App и бот —
    один разговор глобальной оболочки человека.

    ``None`` — действие с планом слать нельзя: человек не из MAX, разговора
    нет, он истёк (два часа без хода) или последний ход вердикта не записал.
    Разговор здесь только читается — экран его не создаёт.
    """
    from apps.orchestrator.safety.plan_turn import last_turn_safety

    try:
        conversation = _person_conversation(bot_user)
        if conversation is None:
            return None
        return last_turn_safety(conversation.id)
    except Exception:  # noqa: BLE001 — нет вердикта → нет действия, ручка не падает
        logger.warning(
            "customer_plan.last_turn_safety_unavailable bot_user=%s", bot_user.pk, exc_info=True
        )
        return None


#: Опознаватель карточки предложения — тот же, что в кнопках чата.
_DRAFT_TOKEN_RE = re.compile(r"^[0-9a-f]{8}$")

#: Исход сохранения → отказ экрану. Имена исходов — карточки плана; экран
#: показывает слаг с пометкой «тест», пока слов владельца нет.
_SAVE_REFUSALS: dict[str, tuple[str, int]] = {
    "PLAN_PROPOSAL_EXPIRED": ("plan_proposal_expired", 409),
    "SAFETY_INPUT_UNAVAILABLE": ("plan_safety_unavailable", 409),
    "PLAN_SAVE_SAFETY_BLOCKED": ("plan_safety_blocked", 409),
    "GOAL_NOT_FOUND": ("plan_goal_not_found", 409),
    "PLAN_CAPABILITY_NOT_CONFIRMED": ("plan_proposal_expired", 409),
    PLAN_DELETION_REQUESTED: ("deletion_requested", 423),
    PLAN_CONSENT_REQUIRED: ("plan_consent_required", 403),
    PLAN_BASIS_UNAVAILABLE: ("plan_basis_unavailable", 503),
}


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
def customer_plan_save(request: HttpRequest) -> HttpResponse:
    """POST — сохранить несохранённое предложение, собранное в чате.

    Тело: ``{token}`` — опознаватель показанной карточки (тот же, что в
    кнопке «Сохранить» чата). Сохраняется именно показанная версия; повторное
    нажатие дубля не создаёт (слова владельца, лист 07.10, п.15).

    Это запись:

    * под гейтом согласия (DRF-2967) — до разбора тела и до ядра;
    * с вердиктом последнего хода разговора: нет вердикта → 409
      ``plan_safety_unavailable``, каталог не спрашивается.

    Ответ 200 ``{"saved": true}``. У цели уже действует план — каталог
    сохранит новый предложением; экран перечитывает сохранённое и видит его
    сам (``proposal`` в чтении), ответ один и тот же.
    """
    from apps.orchestrator.plan_engine_card import save_pending

    if not plan_engine_enabled():
        return _error("plan_engine_disabled", "plan engine is not enabled", 404)
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    refused = _basis_refusal(bot_user)
    if refused is not None:
        return refused
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _error("malformed", "body is not valid JSON", 400)
    token = body.get("token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not _DRAFT_TOKEN_RE.match(token):
        return _error("malformed", "token must be the proposal card token", 400)

    try:
        conversation = _person_conversation(bot_user)
    except Exception:  # noqa: BLE001 — нет разговора → нет предложения
        logger.warning(
            "customer_plan_save.conversation_unavailable bot_user=%s", bot_user.pk, exc_info=True
        )
        conversation = None
    if conversation is None:
        return _error("plan_proposal_expired", "there is no proposal to save", 409)

    outcome = save_pending(
        bot_user=bot_user,
        conversation=conversation,
        token=token,
        safety=last_turn_safety_for(bot_user),
        trace_id=str(getattr(request, "trace_id", "") or ""),
    )
    if outcome.saved:
        logger.info("customer_plan_save.done bot_user=%s", bot_user.pk)
        return JsonResponse({"saved": True})
    logger.info("customer_plan_save.refused bot_user=%s outcome=%s", bot_user.pk, outcome.name)
    if outcome.name in _SAVE_REFUSALS:
        error, status = _SAVE_REFUSALS[outcome.name]
        return _error(error, "the plan is not saved", status)
    # Каталог недоступен или отверг НАШ запрос — человеку это одна и та же
    # недоступность; имя уже в журнале карточки.
    return _error("ayla_unavailable", "ayla plan engine unavailable", 502)


#: Действие экрана с шагом → вид нажатия ядра.
_STEP_ACTIONS = {"offers": "step", "choose": "offer", "book": "slot"}
_STEP_TOKEN_RE = re.compile(r"^[0-9a-f]{8}$")

#: Исход действия с шагом → отказ экрану; причины каталога идут своим именем.
_STEP_REFUSALS: dict[str, tuple[str, int]] = {
    "SAFETY_INPUT_UNAVAILABLE": ("plan_safety_unavailable", 409),
    "PLAN_STEP_EXPIRED": ("plan_step_expired", 409),
    "PLAN_STEP_NO_SLOTS": ("plan_step_no_slots", 409),
    "PLAN_STEP_BOOKING_NOT_AVAILABLE": ("plan_step_booking_not_available", 409),
    "HEALTH_CHECK_REQUIRED": ("health_check_required", 409),
    "AYLA_LINK_UNAVAILABLE": ("ayla_unavailable", 502),
    "PLAN_ENGINE_UNAVAILABLE": ("ayla_unavailable", 502),
    "PLAN_CONTRACT_VIOLATION": ("ayla_unavailable", 502),
    PLAN_DELETION_REQUESTED: ("deletion_requested", 423),
    PLAN_CONSENT_REQUIRED: ("plan_consent_required", 403),
    PLAN_BASIS_UNAVAILABLE: ("plan_basis_unavailable", 503),
}


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
def customer_plan_step(request: HttpRequest) -> HttpResponse:
    """POST — от шага сохранённого плана к услуге, времени и записи.

    Тело: ``{action, token, index}``:

    * ``offers`` — услуги для шага: ``token`` — первые восемь знаков
      идентификатора плана, ``index`` — место шага в плане;
    * ``choose`` — выбор услуги: ``token`` — опознаватель подбора из ответа
      ``offers``, ``index`` — номер варианта;
    * ``book`` — запись: тот же ``token``, ``index`` — номер времени.

    Тот же путь, что кнопками в чате (ядро общее, состояние разговора общее).
    Каждое действие — обработка по плану человека, поэтому:

    * под гейтом согласия (DRF-2967) — до разбора тела и до ядра;
    * с вердиктом последнего хода разговора: нет вердикта → 409
      ``plan_safety_unavailable``, каталог не спрашивается.

    Ответ 200 — состояние пути словами каталога: ``options`` (варианты),
    ``slots`` (времена выбранного варианта), ``booked_at`` (время записи).
    Идентификаторы услуги и мастера экрану не уходят.
    """
    from apps.orchestrator.plan_step_card import option_view, step_action

    if not plan_engine_enabled():
        return _error("plan_engine_disabled", "plan engine is not enabled", 404)
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    refused = _basis_refusal(bot_user)
    if refused is not None:
        return refused
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _error("malformed", "body is not valid JSON", 400)
    action = body.get("action") if isinstance(body, dict) else None
    token = body.get("token") if isinstance(body, dict) else None
    index = body.get("index") if isinstance(body, dict) else None
    if (
        action not in _STEP_ACTIONS
        or not isinstance(token, str)
        or not _STEP_TOKEN_RE.match(token)
        or isinstance(index, bool)
        or not isinstance(index, int)
        or not 0 <= index <= 99
    ):
        return _error("malformed", "action, token and index are required", 400)

    try:
        conversation = _person_conversation(bot_user)
    except Exception:  # noqa: BLE001 — нет разговора → нет вердикта
        logger.warning(
            "customer_plan_step.conversation_unavailable bot_user=%s", bot_user.pk, exc_info=True
        )
        conversation = None
    if conversation is None:
        # Вердикт экран берёт из разговора; без разговора его нет вовсе.
        return _error("plan_safety_unavailable", "no verdict of the last chat turn", 409)

    done = step_action(
        kind=_STEP_ACTIONS[action],
        token=token,
        index=index,
        bot_user=bot_user,
        conversation=conversation,
        trace_id=str(getattr(request, "trace_id", "") or ""),
        safety=last_turn_safety_for(bot_user),
    )
    state = done.state or {}
    options = [o for o in state.get("options") or [] if isinstance(o, dict)]
    chosen = state.get("chosen")
    slots = [s for s in state.get("slots") or [] if isinstance(s, str)]
    logger.info(
        "customer_plan_step.done bot_user=%s action=%s outcome=%s", bot_user.pk, action, done.name
    )
    if done.name == "PLAN_STEP_OFFERS":
        return JsonResponse({"token": done.token, "options": [option_view(o) for o in options]})
    if done.name == "PLAN_STEP_SLOTS" and isinstance(chosen, int) and chosen < len(options):
        return JsonResponse(
            {"token": done.token, "option": option_view(options[chosen]), "slots": slots}
        )
    if done.name == "PLAN_STEP_BOOKED" and index < len(slots):
        return JsonResponse({"booked_at": slots[index]})
    if done.name in _STEP_REFUSALS:
        error, status = _STEP_REFUSALS[done.name]
        return _error(error, "the step action is refused", status)
    # Причина каталога (нет услуг, шаг не допущен, отказ записи) — своим
    # именем: экран показывает его с пометкой «тест», пока слов владельца нет.
    return _error(done.name.lower().replace(":", "."), "the step action is refused", 409)


def _plan_ids(request: HttpRequest, *names: str) -> dict[str, str] | JsonResponse:
    """Идентификаторы планов из тела — UUID, ничего кроме них."""
    import uuid as _uuid

    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _error("malformed", "body is not valid JSON", 400)
    if not isinstance(body, dict):
        return _error("malformed", "body must be a JSON object", 400)
    out: dict[str, str] = {}
    for name in names:
        raw = body.get(name)
        try:
            out[name] = str(_uuid.UUID(raw)) if isinstance(raw, str) else ""
        except ValueError:
            out[name] = ""
        if not out[name]:
            return _error("malformed", f"{name} must be a plan id", 400)
    return out


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
def customer_plan_replace(request: HttpRequest) -> HttpResponse:
    """POST — заменить действующий план предложением.

    Тело: ``{plan_id, replaces_plan_id}`` — предложение и план, о замене
    которого экран спросил человека («Заменить текущий план новым? Прежний
    останется в истории» — слова владельца, лист 07.10, п.15). Нажатие
    «Заменить план» — само подтверждение.

    Это запись, поэтому:

    * под гейтом согласия (DRF-2967) — до всего остального;
    * с вердиктом последнего хода разговора: нет вердикта → 409
      ``plan_safety_unavailable``, каталог не спрашивается (экран зовёт в
      чат); «стоп» → 409 ``plan_safety_blocked`` от каталога.

    Ответ 200 ``{"replaced": bool}``; ``false`` — замена уже была выполнена
    (повторное нажатие), не ошибка.
    """
    from apps.integrations.ayla import external_user_id_for
    from apps.integrations.ayla.plan_engine_client import (
        PlanNotFoundError,
        PlanReplacementTargetChangedError,
        PlanSaveSafetyBlockedError,
        PlanTransitionRefusedError,
    )

    if not plan_engine_enabled():
        return _error("plan_engine_disabled", "plan engine is not enabled", 404)
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    refused = _basis_refusal(bot_user)
    if refused is not None:
        return refused
    ids = _plan_ids(request, "plan_id", "replaces_plan_id")
    if isinstance(ids, JsonResponse):
        return ids
    safety = last_turn_safety_for(bot_user)
    if safety is None:
        logger.info("customer_plan_replace.no_turn_safety bot_user=%s", bot_user.pk)
        return _error("plan_safety_unavailable", "no verdict of the last chat turn", 409)
    try:
        data = PlanEngineHttpClient().replace_plan(
            external_user_id=external_user_id_for(bot_user),
            plan_id=ids["plan_id"],
            replaces_plan_id=ids["replaces_plan_id"],
            safety_state=safety.safety_state,
            safety_policy_version=safety.safety_policy_version,
            evaluated_at_revision=safety.evaluated_at_revision,
            # DRF-2967 — основание обработки едет с каждой записью плана;
            # отказы каталога по нему (423 / 422) называет общий ``_refusal``.
            consent=plan_consent_basis(bot_user),
        )
    except PlanSaveSafetyBlockedError:
        return _error("plan_safety_blocked", "the plan is not replaced now", 409)
    except PlanReplacementTargetChangedError:
        return _error("plan_replacement_target_changed", "another plan is in effect now", 409)
    except (PlanTransitionRefusedError, PlanNotFoundError):
        return _error("plan_proposal_expired", "this is not a proposal any more", 409)
    except Exception as exc:  # noqa: BLE001 — каждый класс назван в _refusal
        return _refusal(exc)
    logger.info("customer_plan_replace.done bot_user=%s replaced=%s", bot_user.pk, data["replaced"])
    return JsonResponse({"replaced": bool(data["replaced"])})


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
def customer_plan_keep(request: HttpRequest) -> HttpResponse:
    """POST — отказаться от предложения: «Оставить текущий».

    Тело: ``{plan_id}`` — предложение. Оно уходит в архив; действующий план
    не меняется. Вердикта не требует и гейтом согласия не закрыто — это отказ
    от предложения, а не новая обработка (решение автора гейта, DRF-2967).
    Повтор — не ошибка.
    """
    from apps.integrations.ayla import external_user_id_for
    from apps.integrations.ayla.plan_engine_client import (
        PlanNotFoundError,
        PlanTransitionRefusedError,
    )

    if not plan_engine_enabled():
        return _error("plan_engine_disabled", "plan engine is not enabled", 404)
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    ids = _plan_ids(request, "plan_id")
    if isinstance(ids, JsonResponse):
        return ids
    try:
        PlanEngineHttpClient().archive_plan(
            external_user_id=external_user_id_for(bot_user), plan_id=ids["plan_id"]
        )
    except (PlanTransitionRefusedError, PlanNotFoundError):
        return _error("plan_proposal_expired", "this is not a proposal any more", 409)
    except Exception as exc:  # noqa: BLE001 — каждый класс назван в _refusal
        return _refusal(exc)
    logger.info("customer_plan_keep.done bot_user=%s", bot_user.pk)
    return JsonResponse({"kept": True})


#: Имя отказа гейта → (код ошибки, HTTP). 423 при заявке на удаление — как
#: у полки (``customer_shelf``) и у каталога.
_BASIS_REFUSALS: dict[str, tuple[str, int]] = {
    PLAN_DELETION_REQUESTED: ("deletion_requested", 423),
    PLAN_CONSENT_REQUIRED: ("plan_consent_required", 403),
    PLAN_BASIS_UNAVAILABLE: ("plan_basis_unavailable", 503),
}


def _basis_refusal(bot_user: BotUser) -> JsonResponse | None:
    outcome = plan_processing_refusal(bot_user)
    if outcome is None:
        return None
    error, status = _BASIS_REFUSALS[outcome]
    logger.info("customer_plan_decision.refused bot_user=%s reason=%s", bot_user.pk, outcome)
    return _error(error, "the plan is not processed for this person", status)


def _refusal(exc: Exception) -> JsonResponse:
    # DRF-2967 — вторая линия каталога отвечает теми же двумя отказами, что
    # и гейт бота: экран видит одно и то же с любой стороны.
    if isinstance(exc, PlanDeletionInProgressError):
        error, status = _BASIS_REFUSALS[PLAN_DELETION_REQUESTED]
        return _error(error, "the plan is not processed for this person", status)
    if isinstance(exc, PlanConsentRequiredError):
        error, status = _BASIS_REFUSALS[PLAN_CONSENT_REQUIRED]
        return _error(error, "the plan is not processed for this person", status)
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
    "customer_plan_keep",
    "customer_plan_replace",
    "customer_plan_save",
    "customer_plan_step",
    "last_turn_safety_for",
    "plan_decision_payload",
    "plan_engine_enabled",
    "plan_safety_input",
    "saved_plan_payload",
]
