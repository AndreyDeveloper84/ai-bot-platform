"""Billing event consumer (C4 — pilot 2026-08-15; mirror — DRF-2984).

Per ``beautygo_backend/docs/PILOT_CONTRACTS_2026-08-15.md`` §5 (contract version 1.0.0,
frozen): W2's outbox produces three billing events; W3 (this repo) is
the consumer. DRF-2984 (design ``DESIGN_PLATFORM_BILLING_2026-10-20`` §6.2)
adds four subscription events and makes the subscription family write the
tenant's subscription mirror (:mod:`apps.tenancy.billing_mirror`).

| event_name                    | payload (v1)                                        | mirror       |
|-------------------------------|-----------------------------------------------------|--------------|
| ``subscription.trial_started`` | ``{tariff, trial_ends_at}``                        | trial        |
| ``subscription.activated``     | ``{specialist_id, tariff, period_end}``            | active       |
| ``subscription.renewed``       | ``{period_end}`` (+ ``tariff``, optional)          | active       |
| ``subscription.plan_changed``  | ``{tariff, effective_from}``                       | pending plan |
| ``subscription.past_due``      | ``{specialist_id, debt_amount, failed_attempts}``  | past_due     |
| ``subscription.frozen``        | ``{reason}``                                       | frozen       |
| ``subscription.canceled``      | ``{effective_at}``                                 | canceled     |
| ``billing.fee_charged``        | ``{specialist_id, appointment_id, amount, period}`` | —           |

### Contract obligations honoured here

* **Dedupe by ``event_id``** — handled at the dispatcher layer
  (:class:`IngestDedupe`); the mirror write runs inside the dispatcher's
  transaction, so the dedupe row and the mirror land together. A replay is
  short-circuited before the handler; an out-of-order older event is a
  no-op in the mirror (``occurred_at`` guard).
* **Unknown ``event_version`` → DLQ** — only v1 handlers are registered,
  so a v2 envelope dead-letters (``unknown_event_version``) instead of
  being silently accepted.
* **Forward compatibility** — unknown *additional* payload keys are
  ignored; required keys (table above) are validated and a miss — or a
  value outside the mirror's closed vocabulary — is a permanent producer
  bug: logged loudly, acked (never retried — the dispatcher's 500 path is
  for transient faults).

### What the mirror does not cover

* ``tenant_id=None`` (AMD-015 — a legacy solo personal subscription) has no
  tenant to mirror onto: observed and logged, nothing written.
* The master notification side-effect still waits on the
  ``specialist_id`` → bot master mapping (escalated 2026-07-18).
* Behaviour gates on the mirror are PR-B2, behind ``BILLING_ENABLED``.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable

from apps.eventbus.ingest_dispatcher import register
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.ingest_tenancy import assert_envelope_tenant_authorized
from apps.tenancy.billing_mirror import (
    BillingMirrorError,
    MirrorUpdate,
    apply_pending_plan,
    apply_subscription_event,
)
from apps.tenancy.models import SubscriptionStatus


logger = logging.getLogger(__name__)


# v1 required payload keys. Unknown extras are ignored (forward-compat).
_REQUIRED_PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    "subscription.trial_started": ("tariff", "trial_ends_at"),
    "subscription.activated": ("specialist_id", "tariff", "period_end"),
    "subscription.renewed": ("period_end",),
    "subscription.plan_changed": ("tariff", "effective_from"),
    "subscription.past_due": ("specialist_id", "debt_amount", "failed_attempts"),
    "subscription.frozen": ("reason",),
    "subscription.canceled": ("effective_at",),
    "billing.fee_charged": ("specialist_id", "appointment_id", "amount", "period"),
}


def _date(data: dict, key: str) -> dt.date:
    value = data[key]
    if not isinstance(value, str):
        raise BillingMirrorError(f"{key}: дата строкой YYYY-MM-DD, не {type(value).__name__}")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise BillingMirrorError(f"{key}: не дата: {value!r}") from exc


#: Событие → что оно сообщает зеркалу. ``billing.fee_charged`` зеркала не трогает.
_MIRROR: dict[str, Callable[[dict], MirrorUpdate]] = {
    "subscription.trial_started": lambda d: MirrorUpdate(
        SubscriptionStatus.TRIAL, plan=d["tariff"], trial_ends_at=_date(d, "trial_ends_at")
    ),
    "subscription.activated": lambda d: MirrorUpdate(
        SubscriptionStatus.ACTIVE, plan=d["tariff"], current_period_end=_date(d, "period_end")
    ),
    # tariff — с B2 (ayla-2c): первый renewed после смены тарифа переписывает plan.
    "subscription.renewed": lambda d: MirrorUpdate(
        SubscriptionStatus.ACTIVE, plan=d.get("tariff"), current_period_end=_date(d, "period_end")
    ),
    "subscription.past_due": lambda d: MirrorUpdate(SubscriptionStatus.PAST_DUE),
    "subscription.frozen": lambda d: MirrorUpdate(SubscriptionStatus.FROZEN),
    "subscription.canceled": lambda d: MirrorUpdate(SubscriptionStatus.CANCELED),
}


def _handle(envelope: IngestEnvelope, *, slug: str) -> None:
    """Shared v1 body: payload-validate → mirror (subscription family) → observe.

    Tenant verification happens in each public handler directly — the
    tenant-mandate lint
    (:mod:`tests.contracts.test_consumer_tenant_verification_mandate`)
    scans the *registered* function's source for the
    ``assert_envelope_tenant_authorized`` call, so delegating it here
    would fail the gate.

    ``slug`` is the log/event discriminator (e.g. ``subscription_activated``).
    Money values (``debt_amount``, ``amount``) are deliberately NOT logged —
    ids and counts are enough for triage and keep financial PII out of the
    log stream (the values remain available in the DLQ redacted raw_body
    should the event ever dead-letter).
    """
    required = _REQUIRED_PAYLOAD_KEYS[envelope.event_name]
    missing = [key for key in required if key not in envelope.data]
    if missing:
        # Permanent producer-side contract violation. Ack (do NOT raise —
        # the dispatcher maps exceptions to 500/retry, and retrying a
        # permanently malformed event just burns the failure tracker).
        logger.warning(
            "eventbus.consumer.billing.%s.missing_payload_keys event_id=%s tenant_id=%s missing=%s",
            slug,
            envelope.event_id,
            envelope.tenant_id,
            ",".join(missing),
        )
        return

    mirrored = "n/a"
    to_mirror = _MIRROR.get(envelope.event_name)
    if envelope.event_name == "subscription.plan_changed":
        if envelope.tenant_id is None:
            mirrored = "no_tenant"
        else:
            try:
                written = apply_pending_plan(
                    envelope.tenant_id,
                    plan=envelope.data["tariff"],
                    effective_from=_date(envelope.data, "effective_from"),
                )
            except BillingMirrorError as exc:
                logger.warning(
                    "eventbus.consumer.billing.%s.invalid_payload event_id=%s tenant_id=%s error=%s",
                    slug,
                    envelope.event_id,
                    envelope.tenant_id,
                    exc,
                )
                return
            mirrored = "written" if written else "skipped"
    elif to_mirror is not None:
        if envelope.tenant_id is None:
            mirrored = "no_tenant"
        else:
            try:
                update = to_mirror(envelope.data)
            except BillingMirrorError as exc:
                # Same class as a missing key: permanent, ack, loud.
                logger.warning(
                    "eventbus.consumer.billing.%s.invalid_payload event_id=%s tenant_id=%s error=%s",
                    slug,
                    envelope.event_id,
                    envelope.tenant_id,
                    exc,
                )
                return
            written = apply_subscription_event(
                envelope.tenant_id, update, occurred_at=envelope.occurred_at
            )
            mirrored = "written" if written else "skipped"

    logger.info(
        "eventbus.consumer.billing.%s.received event_id=%s tenant_id=%s specialist_id=%s mirror=%s",
        slug,
        envelope.event_id,
        envelope.tenant_id,
        envelope.data.get("specialist_id"),
        mirrored,
    )


def handle_subscription_trial_started(envelope: IngestEnvelope) -> None:
    """``subscription.trial_started`` v1 (DRF-2984)."""
    assert_envelope_tenant_authorized(envelope)
    _handle(envelope, slug="subscription_trial_started")


def handle_subscription_activated(envelope: IngestEnvelope) -> None:
    """``subscription.activated`` v1 (C4)."""
    assert_envelope_tenant_authorized(envelope)
    _handle(envelope, slug="subscription_activated")


def handle_subscription_renewed(envelope: IngestEnvelope) -> None:
    """``subscription.renewed`` v1 (DRF-2984)."""
    assert_envelope_tenant_authorized(envelope)
    _handle(envelope, slug="subscription_renewed")


def handle_subscription_plan_changed(envelope: IngestEnvelope) -> None:
    """``subscription.plan_changed`` v1 (DRF-2984 B2)."""
    assert_envelope_tenant_authorized(envelope)
    _handle(envelope, slug="subscription_plan_changed")


def handle_subscription_past_due(envelope: IngestEnvelope) -> None:
    """``subscription.past_due`` v1 (C4)."""
    assert_envelope_tenant_authorized(envelope)
    _handle(envelope, slug="subscription_past_due")


def handle_subscription_frozen(envelope: IngestEnvelope) -> None:
    """``subscription.frozen`` v1 (DRF-2984)."""
    assert_envelope_tenant_authorized(envelope)
    _handle(envelope, slug="subscription_frozen")


def handle_subscription_canceled(envelope: IngestEnvelope) -> None:
    """``subscription.canceled`` v1 (DRF-2984)."""
    assert_envelope_tenant_authorized(envelope)
    _handle(envelope, slug="subscription_canceled")


def handle_billing_fee_charged(envelope: IngestEnvelope) -> None:
    """``billing.fee_charged`` v1 (C4)."""
    assert_envelope_tenant_authorized(envelope)
    _handle(envelope, slug="billing_fee_charged")


# ─── registration ──────────────────────────────────────────────────────────


def register_billing_handlers() -> None:
    """Register billing handlers with the ingest dispatcher.

    Called from :func:`apps.eventbus.apps.EventBusConfig.ready`.
    """
    for event_name, handler in (
        ("subscription.trial_started", handle_subscription_trial_started),
        ("subscription.activated", handle_subscription_activated),
        ("subscription.renewed", handle_subscription_renewed),
        ("subscription.plan_changed", handle_subscription_plan_changed),
        ("subscription.past_due", handle_subscription_past_due),
        ("subscription.frozen", handle_subscription_frozen),
        ("subscription.canceled", handle_subscription_canceled),
        ("billing.fee_charged", handle_billing_fee_charged),
    ):
        try:
            register(event_name=event_name, event_version=1, handler=handler)
        except ValueError:
            # Duplicate registration — silently OK on autoreload.
            pass
