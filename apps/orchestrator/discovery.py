"""Tenant-less discovery reply generator (#1026 / EPIC #1014).

The nationwide bot answers a discovery turn at ``current_tenant()=None`` using
the frozen ayla-ai-core marketplace voice (``AYLA_MARKETPLACE_VOICE``) and
bot-platform's OWN LLM runtime (``apps.llm.router``) — the same mechanism the
per-tenant skills use. Since W5 (pilot 2026-08-15) the concierge DM itself
runs on ayla-ai-core's ``AIConcierge`` — see
:mod:`apps.orchestrator.concierge`. This module keeps the shared building
blocks (prompt builder, ``SHOW_MASTERS_TOOL_SPEC``, card renderer) plus the
hand-rolled reply generator as the tested fallback.

Tenant-independent by construction: ``get_provider(None, ...)`` short-circuits
to the per-skill / org-wide provider tier, and the prompt reads NO
tenant-scoped / commercial data. Any LLM failure degrades to a safe fallback
line, never a 500.

Memory surfacing (M-C1 / #1101): the caller MAY pass an optional
``personal_context`` block — the user's GREEN cross-channel memory + UPC summary
(read via ``apps.identity.services.memory_reader``, rendered by
``apps.persona.memory_surface``). ``UserPersonalContext`` is cross-tenant by
design (ADR-0011) and carries no commercial/tenant-scoped state, so surfacing it
on the tenant-less path is consistent with the discovery contract. When absent
the prompt is unchanged (happy-path intact).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from apps.llm.router import get_router
from apps.marketplace.discovery import discover_masters
from apps.marketplace.dto import MasterCard
from apps.orchestrator.llm.templates import get_fallback
from apps.persona.memory_surface import render_personal_context

if TYPE_CHECKING:
    from apps.identity.services.memory_reader import PersonalContextView

logger = logging.getLogger(__name__)

# Provider-routing skill slug (Tier-2 `SKILL_LLM_PROVIDER` lookup, optional).
DISCOVERY_SKILL = "discovery"

_MAX_REPLY_CHARS = 600
_MAX_MASTER_CARDS = 5

# Callback prefix for the discovery → booking handoff button (#1020). Carries
# the PUBLIC ids from the MasterCard DTO:
# ``cb:discover:book:{tenant_id}:{master_id}`` — plus, when discovery resolved
# the queried service unambiguously (DRF-962),
# ``cb:discover:book:{tenant_id}:{master_id}:{service_id}``. The global handler
# detects this, enters tenant_scope(T), and routes into the per-tenant booking
# flow WITH the service context — without it the booking skill's pick_master
# guard correctly refuses the serviceless tap («Контекст записи устарел»).
# No commercial data is in the callback.
CALLBACK_DISCOVER_BOOK_PREFIX = "cb:discover:book:"

# OpenAI-shaped function spec — the discovery LLM calls this when the user wants
# to find/see masters. We execute it via the sanctioned marketplace carve-out
# (apps.marketplace.discovery.discover_masters), the SOLE cross-tenant catalog
# reader. The tool only takes public filters; no tenant/commercial inputs.
SHOW_MASTERS_TOOL_SPEC: dict[str, Any] = {
    "name": "show_masters",
    "description": (
        "Show bookable beauty masters across all salons matching the user's "
        "request. Call this when the user wants to find, browse, or pick a "
        "master (optionally by city or specialization)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City to filter by (optional)."},
            "specialization": {
                "type": "string",
                "description": "Specialization / service substring (optional), e.g. 'маникюр'.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_MASTER_CARDS,
                "description": "How many masters to show.",
            },
        },
        "required": [],
    },
}

# OpenAI-shaped function spec (DRF-1102) — lets the concierge ask a clarifying
# question AS A TOOL CALL, with tappable options, instead of the only other
# option it has today: plain assistant text. Plain text doesn't move the
# conversation state, so a user who can't guess the exact catalog wording
# (e.g. a service name) gets stuck re-answering the same question forever
# (DRF-1102 §1). Shape mirrors ayla-ai-core's own ``ask_clarification`` tool
# (``ayla_ai_core.tools.ASK_CLARIFICATION``) — kept as a local FLAT spec, not
# a direct import, because ``apps.llm.providers.openai_provider`` wraps every
# entry of ``tools`` in ``{"type": "function", "function": spec}`` itself;
# importing ai-core's already-nested constant here would double-wrap it.
ASK_CLARIFICATION_TOOL_SPEC: dict[str, Any] = {
    "name": "ask_clarification",
    "description": (
        "Ask a clarifying question with suggested answer options, when the "
        "user's request is genuinely unclear. Prefer this over asking in "
        "plain text — options let the user answer with one tap instead of "
        "having to guess exact wording."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 5 short tappable answer options (optional).",
                "maxItems": 5,
            },
        },
        "required": ["question"],
    },
}

# Defensive cap on a clarification option's button label — these strings are
# model-generated, unlike the catalog-bounded master names `_render_master_cards`
# renders, so nothing upstream already bounds their length.
_MAX_OPTION_LABEL_CHARS = 40


@dataclass(frozen=True)
class DiscoveryReply:
    """A discovery turn's outcome: the reply text + optional channel action_data
    (e.g. the master-card keyboard). ``action_data`` mirrors the per-tenant
    SkillResult shape so the MAX handler renders it via ``_build_attachments``.

    ``persisted`` (W5): True when the assistant turn was already persisted
    by the producer (the AIConcierge store in
    :mod:`apps.orchestrator.concierge`) — the handler must NOT record it
    again. Legacy producers leave it False.
    """

    text: str
    action_data: dict[str, Any] | None = None
    persisted: bool = False


# Local mirror of ayla-ai-core's AYLA_MARKETPLACE_VOICE (v0.8.1) for envs where
# the ``[ai-core]`` extra isn't installed (CI installs it → the real frozen
# voice is consumed there). Field values copied verbatim from the frozen
# constant; guarded import below prefers the real thing.
_FALLBACK_VOICE_FIELDS: dict[str, str] = {
    "assistant_name": "Ayla",
    "business_name": "Ayla — AI Self-Care",
    "domain": "beauty-услуги",
    "off_topic_redirect": "Я помогаю с записями к beauty-мастерам, чем помочь?",
}


def _discovery_voice_fields() -> dict[str, str]:
    """Read the marketplace voice from frozen ayla-ai-core, or fall back.

    Consumes (never modifies) the frozen ``AYLA_MARKETPLACE_VOICE`` constant.
    Guarded like the other ayla-ai-core imports in this app — the library is an
    optional extra in some environments.
    """
    try:
        from ayla_ai_core import AYLA_MARKETPLACE_VOICE as voice
    except Exception:  # pragma: no cover - lib not installed in this env
        return dict(_FALLBACK_VOICE_FIELDS)
    return {
        "assistant_name": voice.assistant_name,
        "business_name": voice.business_name,
        "domain": voice.domain,
        "off_topic_redirect": voice.off_topic_redirect,
    }


def build_discovery_prompt(
    message_text: str,
    *,
    history: list[dict[str, Any]] | None = None,
    personal_context: str | None = None,
) -> list[dict[str, str]]:
    """Render the ChatML messages for a discovery turn.

    System message is composed manually from the marketplace voice fields (NOT
    ``ayla_ai_core.render_system_prompt`` — that is booking-domain coupled and
    forbidden by the import allow-list). ``history`` is the short per-turn
    memory (``short_term.recall``). ``personal_context`` is the optional
    surfacing block (M-C1) — a pre-rendered paragraph of the user's GREEN
    memory; appended to the system message when present, omitted otherwise.
    """
    voice = _discovery_voice_fields()
    system_parts = [
        f"Ты — {voice['assistant_name']}, AI-помощник «{voice['business_name']}».",
        "Ты помогаешь клиенту по всей стране подобрать подходящего "
        f"{voice['domain']}-мастера и записаться — конкретный салон выбирается "
        "только в момент записи.",
        "Это разговор-знакомство (discovery): отвечай тепло и кратко, "
        "задавай уточняющие вопросы про услугу, город и предпочтения. НЕ "
        "называй конкретный салон, цену или адрес — этих данных пока нет.",
        f"Если вопрос не про запись к мастеру — мягко верни в тему: "
        f"«{voice['off_topic_redirect']}»",
        f"Ответ не длиннее {_MAX_REPLY_CHARS} символов.",
    ]
    if personal_context:
        system_parts.append(personal_context)
    system_text = "\n\n".join(system_parts)
    messages: list[dict[str, str]] = [{"role": "system", "content": system_text}]
    for item in history or []:
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message_text})
    return messages


def _render_master_cards(cards: list[MasterCard]) -> DiscoveryReply:
    """Render discovered masters as a reply + a one-button-per-card keyboard.

    PUBLIC fields only (the DTO carries nothing else). Each button's callback
    is ``cb:discover:book:{tenant_id}:{master_id}`` — the handoff seam
    (#1020) — extended with ``:{service_id}`` when discovery resolved the
    queried service unambiguously (DRF-962), so the tap enters booking with
    the service context instead of the stale-context dead-end.
    """
    if not cards:
        return DiscoveryReply(
            text="По вашему запросу мастеров пока не нашлось — уточните город или услугу.",
        )

    lines = ["Вот мастера, которые могут подойти:"]
    buttons: list[dict[str, str]] = []
    for card in cards:
        rating = f" · ★ {card.rating}" if card.rating is not None else ""
        city = f" · {card.city}" if card.city else ""
        # The em-dash belongs to the specialization, not to the line. Ayla's
        # specialists feed carries no specialization, so since DRF-945 made
        # service-relation matching the primary discovery path, the empty case
        # is the COMMON one — an unconditional dash renders every card as
        # «• Массажист —  · Пенза».
        spec = f" — {card.specialization}" if card.specialization else ""
        # Surface the resolved service on the card line: the button will carry
        # it into booking, so the user must SEE what they are tapping into.
        service = f" · {card.service_name}" if card.service_id is not None else ""
        lines.append(f"• {card.name}{spec}{service}{rating}{city}")
        service_suffix = f":{card.service_id}" if card.service_id is not None else ""
        buttons.append(
            {
                "label": f"Записаться к {card.name}",
                "callback": (
                    f"{CALLBACK_DISCOVER_BOOK_PREFIX}"
                    f"{card.tenant_id}:{card.master_id}{service_suffix}"
                ),
            }
        )
    action_data = {"attachments": [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]}
    return DiscoveryReply(text="\n".join(lines)[:_MAX_REPLY_CHARS], action_data=action_data)


def _render_ask_clarification(question: str, options: list[str]) -> DiscoveryReply:
    """Render an ``ask_clarification`` tool call as reply text + a tap keyboard.

    Each option becomes a button whose callback is the option's OWN text —
    the same "tap == typed answer" contract the master-card buttons above
    already use on this path. MAX delivers a tapped payload through the same
    field a typed message would (see ``is_menu_callback`` in
    ``apps.skills.menu.matching`` for the established precedent), so a tap
    simply re-enters the normal turn as if the user had typed that option —
    no separate decode step, no server-side pending-question state to keep in
    sync (unlike the legacy ``cb:ai:answer:{conv}:{idx}`` scheme).

    No options → plain question text, no keyboard: the user answers freely.
    """
    text = (question or "Уточните, пожалуйста?").strip()[:_MAX_REPLY_CHARS]
    cleaned = [str(opt).strip() for opt in options if str(opt).strip()]
    if not cleaned:
        return DiscoveryReply(text=text)
    buttons = [{"label": opt[:_MAX_OPTION_LABEL_CHARS], "callback": opt} for opt in cleaned[:5]]
    action_data = {"attachments": [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]}
    return DiscoveryReply(text=text, action_data=action_data)


def generate_discovery_reply(
    message_text: str,
    *,
    history: list[dict[str, Any]] | None = None,
    personal_context: "PersonalContextView | None" = None,
    trace_id: str | None = None,
) -> DiscoveryReply:
    """Generate a discovery reply via the tenant-less LLM path (tool-capable).

    ``get_router().get_provider(None, skill="discovery")`` — ``tenant=None``
    short-circuits to the per-skill / org-wide provider tier, so NO tenant is
    required and no tenant-scoped read happens. The LLM may call the
    ``show_masters`` tool, which we execute via the sanctioned marketplace
    carve-out (``discover_masters``) and render as a master-card keyboard. On any
    ``LLMError`` (or an empty completion) returns a safe fallback line.

    ``personal_context`` (M-C1 / #1101) is the user's surfaceable GREEN memory;
    rendered into a system-prompt block when non-empty, ignored otherwise.
    """
    context_block = render_personal_context(personal_context) if personal_context else None
    messages = build_discovery_prompt(message_text, history=history, personal_context=context_block)
    try:
        provider = get_router().get_provider(None, skill=DISCOVERY_SKILL, op="complete")
        model = getattr(provider, "default_completion_model", None) or ""
        result = asyncio.run(
            provider.complete(messages, model=model, tools=[SHOW_MASTERS_TOOL_SPEC])
        )
    except Exception as exc:  # noqa: BLE001 — discovery must never 500; degrade to fallback
        logger.warning("orchestrator.discovery.llm_error trace=%s err=%s", trace_id, exc)
        return DiscoveryReply(text=get_fallback("ru"))

    # The model asked to see masters → run the cross-tenant carve-out + render.
    for call in result.tool_calls:
        if call.name == SHOW_MASTERS_TOOL_SPEC["name"]:
            args = call.arguments if isinstance(call.arguments, dict) else {}
            limit = args.get("limit")
            cards = discover_masters(
                city=args.get("city") or None,
                specialization=args.get("specialization") or None,
                limit=int(limit) if isinstance(limit, int) and limit > 0 else _MAX_MASTER_CARDS,
                resolve_service=True,
            )
            logger.info(
                "orchestrator.discovery.show_masters count=%d trace=%s", len(cards), trace_id
            )
            return _render_master_cards(cards[:_MAX_MASTER_CARDS])

    text = (result.text or "").strip()
    if not text:
        return DiscoveryReply(text=get_fallback("ru"))
    return DiscoveryReply(text=text[:_MAX_REPLY_CHARS])
