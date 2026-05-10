"""Phase 3.2B T07: ignored detection (Design §10.11, adapted to NudgeEvent).

Plan originally cited message.seen_at — но эти TS живут на NudgeEvent (telemetry
лayer). Сигнатура принимает NudgeEvent, что соответствует data model.

«Ignored» = nudge просмотрен, прошло ≥24ч, не кликнут, и юзер был активен
после (т.е. не просто отсутствовал).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from services_app.models import Message, NudgeEvent


def is_ignored(event: NudgeEvent, *, now: datetime) -> bool:
    if event.seen_at is None:
        return False
    if (now - event.seen_at) < timedelta(hours=24):
        return False
    if event.clicked_at is not None:
        return False
    return Message.objects.filter(
        conversation__bot_user=event.bot_user,
        role="user",
        created_at__gt=event.seen_at,
    ).exists()
