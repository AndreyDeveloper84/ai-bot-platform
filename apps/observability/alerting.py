"""Page-out alerting (Sprint 10 / O2 / DRF-863).

Single entry point :func:`page` routes critical operational alerts to
a dedicated Telegram channel + Sentry. PagerDuty was evaluated and
explicitly skipped — see `docs/runbooks/on-call.md` § Decision.

## Severity matrix

| Severity   | Telegram channel | Sentry capture | Use case                                |
|------------|------------------|----------------|------------------------------------------|
| critical   | ✅ + loud prefix | ✅ fatal-level | F2 scope violations, X-criteria breach, P0 events |
| error      | ✅               | ✅ error-level | Skill dispatch failures, sync errors    |
| warning    | ✅ + muted       | —              | Capacity headroom, slow catalogs        |

`critical` prefixes the body with `🚨🚨🚨` so the channel notification
sound + visual treatment overrides DND on the operator's phone (the
PD-like effect we lose without PD's autocall).

## Dedup

The same `(severity, dedup_key)` pair within the dedup window (default
5 min) collapses to one page. Implementation uses Redis via Django's
cache framework — atomic ``add()`` returns False on a duplicate key so
we can short-circuit safely under concurrent producers.

When ``dedup_key`` is None we hash ``severity || title || body`` and
use that — content-identical pages dedupe by default. Callers with
domain-specific keys (e.g. ``f"f2_scope_violation:{flip_at_iso}"``)
should pass them explicitly.

## Failure semantics

`page()` is **best-effort and never raises**. The whole point is
operational alerting; a page-handler that itself throws inside the
critical path is worse than a missed page. Every failure mode writes
an audit row + a logger.warning so the missing page is debuggable.

## Why Telegram, not MAX

Operators don't have MAX installed; MAX is the channel where customers
chat with the bot. Mixing alert noise with customer signal would
degrade both. Telegram is universally installed in the team, has
per-channel notification priorities, and was already paid for by the
``TELEGRAM_BOT_TOKEN`` env var used elsewhere in the platform.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Final, Literal

import requests
from django.conf import settings
from django.core.cache import cache

from apps.audit.services import write_audit

logger = logging.getLogger(__name__)


# ─── public API ────────────────────────────────────────────────────────────


Severity = Literal["critical", "error", "warning"]

_DEDUP_CACHE_PREFIX: Final = "alerting:dedup"
_SEVERITY_PREFIX: Final[dict[Severity, str]] = {
    "critical": "🚨🚨🚨 CRITICAL",
    "error": "🔴 ERROR",
    "warning": "🟡 WARNING",
}
_SEVERITY_SENTRY_LEVEL: Final[dict[Severity, str]] = {
    "critical": "fatal",
    "error": "error",
    "warning": "warning",
}


def page(
    severity: Severity,
    title: str,
    body: str,
    *,
    dedup_key: str | None = None,
) -> bool:
    """Fire an operational alert.

    Args:
      severity: One of ``critical`` / ``error`` / ``warning``.
      title: One-line headline. Goes into the Telegram first line +
             Sentry event message.
      body: Multi-line detail. Truncated to 3500 chars for Telegram
            (4096 - prefix budget).
      dedup_key: Optional explicit dedup key. None → hash of content.

    Returns:
      True if the page was sent (Telegram OR Sentry succeeded), False
      if dedup'd OR both sinks failed. Best-effort — never raises.
    """
    if severity not in _SEVERITY_PREFIX:
        # Don't crash — degrade. Caller passed bad data; treat as error.
        logger.warning("alerting.page.bad_severity severity=%r", severity)
        severity = "error"

    key = dedup_key or _content_dedup_key(severity, title, body)
    if _is_duplicate(severity, key):
        write_audit(
            "observability.alert.deduped",
            payload={"severity": severity, "dedup_key": key, "title": title[:200]},
        )
        return False

    telegram_ok = _send_telegram(severity, title, body)
    sentry_ok = _send_sentry(severity, title, body)

    write_audit(
        "observability.alert.paged",
        payload={
            "severity": severity,
            "dedup_key": key,
            "title": title[:200],
            "telegram_sent": telegram_ok,
            "sentry_sent": sentry_ok,
        },
    )

    return telegram_ok or sentry_ok


# ─── dedup ────────────────────────────────────────────────────────────────


def _content_dedup_key(severity: Severity, title: str, body: str) -> str:
    """Hash content for default dedup. Stable across processes/workers."""
    h = hashlib.sha256()
    h.update(severity.encode())
    h.update(b"|")
    h.update(title.encode())
    h.update(b"|")
    h.update(body.encode())
    return h.hexdigest()[:16]  # 16 hex = 64 bits = collision-safe at our scale


def _is_duplicate(severity: Severity, dedup_key: str) -> bool:
    """Return True if this (severity, dedup_key) was paged recently.

    Uses Django's cache (Redis in prod, locmem in tests). ``add()`` is
    atomic — it sets only if the key doesn't exist and returns False
    otherwise. Critical-severity pages bypass dedup entirely so a
    duplicate prod incident never gets silently dropped.
    """
    if severity == "critical":
        # Defense in depth: never dedup critical. Two pages for the same
        # incident is annoying; missing a real one is worse.
        return False

    ttl = int(getattr(settings, "ALERTS_DEDUP_TTL_SECONDS", 300))
    cache_key = f"{_DEDUP_CACHE_PREFIX}:{severity}:{dedup_key}"
    # add() returns True on insert, False if key exists.
    inserted = cache.add(cache_key, "1", timeout=ttl)
    return not inserted


# ─── Telegram ─────────────────────────────────────────────────────────────


def _send_telegram(severity: Severity, title: str, body: str) -> bool:
    """POST to Telegram via the alerts channel.

    Returns False on missing config OR HTTP failure (logged). Best-
    effort: any exception is swallowed.
    """
    token = str(getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "")
    chat_id = str(getattr(settings, "ALERTS_TELEGRAM_CHAT_ID", "") or "")
    if not token or not chat_id:
        logger.info(
            "alerting.telegram.skipped reason=no_credentials severity=%s",
            severity,
        )
        return False

    prefix = _SEVERITY_PREFIX[severity]
    text = f"{prefix}\n<b>{_escape_html(title)}</b>\n\n{_escape_html(body)[:3500]}"

    proxies = _telegram_proxies()

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                # disable_notification stays False for critical/error;
                # for warning we set it True so warnings don't wake people.
                "disable_notification": severity == "warning",
            },
            timeout=10,
            proxies=proxies,
        )
        if not resp.ok:
            logger.warning(
                "alerting.telegram.http_%d body=%.200s",
                resp.status_code,
                resp.text,
            )
            return False
        return True
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception("alerting.telegram.failed severity=%s", severity)
        return False


def _telegram_proxies() -> dict[str, str] | None:
    """Pick TELEGRAM_PROXY → OPENAI_PROXY fallback.

    api.telegram.org is blocked in RU; production needs the proxy
    threaded through. See memory `feedback_telegram_proxy.md`.
    """
    proxy = str(
        getattr(settings, "TELEGRAM_PROXY", "") or getattr(settings, "OPENAI_PROXY", "") or ""
    )
    return {"https": proxy, "http": proxy} if proxy else None


def _escape_html(s: str) -> str:
    """Minimal HTML escape for Telegram parse_mode=HTML.

    Telegram's HTML mode is a strict subset — only `<`, `>`, `&` need
    escaping in body text. We intentionally do NOT escape inside tag
    parameters (we don't use them).
    """
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ─── Sentry ───────────────────────────────────────────────────────────────


def _send_sentry(severity: Severity, title: str, body: str) -> bool:
    """Capture as a Sentry event. No-op without sentry-sdk."""
    try:
        import sentry_sdk  # noqa: PLC0415 — optional at module import
    except ImportError:
        return False

    try:
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("alert.severity", severity)
            scope.set_level(_SEVERITY_SENTRY_LEVEL[severity])
            scope.set_context(
                "alert",
                {"title": title, "body": body[:4000]},
            )
            sentry_sdk.capture_message(title)
        return True
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception("alerting.sentry.failed severity=%s", severity)
        return False
