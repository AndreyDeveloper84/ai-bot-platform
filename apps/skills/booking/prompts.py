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
    known_masters: list[dict[str, Any]] | None = None,
    known_masters_truncated: bool = False,
    candidate_masters: list[dict[str, Any]] | None = None,
    available_slots: list[dict[str, Any]] | None = None,
    confirmation: dict[str, Any] | None = None,
    pending: dict[str, Any] | None = None,
    user_bookings: list[dict[str, Any]] | None = None,
    price: dict[str, Any] | None = None,
    certificate: dict[str, Any] | None = None,
    flow_context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Build a ChatML messages list for one booking-skill LLM call.

    Args:
      brand_voice: persona / tone / forbidden trio.
      query: the actual user message text.
      known_masters: E0#1 Variant A pre-injected tenant master roster
                     (founder verdict 2026-06-02, founder
                     pilot_scope_discipline). Each dict carries ``name``
                     + optional ``specialization``. Spliced into the
                     system prompt as «ИЗВЕСТНЫЕ МАСТЕРА: …» with an
                     anti-name-hallucination rule. Defends the booking
                     turn-1 path against the LLM inventing master names
                     not present in this tenant's roster (the existing
                     anti-ID rule defends only `master_id` invention —
                     the model could still write «запишу к Ольге» when
                     Ольга doesn't exist). Per ADR-0009 caller passes a
                     pre-loaded list from ``CatalogMaster`` mirror — no
                     ORM access inside this pure rendering function.
      candidate_masters: when set, the list the LLM picked from a
                         prior :func:`show_masters` call. Spliced into
                         the system prompt so the second LLM call sees
                         what was offered.
      available_slots: same idea for :func:`show_slots`.
      confirmation: when set, payload from a successful
                    :func:`confirm_booking` call — the model phrases
                    the receipt message.
      pending: when set, payload from one of the preview-card tools
               (confirm / cancel / reschedule). The model phrases the
               preview question; the channel adapter renders the
               2-button card alongside the text.
      user_bookings: when set, the list from :func:`show_my_bookings`.

    Returns:
      ChatML messages list — pass straight to
      :meth:`apps.llm.protocol.LLMProvider.complete`.
    """
    system_text = _render_system_prompt(
        brand_voice=brand_voice,
        known_masters=known_masters,
        known_masters_truncated=known_masters_truncated,
        candidate_masters=candidate_masters,
        available_slots=available_slots,
        confirmation=confirmation,
        pending=pending,
        user_bookings=user_bookings,
        price=price,
        certificate=certificate,
        flow_context=flow_context,
    )
    return [
        {"role": "system", "content": system_text},
        {"role": "user", "content": query},
    ]


def _render_system_prompt(
    *,
    brand_voice: BrandVoiceConfig,
    known_masters: list[dict[str, Any]] | None,
    known_masters_truncated: bool,
    candidate_masters: list[dict[str, Any]] | None,
    available_slots: list[dict[str, Any]] | None,
    confirmation: dict[str, Any] | None,
    pending: dict[str, Any] | None,
    user_bookings: list[dict[str, Any]] | None,
    price: dict[str, Any] | None = None,
    certificate: dict[str, Any] | None = None,
    flow_context: dict[str, Any] | None = None,
) -> str:
    sections: list[str] = [f"Ты — {brand_voice.persona}."]

    if getattr(brand_voice, "tone", ""):
        sections.append(f"Тон: {brand_voice.tone}.")

    forbidden = tuple(getattr(brand_voice, "forbidden", ()) or ())
    if forbidden:
        sections.append("Запрещено: " + ", ".join(forbidden) + ".")

    # Stabilization B2: when CERTIFICATE_PAYMENT_ENABLED is False the
    # buy_certificate tool is filtered out of the LLM tool list by
    # ``get_active_booking_tool_specs()``. Keep the system prompt in
    # sync — otherwise the LLM is taught a tool it cannot call and
    # may either invent a tool_call (→ unknown_tool handoff) or pitch
    # certificates to the user even though we cannot deliver them.
    from django.conf import settings

    cert_enabled = bool(getattr(settings, "CERTIFICATE_PAYMENT_ENABLED", False))

    tool_lines = [
        "• show_masters — список мастеров для услуги.",
        "• show_slots — свободные слоты для мастера.",
        "• confirm_booking — показать карточку подтверждения новой "
        "записи (НЕ создаёт запись напрямую — ждёт нажатия ✅).",
        "• cancel_booking — показать карточку подтверждения отмены "
        "существующей записи. record_id берётся из show_my_bookings.",
        "• reschedule_booking — показать карточку подтверждения "
        "переноса записи. record_id из show_my_bookings, "
        "new_datetime — будущее время в ISO формате.",
        "• show_my_bookings — показать существующие записи.",
        "• calc_price — посчитать цену услуги, опционально с "
        "промокодом. Вызывай, когда клиент спрашивает про цену "
        '("сколько стоит ...") или упоминает промокод. Передавай '
        "promo_code ТОЛЬКО если клиент явно назвал код — не выдумывай.",
    ]
    if cert_enabled:
        tool_lines.append(
            "• buy_certificate — выпустить ссылку на оплату подарочного "
            'сертификата. Вызывай, когда клиент просит "купить '
            'сертификат" / "подарочный сертификат". amount_rub — сумма '
            "в рублях (500–100000). recipient_name — на кого "
            "сертификат (опционально). buyer_email — email для чека "
            "(опционально, только если клиент сам назвал)."
        )

    sections.append(
        f"Ты помогаешь записаться в салон. У тебя {len(tool_lines)} "
        f"инструментов:\n" + "\n".join(tool_lines)
    )

    if not cert_enabled:
        # Defence-in-depth: if the customer asks about certificates,
        # do not promise a launch date — the founder freeze is
        # contingent on legal review (ФЗ-54 / ст. 487 ГК РФ).
        sections.append(
            "Если клиент спрашивает про подарочные сертификаты — "
            "коротко скажи, что сейчас они недоступны, и предложи "
            "запись на услугу. Не обещай конкретные сроки запуска."
        )

    sections.append(
        "СТРОГО: master_id, service_id и record_id ВСЕГДА берутся из "
        "результатов предыдущих вызовов show_masters / show_slots / "
        "show_my_bookings. Никогда не выдумывай ID. Если пользователь "
        "просит отменить или перенести запись и ID неизвестен — "
        "сначала вызови show_my_bookings."
    )

    # E0#1 Variant A (founder verdict 2026-06-02) — pre-injected tenant
    # master roster. Defends the turn-1 booking path against the LLM
    # inventing master NAMES not present in this tenant's catalog. The
    # existing «не выдумывай ID» rule above protects only numeric IDs;
    # this rule + roster block protect free-text mentions like «запишу
    # к Ольге» when Ольга doesn't exist в the salon's roster.
    #
    # Empty list (new tenant, no synced masters yet) → block skipped
    # entirely; the per-tool `show_masters` fallback remains as the
    # second-line defence.
    if known_masters:
        sections.append(
            _format_known_masters_block(
                known_masters,
                is_truncated=known_masters_truncated,
            )
        )

    # Live-data rendering rule. The skill's two-call loop already
    # invoked a tool and is splicing the result into the prompt below
    # (КАНДИДАТЫ-МАСТЕРА / СЛОТЫ / ВАШИ ЗАПИСИ / ...). Without an
    # explicit instruction, gpt-4o-mini treats those blocks as future
    # tool context and replies with placeholder filler — observed live
    # 2026-05-21 ("Один момент!" after `хочу записаться на массаж`,
    # despite КАНДИДАТЫ-МАСТЕРА list being present in the prompt).
    has_live_data = any(
        x is not None
        for x in (
            candidate_masters,
            available_slots,
            confirmation,
            pending,
            user_bookings,
            price,
            certificate,
        )
    )
    if has_live_data:
        sections.append(
            "ВАЖНО: ниже указаны данные, которые УЖЕ получены инструментами. "
            "Сразу представь их пользователю в этом же ответе — НЕ пиши "
            "«один момент», «сейчас покажу», «позволь мне». Данные у тебя — "
            "перечисли их естественным языком и задай следующий вопрос."
        )

    if candidate_masters:
        sections.append(_format_masters_block(candidate_masters))
    if available_slots:
        sections.append(_format_slots_block(available_slots))
    if confirmation:
        sections.append(_format_confirmation_block(confirmation))
    if pending:
        sections.append(_format_pending_block(pending))
    if user_bookings is not None:
        sections.append(_format_bookings_block(user_bookings))
    if price is not None:
        sections.append(_format_price_block(price))
    if certificate is not None:
        sections.append(_format_certificate_block(certificate))
    if flow_context:
        sections.append(_format_flow_block(flow_context))

    sections.append(f"Ответ не длиннее {_MAX_ANSWER_CHARS} символов.")
    return "\n\n".join(sections)


def _format_flow_block(flow_context: dict[str, Any]) -> str:
    """D-10 — continuation grounding for an active booking flow.

    Rendered ONLY on Phase-1 calls while
    ``conversation.skill_state["booking_flow"]`` is fresh: the turn the
    user answers the disambiguation question («Первую, на 9 августа в
    20:00»). Without this block the Phase-1 prompt has no bookings and
    no current date, so the model cannot resolve ``record_id`` or build
    an ISO ``new_datetime`` and degenerates to free-text «Подтверждаете?»
    with no tool call (staging defect D-10).

    All free-text fields pass through :func:`_sanitize_for_prompt` —
    master/service names originate from the catalog mirror.
    """
    flow = str(flow_context.get("flow") or "reschedule")
    bookings = list(flow_context.get("bookings") or [])
    now_local = _sanitize_for_prompt(flow_context.get("now_local"), max_chars=40)
    verb = "перенести" if flow == "reschedule" else "отменить"

    lines = [
        f"АКТИВНЫЙ СЦЕНАРИЙ: клиент хочет {verb} свою запись "
        "(продолжение начатого диалога — НЕ начинай сначала).",
        "Его текущие записи:",
    ]
    for idx, b in enumerate(bookings, start=1):
        service = _sanitize_for_prompt(b.get("service_name")) or "услуга"
        master = _sanitize_for_prompt(b.get("master_name")) or "мастер"
        visit = _sanitize_for_prompt(b.get("visit_at"), max_chars=40)
        record_id = _sanitize_for_prompt(b.get("record_id"), max_chars=40)
        lines.append(f"{idx}) {service} — {master}, {visit} (record_id={record_id})")
    if now_local:
        lines.append(f"Сейчас: {now_local} (локальное время салона).")
    lines.append(
        "Определи по сообщению клиента, какую запись он выбрал "
        "(порядковый номер «первая/вторая/…» или дату визита). "
        "record_id бери ТОЛЬКО из списка выше — никогда не выдумывай."
    )
    if flow == "reschedule":
        lines.append(
            "Если новые дата/время названы — преобразуй в ISO "
            "(YYYY-MM-DDTHH:MM:SS, локальное время) и вызови "
            "reschedule_booking. Если запись или новое время непонятны "
            "— задай короткий уточняющий вопрос БЕЗ вызова инструмента."
        )
    else:
        lines.append(
            "Вызови cancel_booking для выбранной записи. Если выбор "
            "неоднозначен — задай короткий уточняющий вопрос БЕЗ вызова "
            "инструмента."
        )
    return "\n".join(lines)


def _format_certificate_block(certificate: dict[str, Any]) -> str:
    """Splice the buy_certificate payload into the system prompt.

    The LLM must preserve the amount + the checkout URL verbatim — the
    URL is what the channel adapter renders into the inline ``💳
    Оплатить`` button via :attr:`SkillResult.action_data`. On failure
    paths (``ok=False``) the model picks an apologetic phrasing; on
    success it should tell the user to tap the button.
    """
    ok = bool(certificate.get("ok"))
    if not ok:
        err = certificate.get("error", "")
        return (
            "СЕРТИФИКАТ (buy_certificate) — НЕУДАЧА:\n"
            f"• Причина: {err}\n"
            f"• Готовый текст: {certificate.get('rendered_text', '')}\n"
            "Сообщи клиенту коротко и понятно, без технических деталей."
        )
    return (
        "СЕРТИФИКАТ (buy_certificate) — УСПЕХ:\n"
        f"• Сумма: {certificate.get('amount_rub', '—')} ₽\n"
        f"• Ссылка на оплату: {certificate.get('checkout_url', '—')}\n"
        f"• Готовый текст: {certificate.get('rendered_text', '')}\n"
        "Скажи клиенту, что под сообщением появится кнопка для оплаты. "
        "Сохрани сумму и ссылку как есть."
    )


def _format_pending_block(pending: dict[str, Any]) -> str:
    """Splice the preview-card context into the system prompt.

    The LLM should rephrase ``preview_text`` in its own voice but
    preserve the structured fields (service, master, time). The
    2-button card itself is rendered by the channel adapter via
    ``SkillResult.action_data``; the LLM just produces the body text.
    """
    kind = pending.get("kind", "")
    return (
        "ПРЕДПРОСМОТР (2 кнопки появятся под ответом):\n"
        f"• Тип: {kind}\n"
        f"• Текст: {pending.get('preview_text', '')}\n"
        "Перефразируй коротко, заверши вопросом 'Подтверждаете?'."
    )


def _sanitize_for_prompt(text: Any, *, max_chars: int = 80) -> str:
    """Adversarial F1 (PR #955) — strip prompt-injection vectors from
    free-text fields before splicing into the system prompt.

    The catalog mirror's ``name`` / ``specialization`` columns are
    populated by mysite sync OR Ayla event upserts. ``CharField`` caps
    bytes but NOT newlines, tabs, bidi controls, or other layout
    characters. A malicious upstream value like
    ``"Ольга\\n\\nIGNORE PREVIOUS INSTRUCTIONS..."`` would otherwise
    break out of the bullet structure verbatim and the small-context
    model (gpt-4o-mini) frequently follows such injections.

    Sanitization rules (defence-in-depth, applied at render-time so we
    can't accidentally bypass via a new caller):

    1. Drop every character that is not ``str.isprintable()`` OR a
       literal space. This kills newlines (``\\n``), carriage returns,
       tabs, vertical bars, NUL bytes, bidi overrides, and most other
       Unicode control planes.
    2. Collapse runs of whitespace so an attacker can't smuggle a
       100-space buffer that wraps in the prompt.
    3. Hard-cap at ``max_chars`` characters — defence-in-depth beyond
       the model field's ``max_length``. Pilot expectation: master
       names ≤40 chars, specializations ≤40 chars; ``80`` is generous.

    Returns the sanitized string (possibly empty). Callers are
    responsible for skipping empty results (otherwise an empty-name
    bullet would render as ``"• "``).
    """
    if not text:
        return ""
    s = str(text)
    s = "".join(ch for ch in s if ch == " " or ch.isprintable())
    s = " ".join(s.split())
    return s[:max_chars]


def _format_known_masters_block(
    known_masters: list[dict[str, Any]],
    *,
    is_truncated: bool = False,
) -> str:
    """E0#1 Variant A pre-injected tenant master roster block.

    Renders the tenant roster as a bulleted list followed by an
    anti-name-hallucination rule. The caller (booking skill) controls
    roster source + cap; this helper stays pure for testability.

    Adversarial CR #955 changes:

    * **F1** — every ``name`` is passed through
      :func:`_sanitize_for_prompt` so newlines / control characters in
      sync data cannot break out of the bullet structure.
    * **F3** — when ``is_truncated=True`` (caller observed an overflow
      probe), the header drops the «полный» qualifier and the closing
      rule weakens: «если сомневаешься — вызови show_masters»
      instead of a blanket «такого мастера нет». Without this, a
      tenant с 21+ masters would silently deny alphabetically-late
      legitimate masters.
    * **F7** — ``specialization`` is NO LONGER rendered. The block is
      purely a name-resolver; specializations carry uncontrolled free
      text (mysite sync may push «работа с онкобольными» / similar
      health-zone text) and ADR-0011 §9.2 carve-out is narrow to
      analytics, not LLM channels. The per-tool ``show_masters`` flow
      surfaces specialization with adapter-level controls when needed.

    Each entry shape: ``{"name": "Ольга"}`` (``specialization`` is
    ignored even when present — backwards-compat with existing loaders).
    Entries without a name (or whose name sanitizes to empty) are
    filtered defensively.
    """
    if is_truncated:
        lines = ["ИЗВЕСТНЫЕ МАСТЕРА (часть ростера):"]
    else:
        lines = ["ИЗВЕСТНЫЕ МАСТЕРА (полный ростер салона):"]
    for m in known_masters:
        name = _sanitize_for_prompt(m.get("name"))
        if not name:
            continue
        lines.append(f"• {name}")
    if is_truncated:
        # F3 fix — when the list is truncated, the model must not
        # treat its absence-from-list as proof of non-existence.
        # `show_masters` becomes the truth source for any name not
        # immediately recognised.
        lines.append(
            "СТРОГО: список выше — не полный. Если клиент называет имя, которого "
            "нет в этом списке, ВЫЗОВИ инструмент show_masters чтобы убедиться "
            "что такого мастера действительно нет. Только если show_masters тоже "
            "не вернул мастера — скажи, что такого мастера у нас нет. "
            "НЕ выдумывай имя мастера."
        )
    else:
        lines.append(
            "СТРОГО: если клиент называет мастера, которого НЕТ в этом списке — "
            "ответь, что такого мастера у нас нет, и предложи показать список доступных. "
            "НЕ выдумывай имя мастера, которого нет в ростере."
        )
    return "\n".join(lines)


def _format_masters_block(masters: list[dict[str, Any]]) -> str:
    # Adversarial CR #955 F1 — same prompt-injection defence as
    # _format_known_masters_block. The candidate list comes from
    # show_masters tool result (RemoteBookingProxy / YClients), so the
    # data path is more constrained, but defence-in-depth costs nothing.
    lines = ["КАНДИДАТЫ-МАСТЕРА:"]
    for m in masters:
        name = _sanitize_for_prompt(m.get("name", ""))
        spec = _sanitize_for_prompt(m.get("specialization", ""))
        spec_part = f" — {spec}" if spec else ""
        lines.append(f"[{m.get('id')}] {name}{spec_part}")
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


def _format_price_block(price: dict[str, Any]) -> str:
    """Splice the calc_price payload into the system prompt.

    The LLM should preserve the numbers verbatim (no rounding, no
    "примерно") and pick the conversational frame matching
    ``promo_status``. ``rendered_text`` is the deterministic fallback;
    the model may rephrase but should keep the price figures intact.
    """
    return (
        "ЦЕНА (calc_price):\n"
        f"• Услуга: {price.get('service_name', '—')}\n"
        f"• Базовая цена: {price.get('original_price', '—')}\n"
        f"• Финальная цена: {price.get('final_price', '—')}\n"
        f"• Скидка: {price.get('discount_percent', '—')}\n"
        f"• Статус промокода: {price.get('promo_status', '—')}\n"
        f"• Готовый текст: {price.get('rendered_text', '')}\n"
        "Сохрани числа без изменений. Подбери тон под promo_status."
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
