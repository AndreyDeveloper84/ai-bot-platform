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


def _post_to_max(token: str, chat_id: str, text: str) -> None:
    """Thin MAX-API poke. Isolated for easy test mocking.

    The platform already uses the MAX bot API (Sprint 2 / D2) for
    outbound; we reuse the same endpoint with a low timeout — the
    digest is best-effort.
    """
    import httpx

    api_base = str(getattr(settings, "MAX_API_BASE", "https://botapi.max.ru"))
    url = f"{api_base}/messages?access_token={token}"
    httpx.post(
        url,
        json={"chat_id": chat_id, "text": text},
        timeout=5.0,
    )
