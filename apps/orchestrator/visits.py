"""Channel adapter for «Мои визиты» and «Записаться ещё» (DRF-1032).

The thin half of OD-IR3. Everything about WHAT is true lives in
``apps.booking.services.records``; everything about HOW it reads in a MAX
chat lives here — Russian wording, date formatting, buttons. The deterministic
detector calls this today; the concierge tool adapter will call the same
capability after the pilot without touching either side.

Naming follows the owner's ruling (H-2): the past is «визиты» — the list
shows only visits that happened — while what is still ahead stays «записи».
The Mini App keeps its own «Записи»/«История» tabs; that is a different
surface and is not touched here.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal

from apps.booking.services.records import (
    DEFAULT_VISIT_LIMIT,
    RepeatResult,
    Visit,
    VisitsResult,
    list_upcoming,
    list_visits,
    prepare_repeat,
)
from apps.bookings.keyboards import CALLBACK_BOOK_PICK_MASTER_PREFIX
from apps.events.services import emit
from apps.events.vocabulary import REPEAT_CHECKED, VISIT_CARD_OPENED, VISITS_LISTED
from apps.orchestrator.discovery import DiscoveryReply


logger = logging.getLogger(__name__)

# Opening a visit card. Flat colon-separated slug, the same shape every other
# booking callback uses.
CALLBACK_VISIT_CARD_PREFIX = "cb:visit:card:"
CALLBACK_VISIT_REPEAT_PREFIX = "cb:visit:repeat:"

# What the global handler matches to route a tap here.
VISIT_CALLBACK_PREFIXES = (CALLBACK_VISIT_CARD_PREFIX, CALLBACK_VISIT_REPEAT_PREFIX)

_MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

# Honest, temporary, and never backfilled from the mirror (§30): when the
# backend is unreachable the customer is told so, not shown yesterday's truth.
_UNAVAILABLE_TEXT = "Не смогла получить ваши записи — попробуйте, пожалуйста, чуть позже."

_EMPTY_TEXT = (
    "У вас пока нет завершённых визитов. "
    "Могу подобрать мастера и записать вас — скажите, что вам нужно."
)


def route_visits(
    *,
    global_bot_user,
    trace_id: str | uuid.UUID | None = None,
) -> DiscoveryReply:
    """Answer «покажи мои записи» / «мои визиты» from the backend.

    One question, one answer: upcoming bookings and past visits come from the
    same source, so the reply cannot contradict itself depending on which word
    the customer used (H-1).
    """
    upcoming = list_upcoming(bot_user=global_bot_user, limit=DEFAULT_VISIT_LIMIT)
    visits = list_visits(bot_user=global_bot_user, limit=DEFAULT_VISIT_LIMIT)

    emit(
        VISITS_LISTED,
        payload={
            "bot_user_id": str(global_bot_user.id),
            "upcoming_status": upcoming.status,
            "visits_status": visits.status,
            "upcoming_count": len(upcoming.visits),
            "visits_count": len(visits.visits),
        },
    )

    # A single failing half is still a failure: a list that silently drops
    # the part we could not read looks complete when it is not.
    if "backend_unavailable" in (upcoming.status, visits.status):
        return DiscoveryReply(text=_UNAVAILABLE_TEXT)

    if not upcoming.visits and not visits.visits:
        return DiscoveryReply(text=_EMPTY_TEXT)

    blocks: list[str] = []
    if upcoming.visits:
        blocks.append(_render_upcoming(upcoming))
    if visits.visits:
        blocks.append(_render_visits(visits))

    return DiscoveryReply(
        text="\n\n".join(blocks),
        action_data=_visit_buttons(visits.visits),
    )


def route_visit_callback(
    *,
    global_bot_user,
    callback_text: str,
    trace_id: str | uuid.UUID | None = None,
) -> DiscoveryReply:
    """Dispatch a ``cb:visit:*`` tap to the card or the repeat check.

    The id is whatever the bot itself put in the button. A forged one buys
    nothing: the backend scopes every read to the resolved subject and
    answers 404 for anyone else's booking, so a bad id ends as "not found",
    never as someone else's visit.
    """
    if callback_text.startswith(CALLBACK_VISIT_REPEAT_PREFIX):
        return route_repeat(
            global_bot_user=global_bot_user,
            appointment_id=callback_text[len(CALLBACK_VISIT_REPEAT_PREFIX) :].strip(),
            trace_id=trace_id,
        )
    return route_visit_card(
        global_bot_user=global_bot_user,
        appointment_id=callback_text[len(CALLBACK_VISIT_CARD_PREFIX) :].strip(),
        trace_id=trace_id,
    )


def route_visit_card(
    *,
    global_bot_user,
    appointment_id: str,
    trace_id: str | uuid.UUID | None = None,
) -> DiscoveryReply:
    """Open one visit — service, master, date, what it cost then."""
    from apps.booking.services.records import get_visit

    visit = get_visit(bot_user=global_bot_user, appointment_id=appointment_id)
    emit(
        VISIT_CARD_OPENED,
        payload={"bot_user_id": str(global_bot_user.id), "found": visit is not None},
    )
    if visit is None:
        return DiscoveryReply(text=_UNAVAILABLE_TEXT)

    lines = [
        f"{visit.service_name or 'Визит'} — {_format_when(visit.start_at)}",
    ]
    if visit.master_name:
        lines.append(f"Мастер: {visit.master_name}")
    if visit.price is not None:
        lines.append(f"Стоил: {_format_money(visit.price)}")

    return DiscoveryReply(
        text="\n".join(lines),
        action_data=_repeat_button(visit),
    )


def route_repeat(
    *,
    global_bot_user,
    appointment_id: str,
    trace_id: str | uuid.UUID | None = None,
) -> DiscoveryReply:
    """«Записаться ещё» — check the present, then hand over to booking.

    A repeat is an intent, not a replay (OD-H4): the historical pair is
    re-validated against current state, and when something is gone the
    customer gets a way forward instead of an error slug.
    """
    result = prepare_repeat(bot_user=global_bot_user, appointment_id=appointment_id)
    emit(
        REPEAT_CHECKED,
        payload={"bot_user_id": str(global_bot_user.id), "status": result.status},
    )

    if result.status == "ok" and result.entry is not None:
        return DiscoveryReply(
            text=_repeat_intro(result),
            action_data={
                "buttons": [
                    {
                        "label": "Выбрать время",
                        "callback": (
                            f"{CALLBACK_BOOK_PICK_MASTER_PREFIX}"
                            f"{result.entry.specialist_id}:{result.entry.service_id}"
                        ),
                    }
                ]
            },
        )

    return DiscoveryReply(text=_repeat_refusal_text(result))


# ── presentation ────────────────────────────────────────────────────────────


def _render_upcoming(result: VisitsResult) -> str:
    lines = ["Ваши предстоящие записи:"]
    lines += [f"• {_visit_line(v)}" for v in result.visits]
    return "\n".join(lines)


def _render_visits(result: VisitsResult) -> str:
    lines = ["Ваши последние визиты:"]
    lines += [f"• {_visit_line(v)}" for v in result.visits]
    return "\n".join(lines)


def _visit_line(visit: Visit) -> str:
    """One line per visit, joined by «·» rather than by prepositions.

    Deliberately no «у {мастер}»: the name arrives in the nominative case and
    Russian would need the genitive («у Инны», not «у Инна»). Declension is
    not something to guess at on someone's name — the separator says the same
    thing and cannot be wrong.
    """
    parts = [visit.service_name or "услуга"]
    if visit.master_name:
        parts.append(visit.master_name)
    when = _format_when(visit.start_at)
    if when:
        parts.append(when)
    line = " · ".join(parts)
    if visit.price is not None:
        line = f"{line} — {_format_money(visit.price)}"
    return line


def _visit_buttons(visits: tuple[Visit, ...]) -> dict | None:
    """One «Подробнее» per visit, capped by the list itself.

    Canonical envelope so the same reply also renders in Telegram.
    """
    if not visits:
        return None
    buttons = [
        {
            "label": f"Подробнее: {v.service_name or 'визит'}",
            "callback": f"{CALLBACK_VISIT_CARD_PREFIX}{v.appointment_id}",
        }
        for v in visits
    ]
    return {"attachments": [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]}


def _repeat_button(visit: Visit) -> dict:
    return {
        "attachments": [
            {
                "type": "inline_keyboard",
                "payload": {
                    "buttons": [
                        {
                            "label": "Записаться ещё",
                            "callback": f"cb:visit:repeat:{visit.appointment_id}",
                        }
                    ]
                },
            }
        ]
    }


def _repeat_intro(result: RepeatResult) -> str:
    what = result.service_name or "ту же услугу"
    # Same reason as ``_visit_line``: «к {имя}» needs the dative and the name
    # comes in the nominative, so the master is named on its own line.
    text = f"Повторим: {what}."
    if result.master_name:
        text += f"\nМастер: {result.master_name}."
    text += "\nКогда вам удобно?"
    # Never let the old number pass for the current one (OD-H4). Showing both
    # is the honest form when they differ.
    if result.price_changed:
        text += (
            f"\nВ прошлый раз — {_format_money(result.historical_price)}, "
            f"сейчас — {_format_money(result.current_price)}."
        )
    return text


def _repeat_refusal_text(result: RepeatResult) -> str:
    """A человеческий ответ for every refusal — never a technical slug."""
    master = result.master_name or "Мастер"
    if result.status == "master_unavailable":
        return (
            f"{master} сейчас не принимает. Могу подобрать другого мастера "
            "на эту же услугу — поискать?"
        )
    if result.status == "service_unavailable":
        return "Эту услугу сейчас не оказывают. Могу подобрать похожую — рассказать, что есть?"
    if result.status == "link_unavailable":
        return f"{master} больше не делает эту услугу. Поискать другого мастера на неё?"
    if result.status == "prefill_unusable":
        return (
            "Не смогла разобрать эту запись, чтобы повторить её. "
            "Давайте подберём заново — скажите, что вам нужно."
        )
    return _UNAVAILABLE_TEXT


def _format_when(raw: str) -> str:
    """ISO timestamp → «12 августа, 09:30». Empty string when unparseable."""
    if not raw:
        return ""
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        logger.warning("visits.unparseable_datetime raw=%r", raw)
        return ""
    return f"{moment.day} {_MONTHS_GENITIVE[moment.month - 1]}, {moment:%H:%M}"


def _format_money(amount: Decimal | None) -> str:
    """«2500 ₽» for round sums, «2500.50 ₽» when there really are kopecks.

    ``normalize()`` alone would render 2500.50 as "2500.5" — a price with a
    stray single decimal reads like a bug to the person paying it.
    """
    if amount is None:
        return ""
    if amount == amount.to_integral_value():
        return f"{int(amount)} ₽"
    return f"{amount.quantize(Decimal('0.01'))} ₽"
