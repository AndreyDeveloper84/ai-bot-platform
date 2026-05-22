"""Celery tasks for admin_api (M3-admin follow-up).

PR #521 adversarial blocker #2 — master DM dispatch on approve/reject
previously ran inline inside ``transaction.on_commit``. On any
``MaxAPIError`` (transient 5xx, rate-limit, etc.) the inline hook
logged a warning and silently swallowed the failure. The master never
learned of the decision, and the inline ``send_message`` blocked the
HTTP response.

This task lets the on_commit hook return immediately (only enqueueing)
while the actual outbound MAX call runs in a Celery worker with
configurable retry + dead-letter behaviour. The API response no longer
blocks on MAX latency, and a transient failure is recovered via Celery
retry rather than silently lost.

The eventbus is the «right» place long-term for cross-process domain
events, but the eventbus app's anti-touch zone forbids modifying its
internals during this fix — so we use a defensively-coded Celery task
with bounded retry. If MAX continues failing past the retry budget,
the failure surfaces in Celery's dead-letter queue / structured logs
where ops can intervene (same as every other ``send_message`` caller).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from celery import shared_task  # type: ignore[import-untyped]
from django.conf import settings

logger = logging.getLogger(__name__)


# Bounded retry budget: 3 attempts with exponential backoff capped at
# ~1 minute. Worst case the master learns of the decision ~1 min late
# instead of never. After max retries the failure is logged + counted;
# the DB decision is authoritative.
DM_MAX_RETRIES = 3
DM_RETRY_BACKOFF_SECONDS = 10  # base; doubled per attempt by Celery.


@shared_task(
    name="admin_api.dispatch_master_decision_dm",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=DM_RETRY_BACKOFF_SECONDS,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=DM_MAX_RETRIES,
    acks_late=True,
)
def dispatch_master_decision_dm(
    self: Any,
    *,
    chat_id: str,
    decision: str,
    date_range_human: str,
    request_id: str,
    master_id: str,
    rejection_reason: str = "",
) -> dict[str, Any]:
    """Send the approve/reject DM to the master via MAX.

    Args mirror the inline dispatch helper's needs but are JSON-safe
    primitives (Celery serialises the kwargs). The task is invoked from
    :mod:`apps.admin_api.services.availability` via
    ``transaction.on_commit(lambda: dispatch_master_decision_dm.delay(...))``
    so the API response returns BEFORE the outbound MAX call.

    Returns: ``{"sent": True}`` on success, otherwise raises (Celery
    will retry up to :data:`DM_MAX_RETRIES`).

    Args:
      chat_id: target MAX chat id (master's ``linked_bot_user.chat_id``).
      decision: ``"approved"`` or ``"rejected"`` — picks the message text.
      date_range_human: pre-formatted Russian date / range string.
      request_id: ScheduleChangeRequest UUID (logging context only).
      master_id: CatalogMaster UUID (logging context only).
      rejection_reason: required when ``decision == "rejected"``.
    """

    # Local import — channels is heavy and only needed in the worker.
    from apps.channels.max.outbound import MaxAPIError, send_message

    master_mini_app_url = getattr(
        settings,
        "MASTER_MINI_APP_URL",
        "https://master.formulatela.ru/schedule",
    )

    if decision == "approved":
        text = (
            f"Ваш запрос на смену расписания на {date_range_human} одобрен. Готово. "
            f"[Открыть расписание]({master_mini_app_url})"
        )
    elif decision == "rejected":
        text = (
            f"Запрос на смену расписания на {date_range_human} отклонён. "
            f"Причина: {rejection_reason}. Спросите у Карины уточнить."
        )
    else:
        logger.error(
            "admin_api.tasks.dispatch_master_decision_dm.invalid_decision "
            "decision=%s request_id=%s",
            decision,
            request_id,
        )
        return {"sent": False, "reason": "invalid_decision"}

    chat_id_norm = (chat_id or "").strip()
    if not chat_id_norm:
        logger.info(
            "admin_api.tasks.dispatch_master_decision_dm.no_chat_id master=%s request=%s",
            master_id,
            request_id,
        )
        return {"sent": False, "reason": "no_chat_id"}

    try:
        send_message(chat_id=chat_id_norm, text=text)
    except MaxAPIError:
        logger.warning(
            "admin_api.tasks.dispatch_master_decision_dm.failed "
            "master=%s request=%s decision=%s attempt=%s",
            master_id,
            request_id,
            decision,
            getattr(self.request, "retries", 0),
            exc_info=True,
        )
        # Re-raise so Celery applies autoretry; after DM_MAX_RETRIES
        # the task moves to the dead-letter / failed-task path where
        # ops can observe.
        raise

    return {"sent": True}


def _safely_validate_uuid(value: UUID | str) -> str:
    """Coerce UUID-or-string to plain str for Celery JSON payload."""

    if isinstance(value, UUID):
        return str(value)
    return str(value)


__all__ = [
    "DM_MAX_RETRIES",
    "DM_RETRY_BACKOFF_SECONDS",
    "dispatch_master_decision_dm",
]
