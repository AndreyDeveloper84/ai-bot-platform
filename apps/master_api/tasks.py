"""Celery tasks for master_api (M6 AI drafts auto-trigger).

Deferred follow-up from PR #535 / #540 (manual «✨ Предложить ответ»):
spec §M6 line 660 mandates a «— помощник готовит ответ —» indicator on
inbound, i.e. the AI draft must generate AUTOMATICALLY when a customer
message arrives, not only when the master taps Generate.

### Why a Celery task (not inline in record_message)

* The LLM call is 1-3s on the happy path. Running inline on the webhook
  hot path would block the channel ingress handler and double its p95.
* Cost guards (per-master $5/day cap, 10/min rate limit) MUST run
  BEFORE the LLM bill — and they live in the master_api service layer.
  We can either pull that layer into the conversations app (couples
  AI/observability into the canonical write path) or keep the canonical
  write path lean and dispatch a Celery task. Task wins.
* §H.3 territory: paid external LLM + concurrent webhook flow + flag-
  gated. The task lets us short-circuit on the feature flag in the
  worker BEFORE any LLM call so a flag-off deploy can't bill.

### Pre-LLM short-circuits (cheap, in this file)

The task body re-checks all the pre-conditions because between hook
enqueue and worker pickup a few seconds may have passed:

1. ``settings.AI_DRAFTS_AUTO_TRIGGER_ENABLED`` is True — env-flip-safe
   even if hook fired before a flag flip.
2. Conversation still exists + tier != HUMAN_LOCKED — the spec refuses
   draft generation on locked conversations (master can't send anyway).
3. Conversation involves a master — replicates
   :func:`apps.master_api.services.conversation_detail._verify_master_involved`
   so we don't waste an LLM call on customer ↔ generic-bot threads
   (welcome flow, FAQ, etc.).
4. Staleness check — if a NEWER customer message arrived on the
   conversation since enqueue, this task's draft would be obsolete the
   moment it lands. Skip; the newer message's task will produce the
   up-to-date draft.
5. Per-conversation debounce — ``SETNX auto_draft_conv:<conv_id> 1 EX
   15`` via ``cache.add``. If two customer messages arrive within 15s
   (rapid typing), only the first survives the gate. Both messages
   land in DB; only one LLM call. Rationale: 15s covers «client typed
   3 follow-ups» without holding back legit single messages.

### LLM error handling

We REUSE :func:`generate_draft_for_conversation` — no re-implementation
of the LLM call. That service converts every LLMError-family failure
to :class:`DraftActionError` with slug ``llm_unavailable``. The task
catches DraftActionError and:

* For slugs in :data:`_NON_RETRY_SLUGS` (rate / cost / locked / 404 /
  stale / already_acted): INFO log + return. Master is hitting their
  own quota or operating on a stale state — not a system failure, no
  retry.
* For ``llm_unavailable``: re-raise as :class:`LLMError` so Celery's
  ``autoretry_for=(LLMError,)`` applies. Bounded retry (3 attempts,
  exponential backoff capped at 60s).

Other Exceptions (DB connection drop, broker hiccup, programming
errors) flow to Celery's default failure path WITHOUT auto-retry —
prevents duplicate LLM calls under ambiguous failures (Surface #2
pattern from PR #539).

### PII / Result expiry

Task kwargs are JSON-safe primitives (conversation/master/message
UUIDs as strings). NO message content travels in the broker payload —
the worker re-reads it from DB. ``result_expires=3600`` bounds how
long failure metadata can linger in the result backend, mirroring
the PR #539 Surface #3 pattern.

### Idempotency with the manual path

The manual «✨ Предложить ответ» view path calls the same
:func:`generate_draft_for_conversation`. The 60s idempotency window
inside that function means an auto-task that ran 30s ago + manual tap
returns the SAME draft (no second LLM call). Concurrent auto-task +
manual tap serialise via the per-Conversation ``select_for_update``
lock (PR #535 Blocker #1). Net: at most one paid LLM call per (master,
conversation, trigger_message) within 60s.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from celery import shared_task  # type: ignore[import-untyped]
from django.conf import settings
from django.core.cache import cache

from apps.llm.protocol import LLMError

logger = logging.getLogger(__name__)


# Per-conversation debounce window. Picked at 15s on the rationale that
# legit single-message customer turns are sub-second; rapid-fire typing
# («привет», «можно записаться?», «на завтра») arrives within 1-10s
# bursts. 15s is enough to coalesce a 3-message burst, short enough that
# a deliberate follow-up 30s later DOES trigger a fresh draft.
AUTO_DRAFT_DEBOUNCE_SECONDS = 15

# Bounded retry budget. The LLM call inside
# :func:`generate_draft_for_conversation` is already 1-3s; auto-task
# isn't on a user-blocking path so we can afford a few retries before
# giving up. Same shape as admin_api.tasks.dispatch_master_decision_dm.
AUTO_DRAFT_MAX_RETRIES = 3
AUTO_DRAFT_RETRY_BACKOFF_SECONDS = 10

# Per-task result expiry (PII safety per PR #539 Surface #3). Task
# kwargs don't carry message body, but the result backend stores
# exception reprs etc; 1h is a debug-friendly upper bound.
AUTO_DRAFT_TASK_RESULT_EXPIRES_S = 3600


# Slugs from :class:`DraftActionError` that represent a legitimate
# refusal — not a system failure. Treated as INFO + no retry.
#
# ``generate_in_flight`` (issue #550) belongs on this list: another
# concurrent generate (master tap OR another auto-task triggered by a
# rapid-fire follow-up customer message) is already holding the
# Conversation row lock. The holder will produce a draft; retrying
# this task would either re-collide on the lock OR succeed but
# duplicate-bill a draft for a now-superseded trigger. The per-
# conversation debounce (``cache.add(... TTL=15s)``) earlier in the
# task already de-duplicates most of these — generate_in_flight is the
# narrow race where the debounce key got cleaned up (legitimate
# refusal cleanup) BUT a parallel master tap re-acquired the lock
# before this task could.
_NON_RETRY_SLUGS: frozenset[str] = frozenset(
    {
        "rate_limit_exceeded",
        "cost_cap_exceeded",
        "conversation_locked",
        "not_found",
        "draft_already_acted",
        "draft_stale",
        "bad_request",
        "tier_locked",
        "generate_in_flight",
        # Issue #551: send/release lock-symmetry slug. The auto-trigger
        # task only ever calls ``generate_draft_for_conversation`` (not
        # send/release), so this entry is defensive — if a future task
        # bridges to send/release, the same «don't retry, the holder
        # will finish» rationale applies.
        "conversation_busy",
    }
)


def _debounce_key(conversation_id: str) -> str:
    """SETNX key for per-conversation auto-draft debounce."""

    return f"auto_draft_conv:{conversation_id}"


@shared_task(
    name="apps.master_api.tasks.auto_generate_draft_for_inbound",
    bind=True,
    # Narrow ``autoretry_for`` to ``LLMError`` only. Other exceptions
    # (cache miss, DB drop, programming errors) reach Celery's default
    # failure path WITHOUT retry — no risk of duplicate LLM calls under
    # ambiguous failures. ``LLMError`` is the canonical LLM-side
    # failure (transport / quota / provider unavailable) and is
    # explicitly re-raised by this task when the inner
    # :class:`DraftActionError` carries ``slug=="llm_unavailable"``.
    autoretry_for=(LLMError,),
    retry_backoff=AUTO_DRAFT_RETRY_BACKOFF_SECONDS,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=AUTO_DRAFT_MAX_RETRIES,
    acks_late=True,
    result_expires=AUTO_DRAFT_TASK_RESULT_EXPIRES_S,
)
def auto_generate_draft_for_inbound(
    self: Any,
    *,
    conversation_id: str,
    trigger_message_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    """Auto-generate an AiDraft when a customer message lands.

    Enqueued via ``transaction.on_commit`` from
    :func:`apps.conversations.services.record_message` whenever a
    Message with ``role=USER`` is persisted. All conditions are
    re-checked HERE (the worker may run seconds after enqueue):

    1. Feature flag :data:`settings.AI_DRAFTS_AUTO_TRIGGER_ENABLED`
       is True.
    2. Conversation tier != HUMAN_LOCKED.
    3. Conversation involves a master (CatalogMaster linked via
       :class:`apps.booking.models.BookingRequest` to the
       conversation's ``bot_user``).
    4. ``trigger_message_id`` is still the latest USER message — if a
       newer customer message arrived between enqueue and pickup, that
       newer message's task will handle the fresh draft.
    5. Per-conversation debounce: ``SETNX`` on
       ``auto_draft_conv:<conv_id>`` with TTL
       :data:`AUTO_DRAFT_DEBOUNCE_SECONDS`. If another auto-trigger
       fired within the window, skip (rapid-fire customer typing
       coalesces to a single draft).

    If all pass → delegates to
    :func:`apps.master_api.services.ai_drafts.generate_draft_for_conversation`
    with ``is_auto=True`` (audit + analytics emit the dedicated
    :data:`MASTER_AI_DRAFT_AUTO_GENERATED` slug).

    Returns a small status dict for observability; never raises on the
    happy path or on legitimate refusals (rate / cost / locked /
    stale). LLM-side failures are re-raised as :class:`LLMError` so
    Celery's autoretry fires.

    Args:
      conversation_id: target Conversation UUID (string).
      trigger_message_id: the USER Message UUID that triggered this
        task. Used for staleness check.
      tenant_id: tenant UUID (string) — required for tenant-scoped
        lookups via ``all_tenants`` (master_api views run outside
        ``tenant_scope``, mirroring the manual path).
    """

    if not getattr(settings, "AI_DRAFTS_AUTO_TRIGGER_ENABLED", False):
        # Flag off — short-circuit BEFORE any DB lookup. Cheapest
        # possible path so a flag-off deploy can re-enable safely.
        logger.debug(
            "master_api.tasks.auto_draft.flag_off conv=%s trigger=%s",
            conversation_id,
            trigger_message_id,
        )
        return {"generated": False, "reason": "flag_off"}

    # Local imports — the master_api service layer is heavy (catalog,
    # booking, LLM router). Keeping them out of module-load avoids a
    # circular at app startup and trims the producer-side import graph.
    from apps.booking.models import BookingRequest
    from apps.conversations.models import Conversation, Message
    from apps.master_api.services.ai_drafts import (
        DraftActionError,
        generate_draft_for_conversation,
    )

    # Validate UUIDs up front — a bad caller arg shouldn't crash the
    # worker (which would then retry against a permanently-broken
    # payload).
    try:
        conv_uuid = uuid.UUID(conversation_id)
        trigger_uuid = uuid.UUID(trigger_message_id)
        tenant_uuid = uuid.UUID(tenant_id)
    except (TypeError, ValueError):
        logger.warning(
            "master_api.tasks.auto_draft.bad_uuid conv=%s trigger=%s tenant=%s",
            conversation_id,
            trigger_message_id,
            tenant_id,
        )
        return {"generated": False, "reason": "bad_uuid"}

    # Step 2: conversation exists + not human-locked.
    conv = (
        Conversation.all_tenants.filter(
            id=conv_uuid,
            tenant_id=tenant_uuid,
            deleted_at__isnull=True,
            is_shadow=False,
            bot_user__isnull=False,
        )
        .select_related("bot_user")
        .first()
    )
    if conv is None:
        logger.info(
            "master_api.tasks.auto_draft.conversation_missing conv=%s tenant=%s",
            conversation_id,
            tenant_id,
        )
        return {"generated": False, "reason": "conversation_missing"}

    if conv.tier == Conversation.Tier.HUMAN_LOCKED:
        logger.info(
            "master_api.tasks.auto_draft.human_locked conv=%s",
            conversation_id,
        )
        return {"generated": False, "reason": "human_locked"}

    # Step 3: master involvement. Find the master linked to this
    # conversation's bot_user via the booking history — same predicate
    # as :func:`_verify_master_involved`. We pick the most-recent
    # booking's master as the «owning» master for this conversation
    # (a customer who has booked with multiple masters could in theory
    # have drafts for each; out of MVP scope — first wins).
    from apps.catalog.models import CatalogMaster

    master_id = (
        BookingRequest.all_tenants.filter(
            tenant_id=tenant_uuid,
            bot_user_id=conv.bot_user_id,
        )
        .order_by("-created_at")
        .values_list("master_id", flat=True)
        .first()
    )
    if master_id is None:
        logger.info(
            "master_api.tasks.auto_draft.no_master conv=%s",
            conversation_id,
        )
        return {"generated": False, "reason": "no_master"}

    master = (
        CatalogMaster.all_tenants.filter(
            id=master_id,
            tenant_id=tenant_uuid,
        )
        .select_related("tenant")
        .first()
    )
    if master is None or not master.is_active:
        logger.info(
            "master_api.tasks.auto_draft.master_inactive conv=%s master=%s",
            conversation_id,
            master_id,
        )
        return {"generated": False, "reason": "master_inactive"}

    # Step 4: staleness check — is the trigger_message_id still the
    # latest USER message? If a newer customer message arrived, its
    # own task will produce the fresh draft.
    latest_user_msg_id = (
        Message.all_tenants.filter(
            conversation_id=conv_uuid,
            role=Message.Role.USER,
        )
        .order_by("-created_at")
        .values_list("id", flat=True)
        .first()
    )
    if latest_user_msg_id != trigger_uuid:
        logger.info(
            "master_api.tasks.auto_draft.stale conv=%s trigger=%s latest=%s",
            conversation_id,
            trigger_message_id,
            latest_user_msg_id,
        )
        return {"generated": False, "reason": "stale_trigger"}

    # Step 5: per-conversation debounce — atomic SETNX via
    # ``cache.add``. Returns False if the key already exists.
    # ``cache.add`` is atomic on Redis; on locmem it's process-local
    # which is fine for tests.
    lock_key = _debounce_key(conversation_id)
    if not cache.add(lock_key, "1", timeout=AUTO_DRAFT_DEBOUNCE_SECONDS):
        logger.info(
            "master_api.tasks.auto_draft.debounced conv=%s trigger=%s",
            conversation_id,
            trigger_message_id,
        )
        return {"generated": False, "reason": "debounced"}

    # All gates passed — delegate to the existing generate service.
    # The service has its own Conversation row lock + idempotency
    # window + cost cap. ``actor_bot_user=None`` because the auto path
    # has no human actor (the audit row's ``actor_id`` field stays
    # null, which is the contract for system-initiated actions).
    #
    # Adversarial-review fix: wrap in ``tenant_scope(master.tenant)``
    # so :func:`apps.audit.services.write_audit` reads the correct
    # tenant from the ContextVar.  Without this the audit row would
    # land with ``tenant=NULL`` (the worker has no ambient scope),
    # breaking tenant-scoped audit search + leaving the «who
    # generated this draft» trail unattributable.  Matches what the
    # master_api view middleware (``apps/master_api/auth.py``) does
    # on the manual path.
    from apps.tenancy.context import tenant_scope

    try:
        with tenant_scope(master.tenant):
            response = generate_draft_for_conversation(
                conversation_id=conv_uuid,
                master=master,
                actor_bot_user=None,
                is_auto=True,
            )
    except DraftActionError as exc:
        slug = getattr(exc, "slug", "")
        if slug in _NON_RETRY_SLUGS:
            # Legitimate refusal — quota / state — not a system failure.
            # Release the debounce key so the NEXT customer message
            # (after the 15s window) can trigger again. Without this
            # release a rate-limited master would silently lose the
            # NEXT message's auto-draft too, until the lock TTL'd out.
            # The 15s TTL is short, so the practical leak is small —
            # but explicit cleanup makes intent clear.
            try:
                cache.delete(lock_key)
            except Exception:  # pragma: no cover — cache defensive
                pass
            logger.info(
                "master_api.tasks.auto_draft.refused conv=%s master=%s slug=%s",
                conversation_id,
                master.id,
                slug,
            )
            return {"generated": False, "reason": slug}

        if slug == "llm_unavailable":
            # Release the lock so a Celery retry (which will hit a
            # fresh task body) doesn't get debounced by its own
            # predecessor's leftover key. The 15s TTL would expire
            # anyway, but explicit release means retries land on a
            # clean slate.
            try:
                cache.delete(lock_key)
            except Exception:  # pragma: no cover — cache defensive
                pass
            logger.warning(
                "master_api.tasks.auto_draft.llm_unavailable conv=%s master=%s attempt=%s",
                conversation_id,
                master.id,
                getattr(self.request, "retries", 0),
            )
            # Re-raise as LLMError so Celery's autoretry_for fires.
            # We don't expose DraftActionError to autoretry_for because
            # it's also raised on non-retryable refusals (rate / cost
            # / stale) — those would otherwise spin retries pointlessly.
            raise LLMError(f"draft generation failed: {exc.detail}") from exc

        # Unknown slug — log + don't retry. Better to skip than spin.
        try:
            cache.delete(lock_key)
        except Exception:  # pragma: no cover — cache defensive
            pass
        logger.warning(
            "master_api.tasks.auto_draft.unknown_slug conv=%s master=%s slug=%s",
            conversation_id,
            master.id,
            slug,
        )
        return {"generated": False, "reason": f"unknown_slug:{slug}"}

    logger.info(
        "master_api.tasks.auto_draft.generated conv=%s master=%s draft=%s",
        conversation_id,
        master.id,
        response.draft_id,
    )
    return {"generated": True, "draft_id": response.draft_id}


__all__ = [
    "AUTO_DRAFT_DEBOUNCE_SECONDS",
    "AUTO_DRAFT_MAX_RETRIES",
    "AUTO_DRAFT_RETRY_BACKOFF_SECONDS",
    "AUTO_DRAFT_TASK_RESULT_EXPIRES_S",
    "auto_generate_draft_for_inbound",
]
