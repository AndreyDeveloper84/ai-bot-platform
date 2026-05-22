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

PR #539 amendments (tech-lead APPROVE-WITH-ONE-BLOCKER):

* **Surface #3 (PII in Celery broker, HARD BLOCKER)** — admin-typed
  rejection_reason may carry free-text PII (master full name +
  phone). Task kwargs serialise to JSON for the Redis broker payload
  and sit there until worker pickup; result backend is now bounded
  via per-task ``result_expires=3600`` so dropped failures don't
  linger forever (ADR-0011 §3.4). The error-side logging is structured-
  only (``{decision, request_id, attempt}``) and never renders the
  outbound MAX ``text`` field. ``exc_info=True`` is retained on the
  Python traceback but the message string is PII-free.

* **Surface #2 (duplicate DM under broker hiccup, OPTIONAL pre-pilot)** —
  ``acks_late=True`` + ``autoretry_for=Exception`` allowed a worker
  death between ``send_message`` returning and Celery acking the
  task to cause a second worker to re-run + re-deliver the DM. Fix:
  narrow ``autoretry_for`` to ``MaxAPIError`` only (other exceptions
  reach Celery's default failure path without auto-retry), and add a
  per-(request_id, decision) SETNX lock via ``cache.add`` with TTL
  24h. Re-runs see the lock set and skip the send. Approve/reject
  for the same request_id are different DMs → different lock keys.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from celery import shared_task  # type: ignore[import-untyped]
from django.conf import settings

from apps.channels.max.outbound import MaxAPIError

logger = logging.getLogger(__name__)


# Bounded retry budget: 3 attempts with exponential backoff capped at
# ~1 minute. Worst case the master learns of the decision ~1 min late
# instead of never. After max retries the failure is logged + counted;
# the DB decision is authoritative.
DM_MAX_RETRIES = 3
DM_RETRY_BACKOFF_SECONDS = 10  # base; doubled per attempt by Celery.

# Idempotency lock TTL — 24h. Approve/reject is a one-shot event per
# request; a re-fire 24h later is implausible and would be operationally
# observable (request long since decided). The lock value «1» carries
# no PII so it is safe to keep in Redis at full TTL.
DM_IDEMPOTENCY_LOCK_TTL_S = 60 * 60 * 24

# Per-task result expiry — Surface #3 fix. The default
# ``CELERY_RESULT_EXPIRES`` is unset (1 day default in Celery itself),
# but task kwargs / metadata that may carry PII (rejection_reason)
# must not linger beyond an operationally reasonable window.
# 3600s = 1h: enough to debug a recent worker error, short enough
# that 152-ФЗ retention obligations don't apply to a transient
# in-broker payload.
DM_TASK_RESULT_EXPIRES_S = 3600


def _idempotency_lock_key(*, request_id: str, decision: str) -> str:
    """SETNX key for per-(request_id, decision) DM dedup.

    Both decision kinds for the same request must be allowed through
    (approve and reject carry different bodies + intents), so the
    key includes ``decision`` to keep their locks distinct.
    """

    return f"master_dm_sent:{request_id}:{decision}"


@shared_task(
    name="admin_api.dispatch_master_decision_dm",
    bind=True,
    # Surface #2: narrowed from ``(Exception,)`` to ``(MaxAPIError,)``.
    # Only the MAX upstream's transient failures warrant retry. Other
    # exceptions (programming errors, broker / serialiser issues,
    # signals like SoftTimeLimitExceeded) flow to Celery's standard
    # failure path WITHOUT auto-retry → no risk of duplicate DM on
    # ambiguous failures. ``MaxAPIError`` is imported at module level
    # (lightweight: ``apps.channels.max.outbound`` only pulls httpx +
    # django.conf), since ``@shared_task`` evaluates ``autoretry_for``
    # at decoration time and wraps the task body accordingly.
    autoretry_for=(MaxAPIError,),
    retry_backoff=DM_RETRY_BACKOFF_SECONDS,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=DM_MAX_RETRIES,
    acks_late=True,
    result_expires=DM_TASK_RESULT_EXPIRES_S,
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
    will retry up to :data:`DM_MAX_RETRIES`) — but only on
    :class:`MaxAPIError`. Other exceptions surface to Celery's default
    failure path without auto-retry (Surface #2: prevents duplicate DM
    on ambiguous worker-death failures).

    Idempotency: a SETNX lock keyed on ``(request_id, decision)`` with
    TTL ``DM_IDEMPOTENCY_LOCK_TTL_S`` guarantees at-most-once delivery
    even if the broker re-delivers the task post-send-pre-ack.

    PII handling (PR #539, Surface #3): ``rejection_reason`` is capped
    at 500 chars by upstream validator (see
    :mod:`apps.admin_api.services.availability` reject path) and is
    *never* rendered into log lines — retry/error logging emits only
    ``{decision, request_id, attempt}``. Task kwargs (which serialise
    to the Celery broker JSON payload) carry the reason as-typed for
    the few seconds → minutes between enqueue and worker pickup; the
    per-task ``result_expires=3600`` bounds how long any failure
    metadata can linger in the result backend.

    Args:
      chat_id: target MAX chat id (master's ``linked_bot_user.chat_id``).
      decision: ``"approved"`` or ``"rejected"`` — picks the message text.
      date_range_human: pre-formatted Russian date / range string.
      request_id: ScheduleChangeRequest UUID (logging context only).
      master_id: CatalogMaster UUID (logging context only).
      rejection_reason: required when ``decision == "rejected"``.
        Capped at 500 chars by upstream validator + truncated in logs
        (no rendered text in retry/error logging).
    """

    # Local import — channels' ``send_message`` only needed in the
    # worker path. ``MaxAPIError`` is imported at module level for
    # ``autoretry_for`` binding (see decorator above).
    from apps.channels.max.outbound import send_message
    from django.core.cache import cache

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
        # Structured-only log: no rendered text, no rejection_reason.
        logger.error(
            "admin_api.tasks.dispatch_master_decision_dm.invalid_decision "
            "decision=%s request_id=%s",
            decision,
            request_id,
        )
        return {"sent": False, "reason": "invalid_decision"}

    chat_id_norm = (chat_id or "").strip()
    if not chat_id_norm:
        # Structured-only log: master / request ids, no text.
        logger.info(
            "admin_api.tasks.dispatch_master_decision_dm.no_chat_id master=%s request=%s",
            master_id,
            request_id,
        )
        return {"sent": False, "reason": "no_chat_id"}

    # Surface #2: SETNX idempotency lock. ``cache.add`` returns False
    # if the key already exists — atomic check-and-set so two workers
    # can't both pass through this gate. Same pattern used in
    # apps/orchestrator/pipeline.py (_send_retry_exhausted_alert) and
    # apps/observability/alerting.py.
    lock_key = _idempotency_lock_key(request_id=request_id, decision=decision)
    if not cache.add(lock_key, "1", timeout=DM_IDEMPOTENCY_LOCK_TTL_S):
        # Already-sent (or in-flight from a sibling worker re-fire).
        # Structured-only log: ids + decision + attempt, no rendered
        # text, no rejection_reason.
        logger.info(
            "admin_api.tasks.dispatch_master_decision_dm.idempotent_skip "
            "master=%s request=%s decision=%s attempt=%s",
            master_id,
            request_id,
            decision,
            getattr(self.request, "retries", 0),
        )
        return {"sent": False, "reason": "already_sent"}

    try:
        send_message(chat_id=chat_id_norm, text=text)
    except MaxAPIError:
        # Surface #3: structured-only log — no rendered text, no
        # rejection_reason. ``exc_info=True`` retains the traceback
        # for debugging (the traceback's frame locals may include
        # ``text`` / ``rejection_reason`` if Python's default
        # ``logging.Formatter`` is later swapped for one that walks
        # frames — but the default stringification path used by
        # ``logger.warning(..., exc_info=True)`` only renders the
        # exception's ``__str__``, which for ``MaxAPIError`` is
        # status + truncated response body. No customer free-text.
        logger.warning(
            "admin_api.tasks.dispatch_master_decision_dm.failed "
            "master=%s request=%s decision=%s attempt=%s",
            master_id,
            request_id,
            decision,
            getattr(self.request, "retries", 0),
            exc_info=True,
        )
        # Release the idempotency lock so a successful retry can
        # actually send. Without this, the first failed attempt would
        # block every subsequent retry from delivering the DM at all.
        # The lock's purpose is dedup of *successful* sends, not
        # poisoning the retry chain.
        try:
            cache.delete(lock_key)
        except Exception:  # pragma: no cover — cache backend defensive
            logger.debug(
                "admin_api.tasks.dispatch_master_decision_dm.lock_release_failed "
                "request=%s decision=%s",
                request_id,
                decision,
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
    "DM_IDEMPOTENCY_LOCK_TTL_S",
    "DM_MAX_RETRIES",
    "DM_RETRY_BACKOFF_SECONDS",
    "DM_TASK_RESULT_EXPIRES_S",
    "dispatch_master_decision_dm",
]
