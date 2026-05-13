"""FAQ skill prompt template (DRF-590 / Sprint 7 / F3).

**Decision 15** — Sprint 7 builds its **own** FAQ template, NOT a
re-use of ``ayla_ai_core.prompts.render_system_prompt``. The ayla
template is hard-coded for the booking domain (it inserts a
``masters_summary`` slot and includes anti-hallucination text
specific to "use only these master IDs"). Reusing it for FAQ would
either leak booking phrasing into FAQ answers or require an empty
``SpecialistContext`` (telling the LLM "use only these IDs: (none)"
— hostile to FAQ retrieval).

Instead, F3 ships a focused prompt that:

* Speaks the brand voice via :class:`BrandVoiceConfig.persona`
  (ayla-ai-core IS the source of truth for brand voice).
* Includes few-shot :class:`Example` entries (ayla-ai-core's
  reusable Example dataclass — same import allow-list).
* Splices in retrieved KB chunks so the LLM grounds its answer in
  real content.
* Caps answers at 600 chars + reminds the model to say "уточню у
  мастера" when chunks don't cover the question.

### Import allow-list

Per CLAUDE.md / Decision 1:

* ``BrandVoiceConfig`` — yes
* ``Example`` — yes
* ``SpecialistContext`` — yes (unused by FAQ but allowed)
* ``tools.ActionType`` — yes (unused by FAQ but allowed)
* ``render_system_prompt`` — **NO** (booking-domain coupled)
* ``tool_handlers`` — **NO** (Phase 2.3)

### Return shape

Returns a ChatML messages list ready to feed straight into
:meth:`apps.llm.protocol.LLMProvider.complete`. The first message
is the rendered system prompt; subsequent entries are few-shot
examples (user/assistant pairs); the final entry is the actual
user query.

### F2 wiring (DRF-589)

F2 will call this twice per turn:

1. ``build_faq_prompt(...)`` with ``retrieved_chunks=None`` to get
   the messages list for the *first* LLM call. The model decides
   whether to invoke ``search_knowledge_base`` (tool call) — F2
   inspects the response.
2. After tool execution, F2 calls ``build_faq_prompt(...)`` with
   the retrieved chunks substituted in. The second LLM call grounds
   on the chunks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Platform-owned dataclass: the FAQ skill reads brand voice via
# `persona` / `tone` / `forbidden` fields. F2 (DRF-589) adapts
# tenant-specific brand voice (ayla BrandVoiceConfig or any future
# source) into this shape before calling build_faq_prompt.
#
# We deliberately do NOT import `ayla_ai_core.prompts.BrandVoiceConfig`
# at type-check time: ayla's class has a different field-set (geared
# toward Examples + skill registry) that doesn't match the
# persona/tone/forbidden trio the FAQ template needs. Coupling our
# prompt schema to ayla's exact API would force version-pin lockstep
# nobody wants. F2 owns the adapter; this module owns the template.
@dataclass
class BrandVoiceConfig:
    """FAQ prompt brand-voice inputs.

    Fields:
      persona: short noun phrase the system prompt opens with —
               "Ты — {persona}." Typically the salon's named
               admin persona (e.g. "Алина, администратор салона").
      tone: optional tone descriptor; rendered as a "Тон: {tone}."
            line when present.
      forbidden: phrases the model must avoid. When non-empty,
                 rendered as "Запрещено: <comma-joined>.".
    """

    persona: str
    tone: str = ""
    forbidden: tuple[str, ...] = ()


@dataclass
class Example:
    """Few-shot user/assistant pair for the FAQ prompt.

    Local-owned so the module stays import-safe in CI without the
    `[ai-core]` extra. F2 may convert ayla's Example into this one
    if it wants to reuse the broader ayla example library.
    """

    user: str
    assistant: str


# ---------------------------------------------------------------------------
# Static FAQ examples — module constants
# ---------------------------------------------------------------------------


# Four few-shot exemplars that anchor brand voice + grounding behaviour.
# Stored at module scope so :class:`PromptRegistry` (Sprint 4) can pick
# them up without re-instantiating per call.
FAQ_EXAMPLES: list[Example] = [
    Example(
        user="Какие часы работы?",
        assistant=(
            "Мы работаем понедельник-воскресенье с 10:00 до 22:00. "
            "Если нужна точная дата визита — посмотрю свободные слоты."
        ),
    ),
    Example(
        user="Сколько стоит массаж спины?",
        assistant=(
            "Массаж спины — от 1500 рублей за сеанс 60 минут. "
            "Точная цена зависит от мастера и техники — уточню у конкретного."
        ),
    ),
    Example(
        user="Где вы находитесь?",
        assistant=(
            "Мы на ул. Ленина 1 в Пензе, рядом с метро Каштак. Парковка во дворе бесплатная."
        ),
    ),
    Example(
        user="А свечи Грейс продаёте?",
        assistant=(
            "Не уверена насчёт этой марки — уточню у мастера. "
            "Если нужен подарок, могу подобрать сертификат."
        ),
    ),
]


# Sprint 4 PromptRegistry slug used to fetch this prompt template.
PROMPT_REGISTRY_KEY = "skill.faq.grounded_answer"


# Hard cap so the FAQ answer doesn't blow the MAX message length. The
# F2 skill enforces the cap; this constant ships here so the prompt
# text can quote the same number.
_MAX_ANSWER_CHARS = 600


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def build_faq_prompt(
    *,
    brand_voice: BrandVoiceConfig,
    examples: list[Example] | None = None,
    retrieved_chunks: list[dict[str, Any]] | None = None,
    query: str,
) -> list[dict[str, str]]:
    """Render the messages list for an FAQ-skill LLM call.

    Args:
      brand_voice: tenant's :class:`BrandVoiceConfig` (persona, tone,
                   forbidden phrases). Sprint 4 prompt_registry stores
                   one per tenant; F2 reads it from there.
      examples: few-shot Example list. Defaults to :data:`FAQ_EXAMPLES`
                module constant.
      retrieved_chunks: when non-None, splices a "ИЗВЛЕЧЁННЫЕ ИЗ БАЗЫ
                        ЗНАНИЙ" block into the system prompt with the
                        chunk texts. F2 calls this twice — once with
                        None (tool-call decision), once with chunks
                        (grounded final answer).
      query: the actual user question.

    Returns:
      ChatML messages list ready for :meth:`LLMProvider.complete`.
    """
    examples = examples if examples is not None else FAQ_EXAMPLES

    system_text = _render_system_prompt(
        brand_voice=brand_voice,
        retrieved_chunks=retrieved_chunks,
    )

    messages: list[dict[str, str]] = [{"role": "system", "content": system_text}]

    # Few-shot pairs precede the actual user query so the model sees
    # the voice in context before answering.
    for example in examples:
        messages.append({"role": "user", "content": example.user})
        messages.append({"role": "assistant", "content": example.assistant})

    messages.append({"role": "user", "content": query})
    return messages


# ---------------------------------------------------------------------------
# System prompt rendering
# ---------------------------------------------------------------------------


def _render_system_prompt(
    *,
    brand_voice: BrandVoiceConfig,
    retrieved_chunks: list[dict[str, Any]] | None,
) -> str:
    """Build the system message text.

    Sections:
      1. Voice persona — quoted directly from BrandVoiceConfig.
      2. Forbidden phrases — when the brand voice declares any.
      3. Retrieval-grounding block — when chunks present.
      4. Anti-hallucination rule + answer-length cap.
    """
    sections: list[str] = [
        f"Ты — {brand_voice.persona}.",
    ]

    if getattr(brand_voice, "tone", ""):
        sections.append(f"Тон: {brand_voice.tone}.")

    forbidden = tuple(getattr(brand_voice, "forbidden", ()) or ())
    if forbidden:
        sections.append("Запрещено: " + ", ".join(forbidden) + ".")

    if retrieved_chunks:
        sections.append(_format_chunk_block(retrieved_chunks))
        sections.append(
            "Отвечай ТОЛЬКО на основе извлечённых фрагментов. "
            'Если информации нет — скажи "уточню у мастера".'
        )
    else:
        sections.append(
            "Если вопрос требует фактической информации (часы, цены, адрес, "
            "противопоказания), вызови инструмент search_knowledge_base. "
            'Если ответа не нашлось — скажи "уточню у мастера".'
        )

    sections.append(f"Ответ не длиннее {_MAX_ANSWER_CHARS} символов.")
    return "\n\n".join(sections)


def _format_chunk_block(chunks: list[dict[str, Any]]) -> str:
    """Format chunks for the prompt body.

    Each chunk gets a numeric label so the LLM can reference back
    (Sprint 8 may surface citations to the user; Sprint 7 just keeps
    the structure clean).
    """
    lines = ["ИЗВЛЕЧЁННЫЕ ИЗ БАЗЫ ЗНАНИЙ:"]
    for idx, chunk in enumerate(chunks, start=1):
        text = chunk.get("text", "").strip()
        if not text:
            continue
        lines.append(f"[{idx}] {text}")
    return "\n".join(lines)
