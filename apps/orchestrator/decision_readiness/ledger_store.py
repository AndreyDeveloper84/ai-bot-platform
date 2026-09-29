"""Persistence for the question register. Separated so the engine stays pure.

`ledger.py` holds data and policy and imports neither Django nor a clock, so
`evaluate()` can import it without violating §17.1. Everything that touches the
database is here, and the engine never calls it — the register arrives in the
input.

### The field and the key

`Conversation.skill_state`, sub-key `"decision_readiness"`. The same JSON field
`refusal_memo`, `time_preference`, `booking_flow` and `coach_observation`
already share, with a key that overlaps none of them (checked on `883b7539`).
No migration: the column is an existing `JSONField`.

Writes go through `apps.conversations.services.write_skill_state` rather than a
local read-modify-write. Conversations retro B1 recorded what the local version
cost: two skills writing different sub-keys, each reading the same pre-image,
the second one's `update_fields=["skill_state"]` replacing the whole column with
its single-key view. That bug does not need finding twice.

### Failure raises

Both functions raise `LedgerUnavailable` rather than returning a status.
`refusal_memo` may lose a write and cost one repeated refusal; losing one here
costs a DRF-1542 loop. The caller converts the exception into
`availability.ledger_readable = False`, which blocks — it does not log and
carry on.
"""

from __future__ import annotations

import logging
from typing import Any

from apps.conversations.models import Conversation
from apps.conversations.services import write_skill_state
from apps.orchestrator.decision_readiness.ledger import (
    STATE_KEY,
    LedgerUnavailable,
    QuestionLedger,
)

logger = logging.getLogger(__name__)


def read_ledger(conversation: Conversation) -> QuestionLedger:
    """Load the register, or raise. An unreadable register is not an empty one.

    An empty register means "nothing has been asked", which is a licence to ask.
    Returning that for a payload we failed to parse is how a question loop
    restarts itself after a bad deploy.
    """

    try:
        raw: Any = (conversation.skill_state or {}).get(STATE_KEY)
    except Exception as exc:  # noqa: BLE001 - any failure to reach the field
        raise LedgerUnavailable(f"skill_state unreadable: {exc}") from exc

    return QuestionLedger.from_state(raw)


def write_ledger(conversation: Conversation, ledger: QuestionLedger) -> None:
    """Persist the register, or raise.

    No `except: pass`. See the module docstring on why this one is not
    best-effort.
    """

    try:
        write_skill_state(conversation, STATE_KEY, ledger.to_state())
    except Exception as exc:  # noqa: BLE001 - the caller must block, not continue
        logger.warning(
            "decision_readiness.ledger.write_failed conversation=%s entries=%s error=%s",
            getattr(conversation, "id", None),
            len(ledger.entries),
            exc,
        )
        raise LedgerUnavailable(f"ledger write failed: {exc}") from exc
