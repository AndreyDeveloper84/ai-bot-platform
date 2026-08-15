"""Global concierge — ayla-ai-core AIConcierge wiring (W5 / DRF-241 / pilot 2026-08-15).

The nationwide (tenant-less) concierge DM now runs on
:class:`ayla_ai_core.AIConcierge` instead of the hand-rolled completion in
:func:`apps.orchestrator.discovery.generate_discovery_reply`:

- **LLM client** — ``RouterLLMClient`` proxies ``chat.completions.create``
  onto ``apps.llm.router`` (tenant-less tier), so PII tokenization, cost
  tracking, retry and provider config stay on the platform runtime (152-ФЗ
  §6). The response is reshaped into the OpenAI duck-type AIConcierge parses.
- **ConversationStore** — ``GlobalConversationStore`` adapts the
  ``*_global_*`` conversation services (sentinel-scoped, ``current_tenant()``
  stays ``None``). The user turn is persisted by the channel handler BEFORE
  the concierge runs (all reply branches share that record), so
  ``save_message(role="user")`` is a no-op marker carrying the
  handler-recorded id for history exclusion; assistant turns persist here.
- **Tool dispatch** — DRF-241 hook. The dispatcher is pure validation +
  argument normalisation (no I/O, matching ai-core's "handlers are
  side-effect-free" contract); the sanctioned marketplace carve-out
  (:func:`apps.marketplace.discovery.discover_masters`) executes in the
  wrapper's SYNC scope after ``asyncio.run`` returns, so no sync-ORM call
  ever lands inside the event loop.
- **Prompt** — :func:`build_concierge_system_prompt` composes the frozen
  ``AYLA_MARKETPLACE_VOICE`` with the current-date grounding block
  (DRF-988), the boundary rules (no-sales, helpful
  restraint, S8 medical boundary — Constitution Art. X/XII, Journey Spec
  Stage 8) and the consent-gated ai-core memory block (W5 task 2).

The booking handoff (``cb:discover:book:{tenant}:{master}`` → per-tenant
BookingSkill → Ayla REST) is unchanged — this module owns only the
discovery dialog turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace
from typing import Any

from ayla_ai_core import ActionType, AIConcierge, ToolResult
from asgiref.sync import sync_to_async
from django.utils import timezone

from apps.conversations.services import (
    record_global_message,
    resolve_active_global_conversation,
)
from apps.llm.router import get_router
from apps.marketplace.discovery import discover_masters
from apps.orchestrator.discovery import (
    _MAX_MASTER_CARDS,
    _MAX_REPLY_CHARS,
    ASK_CLARIFICATION_TOOL_SPEC,
    SHOW_MASTERS_TOOL_SPEC,
    DiscoveryReply,
    _discovery_voice_fields,
    _render_ask_clarification,
    _render_master_cards,
)
from apps.orchestrator.llm.templates import get_fallback

logger = logging.getLogger(__name__)

# Provider-routing tier slug — reuse the discovery tier so operator
# overrides (SKILL_LLM_PROVIDER["discovery"]) keep working unchanged.
CONCIERGE_SKILL = "discovery"

# Russian weekday names for the date-grounding block (DRF-988) — weekday()
# indexed, locale-independent (``strftime("%A")`` follows the OS locale).
_WEEKDAYS_RU = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")

# CandidateContext.tenant_id is mandatory non-empty in ai-core (v0.7.0).
# The concierge is tenant-less by design; this sentinel is a log/scope
# label only — no tenant-scoped read is ever made from it.
GLOBAL_TENANT_ID = "ayla-global"


@dataclass(frozen=True)
class ConciergeContext:
    """Minimal ``CandidateContext`` for the concierge.

    No candidates are pre-injected: master discovery happens on demand via
    the ``show_masters`` tool. The shape satisfies ai-core's structural
    Protocol (candidates / candidate_ids / summary_text / tenant_id).
    """

    candidates: list[Any]
    candidate_ids: frozenset
    summary_text: str
    tenant_id: str


def _to_openai_shape(result: Any) -> Any:
    """Reshape an ``apps.llm.protocol.CompletionResult`` into the OpenAI
    duck-type ``AIConcierge._parse_completion`` reads (choices[0].message
    with content + tool_calls[].function.{name,arguments}, usage tokens).
    """
    tool_calls = [
        SimpleNamespace(
            id=tc.id or "",
            type="function",
            function=SimpleNamespace(name=tc.name, arguments=json.dumps(tc.arguments or {})),
        )
        for tc in (result.tool_calls or [])
    ]
    message = SimpleNamespace(
        content=result.text or "",
        tool_calls=tool_calls or None,
    )
    usage = SimpleNamespace(
        prompt_tokens=result.prompt_tokens or 0,
        completion_tokens=result.completion_tokens or 0,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=result.finish_reason or "")],
        usage=usage,
    )


class _RouterCompletions:
    """``chat.completions`` facade routed through ``apps.llm.router``.

    ``get_provider`` is sync and emits an audit row (sync ORM), so it is
    wrapped in ``sync_to_async`` — same pattern as
    ``apps.orchestrator.intent_router._classify_production_path``.
    """

    def __init__(self, *, skill: str) -> None:
        self._skill = skill

    async def create(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        **_: Any,
    ) -> Any:
        def _resolve() -> Any:
            return get_router().get_provider(None, skill=self._skill, op="complete")

        provider = await sync_to_async(_resolve, thread_sensitive=False)()
        result = await provider.complete(messages, model=model, tools=tools)
        return _to_openai_shape(result)


class RouterLLMClient:
    """AsyncOpenAI-shaped client over the platform LLM router (tenant-less)."""

    def __init__(self, *, skill: str) -> None:
        self.chat = SimpleNamespace(completions=_RouterCompletions(skill=skill))


class GlobalConversationStore:
    """``ayla_ai_core.ConversationStore`` over the ``*_global_*`` services.

    ``record_global_message`` carries no ``action_data`` / ``tool_call`` /
    ``tool_call_id`` kwargs (unlike its per-tenant sibling): those payloads
    are transient render data for the channel adapter, so the adapter
    accepts and drops them — persisted fields match today's global path
    exactly (content, action_type, token telemetry).
    """

    def __init__(self, *, user_message_id: Any = None) -> None:
        self._user_message_id = user_message_id

    def resolve_active_conversation(self, bot_user: Any) -> Any:
        return resolve_active_global_conversation(bot_user)

    def save_message(
        self,
        conversation: Any,
        *,
        role: str,
        content: str,
        action_type: str = "",
        action_data: dict | None = None,
        tool_call: dict | None = None,
        tool_call_id: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: int | None = None,
    ) -> Any:
        if role == "user":
            # Persisted upstream by the channel handler — return the marker
            # so AIConcierge can exclude this turn from LLM history.
            return SimpleNamespace(id=self._user_message_id)
        return record_global_message(
            conversation,
            role=role,
            content=content,
            rendered_text=content,
            action_type=action_type or "",
            tokens_in=tokens_in or 0,
            tokens_out=tokens_out or 0,
            latency_ms=latency_ms,
        )

    def load_recent_history(
        self,
        conversation: Any,
        *,
        exclude_id: Any | None = None,
        limit: int = 10,
    ) -> list[Any]:
        from apps.conversations.models import Message

        qs = Message.all_tenants.filter(conversation=conversation).order_by("-created_at")
        if exclude_id is not None:
            qs = qs.exclude(id=exclude_id)
        # Last N (DESC) reversed → chronological, per the Protocol contract.
        return list(reversed(qs[:limit]))


_KNOWN_TOOLS = frozenset({SHOW_MASTERS_TOOL_SPEC["name"], ASK_CLARIFICATION_TOOL_SPEC["name"]})


def _dispatch_tool(tool_call: Any, context: Any) -> ToolResult:
    """DRF-241 dispatcher — pure validation + argument normalisation.

    No I/O: the marketplace carve-out executes in the wrapper's sync scope
    (see module docstring). Unknown tools and malformed arguments degrade
    to ``ask_clarification`` (question-less — ``generate_concierge_reply``
    treats that as an internal failure and falls back to the safe line, same
    as before this tool existed) so the concierge rephrases instead of
    crashing. A genuine ``ask_clarification`` call (DRF-1102) carries the
    model's own question + options through, distinct from that degrade path.
    """
    name = getattr(tool_call.function, "name", "")
    if name not in _KNOWN_TOOLS:
        return ToolResult(
            action_type=ActionType.ASK_CLARIFICATION,
            action_data={"reason": f"unknown_tool:{name}"},
        )
    raw = getattr(tool_call.function, "arguments", "") or "{}"
    try:
        args = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return ToolResult(
            action_type=ActionType.ASK_CLARIFICATION,
            action_data={"reason": "malformed_arguments"},
        )
    if not isinstance(args, dict):
        args = {}
    if name == ASK_CLARIFICATION_TOOL_SPEC["name"]:
        return ToolResult(
            action_type=ActionType.ASK_CLARIFICATION,
            action_data={
                "question": args.get("question", ""),
                "options": args.get("options") or [],
            },
        )
    return ToolResult(
        action_type=ActionType.SHOW_MASTERS,
        action_data={"arguments": args},
    )


def build_concierge_system_prompt(
    *,
    memory_block: str = "",
    extra_system: str = "",
    today: date | None = None,
) -> str:
    """Compose the concierge system prompt.

    Frozen ``AYLA_MARKETPLACE_VOICE`` fields (consumed, never modified) +
    discovery framing + boundary rules (no-sales, helpful restraint, S8
    medical boundary) + optional consent-gated memory block (W5 task 2).

    ``today`` grounds the model in the current date (DRF-988): without it
    the model lives at its training cutoff and rejects real near-future
    booking dates (e.g. август 2026) as «далёкое будущее». Defaults to the
    Django clock (``timezone.localdate()``, same as the legacy concierge);
    tests pass an explicit date for determinism.
    """
    if today is None:
        today = timezone.localdate()
    voice = _discovery_voice_fields()
    parts = [
        f"Ты — {voice['assistant_name']}, AI-помощник «{voice['business_name']}».",
        # DRF-988 — date grounding (date + weekday + timezone).
        f"Сегодня: {today.isoformat()} ({_WEEKDAYS_RU[today.weekday()]}), "
        f"часовой пояс {timezone.get_current_timezone()}. Используй эту дату "
        "для парсинга относительных («завтра», «послезавтра») и конкретных "
        "дат записи.",
        "Ты помогаешь клиенту по всей стране подобрать подходящего "
        f"{voice['domain']}-мастера и записаться — конкретный салон выбирается "
        "только в момент записи.",
        "Это разговор-знакомство (discovery): отвечай тепло и кратко, "
        "задавай уточняющие вопросы про услугу, город и предпочтения. НЕ "
        "называй конкретный салон, цену или адрес — этих данных пока нет.",
        # DRF-1102 — the tool exists now (see tool_definitions); without this
        # line the model has no reason to prefer it over the plain-text habit
        # the rest of this prompt otherwise establishes.
        "Если нужно уточнение — вызывай инструмент ask_clarification с "
        "вариантами ответа, а НЕ пиши уточняющий вопрос обычным текстом: "
        "так клиент отвечает одним тапом, а не гадает формулировку.",
        f"Если вопрос не про запись к мастеру — мягко верни в тему: "
        f"«{voice['off_topic_redirect']}»",
        # Boundaries (W5 task 4) — Constitution Art. X (helpful restraint),
        # Art. XII (competence boundary), Journey Spec Stage 8.
        "Границы (обязательно):\n"
        "- Ты не продаёшь: никаких акций, скидочного давления, «успей "
        "записаться», каталог-перечислений. Предлагаешь только то, что "
        "отвечает запросу клиента.\n"
        "- Иногда лучший ответ — ничего не предлагать. Если запроса нет "
        "или тема исчерпана, не выдумывай предложений.\n"
        "- Медицинские темы (диагнозы, лекарства, боль, травмы, опасные "
        "цели похудения): остановись, коротко назови границу без диагноза "
        "(«я не врач и не оцениваю здоровье»), спокойно обозначь риск "
        "простыми словами и предложи безопасный шаг — обратиться к "
        "профильному специалисту или сформулировать новое безопасное "
        "намерение. Не сохраняй медицинские выводы как факт о клиенте.",
        f"Ответ не длиннее {_MAX_REPLY_CHARS} символов.",
    ]
    if memory_block:
        parts.append(memory_block)
    if extra_system:
        parts.append(extra_system)
    return "\n\n".join(parts)


def generate_concierge_reply(
    message_text: str,
    *,
    bot_user: Any,
    conversation: Any,
    user_message_id: Any = None,
    memory_block: str = "",
    extra_system: str = "",
    trace_id: str | None = None,
) -> DiscoveryReply:
    """One concierge turn through ayla-ai-core AIConcierge (W5 / DRF-241).

    Persists the assistant turn via the store (``persisted=True`` on the
    returned reply so the handler does NOT double-record). On any LLM
    failure degrades to the same safe fallback line as the legacy
    discovery path — the concierge must never 500.
    """
    concierge = AIConcierge(
        openai_client=RouterLLMClient(skill=CONCIERGE_SKILL),
        store=GlobalConversationStore(user_message_id=user_message_id),
        context_builder=lambda: ConciergeContext(
            candidates=[],
            candidate_ids=frozenset(),
            summary_text="",
            tenant_id=GLOBAL_TENANT_ID,
        ),
        tool_definitions=[SHOW_MASTERS_TOOL_SPEC, ASK_CLARIFICATION_TOOL_SPEC],
        tool_dispatcher=_dispatch_tool,
    )

    def _renderer(_ctx: Any) -> str:
        return build_concierge_system_prompt(
            memory_block=memory_block,
            extra_system=extra_system,
        )

    try:
        dto = asyncio.run(
            concierge.send_message(
                user_key=bot_user,
                message_text=message_text,
                prompt_renderer=_renderer,
            )
        )
    except Exception as exc:  # noqa: BLE001 — degrade to safe fallback
        logger.warning("orchestrator.concierge.llm_error trace=%s err=%s", trace_id, exc)
        return DiscoveryReply(text=get_fallback("ru"))

    if dto.action_type == ActionType.SHOW_MASTERS:
        args = (dto.action_data or {}).get("arguments", {})
        limit = args.get("limit")
        cards = discover_masters(
            city=args.get("city") or None,
            specialization=args.get("specialization") or None,
            limit=int(limit) if isinstance(limit, int) and limit > 0 else _MAX_MASTER_CARDS,
            resolve_service=True,
        )
        logger.info(
            "orchestrator.concierge.show_masters count=%d trace=%s",
            len(cards),
            trace_id,
        )
        rendered = _render_master_cards(cards[:_MAX_MASTER_CARDS])
        return DiscoveryReply(
            text=rendered.text,
            action_data=rendered.action_data,
            persisted=True,
        )

    if dto.action_type == ActionType.ASK_CLARIFICATION:
        data = dto.action_data or {}
        question = str(data.get("question") or "").strip()
        if not question:
            # No question text: either _dispatch_tool's internal degrade path
            # (unknown tool / malformed arguments — action_data carries only
            # "reason") or a genuine ask_clarification call with a blank
            # question. Same safe fallback as an LLM error — never send an
            # empty clarification.
            return DiscoveryReply(text=get_fallback("ru"), persisted=True)
        rendered = _render_ask_clarification(question, list(data.get("options") or []))
        return DiscoveryReply(
            text=rendered.text,
            action_data=rendered.action_data,
            persisted=True,
        )

    text = (dto.content or "").strip()
    if not text:
        return DiscoveryReply(text=get_fallback("ru"), persisted=True)
    return DiscoveryReply(text=text[:_MAX_REPLY_CHARS], persisted=True)


def generate_direct_show_masters_reply(
    message_text: str, *, trace_id: str | None = None
) -> DiscoveryReply:
    """Deterministic show-masters short-circuit for a general booking request.

    DRF-1102 — the missing 8th branch in the pre-LLM detector chain in
    :mod:`apps.channels.max.handler` (safety → human_handoff → visit-callbacks
    → personal booking lookup → onboarding → discover-callbacks →
    booking-callbacks → **this**). A turn like «запиши меня на массаж» names
    a service/availability signal (:func:`apps.skills.menu.matching.
    looks_like_booking_request`) but isn't any narrower intent above, so it
    used to fall all the way to the concierge LLM — which, advertising only
    ``show_masters`` and told to "ask clarifying questions", had no tool-call
    path that didn't require a full free-text round trip, and would loop
    re-asking instead of ever calling it (root cause per the DRF-1102 audit).

    Skips the LLM entirely: the search layer already resolves free text fine
    (``discover_masters`` token-matches the raw phrase — DRF-945), so there is
    nothing for a model turn to decide here. Mirrors the per-tenant
    ``MenuSkill``, which already treats the same signal as a last-resort
    booking catch-all (``apps/skills/menu/skill.py``) — this is that same
    catch-all, applied one level up (before the concierge instead of before
    echo) because the global path has no salon-scoped booking skill to hand
    off to yet; showing masters IS the equivalent next step here.
    """
    cards = discover_masters(
        specialization=message_text,
        limit=_MAX_MASTER_CARDS,
        resolve_service=True,
    )
    logger.info(
        "orchestrator.concierge.direct_show_masters count=%d trace=%s",
        len(cards),
        trace_id,
    )
    return _render_master_cards(cards)
