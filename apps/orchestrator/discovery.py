"""Tenant-less discovery reply generator (#1026 / EPIC #1014).

The nationwide bot answers a discovery turn at ``current_tenant()=None`` using
the frozen ayla-ai-core marketplace voice (``AYLA_MARKETPLACE_VOICE``) and
bot-platform's OWN LLM runtime (``apps.llm.router``) — the same mechanism the
per-tenant skills use, NOT ayla-ai-core's ``AIConcierge`` class (which
bot-platform does not use anywhere).

Tenant-independent by construction: ``get_provider(None, ...)`` short-circuits
to the per-skill / org-wide provider tier, and the prompt reads NO
tenant-scoped / commercial / ``UserPersonalContext`` data — only the frozen
brand voice + the short per-turn history. Any LLM failure degrades to a safe
fallback line, never a 500.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from apps.llm.protocol import LLMError
from apps.llm.router import get_router
from apps.orchestrator.llm.templates import get_fallback

logger = logging.getLogger(__name__)

# Provider-routing skill slug (Tier-2 `SKILL_LLM_PROVIDER` lookup, optional).
DISCOVERY_SKILL = "discovery"

_MAX_REPLY_CHARS = 600

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
) -> list[dict[str, str]]:
    """Render the ChatML messages for a discovery turn.

    System message is composed manually from the marketplace voice fields (NOT
    ``ayla_ai_core.render_system_prompt`` — that is booking-domain coupled and
    forbidden by the import allow-list). ``history`` is the short per-turn
    memory (``short_term.recall``); no long-term ``UserPersonalContext``.
    """
    voice = _discovery_voice_fields()
    system_text = "\n\n".join(
        [
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
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system_text}]
    for item in history or []:
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message_text})
    return messages


def generate_discovery_reply(
    message_text: str,
    *,
    history: list[dict[str, Any]] | None = None,
    trace_id: str | None = None,
) -> str:
    """Generate a discovery reply via the tenant-less LLM path.

    ``get_router().get_provider(None, skill="discovery")`` — ``tenant=None``
    short-circuits to the per-skill / org-wide provider tier, so NO tenant is
    required and no tenant-scoped read happens. On any ``LLMError`` (or an empty
    completion) returns a safe fallback line instead of failing the turn.
    """
    messages = build_discovery_prompt(message_text, history=history)
    try:
        provider = get_router().get_provider(None, skill=DISCOVERY_SKILL, op="complete")
        model = getattr(provider, "default_completion_model", None) or ""
        result = asyncio.run(provider.complete(messages, model=model))
    except LLMError as exc:
        logger.warning("orchestrator.discovery.llm_error trace=%s err=%s", trace_id, exc)
        return get_fallback("ru")

    text = (result.text or "").strip()
    if not text:
        return get_fallback("ru")
    return text[:_MAX_REPLY_CHARS]
