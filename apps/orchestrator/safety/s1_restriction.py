"""Durable S1 restriction — the state that outlives the conversation.

[OD-BOT §156] / [§162]: an S1 restriction is not lifted by the next reply, a
new intent, a new session or a TTL. Two carriers were candidates:

* ``Conversation.skill_state`` — per conversation. The runtime starts a NEW
  session by closing the row (``close_conversation`` → ``is_active=False``)
  and letting ``resolve_active_conversation`` create a fresh one for the same
  ``(bot_user, tenant)``; a restriction stored there would vanish with the
  session — «новая сессия не clearance» could not be honoured.
* ``BotUser.context`` — per identity, survives conversations. Already the
  home of health-related state (``nutrition_proactive``), hidden from every
  admin screen by ``adminconsole.client_scope.HIDDEN_FIELDS`` (152-ФЗ). This
  is the carrier.

So: the CONVERSATIONAL question (:mod:`apps.orchestrator.open_question`, B13
two-hour TTL, «does the bot remember it asked») lives on the conversation;
the RESTRICTION lives here, on the BotUser, with NO TTL::

    BotUser.context["s1_restriction"] = {
        "group": "G4" | "G6" | "G3" | … | None,   # None = red flag of an
                                                 # unattributed older rule
        "question_id": "health_screening.g4",
        "status": "open" | "stop",
        "opened_at": iso, "updated_at": iso,
        "provenance": {"source": ..., "outcome": ..., "reason": ...},   # never the raw text (M13)
    }

* ``open`` — an ambiguous signal is awaiting its one routing question ([§164]);
  health-sensitive recommendation / booking stay blocked; the question is put
  again on every turn, in every conversation of this identity.
* ``stop`` — the question was resolved by an S1 sign: STOP. Per [§156] it
  stays: every later turn on every surface gets the S1 STOP reply again.

What does NOT exist here, on purpose: a way out. [§162] allows
``CLEARED_BY_RECHECK`` only through an explicit user-initiated
``safety_recheck`` with all eight conditions and provenance. Neither the entry
point nor the provenance contract nor the adjudication is registered, so
:func:`clear_restriction` REFUSES unconditionally (:class:`RecheckNotRegistered`):
no live code, no test, no arbitrary dict can clear a restriction until the
owner registers the mechanic. That refusal is the blocker, named.

Named limits: identities are channel- and tenant-scoped, so the restriction
follows the BotUser, not the person across bots; a lawful privacy erasure
clears ``BotUser.context`` and with it the restriction (the person's right,
not a clearance mechanic).

Implementation of registered owner policy does not constitute CLINICAL
APPROVED, PHYSICIAN PASS, or SAFE FOR PILOT.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

logger = logging.getLogger(__name__)

#: Own key in ``BotUser.context`` — next to ``nutrition_proactive`` and
#: ``last_followup_sent_at``.
RESTRICTION_KEY = "s1_restriction"

RestrictionStatus = Literal["open", "stop"]


class RecheckNotRegistered(RuntimeError):
    """``CLEARED_BY_RECHECK`` cannot be issued: the mechanic is not registered."""


@dataclass(frozen=True)
class S1Restriction:
    #: The S1 group the restriction is attributed to — ``None`` when the STOP
    #: came from an older flat rule the classifier cannot attribute.
    group: str | None
    question_id: str
    status: RestrictionStatus
    opened_at: datetime | None
    provenance: dict[str, Any]

    @property
    def blocks(self) -> bool:
        """Both statuses block health-sensitive recommendation / booking."""

        return True


def _context(bot_user: Any) -> dict[str, Any]:
    raw = getattr(bot_user, "context", None)
    return raw if isinstance(raw, dict) else {}


def _parse_at(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def restriction(bot_user: Any) -> S1Restriction | None:
    """The durable restriction of this identity, or None. Never raises.

    Malformed rows read as «restricted, open» rather than «no restriction»:
    here the safe direction is to keep blocking.
    """

    if bot_user is None:
        return None
    row = _context(bot_user).get(RESTRICTION_KEY)
    if row is None:
        return None
    if not isinstance(row, dict):
        logger.warning(
            "s1_restriction.malformed bot_user=%s — read as open", getattr(bot_user, "pk", None)
        )
        return S1Restriction(None, "", "open", None, {"malformed": True})
    status: RestrictionStatus = "stop" if row.get("status") == "stop" else "open"
    provenance = row.get("provenance")
    group = row.get("group")
    return S1Restriction(
        group=str(group) if isinstance(group, str) and group else None,
        question_id=str(row.get("question_id") or ""),
        status=status,
        opened_at=_parse_at(row.get("opened_at")),
        provenance=provenance if isinstance(provenance, dict) else {},
    )


def open_restriction(bot_user: Any, *, group: str, question_id: str, source: str) -> bool:
    """Record «awaiting the routing question» — idempotent, keeps an existing row.

    Returns True when the row is on the identity after the write (re-read),
    False when it could not be persisted: the caller must NOT proceed as if
    unrestricted (fail-closed).
    """

    if bot_user is None:
        return False
    if restriction(bot_user) is not None:
        return True  # never downgrade: an open or stop row stays as it is
    now = _now()
    _write_row(
        bot_user,
        {
            "group": group,
            "question_id": question_id,
            "status": "open",
            "opened_at": now,
            "updated_at": now,
            "provenance": {"source": source, "outcome": "question_pending"},
        },
    )
    persisted = restriction(bot_user) is not None
    logger.info(
        "s1_restriction.opened group=%s bot_user=%s persisted=%s",
        group,
        getattr(bot_user, "pk", None),
        persisted,
    )
    return persisted


def mark_stop(
    bot_user: Any, *, group: str | None, question_id: str, source: str, reason: str
) -> bool:
    """Record the S1 STOP resolution — durable ([§156]).

    ``group`` is the group the classifier attributes the sign to, or ``None``
    when it cannot (an older flat rule): a STOP is never mislabelled as G4.
    ``reason`` names the routing outcome, never the person's words.
    """

    if bot_user is None:
        return False
    current = restriction(bot_user)
    now = _now()
    _write_row(
        bot_user,
        {
            "group": group,
            "question_id": question_id,
            "status": "stop",
            "opened_at": current.opened_at.isoformat() if current and current.opened_at else now,
            "updated_at": now,
            "provenance": {"source": source, "outcome": "stop", "reason": reason},
        },
    )
    persisted = restriction(bot_user)
    ok = persisted is not None and persisted.status == "stop"
    logger.info(
        "s1_restriction.stop group=%s reason=%s bot_user=%s persisted=%s",
        group,
        reason,
        getattr(bot_user, "pk", None),
        ok,
    )
    return ok


def clear_restriction(bot_user: Any, *, provenance: dict[str, Any]) -> None:
    """``CLEARED_BY_RECHECK`` — the only registered way out ([§156], [§162]).

    Refuses unconditionally. The explicit ``safety_recheck`` entry point, the
    provenance contract and the eight-condition adjudication are not
    registered (DRF-2040 open questions 1–3); until they are, no caller — live
    code or test, with any ``provenance`` — can clear a restriction. The
    signature is the landing place for the registered mechanic; the body is
    the blocker.
    """

    raise RecheckNotRegistered(
        "CLEARED_BY_RECHECK is not available: safety_recheck entry point, provenance "
        "contract and the eight §162 conditions are not registered (DRF-2040)"
    )


def _write_row(bot_user: Any, row: dict[str, Any] | None) -> None:
    """Persist the row on ``BotUser.context`` atomically (row lock, sub-key merge).

    A failure is logged and shows up as «not persisted» on the re-read every
    writer does next — the callers fail closed.
    """

    try:
        from django.db import transaction

        from apps.identity.models import BotUser

        with transaction.atomic():
            locked = (
                BotUser.all_tenants.select_for_update().only("id", "context").get(pk=bot_user.pk)
            )
            context = dict(locked.context) if isinstance(locked.context, dict) else {}
            if row is None:
                context.pop(RESTRICTION_KEY, None)
            else:
                context[RESTRICTION_KEY] = row
            BotUser.all_tenants.filter(pk=bot_user.pk).update(context=context)
        # Keep the caller's instance in sync (same pattern as coach_observation).
        bot_user.context = context
    except Exception:  # noqa: BLE001 — the re-read reports it; never raise into a turn
        logger.exception("s1_restriction.write_failed bot_user=%s", getattr(bot_user, "pk", None))


def _now() -> str:
    from django.utils import timezone as dj_timezone

    return dj_timezone.now().isoformat()


__all__ = [
    "RESTRICTION_KEY",
    "RecheckNotRegistered",
    "S1Restriction",
    "clear_restriction",
    "mark_stop",
    "open_restriction",
    "restriction",
]
