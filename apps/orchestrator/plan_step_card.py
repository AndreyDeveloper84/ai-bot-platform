"""Шаг плана → услуга → время → запись, в чате (DRF-2885, часть А).

Задание владельца: человек может «выполнить доступное действие … через подбор
услуги и запись»; приёмка, действие 5 — «перейти от разрешённого шага к
услуге и создать тестовую запись».

### Путь

1. под сохранённым планом («мой план») — кнопка на каждый шаг;
2. тап по шагу → каталог отдаёт услуги, которыми шаг можно выполнить
   (:func:`_show_offers`); кнопка — на каждую пару «услуга × мастер»;
3. тап по услуге → выбор фиксируется в каталоге, показывается ближайший
   день со свободным временем (:func:`_choose_offer`);
4. тап по времени → запись в каталоге с блоком происхождения «шаг плана»
   (:func:`_book_slot`).

Выбор услуги и времени — всегда нажатие человека на КОНКРЕТНУЮ кнопку: модель
здесь не участвует вовсе, и ничего не выбирается за человека.

### Зеркало каталога не участвует

Существующие пути записи бота ищут услугу и мастера в зеркале каталога; для
шага плана этого делать нельзя — услуга может быть помечена синтетической и в
зеркало не попадает по построению. Все идентификаторы (услуга салона, мастер)
и все слова для показа (услуга, салон, мастер, цена, длительность, адрес)
берутся из ответа каталога о кандидатах и держатся в состоянии разговора.

### Что едет с каждым вызовом

* гейт согласия (DRF-2967) — первой строкой каждого входа;
* четвёрка безопасности: тройка вердикта ЭТОГО хода и длительное ограничение
  S1 (``none`` / ``open`` / ``stop``). Каталог закрывает по нему услугу и
  запись; сборка и просмотр плана им не закрыты;
* основание обработки (``consent``).

### Деньги

Запись от шага создаётся БЕЗ предоплаты и только на услугу, помеченную
каталогом синтетической (решение ждёт владельца: правила предоплаты салона в
каталоге нет, а у ветки плана нет зеркала, откуда обычная запись берёт
настройку салона). Для настоящей услуги запись от шага не создаётся —
:data:`PLAN_STEP_BOOKING_NOT_AVAILABLE`.

### Слова

Слова каталога показываются как есть. Всё остальное — имя исхода с пометкой
«тест»: слов владельца для этих мест нет. Пометка синтетики — пример
владельца (решение 08.10): «Допущено для теста · синтетические данные».
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from apps.skills.base import SkillResult

logger = logging.getLogger(__name__)

CB_STEP_PREFIX = "cb:plan:step:"
CB_OFFER_PREFIX = "cb:plan:offer:"
CB_SLOT_PREFIX = "cb:plan:slot:"
STEP_CALLBACK_RE = re.compile(r"^cb:plan:(step|offer|slot):([0-9a-f]{8}):([0-9]{1,2})$")

#: Свой ключ в ``Conversation.skill_state``.
STATE_KEY = "plan_engine_step"

#: Пометка синтетической услуги — пример владельца (решение 08.10, п.4).
SYNTHETIC_MARK = "Допущено для теста · синтетические данные"

#: Имена исходов, которые рождаются здесь.
PLAN_STEP_OFFERS = "PLAN_STEP_OFFERS"
PLAN_STEP_SLOTS = "PLAN_STEP_SLOTS"
PLAN_STEP_NO_SLOTS = "PLAN_STEP_NO_SLOTS"
PLAN_STEP_BOOKED = "PLAN_STEP_BOOKED"
PLAN_STEP_EXPIRED = "PLAN_STEP_EXPIRED"
PLAN_STEP_BOOKING_NOT_AVAILABLE = "PLAN_STEP_BOOKING_NOT_AVAILABLE"
PLAN_STEP_BOOKING_FAILED = "PLAN_STEP_BOOKING_FAILED"
HEALTH_CHECK_REQUIRED = "HEALTH_CHECK_REQUIRED"
AYLA_LINK_UNAVAILABLE = "AYLA_LINK_UNAVAILABLE"

#: Сколько вариантов и времён показывается кнопками за раз.
MAX_OFFERS = 8
MAX_SLOTS = 8
#: На сколько дней вперёд ищется ближайший день со свободным временем.
SLOT_HORIZON_DAYS = 14

TurnSafetyProvider = Callable[[], Any]


def is_step_callback(text: str) -> bool:
    return bool(STEP_CALLBACK_RE.match((text or "").strip()))


def _hex8(value: Any) -> str:
    return str(value or "").replace("-", "")[:8].lower()


def step_buttons(plan: dict[str, Any], labels: dict[str, str]) -> list[dict[str, str]]:
    """Кнопки шагов под сохранённым планом: подпись — слова каталога.

    Кнопка есть только у шага, у которого есть подпись и который ещё не
    доведён до записи; у плана без идентификатора кнопок нет.
    """
    token = _hex8(plan.get("plan_id"))
    raw_revision = plan.get("revision")
    steps = raw_revision.get("steps") if isinstance(raw_revision, dict) else None
    if len(token) != 8 or not isinstance(steps, list):
        return []
    out: list[dict[str, str]] = []
    for index, step in enumerate(steps[:MAX_OFFERS]):
        key = str(step.get("capability_ref") or "") if isinstance(step, dict) else ""
        label = labels.get(key)
        if label:
            out.append({"label": label, "callback": f"{CB_STEP_PREFIX}{token}:{index}"})
    return out


# ─── состояние ───────────────────────────────────────────────────────────


def _read(conversation: Any) -> dict[str, Any] | None:
    state = getattr(conversation, "skill_state", None)
    row = state.get(STATE_KEY) if isinstance(state, dict) else None
    return row if isinstance(row, dict) else None


def _write(conversation: Any, value: dict[str, Any] | None) -> None:
    from apps.orchestrator.open_question import write_conversation_state

    if value is None and _read(conversation) is None:
        return
    write_conversation_state(conversation, STATE_KEY, value)


# ─── общее для входов ────────────────────────────────────────────────────


def _named(outcome: str) -> SkillResult:
    from apps.orchestrator.plan_engine_card import _named as named

    return named(outcome)


def _reply(lines: list[str], outcome: str, buttons: list[dict[str, str]]) -> SkillResult:
    from apps.orchestrator.discovery import keyboard_envelope
    from apps.orchestrator.next_steps import menu_button, next_step_action_data
    from apps.orchestrator.plan_engine_card import TEST_MARK

    kind = "plan_engine_step"
    return SkillResult(
        reply_text="\n".join([*lines, "", f"{outcome} · {TEST_MARK}"]),
        action_type=kind,
        action_data=keyboard_envelope(buttons) or next_step_action_data(menu_button()),
        meta={"reply_kind": kind, "plan_outcome": outcome},
    )


def s1_restriction_of(bot_user: Any) -> str:
    """Длительное ограничение S1 этого человека словом каталога.

    ``none`` — ограничения нет; иначе ``open`` или ``stop``. Сбой чтения —
    ``stop``: молчание об ограничении не читается как его отсутствие.
    """
    try:
        from apps.orchestrator.safety.s1_restriction import restriction

        found = restriction(bot_user)
    except Exception:  # noqa: BLE001 — не прочитали → закрыто
        logger.warning("orchestrator.plan_step_card.s1_read_failed", exc_info=True)
        return "stop"
    if found is None:
        return "none"
    return "stop" if found.status == "stop" else "open"


def _four(bot_user: Any, turn_safety: TurnSafetyProvider | None) -> dict[str, Any] | None:
    """Четвёрка безопасности шага — или ``None``, если тройки хода нет."""
    safety = turn_safety() if turn_safety is not None else None
    if safety is None:
        return None
    return {
        "safety_state": safety.safety_state,
        "safety_policy_version": safety.safety_policy_version,
        "evaluated_at_revision": safety.evaluated_at_revision,
        "s1_restriction": s1_restriction_of(bot_user),
    }


def _basis(bot_user: Any) -> dict[str, str] | None:
    from apps.orchestrator.plan_gate import plan_consent_basis

    return plan_consent_basis(bot_user)


def _gate(bot_user: Any) -> SkillResult | None:
    from apps.orchestrator.plan_gate import plan_processing_refusal

    outcome = plan_processing_refusal(bot_user)
    return None if outcome is None else _named(outcome)


def _plan_refusal(exc: Exception, trace_id: str, door: str) -> SkillResult:
    """Отказ клиента плана → исход по имени. Имена — каталога, где они есть."""
    from apps.integrations.ayla.plan_engine_client import (
        PlanConsentRequiredError,
        PlanDeletionInProgressError,
        PlanEngineContractError,
        PlanNotFoundError,
        PlanStepNotExecutableError,
        PlanStepResolutionRefusedError,
    )
    from apps.orchestrator.plan_engine_card import PLAN_ENGINE_UNAVAILABLE
    from apps.orchestrator.plan_gate import PLAN_CONSENT_REQUIRED, PLAN_DELETION_REQUESTED

    if isinstance(exc, PlanDeletionInProgressError):
        return _named(PLAN_DELETION_REQUESTED)
    if isinstance(exc, PlanConsentRequiredError):
        return _named(PLAN_CONSENT_REQUIRED)
    if isinstance(exc, PlanStepNotExecutableError):
        return _named(f"PLAN_STEP_NOT_EXECUTABLE:{exc.reason}")
    if isinstance(exc, PlanStepResolutionRefusedError):
        return _named(f"PLAN_STEP_RESOLUTION_REFUSED:{exc.reason}")
    if isinstance(exc, PlanNotFoundError):
        return _named(PLAN_STEP_EXPIRED)
    if isinstance(exc, PlanEngineContractError):
        logger.error(
            "orchestrator.plan_step_card.contract_violation door=%s trace=%s reason=%s",
            door,
            trace_id,
            exc.reason,
        )
        return _named("PLAN_CONTRACT_VIOLATION")
    logger.warning(
        "orchestrator.plan_step_card.failed door=%s trace=%s class=%s",
        door,
        trace_id,
        type(exc).__name__,
    )
    return _named(PLAN_ENGINE_UNAVAILABLE)


# ─── вход ────────────────────────────────────────────────────────────────


def try_handle_plan_step(
    *,
    text: str,
    bot_user: Any,
    conversation: Any,
    trace_id: str,
    turn_safety: TurnSafetyProvider | None,
) -> SkillResult | None:
    """Тапы шага, услуги и времени; ``None`` — не наше (форма / механизм выключен)."""
    from apps.orchestrator.plan_engine_card import SAFETY_INPUT_UNAVAILABLE, engine_enabled

    match = STEP_CALLBACK_RE.match((text or "").strip())
    if match is None or not engine_enabled():
        return None
    refusal = _gate(bot_user)
    if refusal is not None:
        return refusal
    kind, token, index = match.group(1), match.group(2), int(match.group(3))

    if kind == "step":
        four = _four(bot_user, turn_safety)
        if four is None:
            return _named(SAFETY_INPUT_UNAVAILABLE)
        return _show_offers(bot_user, conversation, trace_id, four, token, index)

    waiting = _read(conversation)
    if waiting is None or _hex8(waiting.get("search_id")) != token:
        return _named(PLAN_STEP_EXPIRED)
    four = _four(bot_user, turn_safety)
    if four is None:
        return _named(SAFETY_INPUT_UNAVAILABLE)
    if kind == "offer":
        return _choose_offer(bot_user, conversation, trace_id, four, waiting, index)
    return _book_slot(bot_user, conversation, trace_id, four, waiting, index)


# ─── шаг → услуги ────────────────────────────────────────────────────────


def _option_lines(option: dict[str, Any]) -> list[str]:
    """Вариант словами каталога: услуга и салон, мастер с ценой, адрес."""
    place = ", ".join(x for x in (option.get("salon_name"), option.get("salon_city")) if x)
    first = " — ".join(x for x in (option.get("service_name"), place) if x)
    facts = [str(option.get("master_name") or "")]
    if option.get("price"):
        facts.append(f"{option['price']} ₽")
    if option.get("duration_minutes"):
        facts.append(f"{option['duration_minutes']} мин")
    lines = [first, " · ".join(x for x in facts if x)]
    if option.get("place_address"):
        lines.append(str(option["place_address"]))
    if option.get("synthetic"):
        lines.append(SYNTHETIC_MARK)
    return [line for line in lines if line]


def _options(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Кандидаты каталога → варианты «услуга × мастер» для кнопок.

    Вариант без идентификаторов услуги или мастера, либо без названия услуги
    или имени мастера, не показывается: человеку нечем его назвать, а боту —
    нечем записать.
    """
    out: list[dict[str, Any]] = []
    for candidate in candidates:
        display = candidate.get("display")
        if not isinstance(display, dict):
            continue
        raw_salon = display.get("salon")
        salon: dict[str, Any] = raw_salon if isinstance(raw_salon, dict) else {}
        raw_masters = display.get("masters")
        masters: list[Any] = raw_masters if isinstance(raw_masters, list) else []
        for master in masters:
            if not isinstance(master, dict):
                continue
            option = {
                "tenant_offer_ref": candidate.get("tenant_offer_ref"),
                "canonical_service_ref": candidate.get("canonical_service_ref"),
                "specialist_ref": master.get("specialist_ref"),
                "service_name": display.get("service_name"),
                "salon_name": salon.get("name"),
                "salon_city": salon.get("city"),
                "master_name": master.get("name"),
                "price": master.get("price"),
                "duration_minutes": master.get("duration_minutes"),
                # Адрес — только подтверждённого места; пустое значение любого
                # вида значит «адреса нет», вместо него ничего не подставляется.
                "place_address": master.get("place_address") or None,
                "health_check": candidate.get("health_check"),
                "synthetic": candidate.get("synthetic") is True,
            }
            required = ("tenant_offer_ref", "canonical_service_ref", "specialist_ref")
            named = ("service_name", "master_name")
            if all(isinstance(option[k], str) and option[k] for k in (*required, *named)):
                out.append(option)
    return out[:MAX_OFFERS]


def _show_offers(
    bot_user: Any,
    conversation: Any,
    trace_id: str,
    four: dict[str, Any],
    plan_token: str,
    step_index: int,
) -> SkillResult:
    from apps.integrations.ayla import external_user_id_for
    from apps.integrations.ayla.plan_engine_client import PlanEngineError, PlanEngineHttpClient

    client = PlanEngineHttpClient()
    external_user_id = external_user_id_for(bot_user)
    try:
        plan = client.get_plan(external_user_id=external_user_id)
        raw_revision = plan.get("revision") if isinstance(plan, dict) else None
        steps = raw_revision.get("steps") if isinstance(raw_revision, dict) else None
        if (
            not isinstance(plan, dict)
            or _hex8(plan.get("plan_id")) != plan_token
            or not isinstance(steps, list)
            or step_index >= len(steps)
            or not isinstance(steps[step_index], dict)
            or not isinstance(steps[step_index].get("step_id"), str)
        ):
            # План сменился, или кнопка от другого плана: шаг по номеру из
            # чужого плана — не «ближайший подходящий».
            return _named(PLAN_STEP_EXPIRED)
        plan_id = str(plan["plan_id"])
        step_id = steps[step_index]["step_id"]
        found = client.step_candidates(
            external_user_id=external_user_id,
            plan_id=plan_id,
            step_id=step_id,
            consent=_basis(bot_user),
            **four,
        )
    except PlanEngineError as exc:
        return _plan_refusal(exc, trace_id, "candidates")

    options = _options(found["candidates"])
    if not options:
        _write(conversation, None)
        # Причина каталога — как есть; нет причины (кандидаты были, но ни
        # одного нечем показать) — своё имя.
        return _named(str(found.get("nothing_because") or "PLAN_STEP_OFFER_UNSHOWABLE"))

    search_id = str(found["search_id"])
    _write(
        conversation,
        {"plan_id": plan_id, "step_id": step_id, "search_id": search_id, "options": options},
    )
    token = _hex8(search_id)
    lines: list[str] = []
    for option in options:
        lines.extend([*_option_lines(option), ""])
    buttons = [
        {
            "label": f"{option['service_name']} · {option['master_name']}",
            "callback": f"{CB_OFFER_PREFIX}{token}:{index}",
        }
        for index, option in enumerate(options)
    ]
    return _reply(lines[:-1], PLAN_STEP_OFFERS, buttons)


# ─── услуга → время ──────────────────────────────────────────────────────


def _today() -> date:
    from django.utils import timezone

    return timezone.localdate()


def _slot_label(iso: str) -> str:
    """«ДД.ММ ЧЧ:ММ» — время как его прислал каталог (местное время мастера)."""
    moment = datetime.fromisoformat(iso)
    return moment.strftime("%d.%m %H:%M")


def _nearest_slots(option: dict[str, Any]) -> list[str]:
    """Свободное время ближайшего дня, в котором оно есть; ``[]`` — нет в горизонте."""
    from apps.integrations.ayla.booking_client import get_ayla_booking_client

    client = get_ayla_booking_client()
    start = _today()
    for offset in range(SLOT_HORIZON_DAYS):
        day = (start + timedelta(days=offset)).isoformat()
        slots = client.get_available_times(
            specialist_id=option["specialist_ref"],
            date=day,
            service_id=option["tenant_offer_ref"],
        )
        found = [s.datetime for s in slots if isinstance(s.datetime, str) and s.datetime]
        if found:
            return found[:MAX_SLOTS]
    return []


def _choose_offer(
    bot_user: Any,
    conversation: Any,
    trace_id: str,
    four: dict[str, Any],
    waiting: dict[str, Any],
    index: int,
) -> SkillResult:
    from apps.integrations.ayla import external_user_id_for
    from apps.integrations.ayla.booking_client import BookingAPIError
    from apps.integrations.ayla.plan_engine_client import PlanEngineError, PlanEngineHttpClient
    from apps.orchestrator.plan_engine_card import PLAN_ENGINE_UNAVAILABLE

    options = waiting.get("options")
    if (
        not isinstance(options, list)
        or index >= len(options)
        or not isinstance(options[index], dict)
    ):
        return _named(PLAN_STEP_EXPIRED)
    option = options[index]
    if option.get("health_check") != "not_required":
        # Услуге нужен расспрос о здоровье (или ответ каталога о нём не
        # разобран): запись от шага не идёт. Неизвестное не читается как
        # «не нужен».
        return _named(HEALTH_CHECK_REQUIRED)

    try:
        PlanEngineHttpClient().resolve_step(
            external_user_id=external_user_id_for(bot_user),
            plan_id=str(waiting["plan_id"]),
            step_id=str(waiting["step_id"]),
            canonical_service_ref=option["canonical_service_ref"],
            tenant_offer_ref=option["tenant_offer_ref"],
            resolver_decision_id=str(waiting["search_id"]),
            consent=_basis(bot_user),
            **four,
        )
    except PlanEngineError as exc:
        return _plan_refusal(exc, trace_id, "resolution")

    try:
        slots = _nearest_slots(option)
    except BookingAPIError as exc:
        logger.warning(
            "orchestrator.plan_step_card.slots_failed trace=%s class=%s",
            trace_id,
            type(exc).__name__,
        )
        return _named(PLAN_ENGINE_UNAVAILABLE)
    _write(conversation, {**waiting, "chosen": index, "slots": slots})
    if not slots:
        return _named(PLAN_STEP_NO_SLOTS)
    token = _hex8(waiting["search_id"])
    buttons = [
        {"label": _slot_label(iso), "callback": f"{CB_SLOT_PREFIX}{token}:{k}"}
        for k, iso in enumerate(slots)
    ]
    return _reply(_option_lines(option), PLAN_STEP_SLOTS, buttons)


# ─── время → запись ──────────────────────────────────────────────────────


def _idempotency_key(
    external_user_id: str, waiting: dict[str, Any], option: dict[str, Any], iso: str
) -> str:
    """Один ключ на «этот человек · этот шаг · эта услуга · это время».

    Повторное нажатие той же кнопки шлёт тот же ключ — каталог вернёт ту же
    запись, второй не будет.
    """
    seed = "|".join(
        [
            external_user_id,
            "plan-step",
            str(waiting["plan_id"]),
            str(waiting["step_id"]),
            str(option["specialist_ref"]),
            str(option["tenant_offer_ref"]),
            iso,
        ]
    )
    return hashlib.sha256(seed.encode()).hexdigest()[:48]


def _book_slot(
    bot_user: Any,
    conversation: Any,
    trace_id: str,
    four: dict[str, Any],
    waiting: dict[str, Any],
    slot_index: int,
) -> SkillResult:
    from apps.identity.services.ayla_link import ensure_ayla_link
    from apps.integrations.ayla import external_user_id_for
    from apps.integrations.ayla.booking_client import (
        BookingAPIError,
        BookingBadRequestError,
        get_ayla_booking_client,
    )
    from apps.orchestrator.plan_engine_card import PLAN_ENGINE_UNAVAILABLE

    options = waiting.get("options")
    chosen = waiting.get("chosen")
    slots = waiting.get("slots")
    if (
        not isinstance(options, list)
        or not isinstance(chosen, int)
        or isinstance(chosen, bool)
        or chosen >= len(options)
        or not isinstance(slots, list)
        or slot_index >= len(slots)
        or not isinstance(slots[slot_index], str)
    ):
        return _named(PLAN_STEP_EXPIRED)
    option = options[chosen]
    iso = slots[slot_index]
    if option.get("health_check") != "not_required":
        return _named(HEALTH_CHECK_REQUIRED)
    if option.get("synthetic") is not True:
        # Правила предоплаты салона у ветки плана нет (в каталоге его нет
        # вовсе, а зеркала, откуда его берёт обычная запись, здесь нет по
        # построению). Слать признак наугад — либо списание без спроса, либо
        # обход правила салона. До решения владельца запись от шага создаётся
        # только на услугу, помеченную синтетической.
        return _named(PLAN_STEP_BOOKING_NOT_AVAILABLE)

    ayla_user_id = ensure_ayla_link(bot_user, trigger="plan_step_booking")
    if not ayla_user_id:
        return _named(AYLA_LINK_UNAVAILABLE)
    external_user_id = external_user_id_for(bot_user)
    try:
        get_ayla_booking_client().create_appointment(
            external_user_id=external_user_id,
            client_id=str(ayla_user_id),
            specialist_id=option["specialist_ref"],
            service_id=option["tenant_offer_ref"],
            start_datetime=iso,
            idempotency_key=_idempotency_key(external_user_id, waiting, option, iso),
            # Синтетическая услуга — без предоплаты: «реальные списания не
            # выполнять» (решение владельца 08.10).
            payment_required=False,
            quoted_price=str(option["price"]) if option.get("price") else None,
            quoted_duration_minutes=option["duration_minutes"]
            if isinstance(option.get("duration_minutes"), int)
            else None,
            provenance={
                "entry_point": "PLAN_STEP",
                "plan_id": str(waiting["plan_id"]),
                "step_id": str(waiting["step_id"]),
                **four,
            },
            consent=_basis(bot_user),
        )
    except BookingBadRequestError as exc:
        # Отказ каталога — его именем: слот занят, котировка изменилась, шаг
        # не допущен, нужна проверка здоровья, гейт согласия.
        code = str(exc.code or PLAN_STEP_BOOKING_FAILED)
        reason = (exc.details or {}).get("reason") if isinstance(exc.details, dict) else None
        logger.info(
            "orchestrator.plan_step_card.booking_refused trace=%s code=%s reason=%s",
            trace_id,
            code,
            reason,
        )
        return _named(f"{code}:{reason}" if isinstance(reason, str) and reason else code)
    except BookingAPIError as exc:
        logger.warning(
            "orchestrator.plan_step_card.booking_failed trace=%s class=%s",
            trace_id,
            type(exc).__name__,
        )
        return _named(PLAN_ENGINE_UNAVAILABLE)

    # Состояние оставляем: повторное нажатие того же времени шлёт тот же ключ,
    # и каталог отвечает той же записью.
    return _reply([*_option_lines(option), _slot_label(iso)], PLAN_STEP_BOOKED, [])


__all__ = [
    "CB_OFFER_PREFIX",
    "CB_SLOT_PREFIX",
    "CB_STEP_PREFIX",
    "STATE_KEY",
    "SYNTHETIC_MARK",
    "is_step_callback",
    "s1_restriction_of",
    "step_buttons",
    "try_handle_plan_step",
]
