"""Phase 3.2B T06: race-condition guards.

Design §10.10:
- User message за последние 5 мин → skip nudge
- Conversation.last_message_at за последние 10 мин → skip nudge
"""
from __future__ import annotations

from datetime import datetime, timedelta

from services_app.models import Conversation, Message


def should_skip_due_to_recent_activity(
    bot_user, *, now: datetime,
) -> tuple[bool, str | None]:
    """Return (skip, reason). reason — telemetry slug."""
    user_msg_threshold = now - timedelta(minutes=5)
    has_recent_user_msg = Message.objects.filter(
        conversation__bot_user=bot_user,
        role="user",
        created_at__gte=user_msg_threshold,
    ).exists()
    if has_recent_user_msg:
        return True, "race_user_message_5min"

    convo_threshold = now - timedelta(minutes=10)
    has_active_convo = Conversation.objects.filter(
        bot_user=bot_user,
        last_message_at__gte=convo_threshold,
    ).exists()
    if has_active_convo:
        return True, "race_conversation_10min"

    return False, None
