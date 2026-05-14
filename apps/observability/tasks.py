"""Daily shadow-delta task (Sprint 8 / S4 / DRF-719).

Beat-scheduled wrapper around :func:`apps.observability.delta.compute_daily_delta`.
Iterates every tenant whose ``shadow_mode=True`` flag is set, computes
yesterday's delta, and upserts a :class:`ShadowDeltaSnapshot` row.

### Why upsert keyed on (tenant, snapshot_date)

Sprint 8 exit gate reads 7 consecutive snapshot rows. A retry storm
(beat misfires, manual replay) would otherwise double-write the same
day's row — `update_or_create` keeps the constraint clean and lets
operators safely re-run the task to refresh a stale snapshot.

### Telegram digest

Optional — fires only when ``settings.ADMIN_MAX_CHAT_ID`` is set AND
``settings.MAX_BOT_TOKEN`` is set (the platform-side bot credentials
already shipped Sprint 2 / D2). When either is missing we log the
digest at INFO and skip the network call.

The digest carries the headline number (intent_agreement %) per
tenant. Operators that need the full breakdown click through to the
dashboard (D1 / DRF-721).
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any

from celery import shared_task  # type: ignore[import-untyped]
from django.conf import settings

from apps.audit.services import write_audit
from apps.observability.constants import (
    AGREEMENT_THRESHOLD_AMBER,
    AGREEMENT_THRESHOLD_GREEN,
)
from apps.observability.delta import DeltaSummary, compute_daily_delta

logger = logging.getLogger(__name__)


@shared_task(
    name="apps.observability.tasks.compute_shadow_delta",
    bind=True,
    max_retries=2,
    default_retry_delay=300,  # 5 min — give CSV publisher time to recover
)
def compute_shadow_delta(self: Any, target_date_iso: str | None = None) -> dict[str, Any]:
    """Daily delta job — beat-scheduled at 08:00 МСК via CELERY_BEAT_SCHEDULE.

    Args:
      target_date_iso: optional override (``"2026-05-13"``). When None,
        the task picks **yesterday in UTC** so the 08:00 МСК run
        consumes the freshly-deposited CSV.

    Returns:
      Per-tenant summary dict — useful for unit tests and for the
      Telegram digest step.
    """
    from apps.observability.models import ShadowDeltaSnapshot
    from apps.tenancy.models import Tenant

    target_date = _resolve_target_date(target_date_iso)
    summaries: dict[str, DeltaSummary] = {}

    for tenant in Tenant.objects.filter(shadow_mode=True):
        try:
            summary = compute_daily_delta(target_date, tenant)
        except Exception:  # noqa: BLE001 — task must not bring down beat
            logger.exception(
                "observability.shadow_delta.failed tenant=%s date=%s",
                tenant.id,
                target_date,
            )
            continue

        ShadowDeltaSnapshot.objects.update_or_create(
            tenant=tenant,
            snapshot_date=target_date,
            defaults={
                "intent_agreement": summary.intent_agreement,
                "action_type_agreement": summary.action_type_agreement,
                "sample_count": summary.sample_count,
                "payload": summary.as_payload(),
            },
        )
        summaries[tenant.slug] = summary

        write_audit(
            "observability.shadow_delta.computed",
            target="Tenant",
            target_id=tenant.id,
            payload={
                "snapshot_date": target_date.isoformat(),
                "intent_agreement": summary.intent_agreement,
                "sample_count": summary.sample_count,
            },
        )

    _send_telegram_digest(target_date, summaries)
    return {
        "snapshot_date": target_date.isoformat(),
        "tenants_processed": len(summaries),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_target_date(target_date_iso: str | None) -> _dt.date:
    if target_date_iso:
        return _dt.date.fromisoformat(target_date_iso)
    return (_dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(days=1)).date()


def _send_telegram_digest(target_date: _dt.date, summaries: dict[str, DeltaSummary]) -> None:
    """Post the headline digest to the admin chat when configured.

    No-op (with INFO log) when credentials missing — local dev never
    pages anyone accidentally.
    """
    chat_id = str(getattr(settings, "ADMIN_MAX_CHAT_ID", "") or "")
    token = str(getattr(settings, "MAX_BOT_TOKEN", "") or "")
    if not chat_id or not token or not summaries:
        logger.info(
            "observability.shadow_delta.digest_skipped reason=%s tenants=%d",
            "no_credentials" if not (chat_id and token) else "no_data",
            len(summaries),
        )
        return

    lines = [f"📊 Shadow delta {target_date.isoformat()}"]
    for slug, summary in sorted(summaries.items()):
        emoji = _emoji_for(summary.intent_agreement)
        lines.append(
            f"{emoji} {slug}: intent {summary.intent_agreement:.0%} "
            f"({summary.sample_count} samples)"
        )

    try:
        _post_to_max(token, chat_id, "\n".join(lines))
    except Exception:  # noqa: BLE001 — digest is observability; never raise
        logger.exception("observability.shadow_delta.digest_send_failed")


def _emoji_for(agreement: float) -> str:
    if agreement >= AGREEMENT_THRESHOLD_GREEN:
        return "✅"
    if agreement >= AGREEMENT_THRESHOLD_AMBER:
        return "⚠"
    return "🚨"


# ---------------------------------------------------------------------------
# F2 (DRF-731) — STRICT_TENANT_SCOPE post-flip violation monitor
# ---------------------------------------------------------------------------


_POST_FLIP_WINDOW_HOURS = 24
_POST_FLIP_RECHECK_SECONDS = 900  # 15 min


@shared_task(  # type: ignore[misc]
    name="apps.observability.tasks.monitor_post_flip_violations",
    bind=True,
)
def monitor_post_flip_violations(self: Any) -> dict[str, Any]:
    """Watch for tenant_scope_violation audit rows after F1 flip.

    Sprint 8 / F2 (DRF-731). Activated by setting
    ``STRICT_SCOPE_FLIP_AT=<ISO 8601 datetime>`` in the prod env at
    the F1 flip moment. The task:

    1. Reads ``settings.STRICT_SCOPE_FLIP_AT``. Unset OR > 24h old →
       no-op (idle state).
    2. Counts ``AuditLog`` rows with ``action="tenant_scope_violation"``
       and ``created_at >= flip_at``.
    3. Any non-zero count → Telegram alert + Sentry P0 capture +
       ``observability.strict_scope.violation_detected`` audit row.
    4. Self-reschedules ``apply_async(countdown=900)`` (15 min) until
       the 24h window closes.

    After 24h the monitor auto-stops (the conditional in step 1).
    Operators clear ``STRICT_SCOPE_FLIP_AT`` from prod env once the
    runbook gate is confirmed.

    Returns a small status dict — useful for one-shot manual probes
    via ``celery call`` and for the F2 dry-run on staging.
    """
    flip_at_iso = str(getattr(settings, "STRICT_SCOPE_FLIP_AT", "") or "")
    if not flip_at_iso:
        logger.info("observability.post_flip.idle reason=no_flip_at")
        return {"status": "idle", "reason": "no_flip_at"}

    try:
        flip_at = _dt.datetime.fromisoformat(flip_at_iso.replace("Z", "+00:00"))
        if flip_at.tzinfo is None:
            flip_at = flip_at.replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        logger.warning("observability.post_flip.bad_flip_at value=%r — task is no-op", flip_at_iso)
        return {"status": "error", "reason": "bad_flip_at"}

    now = _dt.datetime.now(tz=_dt.timezone.utc)
    age = now - flip_at
    if age > _dt.timedelta(hours=_POST_FLIP_WINDOW_HOURS):
        logger.info(
            "observability.post_flip.window_closed flip_at=%s age_hours=%.1f",
            flip_at_iso,
            age.total_seconds() / 3600,
        )
        return {"status": "window_closed", "age_hours": age.total_seconds() / 3600}

    # Count violations since flip.
    from apps.audit.models import AuditLog

    violations = AuditLog.all_tenants.filter(
        action="tenancy.scope.cross_tenant_attempt",
        created_at__gte=flip_at,
    ).count()
    # Also count the older naming convention that the auditor may emit —
    # cheaper than rebooting the auditor's vocabulary for one rename.
    legacy_violations = AuditLog.all_tenants.filter(
        action="tenant_scope_violation",
        created_at__gte=flip_at,
    ).count()
    total = violations + legacy_violations

    if total > 0:
        _page_post_flip_violation(total, flip_at)

    # Always log a heartbeat — operators want to see the monitor is alive.
    write_audit(
        "observability.post_flip.checked",
        payload={
            "flip_at": flip_at_iso,
            "violations": total,
            "age_minutes": int(age.total_seconds() / 60),
        },
    )

    # Self-reschedule unless the window already closed by the next tick.
    next_tick_due = age + _dt.timedelta(seconds=_POST_FLIP_RECHECK_SECONDS)
    if next_tick_due < _dt.timedelta(hours=_POST_FLIP_WINDOW_HOURS):
        try:
            monitor_post_flip_violations.apply_async(countdown=_POST_FLIP_RECHECK_SECONDS)
        except Exception:  # noqa: BLE001 — broker outage; monitor still ran this cycle
            logger.exception("observability.post_flip.reschedule_failed")

    return {
        "status": "checked",
        "violations": total,
        "age_minutes": int(age.total_seconds() / 60),
    }


def _page_post_flip_violation(count: int, flip_at: _dt.datetime) -> None:
    """Fan out a violation alert to Telegram + Sentry.

    Both channels are best-effort; failure of either does NOT block the
    other. The audit row written by the parent task is the durable record.
    """
    text = (
        f"🚨 STRICT_TENANT_SCOPE post-flip violation\n"
        f"Flip at: {flip_at.isoformat()}\n"
        f"Violations since flip: {count}\n"
        f"Runbook: docs/runbooks/strict-scope-flip.md — consider rollback."
    )
    write_audit(
        "observability.strict_scope.violation_detected",
        payload={"count": count, "flip_at": flip_at.isoformat()},
    )

    # Telegram via the MAX admin chat (Sprint 2 / E2).
    chat_id = str(getattr(settings, "ADMIN_MAX_CHAT_ID", "") or "")
    token = str(getattr(settings, "MAX_BOT_TOKEN", "") or "")
    if chat_id and token:
        try:
            _post_to_max(token, chat_id, text)
        except Exception:  # noqa: BLE001
            logger.exception("observability.post_flip.telegram_failed")
    else:
        logger.info("observability.post_flip.telegram_skipped reason=no_credentials")

    # Sentry P0 capture.
    try:
        import sentry_sdk

        with sentry_sdk.new_scope() as scope:
            scope.set_tag("alert", "strict_scope_post_flip_violation")
            scope.set_tag("flip_at", flip_at.isoformat())
            scope.set_level("fatal")
            sentry_sdk.capture_message(
                f"STRICT_TENANT_SCOPE post-flip: {count} violations since {flip_at.isoformat()}"
            )
    except ImportError:  # pragma: no cover — sentry-sdk optional
        pass
    except Exception:  # noqa: BLE001
        logger.exception("observability.post_flip.sentry_failed")


def _post_to_max(token: str, chat_id: str, text: str) -> None:
    """Thin MAX-API poke. Isolated for easy test mocking.

    The platform already uses the MAX bot API (Sprint 2 / D2) for
    outbound; we reuse the same endpoint with a low timeout — the
    digest is best-effort.

    Sprint 8 review P2-1: a 4xx (e.g. ``403`` for a banned bot or
    expired token) used to be silently swallowed because the body
    returned successfully — operators just saw missing digests with
    no log entry. We now log the status code + body snippet AND
    raise via ``raise_for_status``; the outer ``except`` in
    :func:`_send_telegram_digest` still catches and logs, so the
    daily task is unaffected, but the failure mode is no longer
    invisible.
    """
    import httpx

    api_base = str(getattr(settings, "MAX_API_BASE", "https://botapi.max.ru"))
    url = f"{api_base}/messages?access_token={token}"
    resp = httpx.post(
        url,
        json={"chat_id": chat_id, "text": text},
        timeout=5.0,
    )
    if resp.status_code >= 400:
        # The MAX response body is short JSON — safe to log up to 200 chars.
        # Token is in the URL query string, NOT in the body, so this is
        # safe wrt secret-leak (the URL itself is never logged).
        logger.warning(
            "observability.shadow_delta.digest_http_%d body=%.200s",
            resp.status_code,
            resp.text,
        )
    resp.raise_for_status()
