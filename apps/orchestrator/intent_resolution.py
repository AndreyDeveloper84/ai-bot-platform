"""Canonical intent resolution on the live global path (DRF-1273).

The tenant-less client bot (``ingress:max_global``) never produced the
canonical Intent Resolution Output Contract — the only structured output
was the ai-core tool protocol (``action_type`` / ``action_data``), which
shares not a single field with the contract. This module is the resolver
pass: one extra LLM call per free-text concierge turn that emits the
16-field Output Contract ``0.5`` (Ayla Intent Model Specification v1.0,
``03 AI System/Contracts/intent-output.schema.json``), validated
DETERMINISTICALLY before it is allowed into the turn log.

Design constraints:

- **After the reply, never before it.** The resolver runs once the answer
  is already sent (the handler calls :func:`resolve_and_log_turn_intent`
  after delivery), so it adds zero user-visible latency and a resolver
  failure can never change what the user saw. This is the same
  best-effort pattern as the M-B2 memory write.
- **Model proposes, code disposes.** The LLM draft is treated as
  untrusted: enums are checked against the frozen registry, contract
  invariants are enforced, and every ``evidence.fragment`` must be a
  verbatim substring of the (normalised) user text. A fabricated
  fragment means the WHOLE draft is rejected — better no contract than
  a contract with invented traceability (the failure is logged with the
  raw payload length for forensics). No contract is ever fabricated on
  the failure path: there is no honest ``status_reason`` for «resolver
  broken», so the turn simply gets none and the log says why.
- **Nothing else changes.** The reply path is untouched; the contract is
  written to the turn log (``orchestrator.intent_resolution.ok``) and an
  ``AIRequestMetric`` row (``skill_selected="intent_resolution"``) keeps
  the extra call's cost visible in data, not in the invoice (DRF-1211
  pattern).

Rollback without redeploy: ``INTENT_RESOLUTION_LIVE_ENABLED=0``.

DRF-1385 adds a THIRD path next to «no contract» and «LLM pass»: the
concierge already classified the intent by CHOOSING A TOOL, so when
``INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED`` is on (default OFF) and
the turn carried a tool trace out of the concierge, the contract is
built DETERMINISTICALLY from that choice (:func:`build_draft_from_tool_choice`
+ the same :func:`_validate_and_build` gate) — zero new model calls.
Any gap (unmappable tool, rejected draft, exception) falls back to the
LLM pass below; the resolver stays strictly post-reply either way.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
import uuid
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

CONTRACT_VERSION = "0.5"

# Frozen registry (Roadmap §3.1): 11 product types + UNKNOWN sentinel.
INTENT_TYPES = (
    "DISCOVER_SERVICE",
    "FIND_SPECIALIST",
    "BOOK_APPOINTMENT",
    "RESCHEDULE_APPOINTMENT",
    "CANCEL_APPOINTMENT",
    "ASK_ABOUT_SERVICE",
    "ASK_ABOUT_PRICE",
    "ASK_ABOUT_AVAILABILITY",
    "PROVIDE_CONTEXT",
    "CORRECT_CONTEXT",
    "REVOKE_CONSENT",
    "UNKNOWN",
)
PRODUCT_INTENT_TYPES = INTENT_TYPES[:-1]  # UNKNOWN is never a secondary intent

SLOT_NAMES = frozenset(
    {
        "service_interest",
        "service_ref",
        "service_category",
        "provider_name",
        "provider_preference",
        "time_preference",
        "time_window",
        "time_slot",
        "new_time_slot",
        "appointment_ref",
        "budget",
        "comment",
        "reason",
        "context_fact",
        "context_fact_ref",
        "fact_category",
        "consent_scope",
        "revocation_mode",
    }
)

# Slots that must resolve to a backend entity to be ``confirmed``
# (canon: ``entity_ref`` обязателен для ссылочных слотов в состоянии confirmed).
REFERENCE_SLOTS = frozenset({"service_ref", "time_slot", "new_time_slot", "appointment_ref"})

# First resolution pass can only publish these (``superseded`` / ``expired``
# are lifecycle transitions of an already-published result, never a pass
# outcome — canon § Output Contract, KM-IM-1).
RESOLUTION_STATUSES = frozenset({"resolved", "needs_clarification", "unresolved", "blocked_safety"})

STATUS_REASONS = frozenset(
    {
        "low_confidence",
        "out_of_scope",
        "conflicting_slots",
        "max_clarification_exceeded",
        "intent_shift",
        "session_expired",
        "clarification_timeout",
        "safety_gate_blocked",
        "required_context_not_authorized",
    }
)
UNKNOWN_UNRESOLVED_REASONS = frozenset(
    {"out_of_scope", "max_clarification_exceeded", "required_context_not_authorized"}
)
CLARIFICATION_REASONS = frozenset(
    {
        "intent_low_confidence",
        "conflicting_slot",
        "missing_required_slot",
        "unmet_any_of_requirement",
        "consent_scope_selection",
    }
)
INTENT_LEVEL_CLARIFICATION_REASONS = frozenset({"intent_low_confidence", "conflicting_slot"})
CLARIFICATION_EFFECTS = frozenset({"blocks_current_action", "allows_immediate_safe_action"})
SAFETY_FLAG_CODES = frozenset(
    {"red_zone_conflict", "competence_boundary", "unsafe_service_request"}
)

_ENVELOPE_KEYS = frozenset(
    {"raw_value", "normalized_value", "entity_ref", "confirmation_status", "evidence_refs"}
)

_RESOLUTION_PROMPT = """\
Ты — resolver намерений Ayla. По ОДНОМУ сообщению клиента верни РОВНО ОДИН
JSON-объект — Intent Resolution Output Contract 0.5. Никакого markdown,
никаких пояснений — только JSON.

Реестр типов заморожен (11 + UNKNOWN, других не существует):
- DISCOVER_SERVICE — потребность/поиск услуги без конкретного исполнителя
  («устала, хочу расслабиться»). all_of: [service_interest].
- FIND_SPECIALIST — найти/показать/сравнить специалиста, создание записи НЕ
  запрошено («найди Анну», «кто делает лимфодренажный массаж?»).
  any_of: минимум один из [provider_name, service_category].
- BOOK_APPOINTMENT — прямая просьба создать запись («запиши меня к Анне»).
  all_of: [service_ref, time_slot]. Запрос на создание записи НИКОГДА не
  разрешается как FIND_SPECIALIST.
- RESCHEDULE_APPOINTMENT — перенос записи. all_of: [appointment_ref, new_time_slot].
- CANCEL_APPOINTMENT — отмена записи. all_of: [appointment_ref].
- ASK_ABOUT_SERVICE — вопрос о содержании/длительности/подготовке/
  противопоказаниях услуги. all_of: [service_ref]. safety-sensitive.
- ASK_ABOUT_PRICE — вопрос о стоимости. all_of: [service_ref].
- ASK_ABOUT_AVAILABILITY — вопрос о свободных слотах.
  any_of: минимум один из [service_ref, provider_name].
- PROVIDE_CONTEXT — пользователь добровольно сообщает факт о себе
  («я веган», «у меня аллергия на мед»). all_of: [context_fact]. safety-sensitive.
- CORRECT_CONTEXT — исправление факта текущей сессии.
  all_of: [context_fact_ref, context_fact]. safety-sensitive.
- REVOKE_CONSENT — отзыв согласия на использование данных.
- UNKNOWN — намерение не распознано или вне реестра. Не угадывай: при
  confidence < 0.5 верни UNKNOWN, а не ближайший тип. Сообщение вне области
  продукта (бьюти-услуги, запись, личный контекст, данные) — UNKNOWN с
  status="unresolved" и status_reason="out_of_scope".

status — одно из: resolved | needs_clarification | unresolved | blocked_safety.
- resolved: тип распознан с достаточной confidence (> 0.8). НЕ требует
  полноты слотов: «запиши меня к Анне» — resolved с
  missing_required_slots=["service_ref","time_slot"].
- needs_clarification: неопределён САМ intent (confidence < 0.5) или
  конфликт значений слота. Всегда с requires_clarification=true,
  clarification_reason ∈ {intent_low_confidence, conflicting_slot},
  clarification_effect="blocks_current_action".
- unresolved: вне области (out_of_scope) или исчерпаны уточнения
  (max_clarification_exceeded). Для UNKNOWN+unresolved status_reason строго
  из {out_of_scope, max_clarification_exceeded, required_context_not_authorized}.
- blocked_safety: ТОЛЬКО когда safety_flags непуст, и тогда
  status_reason="safety_gate_blocked". И наоборот: если safety_flags пуст,
  status НЕ blocked_safety.

Поля объекта (ВСЕ обязательны, ровно эти 16):
- intent_id: UUID (сгенерируй).
- intent_type: тип из реестра.
- status, confidence (0.0–1.0).
- slots: объект {имя_слота: envelope}. Имена слотов ТОЛЬКО из реестра:
  service_interest, service_ref, service_category, provider_name,
  provider_preference, time_preference, time_window, time_slot,
  new_time_slot, appointment_ref, budget, comment, reason, context_fact,
  context_fact_ref, fact_category, consent_scope, revocation_mode.
  envelope: {"raw_value": дословная форма из сообщения, "normalized_value":
  нормализованное значение или null, "entity_ref": ссылка на сущность или
  null, "confirmation_status": "filled"|"confirmed", "evidence_refs":
  [evidence_id]}. confirmed для service_ref/time_slot/new_time_slot/
  appointment_ref — только если entity_ref однозначно известен; иначе filled.
- missing_required_slots: незаполненные слоты all_of для выбранного типа.
- evidence: массив {"evidence_id": "ev-1", ...}, где "message_id" — id
  сообщения из ввода, "fragment" — ДОСЛОВНАЯ цитата-подстрока сообщения
  клиента, обосновывающая тип или слот. Для status="resolved" evidence НЕ
  пуст. Ничего не выдумывай: fragment обязан быть точной подстрокой.
- requires_clarification: boolean. Если true — clarification_question
  (ровно один конкретный вопрос, ссылающийся на уже сказанное),
  clarification_reason (intent_low_confidence | conflicting_slot |
  missing_required_slot | unmet_any_of_requirement | consent_scope_selection),
  clarification_effect (blocks_current_action | allows_immediate_safe_action).
  Если false — все три null.
- safety_flags: [] или blocking-коды из {red_zone_conflict,
  competence_boundary, unsafe_service_request}.
- unmet_slot_requirements: невыполненные any_of: [{"requirement_id",
  "requirement_type": "any_of", "candidate_slots": [...], "minimum_present": 1}].
- contract_version: "0.5".
- status_reason: null для resolved; иначе из {low_confidence, out_of_scope,
  conflicting_slots, max_clarification_exceeded, safety_gate_blocked,
  required_context_not_authorized}.
- secondary_intents: другие значимые намерения сообщения (кроме primary):
  [{"intent_type": продуктовый тип (НЕ UNKNOWN), "evidence_refs": [...],
  "message_position": 1}]. [] если одно намерение.
"""

_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Normalisation for the verbatim-evidence check.

    NFKC + casefold + whitespace collapse: a fragment that differs from the
    user text only by case or spacing is still «the user's own words»; a
    fragment with altered content is not.
    """

    return _WS_RE.sub(" ", unicodedata.normalize("NFKC", text)).casefold().strip()


def resolution_enabled() -> bool:
    """Live switch. ``INTENT_RESOLUTION_LIVE_ENABLED`` setting, default on."""

    return bool(getattr(settings, "INTENT_RESOLUTION_LIVE_ENABLED", True))


def tool_choice_resolution_enabled() -> bool:
    """DRF-1385 switch. ``INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED``,
    default OFF — the deterministic path is opt-in; the LLM pass stays
    the default until the flag is flipped in the environment.
    """

    return bool(getattr(settings, "INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED", False))


def _coerce_slots(raw_slots: Any, evidence_ids: set[str]) -> dict[str, Any]:
    """Soft-repair the slots object: drop anything that is not canon-shaped.

    A dropped slot degrades completeness, never honesty — the model's
    ``missing_required_slots`` still says what it said. Repairs:
    unknown slot name → drop; non-str ``raw_value`` → drop; envelope keys
    outside the stable shape → strip; unresolvable ``evidence_refs`` →
    filter (drop the slot if none survive); ``confirmed`` reference slot
    without ``entity_ref`` → downgrade to ``filled`` (canon: entity_ref
    обязателен для confirmed ссылочных слотов).
    """

    slots: dict[str, Any] = {}
    if not isinstance(raw_slots, dict):
        return slots
    for name, envelope in raw_slots.items():
        if name not in SLOT_NAMES or not isinstance(envelope, dict):
            continue
        raw_value = envelope.get("raw_value")
        if not isinstance(raw_value, str) or not raw_value:
            continue
        refs = [
            str(r)
            for r in (envelope.get("evidence_refs") or [])
            if isinstance(r, str) and r in evidence_ids
        ]
        if not refs:
            continue
        confirmation_status = envelope.get("confirmation_status")
        if confirmation_status not in ("filled", "confirmed"):
            confirmation_status = "filled"
        entity_ref = envelope.get("entity_ref")
        if entity_ref is not None and not isinstance(entity_ref, str):
            entity_ref = None
        if confirmation_status == "confirmed" and name in REFERENCE_SLOTS and not entity_ref:
            confirmation_status = "filled"
        slots[name] = {
            "raw_value": raw_value,
            "normalized_value": envelope.get("normalized_value"),
            "entity_ref": entity_ref,
            "confirmation_status": confirmation_status,
            "evidence_refs": sorted(set(refs)),
        }
    return slots


def _validate_and_build(
    raw: Any,
    *,
    user_text: str,
    message_id: str,
    trace_id: str,
) -> dict[str, Any] | None:
    """Validate an LLM draft against the contract invariants; build or reject.

    Returns the normalised 16-field contract, or ``None`` when the draft
    violates a HARD invariant (bad enum, broken status/clarification/safety
    logic, fabricated evidence). Soft deviations are repaired in place —
    see :func:`_coerce_slots`. Every rejection is logged with a reason
    code, never with user content.
    """

    def _reject(reason: str) -> None:
        logger.warning(
            "orchestrator.intent_resolution.invalid trace=%s reason=%s",
            trace_id,
            reason,
        )

    if not isinstance(raw, dict):
        _reject("not_a_json_object")
        return None

    intent_type = raw.get("intent_type")
    if intent_type not in INTENT_TYPES:
        _reject("intent_type_out_of_registry")
        return None

    status = raw.get("status")
    if status not in RESOLUTION_STATUSES:
        # ``superseded``/``expired`` land here too: a resolution pass must
        # not publish lifecycle transitions (canon, KM-IM-1).
        _reject("status_not_a_resolution_outcome")
        return None

    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    status_reason = raw.get("status_reason")
    if status_reason is not None and status_reason not in STATUS_REASONS:
        _reject("status_reason_out_of_enum")
        return None

    safety_flags = raw.get("safety_flags") or []
    if not isinstance(safety_flags, list):
        _reject("safety_flags_not_a_list")
        return None
    safety_flags = sorted({f for f in safety_flags if f in SAFETY_FLAG_CODES})

    # blocked_safety ⇔ safety_flags непуст (двусторонний инвариант канона).
    if status == "blocked_safety":
        if not safety_flags:
            _reject("blocked_safety_without_flags")
            return None
        status_reason = "safety_gate_blocked"
    elif safety_flags:
        _reject("safety_flags_without_blocked_status")
        return None

    if status == "resolved":
        status_reason = None
    elif status in ("unresolved",) and status_reason is None:
        _reject("terminal_status_without_reason")
        return None

    requires_clarification = bool(raw.get("requires_clarification"))
    clarification_question = raw.get("clarification_question")
    clarification_reason = raw.get("clarification_reason")
    clarification_effect = raw.get("clarification_effect")
    if requires_clarification:
        if not isinstance(clarification_question, str) or not clarification_question.strip():
            _reject("clarification_without_question")
            return None
        if clarification_reason not in CLARIFICATION_REASONS:
            _reject("clarification_reason_out_of_enum")
            return None
        if clarification_effect not in CLARIFICATION_EFFECTS:
            _reject("clarification_effect_out_of_enum")
            return None
    else:
        if status == "needs_clarification":
            _reject("needs_clarification_without_flag")
            return None
        clarification_question = None
        clarification_reason = None
        clarification_effect = None

    if status == "needs_clarification":
        if clarification_reason not in INTENT_LEVEL_CLARIFICATION_REASONS:
            _reject("needs_clarification_not_intent_level")
            return None
        if clarification_effect != "blocks_current_action":
            _reject("needs_clarification_not_blocking")
            return None

    if clarification_reason == "consent_scope_selection":
        if not (
            intent_type == "REVOKE_CONSENT"
            and status == "resolved"
            and clarification_effect == "allows_immediate_safe_action"
        ):
            _reject("consent_scope_selection_shape")
            return None

    if intent_type == "UNKNOWN":
        if status not in ("needs_clarification", "unresolved"):
            _reject("unknown_with_non_unknown_status")
            return None
        if status_reason is None:
            _reject("unknown_without_status_reason")
            return None
        if status == "needs_clarification" and clarification_reason != "intent_low_confidence":
            _reject("unknown_clarification_not_low_confidence")
            return None
        if status == "unresolved" and status_reason not in UNKNOWN_UNRESOLVED_REASONS:
            _reject("unknown_unresolved_reason_out_of_enum")
            return None

    # Evidence: every fragment must be a verbatim substring of the user
    # text. This is the anti-fabrication gate — a model that invents its
    # traceability is worse than no resolution at all.
    normalized_text = _normalize(user_text)
    evidence: list[dict[str, Any]] = []
    raw_evidence = raw.get("evidence") or []
    if not isinstance(raw_evidence, list):
        _reject("evidence_not_a_list")
        return None
    seen_ids: set[str] = set()
    for item in raw_evidence:
        if not isinstance(item, dict):
            _reject("evidence_item_not_an_object")
            return None
        evidence_id = item.get("evidence_id")
        fragment = item.get("fragment")
        if not isinstance(evidence_id, str) or not evidence_id:
            _reject("evidence_id_missing")
            return None
        if evidence_id in seen_ids:
            _reject("evidence_id_duplicate")
            return None
        if not isinstance(fragment, str) or not fragment.strip():
            _reject("evidence_fragment_empty")
            return None
        if _normalize(fragment) not in normalized_text:
            _reject("evidence_fragment_not_verbatim")
            return None
        seen_ids.add(evidence_id)
        # message_id is issued by the runtime/channel (canon), never by the
        # model — overwrite whatever the draft carried.
        evidence.append(
            {"evidence_id": evidence_id, "message_id": message_id, "fragment": fragment}
        )
    if status == "resolved" and not evidence:
        _reject("resolved_without_evidence")
        return None

    slots = _coerce_slots(raw.get("slots"), seen_ids)

    missing_required_slots = sorted(
        {
            s
            for s in (raw.get("missing_required_slots") or [])
            if isinstance(s, str) and s in SLOT_NAMES
        }
    )

    unmet_slot_requirements: list[dict[str, Any]] = []
    for req in raw.get("unmet_slot_requirements") or []:
        if not isinstance(req, dict):
            continue
        candidate_slots = sorted(
            {
                s
                for s in (req.get("candidate_slots") or [])
                if isinstance(s, str) and s in SLOT_NAMES
            }
        )
        requirement_id = req.get("requirement_id")
        minimum_present = req.get("minimum_present")
        if (
            not isinstance(requirement_id, str)
            or not requirement_id
            or not candidate_slots
            or not isinstance(minimum_present, int)
            or minimum_present < 1
        ):
            continue
        unmet_slot_requirements.append(
            {
                "requirement_id": requirement_id,
                "requirement_type": "any_of",
                "candidate_slots": candidate_slots,
                "minimum_present": minimum_present,
            }
        )

    secondary_intents: list[dict[str, Any]] = []
    for sec in raw.get("secondary_intents") or []:
        if not isinstance(sec, dict):
            continue
        sec_type = sec.get("intent_type")
        sec_refs = sorted(
            {r for r in (sec.get("evidence_refs") or []) if isinstance(r, str) and r in seen_ids}
        )
        sec_position = sec.get("message_position")
        if sec_type not in PRODUCT_INTENT_TYPES or not sec_refs:
            continue
        if not isinstance(sec_position, int) or sec_position < 1:
            sec_position = 1
        secondary_intents.append(
            {
                "intent_type": sec_type,
                "evidence_refs": sec_refs,
                "message_position": sec_position,
            }
        )

    intent_id = raw.get("intent_id")
    if not isinstance(intent_id, str) or not intent_id:
        intent_id = str(uuid.uuid4())

    return {
        "intent_id": intent_id,
        "intent_type": intent_type,
        "status": status,
        "confidence": confidence,
        "slots": slots,
        "missing_required_slots": missing_required_slots,
        "evidence": evidence,
        "requires_clarification": requires_clarification,
        "clarification_question": clarification_question,
        "safety_flags": safety_flags,
        "unmet_slot_requirements": unmet_slot_requirements,
        "contract_version": CONTRACT_VERSION,
        "status_reason": status_reason,
        "clarification_reason": clarification_reason,
        "clarification_effect": clarification_effect,
        "secondary_intents": secondary_intents,
    }


# ── DRF-1385: детерминированный контракт из выбора инструмента ─────────
#
# Консьерж УЖЕ классифицировал намерение, выбрав инструмент; имена
# инструментов здесь — строковые литералы, а не импорт из concierge:
# отложенный импорт в ``resolve_and_log_turn_intent`` держит этот модуль
# свободным от графа зависимостей ai-core (см. комментарий там же), и
# нарушать это ради константы нельзя.
_SHOW_MASTERS_TOOL = "show_masters"
_START_BOOKING_TOOL = "start_booking"
_ASK_CLARIFICATION_TOOL = "ask_clarification"
_CATALOG_TOOLS = frozenset({"show_salons", "show_services"})
_CONTEXT_TOOLS = frozenset(
    {"health_screening", "log_water", "clarify_food_entry", "start_nutrition_anketa"}
)


def _verbatim_fragment(value: str, user_text: str) -> str | None:
    """Дословный фрагмент ИСХОДНОГО текста для значения из arguments.

    Сравнение — через существующий :func:`_normalize` (NFKC + casefold +
    схлопывание пробелов): значение, отличающееся от слов пользователя
    только регистром или пробелами, — всё ещё «его собственные слова».
    Возвращается фрагмент исходного текста (а не значение аргумента);
    ``None`` — значения в тексте нет, и выдумывать его нельзя.
    """

    if not isinstance(value, str) or not value.strip():
        return None
    if _normalize(value) not in _normalize(user_text):
        return None
    if value in user_text:
        return value
    nf_text = unicodedata.normalize("NFKC", user_text)
    nf_value = unicodedata.normalize("NFKC", value.strip())
    match = re.search(re.escape(nf_value), nf_text, flags=re.IGNORECASE)
    if match:
        return nf_text[match.start() : match.end()]
    # Различие только в пробельной разметке: нормализованная форма
    # значения — подстрока нормализованного текста (проверено выше), так
    # что анти-фантазийный гейт это значение пропустит.
    return value.strip()


def _evidence_id_for(draft: dict[str, Any], fragment: str) -> str:
    """evidence_id фрагмента внутри драфта (один фрагмент — один id)."""

    for item in draft["evidence"]:
        if item["fragment"] == fragment:
            return item["evidence_id"]
    evidence_id = f"ev-{len(draft['evidence']) + 1}"
    # message_id перезаписывается рантаймом в _validate_and_build (канон:
    # id сообщения выдаёт канал, никогда не составитель драфта).
    draft["evidence"].append({"evidence_id": evidence_id, "message_id": "", "fragment": fragment})
    return evidence_id


def _add_slot(draft: dict[str, Any], name: str, value: Any, user_text: str) -> bool:
    """Слот из значения аргумента — ТОЛЬКО с дословным raw из текста.

    Значения в тексте нет → слот не создаётся (выдуманного raw_value
    быть не должно никогда); вызывающий код решает, что это значит для
    missing/unmet.
    """

    if not isinstance(value, str) or not value.strip():
        return False
    fragment = _verbatim_fragment(value.strip(), user_text)
    if fragment is None:
        return False
    draft["slots"][name] = {
        "raw_value": fragment,
        "normalized_value": value.strip(),
        "entity_ref": None,
        "confirmation_status": "filled",
        "evidence_refs": [_evidence_id_for(draft, fragment)],
    }
    return True


def _ensure_evidence(draft: dict[str, Any], user_text: str) -> None:
    """Ни одно значение не нашлось дословно → evidence = весь текст.

    Сообщение — подстрока самого себя и по построению является основанием
    классификации; для ``resolved`` контракт требует непустой evidence.
    """

    if not draft["evidence"] and user_text.strip():
        _evidence_id_for(draft, user_text)


def _base_draft(intent_type: str, status: str, confidence: float) -> dict[str, Any]:
    """Драфт той же формы, что выдаёт LLM (16 полей до нормализации)."""

    return {
        "intent_id": str(uuid.uuid4()),
        "intent_type": intent_type,
        "status": status,
        "confidence": confidence,
        "slots": {},
        "missing_required_slots": [],
        "evidence": [],
        "requires_clarification": False,
        "clarification_question": None,
        "safety_flags": [],
        "unmet_slot_requirements": [],
        "contract_version": CONTRACT_VERSION,
        "status_reason": None,
        "clarification_reason": None,
        "clarification_effect": None,
        "secondary_intents": [],
    }


def build_draft_from_tool_choice(
    tool: str, arguments: dict, *, user_text: str
) -> dict[str, Any] | None:
    """Драфт Output Contract 0.5 из выбора инструмента консьержем (DRF-1385).

    Таблица отображения зафиксирована в брифе задачи; ``None`` — честного
    отображения нет (``show_my_records``, неизвестный инструмент, malformed
    arguments, ``ask_clarification`` без вопроса — внутренний degrade
    диспетчера, а не выбор модели), и вызывающий код падает обратно в
    нынешний LLM-проход. Драфт НЕ валидируется здесь: он прогоняется
    через существующий :func:`_validate_and_build`, как драфт модели.
    """

    if not isinstance(tool, str) or not isinstance(arguments, dict):
        return None

    if tool == _SHOW_MASTERS_TOOL:
        draft = _base_draft("FIND_SPECIALIST", "resolved", 0.9)
        _add_slot(draft, "service_category", arguments.get("specialization"), user_text)
        _add_slot(draft, "provider_name", arguments.get("master"), user_text)
        _ensure_evidence(draft, user_text)
        if not ({"provider_name", "service_category"} & draft["slots"].keys()):
            # any_of FIND_SPECIALIST не выполнен — фиксируем, а не угадываем.
            draft["unmet_slot_requirements"].append(
                {
                    "requirement_id": "find_specialist_any_of",
                    "requirement_type": "any_of",
                    "candidate_slots": ["provider_name", "service_category"],
                    "minimum_present": 1,
                }
            )
        return draft

    if tool in _CATALOG_TOOLS:
        draft = _base_draft("DISCOVER_SERVICE", "resolved", 0.9)
        _ensure_evidence(draft, user_text)
        return draft

    if tool == _START_BOOKING_TOOL:
        draft = _base_draft("BOOK_APPOINTMENT", "resolved", 0.9)
        _add_slot(draft, "provider_name", arguments.get("master"), user_text)
        _add_slot(draft, "service_interest", arguments.get("service"), user_text)
        _ensure_evidence(draft, user_text)
        draft["missing_required_slots"] = ["service_ref", "time_slot"]
        return draft

    if tool == _ASK_CLARIFICATION_TOOL:
        question = arguments.get("question")
        if not isinstance(question, str) or not question.strip():
            return None
        draft = _base_draft("UNKNOWN", "needs_clarification", 0.4)
        draft["requires_clarification"] = True
        draft["clarification_question"] = question.strip()
        draft["clarification_reason"] = "intent_low_confidence"
        draft["clarification_effect"] = "blocks_current_action"
        draft["status_reason"] = "low_confidence"
        _ensure_evidence(draft, user_text)
        return draft

    if tool in _CONTEXT_TOOLS:
        draft = _base_draft("PROVIDE_CONTEXT", "resolved", 0.9)
        # Факт — дословная фраза пользователя из аргументов инструмента
        # (symptom_text / drink_text / food_text); у анкеты аргументов нет.
        fact = next(
            (v for v in arguments.values() if isinstance(v, str) and v.strip()),
            None,
        )
        if fact is None or not _add_slot(draft, "context_fact", fact, user_text):
            draft["missing_required_slots"] = ["context_fact"]
        _ensure_evidence(draft, user_text)
        return draft

    return None


def _draft_from_tool_trace(tool_trace: Any, *, user_text: str) -> dict[str, Any] | None:
    """Драфт по всей трассе: первый инструмент — primary, остальные —
    secondary_intents (только продуктовые типы, UNKNOWN вторичным не
    бывает). Отображение берётся по ПЕРВОМУ инструменту — это решение,
    определившее ход.
    """

    entries = [e for e in tool_trace if isinstance(e, dict)]
    if not entries:
        return None
    first = entries[0]
    draft = build_draft_from_tool_choice(
        first.get("tool"), first.get("arguments"), user_text=user_text
    )
    if draft is None:
        return None
    for position, entry in enumerate(entries[1:], start=2):
        sec = build_draft_from_tool_choice(
            entry.get("tool"), entry.get("arguments"), user_text=user_text
        )
        if sec is None or sec["intent_type"] not in PRODUCT_INTENT_TYPES:
            continue
        refs = [
            _evidence_id_for(draft, item["fragment"]) for item in sec["evidence"]
        ]
        if not refs:
            continue
        draft["secondary_intents"].append(
            {
                "intent_type": sec["intent_type"],
                "evidence_refs": refs,
                "message_position": position,
            }
        )
    return draft


def build_resolution_messages(text: str, *, message_id: str) -> list[dict[str, str]]:
    """Compose the resolver call: schema prompt + the raw user message."""

    return [
        {"role": "system", "content": _RESOLUTION_PROMPT},
        {"role": "user", "content": f"message_id: {message_id}\nСообщение клиента: «{text}»"},
    ]


def _strip_json_fence(response_text: str) -> str:
    """Remove a markdown code fence if the model wrapped the JSON anyway."""

    text = response_text.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()
    return text


def resolve_intent(
    text: str,
    *,
    message_id: str,
    trace_id: str,
    llm_client: Any,
) -> tuple[dict[str, Any] | None, Any]:
    """One resolver pass: LLM draft → deterministic validation.

    Returns ``(contract_or_none, usage)`` where ``usage`` is the OpenAI-
    shaped usage namespace (for the metric row). Never raises: any LLM or
    parse failure is a WARN log + ``(None, usage)``.
    """

    usage = None
    try:
        model = getattr(settings, "INTENT_RESOLUTION_MODEL", "gpt-4o-mini")
        response = asyncio.run(
            llm_client.chat.completions.create(
                model=model,
                messages=build_resolution_messages(text, message_id=message_id),
            )
        )
        usage = getattr(response, "usage", None)
        content = response.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001 — resolver must never break the turn
        logger.warning(
            "orchestrator.intent_resolution.llm_error trace=%s err=%s",
            trace_id,
            exc,
        )
        return None, usage

    try:
        raw = json.loads(_strip_json_fence(content))
    except json.JSONDecodeError:
        logger.warning(
            "orchestrator.intent_resolution.malformed_json trace=%s response_len=%d",
            trace_id,
            len(content),
        )
        return None, usage

    return _validate_and_build(raw, user_text=text, message_id=message_id, trace_id=trace_id), usage


def resolve_and_log_turn_intent(
    *,
    text: str,
    bot_user: Any,
    conversation: Any,
    user_message_id: Any,
    trace_id: str,
    tool_trace: Any = None,
) -> dict[str, Any] | None:
    """Resolve one free-text turn and serialise the contract into the turn log.

    The ONLY entry point the handler needs. Flag-gated, best-effort, never
    raises; returns the contract (tests) or ``None``. The log record
    ``orchestrator.intent_resolution.ok`` carries the full serialized
    contract — that IS the DRF-1273 deliverable: «в логе хода лежит
    сериализованный Output Contract 0.5».

    DRF-1385: ``tool_trace`` — упорядоченная трасса выбора инструментов
    консьержем (``[{"tool": ..., "arguments": {...}}]``). Когда флаг
    ``INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED`` включён и трасса
    непуста, контракт строится детерминированно по ПЕРВОМУ инструменту —
    без единого вызова модели; любой пробел (отображения нет, валидация
    отвергла, исключение) откатывает ход в нынешний LLM-проход ниже.
    """

    if not resolution_enabled():
        return None

    message_id = str(user_message_id) if user_message_id is not None else f"trace:{trace_id}"

    if tool_trace and tool_choice_resolution_enabled():
        deterministic_started = time.monotonic()
        contract = None
        try:
            draft = _draft_from_tool_trace(tool_trace, user_text=text)
            if draft is not None:
                contract = _validate_and_build(
                    draft, user_text=text, message_id=message_id, trace_id=trace_id
                )
        except Exception as exc:  # noqa: BLE001 — resolver must never break the turn
            logger.warning(
                "orchestrator.intent_resolution.tool_choice_failed trace=%s err=%s",
                trace_id,
                exc,
            )
            contract = None
        if contract is not None:
            latency_ms = int((time.monotonic() - deterministic_started) * 1000)
            _record_resolution_metric(
                bot_user=bot_user,
                conversation=conversation,
                trace_id=trace_id,
                text=text,
                outcome="success",
                latency_ms=latency_ms,
                # Вызова модели не было: tokens/cost — None («no LLM call»),
                # клиент не создаётся вообще.
                usage=None,
                llm_client=None,
            )
            # Формат записи сохранён (на неё смотрят мониторы); маркер
            # source=tool_choice добавлен ПОСЛЕ contract и только здесь.
            logger.info(
                "orchestrator.intent_resolution.ok trace=%s contract=%s source=tool_choice",
                trace_id,
                json.dumps(contract, ensure_ascii=False, sort_keys=True),
            )
            return contract
        # Драфта нет или валидация его отвергла — НИЧЕГО не терять:
        # дальше нынешний LLM-проход без изменений.

    # Deferred import: concierge pulls ayla_ai_core at module import time;
    # keeping it out of this module's top level lets the validator unit
    # tests run without the ai-core dependency graph.
    from apps.orchestrator.concierge import CONCIERGE_SKILL, RouterLLMClient

    llm_client = RouterLLMClient(skill=CONCIERGE_SKILL)

    started = time.monotonic()
    contract, usage = resolve_intent(
        text,
        message_id=message_id,
        trace_id=trace_id,
        llm_client=llm_client,
    )
    latency_ms = int((time.monotonic() - started) * 1000)

    _record_resolution_metric(
        bot_user=bot_user,
        conversation=conversation,
        trace_id=trace_id,
        text=text,
        outcome="success" if contract is not None else "error",
        latency_ms=latency_ms,
        usage=usage,
        llm_client=llm_client,
    )

    if contract is None:
        # The invalid/errored draft is already WARN-logged upstream with a
        # reason code; nothing is fabricated in its place.
        return None

    logger.info(
        "orchestrator.intent_resolution.ok trace=%s contract=%s",
        trace_id,
        json.dumps(contract, ensure_ascii=False, sort_keys=True),
    )
    return contract


def _record_resolution_metric(
    *,
    bot_user: Any,
    conversation: Any,
    trace_id: str,
    text: str,
    outcome: str,
    latency_ms: int,
    usage: Any,
    llm_client: Any,
) -> None:
    """One ``AIRequestMetric`` row per resolver pass (DRF-1211 pattern).

    ``llm_pass_index=0`` keeps resolver rows separable from concierge reply
    passes (1..N); ``skill_selected="intent_resolution"`` makes the extra
    call's cost attributable. Best-effort — observability never crashes
    the turn.
    """

    try:
        from apps.identity.services.global_tenant import get_global_bot_tenant
        from apps.llm.pricing import UnknownModelError, compute_cost
        from apps.observability.ai_metrics import record_ai_request
        from apps.observability.models import AIRequestMetric

        try:
            request_uuid = uuid.UUID(str(trace_id))
        except (ValueError, TypeError, AttributeError):
            request_uuid = uuid.uuid5(
                uuid.NAMESPACE_DNS, f"intent-resolution:{trace_id or 'no-trace'}"
            )

        tokens_in = getattr(usage, "prompt_tokens", None) if usage is not None else None
        tokens_out = getattr(usage, "completion_tokens", None) if usage is not None else None
        llm_model = getattr(llm_client, "last_model", "") or ""
        cost_usd = None
        if llm_model and tokens_in is not None:
            try:
                cost_usd = compute_cost(
                    llm_model, input_tokens=tokens_in, output_tokens=tokens_out or 0
                )
            except UnknownModelError:
                cost_usd = None

        record_ai_request(
            tenant=get_global_bot_tenant(),
            bot_user=bot_user,
            conversation=conversation,
            request_id=request_uuid,
            message_text_length=len(text),
            skill_selected="intent_resolution",
            latency_total_ms=latency_ms,
            latency_llm_ms=None,
            llm_provider=getattr(llm_client, "last_provider", "") or "",
            llm_model=llm_model,
            llm_tokens_input=tokens_in,
            llm_tokens_output=tokens_out,
            llm_cost_usd=cost_usd,
            llm_pass_index=0,
            outcome=(
                AIRequestMetric.OUTCOME_SUCCESS
                if outcome == "success"
                else AIRequestMetric.OUTCOME_ERROR
            ),
        )
    except Exception as exc:  # noqa: BLE001 — observability never crashes the turn
        logger.warning(
            "orchestrator.intent_resolution.metric_failed trace=%s err=%s",
            trace_id,
            exc,
        )
