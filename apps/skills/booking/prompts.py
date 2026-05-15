"""Booking-skill prompt template (DRF-839 / Phase 1 / B3).

Mirrors :mod:`apps.skills.faq.prompts` (Decision 15 / F3 — F3's argument
for a focused per-skill template applies here too).

The system prompt:

* Speaks the brand voice via :class:`BrandVoiceConfig`.
* Tells the model which 4 tools it has and when each fires.
* Forbids ID hallucination: ``master_id`` / ``service_id`` MUST come
  from a prior tool call (skill-side validators back this up — the
  prompt is the first line of defence).
* Lists the candidate masters / slots when present so the model has
  the grounded options visible at decision time.

### Why a separate dataclass instead of reusing the FAQ one

:class:`apps.skills.faq.prompts.BrandVoiceConfig` matches the field
set this template needs verbatim (persona / tone / forbidden). Sprint
8 PromptRegistry consolidation will likely fold both onto a single
platform-owned dataclass; until then mirroring the shape keeps the
adapter logic in the skill simple. We DUPLICATE the class instead of
importing across skills so each skill stays independent (Phase 1's
goal — skills are the unit of independent porting).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class BrandVoiceConfig:
    """Booking prompt brand-voice inputs (mirror of FAQ's shape)."""

    persona: str
    tone: str = ""
    forbidden: tuple[str, ...] = ()


# Sprint 4 PromptRegistry slug.
PROMPT_REGISTRY_KEY = "skill.booking.tool_use"


# Booking replies are short by nature — confirmations, slot lists,
# master cards. 600 chars is plenty.
_MAX_ANSWER_CHARS = 600


def build_booking_prompt(
    *,
    brand_voice: BrandVoiceConfig,
    query: str,
    candidate_masters: list[dict[str, Any]] | None = None,
    available_slots: list[dict[str, Any]] | None = None,
    confirmation: dict[str, Any] | None = None,
    user_bookings: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Build a ChatML messages list for one booking-skill LLM call.

    Args:
      brand_voice: persona / tone / forbidden trio.
      query: the actual user message text.
      candidate_masters: when set, the list the LLM picked from a
                         prior :func:`show_masters` call. Spliced into
                         the system prompt so the second LLM call sees
                         what was offered.
      available_slots: same idea for :func:`show_slots`.
      confirmation: when set, payload from a successful
                    :func:`confirm_booking` call — the model phrases
                    the receipt message.
      user_bookings: when set, the list from :func:`show_my_bookings`.

    Returns:
      ChatML messages list — pass straight to
      :meth:`apps.llm.protocol.LLMProvider.complete`.
    """
    system_text = _render_system_prompt(
        brand_voice=brand_voice,
        candidate_masters=candidate_masters,
        available_slots=available_slots,
        confirmation=confirmation,
        user_bookings=user_bookings,
    )
    return [
        {"role": "system", "content": system_text},
        {"role": "user", "content": query},
    ]


def _render_system_prompt(
    *,
    brand_voice: BrandVoiceConfig,
    candidate_masters: list[dict[str, Any]] | None,
    available_slots: list[dict[str, Any]] | None,
    confirmation: dict[str, Any] | None,
    user_bookings: list[dict[str, Any]] | None,
) -> str:
    sections: list[str] = [f"Ты — {brand_voice.persona}."]

    if getattr(brand_voice, "tone", ""):
        sections.append(f"Тон: {brand_voice.tone}.")

    forbidden = tuple(getattr(brand_voice, "forbidden", ()) or ())
    if forbidden:
        sections.append("Запрещено: " + ", ".join(forbidden) + ".")

    sections.append(
        "Ты помогаешь записаться в салон. У тебя 4 инструмента:\n"
        "• show_masters — список мастеров для услуги.\n"
        "• show_slots — свободные слоты для мастера.\n"
        "• confirm_booking — создать запись (только после явного "
        "подтверждения пользователя).\n"
        "• show_my_bookings — показать существующие записи."
    )

    sections.append(
        "СТРОГО: master_id и service_id ВСЕГДА берутся из результатов "
        "предыдущих вызовов show_masters / show_slots. Никогда не "
        "выдумывай ID."
    )

    if candidate_masters:
        sections.append(_format_masters_block(candidate_masters))
    if available_slots:
        sections.append(_format_slots_block(available_slots))
    if confirmation:
        sections.append(_format_confirmation_block(confirmation))
    if user_bookings is not None:
        sections.append(_format_bookings_block(user_bookings))

    sections.append(f"Ответ не длиннее {_MAX_ANSWER_CHARS} символов.")
    return "\n\n".join(sections)


def _format_masters_block(masters: list[dict[str, Any]]) -> str:
    lines = ["КАНДИДАТЫ-МАСТЕРА:"]
    for m in masters:
        spec = m.get("specialization", "")
        spec_part = f" — {spec}" if spec else ""
        lines.append(f"[{m.get('id')}] {m.get('name', '')}{spec_part}")
    return "\n".join(lines)


def _format_slots_block(slots: list[dict[str, Any]]) -> str:
    lines = ["СВОБОДНЫЕ СЛОТЫ:"]
    for s in slots:
        dur = s.get("duration_minutes")
        dur_part = f" ({dur} мин)" if dur else ""
        lines.append(f"• {s.get('datetime', '')}{dur_part}")
    return "\n".join(lines)


def _format_confirmation_block(confirmation: dict[str, Any]) -> str:
    return (
        "ЗАПИСЬ СОЗДАНА:\n"
        f"• Мастер: {confirmation.get('master_name', '—')}\n"
        f"• Услуга: {confirmation.get('service_name', '—')}\n"
        f"• Время: {confirmation.get('visit_at', '—')}\n"
        f"• ID записи: {confirmation.get('record_id', '—')}"
    )


def _format_bookings_block(bookings: list[dict[str, Any]]) -> str:
    if not bookings:
        return "ПРЕДСТОЯЩИЕ ЗАПИСИ: нет."
    lines = ["ПРЕДСТОЯЩИЕ ЗАПИСИ:"]
    for b in bookings:
        lines.append(
            f"• {b.get('service_name', '—')} с {b.get('master_name', '—')} "
            f"в {b.get('visit_at', '—')}"
        )
    return "\n".join(lines)
