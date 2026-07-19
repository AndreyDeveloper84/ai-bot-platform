"""Billing event consumer (C4 — pilot 2026-08-15).

Per ``PILOT_CONTRACTS_2026-08-15.md`` §5 (contract version 1.0.0,
frozen): W2's outbox produces three billing events; W3 (this repo) is
the consumer for master-facing notifications, analytics follow later.

| event_name               | payload (v1)                                        |
|--------------------------|-----------------------------------------------------|
| ``subscription.activated`` | ``{specialist_id, tariff, period_end}``           |
| ``subscription.past_due``  | ``{specialist_id, debt_amount, failed_attempts}`` |
| ``billing.fee_charged``    | ``{specialist_id, appointment_id, amount, period}`` |

### Contract obligations honoured here

* **Dedupe by ``event_id``** — handled at the dispatcher layer
  (:class:`IngestDedupe`); these handlers are side-effect-free today so
  replays are trivially safe.
* **Unknown ``event_version`` → DLQ** — only v1 handlers are registered,
  so a v2 envelope dead-letters (``unknown_event_version``) instead of
  being silently accepted.
* **Forward compatibility** — unknown *additional* payload keys are
  ignored; required keys (table above) are validated and a miss is a
  permanent producer bug: logged loudly, acked (never retried — the
  dispatcher's 500 path is for transient faults).

### Why no side-effects yet

The C4 consumer purpose is «уведомления мастеру», but wiring a MAX DM
requires resolving ``specialist_id`` (Ayla ``SpecialistProfile.id``) to a
bot-side master (:class:`~apps.catalog.models.CatalogMaster`, keyed by
Ayla **User** UUID). That mapping is an open cross-repo contract
question (escalated to the orchestrator 2026-07-18) — guessing it is
worse than acknowledging. Until it lands, these handlers verify tenant
authorization, validate the payload, and emit a structured log line per
event — the events *arrive* and are observable, the notification
side-effect plugs in without touching the registry or the envelope
contract.
"""

from __future__ import annotations

import logging

from apps.eventbus.ingest_dispatcher import register
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.ingest_tenancy import assert_envelope_tenant_authorized


logger = logging.getLogger(__name__)


# C4 v1 required payload keys. Unknown extras are ignored (forward-compat).
_REQUIRED_PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    "subscription.activated": ("specialist_id", "tariff", "period_end"),
    "subscription.past_due": ("specialist_id", "debt_amount", "failed_attempts"),
    "billing.fee_charged": ("specialist_id", "appointment_id", "amount", "period"),
}


def _handle(envelope: IngestEnvelope, *, slug: str) -> None:
    """Shared v1 body: payload-validate → observe.

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

    logger.info(
        "eventbus.consumer.billing.%s.received event_id=%s tenant_id=%s specialist_id=%s",
        slug,
        envelope.event_id,
        envelope.tenant_id,
        envelope.data.get("specialist_id"),
    )


def handle_subscription_activated(envelope: IngestEnvelope) -> None:
    """``subscription.activated`` v1 (C4)."""
    assert_envelope_tenant_authorized(envelope)
    _handle(envelope, slug="subscription_activated")


def handle_subscription_past_due(envelope: IngestEnvelope) -> None:
    """``subscription.past_due`` v1 (C4)."""
    assert_envelope_tenant_authorized(envelope)
    _handle(envelope, slug="subscription_past_due")


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
        ("subscription.activated", handle_subscription_activated),
        ("subscription.past_due", handle_subscription_past_due),
        ("billing.fee_charged", handle_billing_fee_charged),
    ):
        try:
            register(event_name=event_name, event_version=1, handler=handler)
        except ValueError:
            # Duplicate registration — silently OK on autoreload.
            pass
