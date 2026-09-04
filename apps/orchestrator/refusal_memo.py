"""What this conversation has already been told does not exist (DRF-1474).

The live pilot, 04.09, 12:12 — three turns, eight seconds apart:

    чел  Маникюр
    бот  «маникюр» в городе Пенза — такого у наших мастеров сейчас нет.
    чел  Маникюр
    бот  Помогу найти мастера по маникюру! Уточните, в каком городе вы
         находитесь?
    чел  пенза
    бот  «маникюр» в городе Пенза — такого у наших мастеров сейчас нет.

Both refusals are true: the contour has zero nail services and zero masters
who perform one. The sentence between them is the defect — it promises to
find the thing just refused, and asks back the city the refusal itself had
just named. The person answered it, and walked into the same wall.

### Why the middle turn could not know

The two sentences come from two paths that never meet. The refusal is
deterministic (``apps.orchestrator.discovery.render_no_match``, reached from
the concierge's zero-result render); the promise is the model writing prose on
a turn where it chose no tool at all. The worker log of that minute shows it
plainly — ``show_masters count=0 city='Пенза' spec='маникюр'`` on the first
turn, then ``action=text`` on the second, no ``show_masters`` anywhere in it.

Nothing carried the fact ACROSS. The conversation transcript did — the model
could read its own refusal two messages up — and that turned out not to be
enough: a fact stated in prose among a dozen other messages is a fact the
model may re-litigate, and it did.

So this module writes the fact down as a fact: a small per-conversation ledger
of the searches the CATALOG answered with nobody. It is written where the
zero is measured and read in two places:

* the system prompt, as an instruction the model cannot mistake for
  conversational colour (:func:`render_refusal_block`);
* a deterministic short-circuit for the exact repeat
  (``apps.orchestrator.concierge._repeats_a_refusal`` reads it), because a prompt is a request and the guarantee has
  to hold without one — the same rule DRF-1312 wrote down when it moved the
  «маникюра нет» sentence out of the prompt and into a renderer.

### Storage

``Conversation.skill_state``, exactly like ``apps.orchestrator.
time_preference`` — same field, disjoint key, same best-effort contract: a
failure to write must never cost the person their turn, because the worst case
of losing this is the behaviour that already shipped.

TTL is thirty minutes, not the ten of a booking preference: «we have no nail
masters in Пенза» stays true far longer than «завтра» does, and the cost of it
going stale early is one repeated refusal, while the cost of it going stale
late is a refusal the catalog has since outgrown. Thirty minutes is a
conversation, not a day.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

STATE_KEY = "no_match"

# See the module docstring: long enough to span one conversation, short enough
# that a master joining the marketplace is never denied for a whole session.
STATE_TTL_SECONDS = 1800

# The ledger is a hint, not a history. Five is more distinct refusals than a
# real conversation produces, and a bounded list keeps ``skill_state`` from
# growing without limit on a session that types nonsense at the catalog.
_MAX_ENTRIES = 5

# Same ceiling the refusal renderer echoes a query back at
# (``_MAX_ECHOED_QUERY_CHARS``): what is stored is what may be quoted.
_MAX_STORED_CHARS = 60


@dataclass(frozen=True)
class RefusedQuery:
    """One (service, city) pair the catalog answered with zero masters.

    Both halves are stored as the user-facing spelling — this is what gets
    quoted back — and compared case-folded. ``city`` is "" when the search was
    nationwide; that is a different claim from a city-scoped zero and the two
    must not collapse.
    """

    specialization: str
    city: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.specialization.casefold(), self.city.casefold())


def _clean(value: Any) -> str:
    return str(value or "").strip()[:_MAX_STORED_CHARS]


def remember_refusal(conversation: Any, *, specialization: Any, city: Any = None) -> None:
    """Record that ``(specialization, city)`` matched nobody. Best-effort.

    A refusal with no service named is NOT recorded: «в Пензе никого нет» is
    about the city, and there is no query to recognise a repeat of.
    """
    service = _clean(specialization)
    if conversation is None or not service:
        return
    entry = RefusedQuery(specialization=service, city=_clean(city))
    try:
        from django.utils import timezone as dj_timezone

        state = dict(getattr(conversation, "skill_state", None) or {})
        stored = [row for row in state.get(STATE_KEY) or [] if isinstance(row, dict)]
        # Re-refusing the same thing refreshes it rather than appending — the
        # ledger answers «has this been refused», not «how often».
        stored = [
            row
            for row in stored
            if (
                _clean(row.get("specialization")).casefold(),
                _clean(row.get("city")).casefold(),
            )
            != entry.key
        ]
        stored.append(
            {
                "specialization": entry.specialization,
                "city": entry.city,
                "at": dj_timezone.now().isoformat(),
            }
        )
        state[STATE_KEY] = stored[-_MAX_ENTRIES:]
        conversation.skill_state = state
        conversation.save(update_fields=["skill_state"])
    except Exception:  # noqa: BLE001 — never break a turn over a hint
        logger.exception("refusal_memo.save_failed")


def recall_refusals(conversation: Any) -> list[RefusedQuery]:
    """Fresh refusals for this conversation, oldest first. Never raises."""
    if conversation is None:
        return []
    try:
        from django.utils import timezone as dj_timezone

        raw = (getattr(conversation, "skill_state", None) or {}).get(STATE_KEY)
        if not isinstance(raw, list):
            return []
        now = dj_timezone.now()
        out: list[RefusedQuery] = []
        for row in raw[-_MAX_ENTRIES:]:
            if not isinstance(row, dict):
                continue
            service = _clean(row.get("specialization"))
            if not service:
                continue
            stamped = row.get("at")
            if stamped:
                try:
                    age = (now - datetime.fromisoformat(str(stamped))).total_seconds()
                except (TypeError, ValueError):
                    continue
                # A negative age is a clock that moved, not a fresh entry.
                if age > STATE_TTL_SECONDS or age < -STATE_TTL_SECONDS:
                    continue
            out.append(RefusedQuery(specialization=service, city=_clean(row.get("city"))))
        return out
    except Exception:  # noqa: BLE001
        logger.exception("refusal_memo.load_failed")
        return []


def render_refusal_block(conversation: Any) -> str:
    """The system-prompt paragraph stating what this conversation was told.

    Written as verified fact plus two prohibitions, because the two things the
    live turn did wrong were exactly re-opening the fact («помогу найти») and
    asking back a datum the refusal had supplied («в каком городе?»).
    """
    entries = recall_refusals(conversation)
    if not entries:
        return ""
    lines = ["Уже проверено по каталогу в этом разговоре (это факты, не пересматривай их):"]
    for entry in entries:
        where = f" в городе {entry.city}" if entry.city else ""
        lines.append(f"- «{entry.specialization}»{where} — нет ни одного мастера.")
    lines.append(
        "Не обещай найти мастера по такой услуге и не предлагай её. "
        "Не спрашивай город, который выше уже назван — он известен. "
        "Если клиент просит то же самое ещё раз, скажи прямо, что этого нет, "
        "и назови, что есть, отдельно оговорив, что это другая услуга."
    )
    return "\n".join(lines)
