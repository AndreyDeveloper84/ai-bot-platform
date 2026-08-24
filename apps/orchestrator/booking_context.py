"""What the global bot is in the middle of, so a TYPED turn can continue it.

DRF-968 / DRF-1101 — one gap, two tickets.

Since DRF-988 the global bot routes a post-handoff ``cb:book:*`` TAP back
into tenant T's booking pipeline. Nothing routes a typed turn. The global
reply ladder (``apps/channels/max/handler.py``) has no idea a booking is
running: free text falls through to the concierge, the concierge has no
booking tool (``CONCIERGE_TOOL_SPECS``), and the best it can do is call
``show_masters`` again — which redraws the master list the person already
walked past.

That is the whole of both symptoms:

* **DRF-968.** The bot asks «напишите название услуги», the person types
  exactly that name, and gets the master list back. The escape hatch the
  ask-the-service reply offers («если нужной нет в списке, напишите её
  название» — :mod:`apps.orchestrator.handoff`) leads nowhere.

* **DRF-1101.** «Воронка сбрасывается в начало после корректной даты», and
  the intermittency the ticket could not explain: TAPS work (the owner's
  clean run on 15.08 was all chips), TYPING resets (14.08, a date typed by
  hand). Nothing about the date is special — any typed turn mid-booking
  does it.

This module holds the missing memory: what the person is in the middle of,
parked on the GLOBAL conversation the same way the time preference is
(:mod:`apps.orchestrator.time_preference`) and read on the next typed turn.

**It stores no commercial data** — public catalog ids plus the native ids
the booking callback grammar already carries in plain text on the wire.
Nothing here decides whether a slot is free; the schedule read and
``create``'s 409 keep that job (``docs/OD_SALON_P0_CONTRACT.md``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

STATE_KEY = "global_booking"

#: Waiting for the person to name a SERVICE for an already-chosen master —
#: the state the ask-the-service reply leaves behind (DRF-968).
AWAITING_SERVICE = "service"

#: Master + service are settled and the booking flow is asking WHEN — the
#: state a successful handoff dispatch leaves behind (DRF-1101).
AWAITING_SCHEDULE = "schedule"

_AWAITING = frozenset({AWAITING_SERVICE, AWAITING_SCHEDULE})

# Longer than the ten minutes ``time_preference`` gives a «завтра вечером»,
# and for the opposite reason. A stale preference silently books the wrong
# DAY, so it must expire fast; a stale booking context can only ever offer
# the person the master they themselves chose, and the cost of expiring it
# early is the defect this module exists to remove. Fifteen minutes is a
# real booking conversation with pauses in it, and still far short of «I
# came back in the evening and the bot was stuck on this morning».
STATE_TTL_SECONDS = 900


@dataclass(frozen=True)
class BookingContext:
    """The booking the global chat is in the middle of.

    ``tenant_id`` / ``master_id`` are the PUBLIC ids from the tapped card —
    the same pair the handoff callback carries. ``native_master_id`` /
    ``native_service_id`` are the ids tenant T's booking grammar speaks
    (canonical Ayla UUIDs under ``BOOKING_VIA_AYLA_REST``); they are stored
    rather than re-derived so a continuation re-enters with a payload
    byte-identical to the one the chips carry.

    ``query_ref`` is the DRF-1324 request ref, kept so a service menu shown
    on a continuation is narrowed by the same request that surfaced the
    master.
    """

    awaiting: str
    tenant_id: str
    master_id: str
    master_name: str = ""
    native_master_id: str = ""
    native_service_id: str = ""
    query_ref: str = ""

    def as_state(self) -> dict[str, Any]:
        return {
            "awaiting": self.awaiting,
            "tenant_id": self.tenant_id,
            "master_id": self.master_id,
            "master_name": self.master_name,
            "native_master_id": self.native_master_id,
            "native_service_id": self.native_service_id,
            "query_ref": self.query_ref,
        }

    @classmethod
    def from_state(cls, raw: Any) -> BookingContext | None:
        """Rebuild, or ``None`` for anything this module did not write.

        Validated field by field rather than splatted: ``skill_state`` is a
        JSON column several layers write into, and a continuation ACTS on
        what it reads here — an unchecked ``awaiting`` would let a corrupt
        row decide which branch runs.
        """
        if not isinstance(raw, dict):
            return None
        awaiting = raw.get("awaiting")
        tenant_id = raw.get("tenant_id")
        master_id = raw.get("master_id")
        if awaiting not in _AWAITING:
            return None
        if not isinstance(tenant_id, str) or not isinstance(master_id, str):
            return None
        if not tenant_id or not master_id:
            return None
        return cls(
            awaiting=awaiting,
            tenant_id=tenant_id,
            master_id=master_id,
            master_name=str(raw.get("master_name") or ""),
            native_master_id=str(raw.get("native_master_id") or ""),
            native_service_id=str(raw.get("native_service_id") or ""),
            query_ref=str(raw.get("query_ref") or ""),
        )


def save_booking_context(conversation: Any, ctx: BookingContext | None) -> None:
    """Park (or clear) the context. Best-effort by contract.

    Losing it costs exactly what the pilot has today — a typed turn that
    restarts the funnel — so it must never cost the turn itself.
    """
    if conversation is None:
        return
    try:
        from django.utils import timezone as dj_timezone

        state = dict(getattr(conversation, "skill_state", None) or {})
        if ctx is None:
            if STATE_KEY not in state:
                return
            state.pop(STATE_KEY, None)
        else:
            payload = ctx.as_state()
            payload["at"] = dj_timezone.now().isoformat()
            state[STATE_KEY] = payload
        conversation.skill_state = state
        conversation.save(update_fields=["skill_state"])
    except Exception:  # noqa: BLE001 — never break a turn over a hint
        logger.exception("booking_context.save_failed")


def touch_booking_context(conversation: Any) -> None:
    """Restamp an existing context without changing it.

    Every chip tap is a turn that proves the person is still in the funnel.
    Without this the TTL would measure «time since the handoff» rather than
    «time since the last sign of life», and somebody comparing days one tap
    at a time would fall out of it mid-booking.
    """
    ctx = load_booking_context(conversation)
    if ctx is not None:
        save_booking_context(conversation, ctx)


def load_booking_context(conversation: Any) -> BookingContext | None:
    """Read back a FRESH context, or ``None`` (missing, stale, corrupt).

    A clock that moved backwards (``age < 0``) counts as staleness for the
    same reason it does in ``time_preference``: a negative age is not
    evidence of freshness, it is evidence that the stamp cannot be trusted.
    """
    if conversation is None:
        return None
    try:
        from django.utils import timezone as dj_timezone

        raw = (getattr(conversation, "skill_state", None) or {}).get(STATE_KEY)
        if not isinstance(raw, dict):
            return None
        stamped = raw.get("at")
        if stamped:
            try:
                age = (dj_timezone.now() - datetime.fromisoformat(str(stamped))).total_seconds()
            except (TypeError, ValueError):
                return None
            if age > STATE_TTL_SECONDS or age < -STATE_TTL_SECONDS:
                return None
        return BookingContext.from_state(raw)
    except Exception:  # noqa: BLE001
        logger.exception("booking_context.load_failed")
        return None
