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
from zoneinfo import ZoneInfo

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
from apps.orchestrator.discovery import DiscoveryReply, show_salons_button


logger = logging.getLogger(__name__)

# Opening a visit card. Flat colon-separated slug, the same shape every other
# booking callback uses.
CALLBACK_VISIT_CARD_PREFIX = "cb:visit:card:"
CALLBACK_VISIT_REPEAT_PREFIX = "cb:visit:repeat:"

# What the global handler matches to route a tap here.
VISIT_CALLBACK_PREFIXES = (CALLBACK_VISIT_CARD_PREFIX, CALLBACK_VISIT_REPEAT_PREFIX)

# The pilot's timezone. ``TIME_ZONE`` is UTC in this service, so
# ``timezone.localtime`` would keep the bug DRF-1071 reported, and the
# ``me/bookings`` response names the tenant without its zone. Same default
# and same reasoning as ``apps.booking.client_notify.tenant_timezone``: an
# unknown zone degrades to the pilot's real one, never to UTC.
_DISPLAY_TZ = ZoneInfo("Europe/Moscow")

_WEEKDAYS = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)

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

# DRF-1492 — «Могу подобрать мастера и записать вас» named an action and gave
# the reader nothing to press. The offer stands; it is now a chip, and the
# chip is the first rung of a ladder that is tappable to the end (салоны →
# услуги → мастер → запись). Typing still works and is still invited — the
# button is the floor, not the ceiling.
_EMPTY_TEXT = (
    "У вас пока нет завершённых визитов. "
    "Скажите, что вам нужно, — или посмотрите наши салоны, оттуда можно записаться."
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
        distinct_id=str(global_bot_user.id),
        properties={
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
        return DiscoveryReply(text=_EMPTY_TEXT, action_data=_chips([show_salons_button()]))

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
        distinct_id=str(global_bot_user.id),
        properties={"found": visit is not None},
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
        distinct_id=str(global_bot_user.id),
        properties={"status": result.status},
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

    text, buttons = _repeat_refusal(result)
    return DiscoveryReply(text=text, action_data=_chips(buttons))


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


#: Chip labels here are catalog service names, not model output, but MAX
#: truncates a long label at the tail — the same cap the discovery renderer
#: applies to its own option labels.
_MAX_CHIP_LABEL_CHARS = 40


def _chips(buttons: list[dict[str, str]]) -> dict | None:
    """The canonical keyboard envelope, or ``None`` for no buttons.

    ``None``, never an empty ``inline_keyboard``: an attachment with nothing
    in it renders as a broken message rather than as a message without
    buttons (the rule ``apps.orchestrator.discovery._reply_with_chips``
    states, kept identical here).
    """
    if not buttons:
        return None
    return {"attachments": [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]}


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
                            "callback": f"{CALLBACK_VISIT_REPEAT_PREFIX}{visit.appointment_id}",
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


def _repeat_refusal(result: RepeatResult) -> tuple[str, list[dict[str, str]]]:
    """A человеческий ответ for every refusal — never a technical slug, and
    never a question nobody can answer with a tap (DRF-1492).

    Three of these four branches ended in a yes/no question — «поискать?»,
    «рассказать, что есть?» — under a message with no buttons. A question
    whose only answer is a typed «да» is not an offer, it is homework: the
    person has to restate an intent the bot has just demonstrated it holds.

    Two shapes of chip, and which one applies is decided by what this layer
    can actually ground:

    * **the service name**, when the refusal is about the MASTER and the
      service itself is still fine. The callback IS the name — the «tap ==
      typed answer» contract ``_render_ask_clarification`` has shipped on this
      path since DRF-1102 — so the tap re-enters the ordinary turn and comes
      back with the masters who do perform it. Not an id: the id this layer
      holds is Ayla's canonical ``service_id``, and the catalog chips address
      ``CatalogService.pk``, a different key space. Sending one where the
      other is expected would render a chip that answers «услуга не найдена»
      — the dead end with a button on it.
    * **«Показать салоны»** when the service is the thing that went away.
      Suggesting «похожую» would be a claim about a catalog this function has
      not read; the salon list is the honest form of the same offer.
    """
    master = result.master_name or "Мастер"
    service = (result.service_name or "").strip()
    if result.status in {"master_unavailable", "link_unavailable"}:
        gone = (
            f"{master} сейчас не принимает."
            if result.status == "master_unavailable"
            else f"{master} больше не делает эту услугу."
        )
        if service:
            return (
                f"{gone} Нажмите на услугу — покажу, кто ещё её делает.",
                [{"label": service[:_MAX_CHIP_LABEL_CHARS], "callback": service}],
            )
        # No service name to press. The offer is withdrawn from the wording
        # rather than left standing over a button that cannot be built.
        return (
            f"{gone} Посмотрите наши салоны — подберём другого мастера.",
            [show_salons_button()],
        )
    if result.status == "service_unavailable":
        return (
            "Эту услугу сейчас не оказывают. Посмотрите, что есть в наших салонах.",
            [show_salons_button()],
        )
    if result.status == "prefill_unusable":
        return (
            "Не смогла разобрать эту запись, чтобы повторить её. "
            "Скажите, что вам нужно, — или посмотрите наши салоны.",
            [show_salons_button()],
        )
    # backend_unavailable and anything new: an outage is not a menu. There is
    # no action to offer, so none is named — waiting is the whole answer.
    return (_UNAVAILABLE_TEXT, [])


def _format_when(raw: str) -> str:
    """ISO timestamp → «19 августа, среда, 14:00» in the salon's local time.

    The backend serialises ``start_datetime`` straight from the database, so
    the wire carries UTC (``...Z``). Printing it verbatim is the bug DRF-1071
    caught on the pilot: someone booked for 14:00 Moscow time was shown
    11:00 — the one formatting error that makes a person arrive on the wrong
    hour. Converting is therefore not cosmetic.

    The response names the tenant but not its timezone, so the pilot's zone
    is the fallback, exactly as ``client_notify.tenant_timezone`` degrades.
    A naive timestamp is left alone: inventing an offset for it would be the
    same class of guess this fixes.
    """
    if not raw:
        return ""
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        logger.warning("visits.unparseable_datetime raw=%r", raw)
        return ""
    if moment.tzinfo is not None:
        moment = moment.astimezone(_DISPLAY_TZ)
    weekday = _WEEKDAYS[moment.weekday()]
    return f"{moment.day} {_MONTHS_GENITIVE[moment.month - 1]}, {weekday}, {moment:%H:%M}"


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
