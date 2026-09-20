"""Page-out alerting (Sprint 10 / O2 / DRF-863; MAX sink — DRF-2158).

Single entry point :func:`page` routes critical operational alerts to
a dedicated Telegram channel + Sentry + the operators' MAX chat.
PagerDuty was evaluated and explicitly skipped — see
`docs/runbooks/on-call.md` § Decision.

## Severity matrix

| Severity   | Telegram channel | Sentry capture | MAX chat          | Use case                                |
|------------|------------------|----------------|-------------------|------------------------------------------|
| critical   | ✅ + loud prefix | ✅ fatal-level | ✅ ``[CRITICAL]`` | F2 scope violations, X-criteria breach, P0 events |
| error      | ✅               | ✅ error-level | ✅ ``[ERROR]``    | Skill dispatch failures, sync errors    |
| warning    | ✅ + muted       | —              | ✅ ``[WARNING]``  | Capacity headroom, slow catalogs        |

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

## Telegram, Sentry — and MAX (DRF-2158)

The original design (Sprint 10) sent to Telegram + Sentry only: MAX was
the customers' channel and operators were expected to live in Telegram.
Fact of the pilot host on 2026-09-20: ``ALERTS_TELEGRAM_CHAT_ID``,
``TELEGRAM_BOT_TOKEN`` and ``SENTRY_DSN`` are all unset, so every
``page(...)`` ended in ``alerting.telegram.skipped reason=no_credentials``
— silence. The one operator channel that does work is the MAX chat in
``HANDOFF_NOTIFY_MAX_CHAT_IDS`` (the DRF-1029 escalation recipients,
where «LLM доступна/недоступна» already lands). So :func:`_send_max` is
the third sink: same dedup, same audit row (``max_sent``), one line
«⚠️ [LEVEL] {title} — {body}» capped at 1000 characters with phone /
e-mail / card masked via :func:`apps.observability.pii_filter.redact_pii`.
Telegram and Sentry stay as sinks whenever they are configured.

### Limits of the MAX sink (owner-visible, not fixable here)

* **The recipient is a personal dialog with the bot, not a group.**
  ``HANDOFF_NOTIFY_MAX_CHAT_IDS`` holds ``chat_id`` values copied out of a
  dialog; they only work for the bot whose dialog they were copied from.
  ``HANDOFF_NOTIFY_MAX_USER_IDS`` (people) replaces the list when set —
  see :func:`apps.handoff.notify.get_notify_addresses`.
* **The sender is the legacy token** (``MAX_BOT_TOKEN``) — on the pilot
  that is the client bot, so the alert arrives in the same dialog a
  client conversation would.
* **Nobody may be sitting in that dialog.** 2026-09-15 the LLM-down
  message reached the chat and went unnoticed for 45 minutes
  (DRF-1938). Delivery here means «accepted by the MAX API», not «seen».
* **Sentry is counted as delivered only when the SDK is initialised**
  (``SENTRY_DSN`` set). Before DRF-2158 ``_send_sentry`` returned True
  whenever ``sentry_sdk`` merely imported, so ``page`` reported a
  delivery into nothing.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Final, Literal

import requests  # type: ignore[import-untyped]
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
      True if at least one sink delivered (Telegram OR Sentry OR MAX),
      False if dedup'd OR no sink is configured OR every configured sink
      failed. Best-effort — never raises.

    MAX (DRF-2158): recipients come from ``HANDOFF_NOTIFY_MAX_CHAT_IDS``
    (or ``HANDOFF_NOTIFY_MAX_USER_IDS`` when set — people replace
    dialogs), the text is one line «⚠️ [LEVEL] {title} — {body}» ≤ 1000
    chars with phone / e-mail / card masked. See the module docstring
    for the limits (personal dialog, legacy token, «nobody in the
    dialog»).
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
    max_ok = _send_max(severity, title, body)

    write_audit(
        "observability.alert.paged",
        payload={
            "severity": severity,
            "dedup_key": key,
            "title": title[:200],
            "telegram_sent": telegram_ok,
            "sentry_sent": sentry_ok,
            "max_sent": max_ok,
        },
    )

    sent = telegram_ok or sentry_ok or max_ok
    if not sent:
        logger.warning(
            "alerting.page.no_sinks severity=%s telegram_sent=%s sentry_sent=%s max_sent=%s",
            severity,
            telegram_ok,
            sentry_ok,
            max_ok,
        )
    return sent


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

    if not sentry_sdk.is_initialized():
        # No SENTRY_DSN → the SDK is a no-op client. Counting that as a
        # delivery is how the pilot ran silent (DRF-2158).
        logger.info("alerting.sentry.skipped reason=not_initialized severity=%s", severity)
        return False

    try:
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("alert.severity", severity)
            scope.set_level(_SEVERITY_SENTRY_LEVEL[severity])  # type: ignore[arg-type]
            scope.set_context(
                "alert",
                {"title": title, "body": body[:4000]},
            )
            sentry_sdk.capture_message(title)
        return True
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception("alerting.sentry.failed severity=%s", severity)
        return False


# ─── MAX (DRF-2158) ───────────────────────────────────────────────────────

_MAX_TEXT_LIMIT: Final = 1000
_MAX_SEVERITY_LABEL: Final[dict[Severity, str]] = {
    "critical": "⚠️ [CRITICAL]",
    "error": "⚠️ [ERROR]",
    "warning": "⚠️ [WARNING]",
}
_MAX_LINE_JOINER: Final = " · "


def _send_max(severity: Severity, title: str, body: str) -> bool:
    """Fan the alert out to the operators' MAX recipients. Never raises.

    Recipients: :func:`apps.handoff.notify.get_notify_addresses` —
    ``HANDOFF_NOTIFY_MAX_USER_IDS`` (people) when set, otherwise
    ``HANDOFF_NOTIFY_MAX_CHAT_IDS`` (dialogs). Empty list = sink off,
    logged as ``alerting.max.skipped reason=no_recipients``.

    Returns True when at least one recipient accepted the message.
    Imports lazily: ``apps.handoff.notify`` pulls in models and the MAX
    outbound client, and this module is imported at boot by channel
    handlers.
    """
    try:
        from apps.handoff.notify import (  # noqa: PLC0415 — see docstring
            get_notify_addresses,
            send_max_notification,
        )

        recipients = get_notify_addresses()
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception("alerting.max.failed stage=recipients severity=%s", severity)
        return False

    if not recipients:
        logger.info("alerting.max.skipped reason=no_recipients severity=%s", severity)
        return False

    text = _max_text(severity, title, body)
    try:
        failures = int(send_max_notification(text=text, addresses=recipients))
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception("alerting.max.failed stage=send severity=%s", severity)
        return False

    delivered = len(recipients) - failures
    if delivered <= 0:
        logger.warning(
            "alerting.max.all_failed severity=%s recipients=%d", severity, len(recipients)
        )
        return False
    logger.info(
        "alerting.max.sent severity=%s recipients=%d failures=%d",
        severity,
        len(recipients),
        failures,
    )
    return True


def _max_text(severity: Severity, title: str, body: str) -> str:
    """One line «⚠️ [LEVEL] {title} — {body}», masked, ≤ 1000 chars.

    Lines of a multi-line body are joined with « · » so the health
    probe's «🔴 LLM недоступна / Причина: … / Ошибка: …» stays readable
    in a chat bubble. Masking runs BEFORE the cut so a phone number on
    the 1000-character boundary cannot leave half its digits behind.
    """
    from apps.observability.pii_filter import redact_pii  # noqa: PLC0415 — sibling, lazy

    label = _MAX_SEVERITY_LABEL[severity]
    head = _one_line(title)
    tail = _one_line(body)
    text = f"{label} {head} — {tail}" if tail else f"{label} {head}"
    text = redact_pii(text)
    if len(text) > _MAX_TEXT_LIMIT:
        text = text[: _MAX_TEXT_LIMIT - 1] + "…"
    return text


def _one_line(s: str) -> str:
    """Collapse newlines into « · » and runs of whitespace into one space."""
    lines = [" ".join(line.split()) for line in s.splitlines()]
    return _MAX_LINE_JOINER.join(line for line in lines if line)
