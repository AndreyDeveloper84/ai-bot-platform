"""Who may receive a bot-initiated nutrition message (DRF-1285).

One implementation of the gate, read by both beat tasks and by the dry-run
command, so "who would we write to?" can never be answered differently from
"who did we write to?".

### The gate, in order

``proactive_messages_opt_out`` is checked **first and unconditionally**. It
is the person's global veto on the bot writing first; nothing downstream can
re-enable a message for someone who set it, and no code path here reads it
after any other decision. That ordering is the point -- a veto evaluated
late is a veto that a future edit can accidentally skip.

Then, in order:

* ``deleted_at`` -- a GDPR-erased row is not a recipient.
* ``chat_id`` -- no address, no message.
* ``consent_at`` -- the broad 152-FZ welcome consent.
* ``food_scanner_consent_at`` -- the feature-specific 152-FZ consent for the
  nutrition diary surface. Reading someone's food diary back to them,
  unprompted, is processing that data; the same consent that gates writing
  it gates volunteering it.
* the per-feature opt-in (``daily_report_time`` / ``water_reminders``),
  which is OFF for everyone until they ask.

### Consent is a different question from the opt-out flag

The opt-out flag says "do not write to me first". The consent timestamps say
"you may process this class of my data at all". A proactive nutrition
message needs both, and neither substitutes for the other -- which is why
both are checked here rather than collapsing them into one boolean.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.identity.models import BotUser

#: Cap on rows pulled per beat tick. The pilot recipient list is single
#: digits; the cap exists so a future import cannot turn one tick into an
#: unbounded fan-out.
BATCH_LIMIT = 500


@dataclass(frozen=True)
class Blocked:
    """A candidate that will not be written to, and why."""

    bot_user_id: Any
    reason: str


def base_queryset():
    """BotUsers that could conceivably be written to.

    Cross-tenant (``all_tenants``) because the beat is system-level, and
    ``select_related("tenant")`` because :func:`~apps.nutrition_proactive.
    prefs.resolve_timezone` reads the salon timezone as a fallback.

    The opt-out veto is applied here as a queryset filter *and* re-asserted
    per row in :func:`check_common` -- belt and braces on the one condition
    whose failure is a trust break rather than a missed message.
    """
    return (
        BotUser.all_tenants.filter(proactive_messages_opt_out=False)
        .filter(deleted_at__isnull=True)
        .exclude(chat_id="")
        .exclude(chat_id__isnull=True)
        .select_related("tenant")
        .order_by("pk")[:BATCH_LIMIT]
    )


def check_common(bot_user: Any) -> str | None:
    """Return a block reason, or None when the common gate passes."""
    if getattr(bot_user, "proactive_messages_opt_out", False):
        return "proactive_opt_out"
    if getattr(bot_user, "deleted_at", None) is not None:
        return "deleted"
    if not (getattr(bot_user, "chat_id", "") or "").strip():
        return "no_chat_id"
    if getattr(bot_user, "consent_at", None) is None:
        return "no_consent"
    if getattr(bot_user, "food_scanner_consent_at", None) is None:
        return "no_food_consent"
    return None
