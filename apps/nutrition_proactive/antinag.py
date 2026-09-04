"""The shared ignore-streak arithmetic (DRF-1468).

Policy R2/R6 (``docs/design/policies/nutrition-coach-copy-policy.md``):
ignoring a message is a legitimate answer, and the system respects it by
going quiet -- without ever saying «ты не ответила». This module computes
*how* quiet, from data that cannot drift:

* the sends come from the outbound journal (``prefs.OUTBOX_KEY``), which is
  written by the one persist path every successful send takes;
* the "was heeded" signal is a ``Message(role=user)`` anywhere after the
  send. MAX sends no read receipts, so a reply is the only observable
  acknowledgement there is.

### The limitation this carries, stated plainly

There are no read receipts and there never will be (the channel does not
send them, and ``send_message`` stores no channel message id). "Ignored"
is therefore INFERRED, not measured: a person who reads every message but
never types a reply is indistinguishable from a person who never reads,
and after :data:`SURFACE_IGNORE_LIMIT` sends their surface auto-pauses.
That is the deliberate direction of the error -- the cost of a false
"ignored" is silence (the person can re-enable), the cost of a false
"heeded" would be pestering, and policy 2.5 prices pestering infinitely
higher. Do not "fix" this by relaxing the streak; if a real read signal
ever appears, plug it in here as a stronger heeded-proof alongside the
reply. Related known gap: all pilot users sit on the default timezone,
so quiet hours are effectively Moscow time (DRF-1477).

### Why water is not here

Water keeps its own domain streak
(:data:`~apps.nutrition_proactive.prefs.IGNORED_STREAK_LIMIT`, intake
comparison in ``tasks.plan_water_reminders``). That comparison is strictly
stronger evidence than a reply: a person who drinks without writing
anything IS heeding the reminder, and a reply-based streak would pause a
feature that is measurably working. The universal streak applies to
surfaces with no domain signal of their own -- the report today, the hint
and the weekly report tomorrow.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from apps.nutrition_proactive import prefs


def last_user_message_at(bot_user: Any) -> datetime | None:
    """The newest user turn anywhere in this person's conversations.

    Cross-tenant and cross-conversation on purpose: a reply in ANY thread
    proves the person is alive and reading, which is the whole question
    the streak asks. Costs one indexed query per candidate that reaches
    the streak check -- bounded by the beat's ``BATCH_LIMIT``.
    """
    from apps.conversations.models import Message

    return (
        Message.all_tenants.filter(
            conversation__bot_user_id=bot_user.pk,
            role=Message.Role.USER,
        )
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )


def surface_ignored_streak(
    bot_user: Any,
    user_prefs: dict[str, Any],
    *,
    surface: str,
) -> int:
    """Trailing consecutive sends on ``surface`` that no user message followed.

    Computed from the journal rather than a stored counter, so it can never
    disagree with what was actually sent: any send newer than the newest
    user message is unanswered by definition.
    """
    entries = [
        entry for entry in prefs.outbox_entries(user_prefs) if entry.get("surface") == surface
    ]
    if not entries:
        return 0
    last_reply = last_user_message_at(bot_user)
    last_reply_iso = last_reply.isoformat() if last_reply is not None else ""
    streak = 0
    for entry in reversed(entries):
        if str(entry.get("sent_at", "")) > last_reply_iso:
            streak += 1
        else:
            break
    return streak
