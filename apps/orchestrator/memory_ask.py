"""Memory-ask (S3.5) — the concierge asks ONE green-zone question (W5).

Flow (acceptance #7: «вопрос → ответ → память обновилась → следующая
рекомендация учла»):

1. :func:`maybe_weave_question` — after the concierge reply, gated
   ask-eligibility (Ayla anti-spam engine) decides whether ONE question
   may be asked; the contract ``prompt_hint`` is woven into the reply
   organically, ``mark-asked`` stamps the 24h cooldown EXACTLY ONCE
   (non-idempotent — never retried), and the pending question is parked
   in Redis (TTL mirrors the cooldown window).
2. :func:`try_handle_answer` — on the next turn, a pending question
   treats the message as its answer: explicit skip → ``POST /skip/``
   (also non-idempotent, exactly once); a parseable answer →
   ``PATCH personal-context`` with ``source: conversational``; unrelated
   text abandons the pending question quietly (helpful restraint —
   the cooldown already prevents an immediate re-ask).

Contract discipline (PERSONAL_CONTEXT_INTERNAL_API_CONTRACT v1.0): all
wire calls go through the W3 gated services (memory_green enforced
inside; exception-free GatedResult). ``favorite_masters`` is NOT asked
bot-side in the pilot: answers can't be resolved to SpecialistProfile
UUIDs cross-tenant (the contract requires UUIDs) — the field is skipped
here and flagged to the orchestrator; surfacing of Ayla-filled values
still works via the memory block.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from apps.identity.services.personal_context import (
    GateStatus,
    get_ask_eligibility,
    mark_asked,
    patch_declared_prefs,
    skip,
)
from apps.orchestrator.discovery import DiscoveryReply
from apps.orchestrator.memory import short_term

logger = logging.getLogger(__name__)

# Mirrors the 24h mark-asked cooldown — an abandoned question must not
# outlive the window in which a re-ask is blocked anyway.
_PENDING_TTL_SECONDS = 24 * 3600

_UNPARSED = object()

_SKIP_MARKERS = (
    "не хочу отвечать",
    "не буду отвечать",
    "не отвечу",
    "не скажу",
    "пропусти",
    "пропустим",
    "не хочу говорить",
    "потом расскажу",
    "не сейчас",
    "skip",
)


def _pending_key(conversation_id: Any) -> str:
    return f"conv:{conversation_id}:memory_ask_pending"


def read_pending(conversation_id: Any) -> dict | None:
    """Read the pending memory question, or None. Never raises."""
    try:
        raw = short_term._redis_client().get(_pending_key(conversation_id))
        if not raw:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) and data.get("field") else None
    except Exception:  # noqa: BLE001
        logger.warning("orchestrator.memory_ask.pending_read_failed", exc_info=True)
        return None


def _write_pending(conversation_id: Any, pending: dict) -> None:
    short_term._redis_client().setex(
        _pending_key(conversation_id),
        _PENDING_TTL_SECONDS,
        json.dumps(pending, ensure_ascii=False),
    )


def _clear_pending(conversation_id: Any) -> None:
    short_term._redis_client().delete(_pending_key(conversation_id))


def _is_skip(text: str) -> bool:
    low = (text or "").strip().lower()
    return any(marker in low for marker in _SKIP_MARKERS)


def _weave(hint: str) -> str:
    """Organic wrapper for the contract prompt_hint (not robotic-verbatim)."""
    return f"Кстати, чтобы подбирать точнее — {hint[0].lower() + hint[1:]}"


def maybe_weave_question(
    conversation: Any,
    bot_user: Any,
    reply: DiscoveryReply,
) -> DiscoveryReply:
    """Append ONE organic memory question to the reply when Ayla allows it.

    Returns the input reply unchanged in every no-ask case (no eligibility,
    unsupported field, hint missing, gate closed, any error).
    """
    try:
        if read_pending(conversation.id) is not None:
            return reply
        elig = get_ask_eligibility(bot_user)
        if (
            elig.status is not GateStatus.OK
            or elig.eligibility is None
            or not elig.eligibility.should_ask
        ):
            return reply
        field = elig.eligibility.field or ""
        if field not in _FIELD_PARSERS:
            # favorite_masters (and any future field without a bot-side
            # parser): answers can't be grounded to contract values —
            # don't ask what we can't store (see module docstring).
            logger.info("orchestrator.memory_ask.unsupported_field field=%s", field)
            return reply
        hint = (elig.eligibility.prompt_hint or "").strip()
        if not hint:
            return reply
        # We WILL ask — stamp the cooldown exactly once (non-idempotent).
        stamped = mark_asked(bot_user, field)
        if stamped.status is not GateStatus.OK:
            return reply
        _write_pending(conversation.id, {"field": field, "hint": hint})
        return DiscoveryReply(
            text=f"{reply.text}\n\n{_weave(hint)}",
            action_data=reply.action_data,
            persisted=reply.persisted,
        )
    except Exception:  # noqa: BLE001 — asking must never break the turn
        logger.exception("orchestrator.memory_ask.weave_failed")
        return reply


def try_handle_answer(
    conversation: Any,
    bot_user: Any,
    text: str,
) -> DiscoveryReply | None:
    """Treat ``text`` as the answer to a pending memory question (or skip).

    Returns None when no question is pending (caller proceeds with the
    normal concierge turn) or when the text is unrelated — the pending
    question is then abandoned quietly (helpful restraint; the 24h
    cooldown already blocks an immediate re-ask).
    """
    try:
        pending = read_pending(conversation.id)
        if pending is None:
            return None
        field = pending["field"]
        if _is_skip(text):
            skip(bot_user, field)  # non-idempotent: exactly once per skip
            _clear_pending(conversation.id)
            return DiscoveryReply(text="Хорошо, не буду спрашивать 🤍")
        value = _FIELD_PARSERS[field](text)
        if value is _UNPARSED:
            _clear_pending(conversation.id)
            return None
        result = patch_declared_prefs(
            bot_user,
            [{"field": field, "value": value, "source": "conversational"}],
        )
        if result.status is GateStatus.OK:
            _clear_pending(conversation.id)
            return DiscoveryReply(text="Записала, спасибо 🤍 Учту в следующих подборках.")
        # Upstream failure — PATCH is idempotent (LWW), keep the pending
        # question so the next message can retry the save.
        logger.warning("orchestrator.memory_ask.patch_failed field=%s", field)
        return DiscoveryReply(
            text="Не получилось сохранить ответ — повтори, пожалуйста, чуть позже 🤍",
        )
    except Exception:  # noqa: BLE001 — degrade to a normal concierge turn
        logger.exception("orchestrator.memory_ask.answer_failed")
        return None


# ---------------------------------------------------------------------------
# Answer parsers (deterministic, per askable field)
# ---------------------------------------------------------------------------


def _parse_time_slots(text: str) -> Any:
    low = text.lower()
    checks = (
        ("early_morning", r"ранн\w*\s+утр"),
        ("morning", r"\bутр"),
        ("afternoon", r"\bдн[её]м\b|\bдень\b|\bдня\b"),
        ("evening", r"вечер"),
        ("late_evening", r"поздн|ноч"),
    )
    matched = {value for value, pattern in checks if re.search(pattern, low)}
    # «поздний вечер» ⊃ «вечер», «раннее утро» ⊃ «утро» — keep the specific one.
    if "late_evening" in matched:
        matched.discard("evening")
    if "early_morning" in matched:
        matched.discard("morning")
    ordered = [value for value, _ in checks if value in matched]
    return ordered or _UNPARSED


def _parse_price_max(text: str) -> Any:
    nums = [int(n) for n in re.findall(r"\d+", text.replace("\u00a0", "").replace(" ", ""))]
    plausible = [n for n in nums if 100 <= n <= 1_000_000]
    if not plausible:
        return _UNPARSED
    return f"{max(plausible)}.00"


def _parse_free_text(text: str) -> Any:
    value = text.strip()
    return value if value else _UNPARSED


_DAY_WORDS = (
    ("mon", r"понедельн|\bпн\b"),
    ("tue", r"вторник|\bвт\b"),
    ("wed", r"сред|\bср\b"),
    ("thu", r"четверг|\bчт\b"),
    ("fri", r"пятниц|\bпт\b"),
    ("sat", r"суббот|\bсб\b"),
    ("sun", r"воскресен|\bвс\b"),
)


def _parse_busy_days(text: str) -> Any:
    low = text.lower()
    days = [value for value, pattern in _DAY_WORDS if re.search(pattern, low)]
    return days or _UNPARSED


def _parse_rating(text: str) -> Any:
    match = re.search(r"(\d(?:[.,]\d)?)", text)
    if not match:
        return _UNPARSED
    value = float(match.group(1).replace(",", "."))
    if not 0.0 <= value <= 5.0:
        return _UNPARSED
    return value


_DIET_WORDS = (
    ("vegan", r"веган"),
    ("vegetarian", r"вегетариан"),
    ("keto", r"\bкето\b"),
    ("halal", r"халял"),
    ("kosher", r"кошер"),
)


def _parse_diet(text: str) -> Any:
    low = text.lower()
    for value, pattern in _DIET_WORDS:
        if re.search(pattern, low):
            return value
    return "other" if text.strip() else _UNPARSED


# Parsers for the bot-askable fields (ask-eligibility priority order from
# the frozen contract, minus favorite_masters — see module docstring).
_FIELD_PARSERS = {
    "preferred_time_slots": _parse_time_slots,
    "price_range_max": _parse_price_max,
    "workplace_district": _parse_free_text,
    "home_district": _parse_free_text,
    "busy_days": _parse_busy_days,
    "min_rating_preference": _parse_rating,
    "diet_type": _parse_diet,
}
