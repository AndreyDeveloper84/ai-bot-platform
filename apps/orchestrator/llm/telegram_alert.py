"""Breaker state-transition Telegram alert (DRF-455 / Sprint 2 / E2).

Sprint-1 debt clear-up: per CR-3 (DRF-388) original spec, breaker state
transitions should alert admins via Telegram. Sprint 1 D1 shipped the
breaker with `write_audit` logging only — this module wires the
Telegram side.

### Why a thin shim

The legacy `notifications/max_bot.py::send_max_message` is the only
working MAX REST sender in the repo today (running in prod since
2026-04). It uses `requests` (not httpx — pre-Sprint-1 stack) and
takes int chat_id. We don't port the whole thing into apps/ — that's
Sprint 7+ when we generalise alerting.

Instead this module imports it lazily (so its import-time side
effects don't run unless we actually alert) and adapts the call. If
the legacy module is later drained, this shim is the only thing to
update.

### Never raises

Per audit-path principle (write_audit doesn't raise; events.emit
doesn't raise): alert delivery is forensic, not critical-path. A
failed Telegram delivery must NOT prevent the breaker from
completing its state transition. Wrap the entire body in try/except
and swallow.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


def send_breaker_alert(provider: str, transition: str, details: dict[str, Any]) -> None:
    """Send a Telegram alert about a breaker state transition.

    Args:
      provider: breaker name (e.g. "openai").
      transition: human-readable "closed → open", "open → half_open", etc.
      details: extra structured data for the alert body (failure count,
               cooldown remaining, etc.).

    Behaviour:
      - No-op when `settings.ADMIN_MAX_CHAT_ID` is empty (dev/CI default).
      - Best-effort delivery. Any exception is swallowed + logged.
    """

    chat_id_raw = getattr(settings, "ADMIN_MAX_CHAT_ID", "")
    if not chat_id_raw:
        # Local dev, CI, or environments without an admin alert chat.
        # Audit-log path still writes via the breaker's own write_audit.
        return

    try:
        chat_id = int(chat_id_raw)
    except (TypeError, ValueError):
        logger.warning(
            "orchestrator.llm.telegram_alert.invalid_chat_id chat_id=%r",
            chat_id_raw,
        )
        return

    text = _format_alert(provider, transition, details)

    # `importlib` rather than `from legacy_notifications.max_bot import …`
    # because mypy follows static imports into the legacy module
    # (which has its own untyped `requests` dependency + stale
    # `services_app.models` reference Sprint 1 didn't clean up).
    # Dynamic import keeps the static checker out of legacy code and
    # preserves the "legacy module may disappear" defensive path.
    import importlib

    try:
        max_bot = importlib.import_module("legacy_notifications.max_bot")
    except Exception:  # noqa: BLE001 — defensive against drained legacy
        logger.warning("orchestrator.llm.telegram_alert.legacy_unavailable")
        return

    send_max_message = getattr(max_bot, "send_max_message", None)
    if send_max_message is None:
        logger.warning("orchestrator.llm.telegram_alert.send_max_message_missing")
        return

    try:
        send_max_message(chat_id=chat_id, text=text)
    except Exception:  # noqa: BLE001 — alert delivery never breaks caller
        logger.exception(
            "orchestrator.llm.telegram_alert.send_failed provider=%s transition=%s",
            provider,
            transition,
        )


def _format_alert(provider: str, transition: str, details: dict[str, Any]) -> str:
    """Format a one-shot admin alert message body."""

    details_str = ", ".join(f"{k}={v}" for k, v in details.items()) or "—"
    return (
        f"🚨 Breaker {transition} for {provider}\n"
        f"Details: {details_str}\n"
        f"(see AuditLog for the full breaker.* trail)"
    )
