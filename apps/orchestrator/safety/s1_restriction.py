"""Durable S1 restriction — the state that outlives the conversational question.

[OD-BOT §156] / [§162]: an S1 restriction is not lifted by the next reply, a
new intent, a new session or a TTL. The open question of
:mod:`apps.orchestrator.open_question` is CONVERSATIONAL state (B13 two-hour
context TTL, «does the bot remember it asked»); it cannot carry a safety
restriction, because its expiry would read as clearance — exactly what §162
forbids. So the restriction lives here, under its own key, with NO TTL.

One record per conversation, in ``Conversation.skill_state`` (the carrier every
surface shares — MAX / Telegram per-tenant, the global concierge, the Mini App
through ``resolve_conversation_for_bot_user``)::

    skill_state["s1_restriction"] = {
        "group": "G4",                      # S1 group the restriction belongs to
        "question_id": "health_screening.g4",
        "status": "open" | "stop",          # see below
        "opened_at": iso, "updated_at": iso,
        "provenance": {"source": ..., "outcome": ...},   # never the raw text (M13)
    }

* ``open`` — an ambiguous signal is awaiting its one routing question ([§164]);
  health-sensitive recommendation / booking stay blocked; the question is put
  again on every turn (re-opened if its conversational record expired).
* ``stop`` — the question was resolved by a positive / recent-resolved sign or
  by another red flag: S1 STOP. Per [§156] it stays: every later turn on every
  surface gets the S1 STOP reply again and nothing is forwarded.

What does NOT exist here, on purpose: a way out. [§162] allows
``CLEARED_BY_RECHECK`` only through an explicit user-initiated
``safety_recheck`` with all eight conditions and provenance; the registered
sources give no deterministic contract for such an answer, and inventing one
(a «нет»-regex) is forbidden. :func:`clear_restriction` therefore exists for
the future recheck mechanic and is called by nothing on a live path — the
owner blocker is named in the PR. A plain «нет», «не знаю», «всё прошло», a
new intent or TTL never clears.

Implementation of registered owner policy does not constitute CLINICAL
APPROVED, PHYSICIAN PASS, or SAFE FOR PILOT.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from apps.orchestrator.open_question import write_conversation_state

logger = logging.getLogger(__name__)

#: Own key in ``skill_state`` — next to ``awaiting_answer`` / ``last_answered``
#: (open question), ``health_screening_asked`` (memo), ``no_match``.
RESTRICTION_KEY = "s1_restriction"

RestrictionStatus = Literal["open", "stop"]


@dataclass(frozen=True)
class S1Restriction:
    group: str
    question_id: str
    status: RestrictionStatus
    opened_at: datetime | None
    provenance: dict[str, Any]

    @property
    def blocks(self) -> bool:
        """Both statuses block health-sensitive recommendation / booking."""

        return True


def _state(conversation: Any) -> dict[str, Any]:
    raw = getattr(conversation, "skill_state", None)
    return raw if isinstance(raw, dict) else {}


def _parse_at(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def restriction(conversation: Any) -> S1Restriction | None:
    """The durable restriction on this conversation, or None. Never raises.

    Malformed rows read as «restricted, open» rather than «no restriction»:
    here the safe direction is to keep blocking.
    """

    if conversation is None:
        return None
    row = _state(conversation).get(RESTRICTION_KEY)
    if row is None:
        return None
    if not isinstance(row, dict):
        logger.warning(
            "s1_restriction.malformed conversation=%s — read as open",
            getattr(conversation, "id", None),
        )
        return S1Restriction("G4", "", "open", None, {"malformed": True})
    status: RestrictionStatus = "stop" if row.get("status") == "stop" else "open"
    provenance = row.get("provenance")
    return S1Restriction(
        group=str(row.get("group") or "G4"),
        question_id=str(row.get("question_id") or ""),
        status=status,
        opened_at=_parse_at(row.get("opened_at")),
        provenance=provenance if isinstance(provenance, dict) else {},
    )


def open_restriction(conversation: Any, *, group: str, question_id: str, source: str) -> bool:
    """Record «awaiting the routing question» — idempotent, keeps an existing row.

    Returns True when the row is on the conversation after the write (re-read),
    False when it could not be persisted: the caller must NOT proceed as if
    unrestricted (fail-closed).
    """

    if conversation is None:
        return False
    current = restriction(conversation)
    if current is not None:
        return True  # never downgrade: an open or stop row stays as it is
    now = _now()
    _write_row(
        conversation,
        {
            "group": group,
            "question_id": question_id,
            "status": "open",
            "opened_at": now,
            "updated_at": now,
            "provenance": {"source": source, "outcome": "question_pending"},
        },
    )
    persisted = restriction(conversation) is not None
    logger.info(
        "s1_restriction.opened group=%s conversation=%s persisted=%s",
        group,
        getattr(conversation, "id", None),
        persisted,
    )
    return persisted


def mark_stop(conversation: Any, *, group: str, question_id: str, source: str, reason: str) -> bool:
    """Record the S1 STOP resolution of the question — durable ([§156]).

    ``reason`` names the routing outcome (``positive_sign`` / ``g6_sign`` /
    ``red_flag``), never the person's words.
    """

    if conversation is None:
        return False
    current = restriction(conversation)
    now = _now()
    _write_row(
        conversation,
        {
            "group": group,
            "question_id": question_id,
            "status": "stop",
            "opened_at": current.opened_at.isoformat() if current and current.opened_at else now,
            "updated_at": now,
            "provenance": {"source": source, "outcome": "stop", "reason": reason},
        },
    )
    persisted = restriction(conversation)
    ok = persisted is not None and persisted.status == "stop"
    logger.info(
        "s1_restriction.stop group=%s reason=%s conversation=%s persisted=%s",
        group,
        reason,
        getattr(conversation, "id", None),
        ok,
    )
    return ok


def clear_restriction(conversation: Any, *, provenance: dict[str, Any]) -> None:
    """``CLEARED_BY_RECHECK`` — the only registered way out ([§156], [§162]).

    Not reachable from any live path today: the explicit ``safety_recheck``
    entry point, its eight-condition adjudication and the provenance carrier
    are an owner decision (DRF-2040 open questions 1–3). Kept so the future
    mechanic has one place to land and so tests can pin that nothing else
    clears. ``provenance`` is mandatory by contract.
    """

    if conversation is None or not provenance:
        raise ValueError("CLEARED_BY_RECHECK requires provenance ([OD-BOT §162])")
    _write_row(conversation, None)
    logger.info(
        "s1_restriction.cleared_by_recheck conversation=%s provenance=%s",
        getattr(conversation, "id", None),
        sorted(provenance),
    )


def _write_row(conversation: Any, row: dict[str, Any] | None) -> None:
    """Persist the row; a failure is logged and shows up as «not persisted» on
    the re-read that every writer does next — the callers fail closed."""

    try:
        write_conversation_state(conversation, RESTRICTION_KEY, row)
    except Exception:  # noqa: BLE001 — the re-read reports it; never raise into a turn
        logger.exception(
            "s1_restriction.write_failed conversation=%s", getattr(conversation, "id", None)
        )


def _now() -> str:
    from django.utils import timezone as dj_timezone

    return dj_timezone.now().isoformat()


__all__ = [
    "RESTRICTION_KEY",
    "S1Restriction",
    "clear_restriction",
    "mark_stop",
    "open_restriction",
    "restriction",
]
