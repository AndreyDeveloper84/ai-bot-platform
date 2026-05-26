"""Payment.* event consumers (#443).

Per ``docs/architecture/event-contract.md`` §3.5–§3.8. Four handlers
for the payment lifecycle:

* ``payment.authorized`` — set ``Conversation.pending_payment_id``.
* ``payment.captured`` — clear pending slot, set captured timestamp,
  reset consecutive_payment_failures counter, emit internal
  ``loyalty_bonus_eligible``.
* ``payment.failed`` — set failed timestamp + enum-mapped failure_code,
  increment consecutive_payment_failures, conditionally invoke
  :func:`apps.skills.payment_failed.skill.on_payment_failed_event`
  once the threshold trips (#738, founder verdict 2026-05-26).
* ``payment.refunded`` — set refunded timestamp, emit internal
  ``loyalty_refund_reverse``.

### Hard rules

* **ADR-0009 Rule #5** — bot-platform NEVER writes a Payment row.
  This consumer only mirrors event-derived state into
  ``Conversation`` (a bot-platform domain) and emits internal
  events for downstream subscribers (loyalty ledger lives in
  ``apps/loyalty``; payment skill lands in a separate PR).

* **PII rule §7** — failure reason is mapped to a closed enum via
  :func:`_map_yookassa_failure_code` before being persisted.
  Free-text YooKassa messages may contain card-tail PII; we never
  store them locally.

* **Canonical handler shape** (locked through PR #623, see
  ``apps/eventbus/consumers/__init__.py``): tenant guard first,
  idempotency short-circuit second, side-effects under
  dispatcher's ``transaction.atomic``, atomic upserts via
  ``get_or_create`` where applicable.

### Handler registration

Module-level :func:`register_payment_handlers` — called from
``apps/eventbus/apps.py::EventBusConfig.ready`` so the registry
shape is deterministic at every Django start.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Final
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction

from apps.conversations.models import Conversation
from apps.events.services import emit as emit_internal_event
from apps.eventbus.ingest_dispatcher import register
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.ingest_tenancy import assert_envelope_tenant_authorized
from apps.eventbus.models import PaymentTerminalDedupe
from apps.tenancy.models import Tenant


logger = logging.getLogger(__name__)


# ─── YooKassa code mapping (PII protection) ────────────────────────────────


# Closed enum of failure codes we persist on Conversation. Anything
# YooKassa returns outside this set maps to ``"other"`` — we never
# write the provider's free-text into our DB (it may contain card-tail
# PII).
class FailureCode:
    INSUFFICIENT_FUNDS = "insufficient_funds"
    CARD_EXPIRED = "card_expired"
    CARD_DECLINED = "card_declined"
    HOLD_EXPIRED = "hold_expired"
    PROVIDER_ERROR = "provider_error"
    CANCELLED_BY_USER = "cancelled_by_user"
    OTHER = "other"


# Mapping from the contract-defined ``reason`` enum (event-contract.md
# §3.7) to our local Conversation enum. The contract enum already
# matches our internal vocabulary in most cases — the helper exists so
# unknown values fail closed to ``OTHER`` rather than propagating.
_REASON_MAP: Final[dict[str, str]] = {
    "card_declined": FailureCode.CARD_DECLINED,
    "insufficient_funds": FailureCode.INSUFFICIENT_FUNDS,
    "hold_expired": FailureCode.HOLD_EXPIRED,
    "provider_error": FailureCode.PROVIDER_ERROR,
    "cancelled_by_user": FailureCode.CANCELLED_BY_USER,
    # YooKassa-specific codes the contract may surface in future
    # event versions. Keep the mapping permissive but pinned.
    "expired_card": FailureCode.CARD_EXPIRED,
    "card_expired": FailureCode.CARD_EXPIRED,
}


def _map_yookassa_failure_code(reason: str | None) -> str:
    """Map a contract ``reason`` value to our enum, fail-closed to OTHER.

    Per Round-5 design (PII protection): unknown / null / unexpected
    free-text reasons MUST NOT propagate to ``Conversation`` —
    YooKassa error messages can contain card-tail digits or other
    PII. The closed enum guarantees nothing leaks.
    """
    if not reason:
        return FailureCode.OTHER
    return _REASON_MAP.get(reason.strip().lower(), FailureCode.OTHER)


# ─── helpers ───────────────────────────────────────────────────────────────


def _parse_iso(value: str) -> dt.datetime:
    """Parse the envelope's ISO8601 timestamp into a tz-aware datetime."""
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _resolve_tenant(tenant_id: str | None) -> Tenant | None:
    if not tenant_id:
        return None
    return Tenant.objects.filter(id=tenant_id).first()


def _resolve_conversation(
    *,
    tenant: Tenant,
    user_id: str,
) -> Conversation | None:
    """Locate the active Conversation for ``(tenant, ayla_user_id)``.

    Per Round-5 #442 F-Adv3: multi-channel users have one BotUser per
    channel. We pick the most-recently-active Conversation for the
    Ayla user across all their BotUsers under this tenant. Returns
    ``None`` if the user has no Conversation yet (handler skips DM-side
    effects + only emits analytics).
    """
    # Lazy import — apps.identity is loaded by the time this fires
    # but keep the import inside the function for clarity.
    from apps.identity.models import BotUser

    bot_user_ids = list(
        BotUser.all_tenants.filter(tenant=tenant, ayla_user_id=user_id).values_list("id", flat=True)
    )
    if not bot_user_ids:
        return None
    return (
        Conversation.all_tenants.filter(
            tenant=tenant,
            bot_user_id__in=bot_user_ids,
            is_active=True,
            is_shadow=False,
        )
        # Round-1 PP-3: ``id`` as final tiebreaker so identical
        # timestamps (test fixtures inserting in the same microsecond,
        # rare prod races) don't produce non-deterministic picks.
        .order_by("-last_message_at", "-created_at", "id")
        .first()
    )


# ─── handlers ──────────────────────────────────────────────────────────────


def handle_payment_authorized(envelope: IngestEnvelope) -> None:
    """``payment.authorized`` — event-contract.md §3.5.

    Records the pending-payment slot on the Conversation. No user-facing
    notification (captures are the visible event).
    """
    assert_envelope_tenant_authorized(envelope)

    data = envelope.data
    payment_id = UUID(data["payment_id"])

    tenant = _resolve_tenant(envelope.tenant_id)
    if tenant is None:
        logger.warning(
            "eventbus.consumer.payment.authorized.unknown_tenant tenant_id=%s",
            envelope.tenant_id,
        )
        return

    conversation = _resolve_conversation(tenant=tenant, user_id=envelope.user_id)
    if conversation is None:
        logger.info(
            "eventbus.consumer.payment.authorized.no_conversation "
            "user_id=%s tenant_id=%s payment_id=%s",
            envelope.user_id,
            envelope.tenant_id,
            payment_id,
        )
        return

    # Handler-level idempotency short-circuit (W1 handoff #2 / #635).
    # Closes the category-2 risk that replaying a payment.* event with
    # the dispatcher's IngestDedupe disabled would double-fire side
    # effects (false threshold trips, double pending-payment writes).
    if conversation.last_payment_event_id == envelope.event_id:
        logger.info(
            "eventbus.consumer.payment.authorized.replay_skipped conversation_id=%s event_id=%s",
            conversation.pk,
            envelope.event_id,
        )
        return

    Conversation.all_tenants.filter(pk=conversation.pk).update(
        pending_payment_id=payment_id,
        last_payment_event_id=envelope.event_id,
    )


def handle_payment_captured(envelope: IngestEnvelope) -> None:
    """``payment.captured`` — event-contract.md §3.6.

    Clears pending slot, resets failure counter, emits loyalty
    fan-out. No customer DM (booking confirmation flow already
    covers it).
    """
    assert_envelope_tenant_authorized(envelope)

    data = envelope.data
    payment_id = UUID(data["payment_id"])
    appointment_id = UUID(data["appointment_id"])

    tenant = _resolve_tenant(envelope.tenant_id)
    if tenant is None:
        logger.warning(
            "eventbus.consumer.payment.captured.unknown_tenant tenant_id=%s",
            envelope.tenant_id,
        )
        return

    conversation = _resolve_conversation(tenant=tenant, user_id=envelope.user_id)

    # Round-2 NEW-2 fix: put dedupe INSERT + Conversation update +
    # emit inside the SAME inner atomic so a partial failure rolls
    # back ALL of them together. The previous version committed the
    # dedupe row independently — if emit then raised, future retries
    # were locked out and the loyalty fan-out was lost permanently.
    #
    # Round-2 NEW-6 fix: lock the Conversation row with
    # select_for_update so a concurrent payment.failed handler can't
    # increment ``consecutive_payment_failures`` between our read
    # and the reset-to-0 UPDATE here (lost-update race).
    #
    # Round-1 BLOCKER-1: per-(tenant, payment_id, terminal_state)
    # dedupe via the unique constraint on PaymentTerminalDedupe.
    # Round-2 NEW-1: tenant_id in the unique key blocks the
    # cross-tenant pre-claim DoS where a spoofer would globally
    # claim (payment_id, CAPTURED) and starve the legitimate tenant.
    try:
        with transaction.atomic():
            PaymentTerminalDedupe.objects.create(
                tenant_id=tenant.id,
                payment_id=payment_id,
                terminal_state=PaymentTerminalDedupe.TerminalState.CAPTURED,
                event_id=envelope.event_id,
            )

            if conversation is not None:
                locked = (
                    Conversation.all_tenants.select_for_update().filter(pk=conversation.pk).first()
                )
                if locked is not None:
                    # PP-1: clear pending slot ONLY if it matches THIS
                    # payment. Don't wipe a sibling's pending slot.
                    if locked.pending_payment_id == payment_id:
                        Conversation.all_tenants.filter(pk=locked.pk).update(
                            pending_payment_id=None,
                        )

                    Conversation.all_tenants.filter(pk=locked.pk).update(
                        last_payment_captured_at=envelope.occurred_at,
                        consecutive_payment_failures=0,
                        last_payment_event_id=envelope.event_id,
                    )

            # Loyalty fan-out — apps/events/ snake_case bus per §3.6
            # step 2. The downstream loyalty subscriber (Epic #289)
            # computes + writes the ledger row; this consumer does
            # NOT touch the ledger directly. Inside the inner atomic
            # so a failure here rolls back the dedupe + Conv update.
            emit_internal_event(
                "loyalty_bonus_eligible",
                properties={
                    "user_id": envelope.user_id,
                    "tenant_id": envelope.tenant_id,
                    "payment_id": str(payment_id),
                    "appointment_id": str(appointment_id),
                    "amount": data.get("amount", ""),
                    "currency": data.get("currency", "RUB"),
                },
            )
    except IntegrityError:
        logger.info(
            "eventbus.consumer.payment.captured.terminal_already_processed "
            "tenant_id=%s payment_id=%s event_id=%s",
            tenant.id,
            payment_id,
            envelope.event_id,
        )
        return


def handle_payment_failed(envelope: IngestEnvelope) -> None:
    """``payment.failed`` — event-contract.md §3.7.

    Records failure on Conversation, increments the consecutive-failure
    counter, conditionally emits ``payment_failed_skill_triggered``
    once the threshold trips. Booking cancellation is NOT triggered
    here — Ayla emits ``booking.cancelled`` separately if the
    pending-payment slot's grace clock expires.
    """
    assert_envelope_tenant_authorized(envelope)

    data = envelope.data
    payment_id = UUID(data["payment_id"])
    appointment_id = UUID(data["appointment_id"])
    failure_code = _map_yookassa_failure_code(data.get("reason"))

    tenant = _resolve_tenant(envelope.tenant_id)
    if tenant is None:
        logger.warning(
            "eventbus.consumer.payment.failed.unknown_tenant tenant_id=%s",
            envelope.tenant_id,
        )
        return

    conversation = _resolve_conversation(tenant=tenant, user_id=envelope.user_id)
    if conversation is None:
        logger.info(
            "eventbus.consumer.payment.failed.no_conversation "
            "user_id=%s tenant_id=%s payment_id=%s",
            envelope.user_id,
            envelope.tenant_id,
            payment_id,
        )
        return

    # Round-1 BLOCKER-2: counter + threshold under row lock. F() +
    # refresh_from_db left a TOCTOU window where two concurrent
    # ``payment.failed`` events on the same Conversation could both
    # observe the threshold crossing → double skill handoff invocation
    # → double DM to the customer. Wrap read-modify-write + threshold
    # compare in select_for_update so the second worker blocks until
    # the first commits.
    #
    # Also Round-1 idempotency short-circuit moved INSIDE the locked
    # block to defeat the race between «check last_payment_event_id»
    # and «UPDATE» that the lock-free version had.
    #
    # #738 (founder verdict 2026-05-26) — select_related("bot_user")
    # pre-fetches client_name into the locked row so the enriched
    # payload built post-commit doesn't issue a second query (and
    # doesn't risk the bot_user being mutated between commit and
    # skill dispatch).
    with transaction.atomic():
        locked = (
            Conversation.all_tenants.select_for_update()
            .select_related("bot_user")
            .filter(pk=conversation.pk)
            .first()
        )
        if locked is None:
            # Race: conversation deleted between resolve and lock. Bail.
            return
        if locked.last_payment_event_id == envelope.event_id:
            logger.info(
                "eventbus.consumer.payment.failed.replay_skipped conversation_id=%s event_id=%s",
                locked.pk,
                envelope.event_id,
            )
            return

        new_count = locked.consecutive_payment_failures + 1
        Conversation.all_tenants.filter(pk=locked.pk).update(
            last_payment_failed_at=envelope.occurred_at,
            last_payment_failure_code=failure_code,
            consecutive_payment_failures=new_count,
            pending_payment_id=None,
            last_payment_event_id=envelope.event_id,
        )

        # Round-1 BLOCKER-2: strict equality (== threshold), not >=.
        # Hand off to the payment_failed skill exactly once on the
        # crossing — never on subsequent failures above the threshold
        # (would otherwise spam DMs every failure once a user is over
        # the line).
        threshold = settings.PAYMENT_FAILED_HANDOFF_THRESHOLD
        should_dispatch = new_count == threshold

        # #738 Phase 1 (Option C minimum) — payload enrichment from
        # envelope + Conversation context ONLY. Per ADR-0009 §Hard rule
        # #2 we MUST NOT query Ayla's Appointment row (cross-repo DB
        # access forbidden); per founder verdict 2026-05-26 we also
        # MUST NOT call Ayla REST per event (hot-path latency). Phase 2
        # post-pilot extends envelope to event_version=2 with enriched
        # fields — task #93. Until then, missing fields (master_name,
        # service_name, amount, currency, appointment_date) are
        # gracefully degraded by the skill's α-mode fallbacks (see
        # apps/skills/payment_failed/skill.py:36-48).
        #
        # ``failure_code`` is the MAPPED value, not raw envelope.data
        # ["reason"], per PII rule §7 — provider free-text may carry
        # card-tail digits. Same value as Conversation.last_payment_
        # failure_code persisted above for trace consistency.
        bot_user_client_name = (
            (locked.bot_user.client_name or None) if locked.bot_user is not None else None
        )
        skill_payload = {
            "payment_id": str(payment_id),
            "appointment_id": str(appointment_id),
            "client_user_id": envelope.user_id,
            "tenant_id": envelope.tenant_id,
            "failure_code": failure_code,
            "consecutive_failures": new_count,
            "failed_at": data.get("failed_at"),
            "payment_event_id": envelope.event_id,
            "client_name": bot_user_client_name,
        }

    # Dispatch AFTER the transaction commits so the skill (which may
    # send DMs, write audit rows, call channel outbound) observes a
    # state consistent with the row lock's release. Lazy import keeps
    # apps.skills out of the consumer module's import graph at boot
    # (avoids potential circular imports through the skills registry).
    if should_dispatch:
        from apps.skills.payment_failed.skill import on_payment_failed_event

        try:
            on_payment_failed_event(skill_payload)
        except Exception as exc:  # noqa: BLE001
            # Skill MUST NOT break the consumer loop. The Conversation
            # state is already committed; a skill failure is a UX
            # degradation (no DM) but not a data-integrity issue. The
            # dedupe row prevents a retry from double-firing the skill;
            # forensic log here is the only trail.
            logger.exception(
                "eventbus.consumer.payment.failed.skill_handoff_failed "
                "event_id=%s payment_id=%s err=%s",
                envelope.event_id,
                payment_id,
                exc,
            )


def handle_payment_refunded(envelope: IngestEnvelope) -> None:
    """``payment.refunded`` — event-contract.md §3.8.

    Records refund timestamp on Conversation, emits loyalty reverse
    event. Customer-facing refund DM is owned by Ayla's notification
    stream — NOT this consumer.
    """
    assert_envelope_tenant_authorized(envelope)

    data = envelope.data
    payment_id = UUID(data["payment_id"])
    appointment_id = UUID(data["appointment_id"])

    tenant = _resolve_tenant(envelope.tenant_id)
    if tenant is None:
        logger.warning(
            "eventbus.consumer.payment.refunded.unknown_tenant tenant_id=%s",
            envelope.tenant_id,
        )
        return

    conversation = _resolve_conversation(tenant=tenant, user_id=envelope.user_id)

    # Round-2 fixes (mirror handle_payment_captured):
    # * NEW-1: tenant in dedupe unique key blocks cross-tenant
    #   pre-claim DoS that would starve victim's loyalty reverse.
    # * NEW-2: dedupe INSERT + Conv update + emit in ONE atomic so
    #   partial failure rolls back all three together — preserves
    #   retry-ability.
    try:
        with transaction.atomic():
            PaymentTerminalDedupe.objects.create(
                tenant_id=tenant.id,
                payment_id=payment_id,
                terminal_state=PaymentTerminalDedupe.TerminalState.REFUNDED,
                event_id=envelope.event_id,
            )

            if conversation is not None:
                Conversation.all_tenants.filter(pk=conversation.pk).update(
                    last_payment_refunded_at=envelope.occurred_at,
                    last_payment_event_id=envelope.event_id,
                )

            # Loyalty reverse — apps/events/ snake_case bus per §3.8
            # step 2. Inside the inner atomic so a failure rolls back
            # the dedupe row + Conv update together.
            emit_internal_event(
                "loyalty_refund_reverse",
                properties={
                    "user_id": envelope.user_id,
                    "tenant_id": envelope.tenant_id,
                    "payment_id": str(payment_id),
                    "appointment_id": str(appointment_id),
                    "refund_amount": data.get("amount", ""),
                    "currency": data.get("currency", "RUB"),
                    "reason": data.get("reason", ""),
                },
            )
    except IntegrityError:
        logger.info(
            "eventbus.consumer.payment.refunded.terminal_already_processed "
            "tenant_id=%s payment_id=%s event_id=%s",
            tenant.id,
            payment_id,
            envelope.event_id,
        )
        return


# ─── registration ──────────────────────────────────────────────────────────


def register_payment_handlers() -> None:
    """Register all payment.* handlers with the ingest dispatcher.

    Called from :func:`apps.eventbus.apps.EventBusConfig.ready`. Each
    ``register()`` is wrapped in try/except so re-imports during
    autoreload don't crash startup.
    """
    for event_name, handler in (
        ("payment.authorized", handle_payment_authorized),
        ("payment.captured", handle_payment_captured),
        ("payment.failed", handle_payment_failed),
        ("payment.refunded", handle_payment_refunded),
    ):
        try:
            register(event_name=event_name, event_version=1, handler=handler)
        except ValueError:
            # Duplicate registration — silently OK on autoreload.
            pass
