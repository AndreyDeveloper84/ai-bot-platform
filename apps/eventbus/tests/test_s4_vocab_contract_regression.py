"""S4 cross-service vocabulary-drift regression (cluster #945/#944/#946).

This cluster's failure mode is silent: a name/field mismatch between what
Ayla emits and what the bot's contract accepts sends a perfectly valid
booking/payment event to the DLQ instead of a consumer — quietly breaking
the pilot booking flow.

The canonical decisions (``docs/architecture/event-contract.md`` §1
addendum 2026-06-01 + §3) are:

* §1(c) ``payment.captured`` is the confirmed canonical name; the legacy
  ``payment.confirmed`` emit name is NOT accepted by the bot (#944).
* §3.1/§3.6 the booking identifier on the wire is ``appointment_id`` (#945).
* §3.2 a no-show is ``booking.cancelled`` with ``reason_code=user_no_show`` —
  ``booking.no_show`` is NOT a cross-service event (#946); the 12+1 event set
  stays closed (no event #13).

The bot side already conforms on ``dev``; these tests are the *guard* that a
future edit can't reintroduce the drift. They complement the HTTP-level
smoke in ``test_e2e_ingest_smoke.py`` by pinning the contract at the pure
``dispatch_envelope`` layer with an explicit "zero DLQ" assertion, and by
pinning the two drift names that MUST be refused at parse time.

DoD (S4): a contract-conformant Ayla payload runs through the consumer
without landing in the DLQ.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from uuid import UUID

import pytest

from apps.eventbus.ingest_dispatcher import DispatchOutcome, dispatch_envelope
from apps.eventbus.ingest_envelope import IngestEnvelopeError, parse_envelope
from apps.eventbus.models import IngestDLQ
from tests.fixtures.contracts import load_contract

pytestmark = pytest.mark.django_db

# Shared canonical IDs — every A10 fixture references the same booking.
TENANT_ID = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"
USER_ID = "f1a2b3c4-d5e6-4789-9abc-def012345678"
APPOINTMENT_ID = "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"

# The full set of conformant Ayla events the pilot delivers. A real Ayla
# payload for each of these MUST reach its consumer, never the DLQ.
CONFORMANT_FIXTURES = (
    "booking.created.v1.json",
    "booking.confirmed.v1.json",
    "payment.captured.v1.json",
    "payment.failed.v1.json",
)


@pytest.fixture
def _handlers_registered() -> Iterator[None]:
    """Register the real booking + payment handlers for the duration of the
    test, restoring the registry afterwards (same hygiene as the e2e smoke)."""
    import apps.eventbus.ingest_dispatcher as dispatcher_module
    from apps.eventbus.consumers.booking import register_booking_handlers
    from apps.eventbus.consumers.payment import register_payment_handlers

    snapshot = dict(dispatcher_module._REGISTRY)
    dispatcher_module._REGISTRY.clear()
    register_booking_handlers()
    register_payment_handlers()
    try:
        yield
    finally:
        dispatcher_module._REGISTRY.clear()
        dispatcher_module._REGISTRY.update(snapshot)


@pytest.fixture
def seed(settings):
    """Tenant + linked BotUser + Conversation + a pending RemoteBookingProxy,
    so the booking.confirmed / payment.* handlers have a real target to
    mutate — mirrors test_e2e_ingest_smoke.seed."""
    # Pre-#246 tenant-verify bridge (Round-3 NEW-5), same as the other
    # fixture-driven consumer tests.
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True

    from apps.booking.models import RemoteBookingProxy
    from apps.conversations.models import Conversation
    from apps.identity.models import BotUser
    from apps.tenancy.models import Tenant

    tenant = Tenant.objects.create(id=TENANT_ID, slug="t-s4", name="S4 regression tenant")
    bot_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="9300",
        chat_id="chat-9300",
        ayla_user_id=USER_ID,
    )
    Conversation.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        state=Conversation.State.IDLE,
        last_message_at=dt.datetime(2026, 5, 21, 10, 0, tzinfo=dt.timezone.utc),
    )
    RemoteBookingProxy.all_tenants.create(
        appointment_id=UUID(APPOINTMENT_ID),
        tenant=tenant,
        bot_user=None,
        start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
        end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
        status="pending_payment",
    )
    return tenant


class TestConformantPayloadNeverHitsDLQ:
    """DoD: a real, contract-conformant Ayla payload runs through the
    consumer without landing in the DLQ."""

    def test_every_conformant_event_dispatches_ok_with_zero_dlq(
        self, _handlers_registered, seed
    ) -> None:
        # Iterate in lifecycle order so each event sees the proxy in the
        # state the prior event left it (the proxy itself is created by the
        # ``seed`` fixture; booking.created's get_or_create finds + updates it).
        for name in CONFORMANT_FIXTURES:
            envelope = parse_envelope(load_contract(name))
            result = dispatch_envelope(envelope)
            assert result.outcome is DispatchOutcome.OK, (
                f"{name} did not dispatch cleanly: {result.outcome} "
                f"({result.exception!r}) — vocabulary drift would surface here."
            )

        # The whole point of the cluster: not one of these canonical events
        # ended up quarantined.
        assert IngestDLQ.objects.count() == 0


class TestDriftNamesAreRefused:
    """Pin the locked S4 vocabulary decisions as executable regressions:
    the bot is NOT made "bilingual" toward Ayla's legacy/internal names."""

    def test_legacy_payment_confirmed_name_rejected(self) -> None:
        # #944: canonical is payment.captured; the pre-rename emit name
        # payment.confirmed must be refused at the parse layer (not silently
        # accepted), so a regression to the old name fails loudly.
        body = load_contract("payment.captured.v1.json")
        body["event_name"] = "payment.confirmed"
        with pytest.raises(IngestEnvelopeError) as exc:
            parse_envelope(body)
        assert exc.value.reason == "invalid_event_name"

    def test_booking_no_show_is_not_a_cross_service_event(self) -> None:
        # #946: no-show is modeled as booking.cancelled + reason_code,
        # NOT as a standalone event #13. booking.no_show must be refused.
        body = load_contract("booking.confirmed.v1.json")
        body["event_name"] = "booking.no_show"
        with pytest.raises(IngestEnvelopeError) as exc:
            parse_envelope(body)
        assert exc.value.reason == "invalid_event_name"
