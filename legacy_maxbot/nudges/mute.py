"""Phase 3.2B T05: mute engine.

Design §10.6 mute system:
- mode=off → block immediately (per kind или per class)
- mode=less_often → не блокирует, но cooldown × 2 (handled in cooldowns.py)
- expires_at set → ignore mute if expired
- auto-mute: ≥2 ignored same kind в 30d → create mute(off, auto_ignored_twice)
"""
from __future__ import annotations

from datetime import datetime, timedelta

from django.db.models import Q

from services_app.models import NudgeEvent, NudgeMute


def is_muted(
    bot_user, *, kind: str, nudge_class: str, now: datetime,
) -> tuple[bool, str | None]:
    """Block if active mute(off) на kind ИЛИ на class."""
    qs = NudgeMute.objects.filter(
        bot_user=bot_user, mode="off",
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))

    if qs.filter(kind=kind).exists():
        return True, "muted_kind_off"
    if qs.filter(nudge_class=nudge_class).exists():
        return True, "muted_class_off"
    return False, None


def apply_mute(
    bot_user, *,
    kind: str | None = None,
    nudge_class: str | None = None,
    mode: str,
    reason: str,
    expires_at: datetime | None = None,
) -> NudgeMute:
    """Create mute record. Caller отвечает за валидацию kind XOR nudge_class."""
    return NudgeMute.objects.create(
        bot_user=bot_user, kind=kind, nudge_class=nudge_class,
        mode=mode, reason=reason, expires_at=expires_at,
    )


def maybe_auto_mute_after_ignore(
    bot_user, *, kind: str, now: datetime,
) -> bool:
    """Если ≥2 ignored same kind за последние 30 дней — auto-mute(off).

    Idempotent: skip если auto-mute(off) уже существует для этого kind.
    Returns True если создал новый mute.
    """
    # Already auto-muted?
    if NudgeMute.objects.filter(
        bot_user=bot_user, kind=kind, mode="off",
        reason="auto_ignored_twice",
    ).exists():
        return False

    since = now - timedelta(days=30)
    ignored_count = NudgeEvent.objects.filter(
        bot_user=bot_user, kind=kind,
        ignored_at__isnull=False, ignored_at__gte=since,
    ).count()
    if ignored_count < 2:
        return False

    NudgeMute.objects.create(
        bot_user=bot_user, kind=kind,
        mode="off", reason="auto_ignored_twice",
    )
    return True
