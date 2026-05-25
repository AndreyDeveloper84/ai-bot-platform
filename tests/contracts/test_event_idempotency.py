"""#447 — Idempotency contract test (Bucket 7).

Per ADR-0009 hard rule #7 and event-contract.md §5.1: consumers MUST
be idempotent on ``event_id``. This test enforces the contract by
calling :func:`dispatch_envelope` three times with the SAME envelope
and asserting:

1. 1st call → ``DispatchOutcome.OK`` + exactly ONE side-effect.
2. 2nd + 3rd calls → ``DispatchOutcome.DUPLICATE`` + ZERO additional
   side-effects.
3. ``IngestDedupe`` table has exactly ONE row for that ``event_id``.

### Why dispatch_envelope, not the HTTP layer

Per tech-lead decision Q1=A: this test exercises the dispatcher +
handler layer directly. HMAC verification, view routing, and HTTP
status codes are covered by the ingest-view contract tests
(``apps/eventbus/tests/test_ingest_view.py``). Layering both into
one test would duplicate coverage and slow CI. A FOLLOW_UP issue
covers the full HTTP round-trip if needed.

### Coverage scope

| Family | Shipped? | Test status |
|--------|----------|-------------|
| ``booking.*`` (4 events) | #442 | ✅ Active replay-3× |
| ``payment.*`` (4 events) | #443 | ✅ Active replay-3× |
| ``service.updated`` | #444 | ✅ Active replay-3× |
| ``user.profile.updated`` | #446 | ✅ Active replay-3× |
| ``master.schedule.updated`` | #445 | ✅ Active replay-3× |
| ``review.created`` | #445 | ✅ Active replay-3× |

**All 12 §3 events covered.** The DLQ-smoke harness is retired (one
negative test remains for «unknown event_name» dispatcher contract).

The DLQ smoke (``TestUnshippedConsumerNamesDeadLetter``) is the
mechanical enforcement of «no consumer ships without this gate»: it
asserts unshipped names return ``UNKNOWN_EVENT_VERSION``. When the
consumer's PR registers a handler, that case FLIPS to failing — the
author MUST replace the smoke with a real ``_dispatch_3x_and_assert``
to make CI green. Round-1 adversarial M1 fix — ``xfail(strict=False)``
was the wrong primitive: an XPASS log line wouldn't have alerted CI.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest

from apps.booking.models import BookingReminder, RemoteBookingProxy
from apps.catalog.models import CatalogService
from apps.conversations.models import Conversation
from apps.eventbus import ingest_dispatcher
from apps.eventbus.ingest_dispatcher import (
    DispatchOutcome,
    dispatch_envelope,
    registered_handlers,
)
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.models import IngestDedupe, PaymentTerminalDedupe
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


# Canonical fixture UUIDs.
AYLA_USER_ID = "f1a2b3c4-d5e6-4789-9abc-def012345678"
TENANT_ID = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"
APPOINTMENT_ID = "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"
PAYMENT_ID = "5c8e2d1f-3b4a-4c6d-9e8f-1a2b3c4d5e6f"
SERVICE_ID = "3d5f7e1c-8a2d-4e6f-b9c0-1d2e3f4a5b6c"
SPECIALIST_ID = "7c2d8e1f-0a5c-4c3a-9e1b-4d52f8eb3a17"


# ─── helpers ───────────────────────────────────────────────────────────────


def _envelope(
    *,
    event_name: str,
    data: dict[str, Any],
    event_id: str = "01J9CONTRACT00000000000000",  # pragma: allowlist secret
) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=event_id,
        event_name=event_name,
        event_version=1,
        occurred_at=dt.datetime(2026, 5, 21, 14, 32, 11, tzinfo=dt.timezone.utc),
        tenant_id=TENANT_ID,
        user_id=AYLA_USER_ID,
        actor="user",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        causation_id=None,
        data=data,
    )


def _dispatch_3x_and_assert(
    envelope: IngestEnvelope,
    *,
    side_effect_check: Callable[[], None],
) -> None:
    """Dispatch ``envelope`` 3× through :func:`dispatch_envelope` and
    assert idempotency.

    * 1st dispatch → ``DispatchOutcome.OK`` + ``side_effect_check``
      passes (side-effect applied exactly once).
    * 2nd + 3rd dispatches → ``DispatchOutcome.DUPLICATE``.
    * ``side_effect_check`` STILL passes (no additional side-effects).
    * ``IngestDedupe`` has exactly one row for this ``event_id``.
    * **The registered handler is called exactly once** across the 3
      dispatches. This is the LOAD-BEARING assertion that distinguishes
      «dispatcher-level dedupe contract» (what this file tests) from
      «handler-level idempotency» (covered by per-handler replay-3×
      tests in apps/eventbus/tests/). Round-1 adversarial M2 +
      friendly S2 fix: without this, a buggy refactor that moved
      dedupe out of the dispatcher would silently pass this test as
      long as handlers retained their own short-circuits.

    ``side_effect_check`` MUST re-query the DB on every call (do not
    cache Conversation/Proxy Python objects between dispatches —
    refresh_from_db inside the closure).
    """
    key = (envelope.event_name, envelope.event_version)
    registry = registered_handlers()
    real_handler = registry.get(key)
    assert real_handler is not None, (
        f"Handler not registered for {envelope.event_name}@v{envelope.event_version}"
    )

    # Pre: no dedupe row yet.
    assert IngestDedupe.objects.filter(event_id=envelope.event_id).count() == 0, (
        "Pre-condition: no IngestDedupe row for this event_id"
    )

    # Wrap the registered handler in a counting spy. Mutates the
    # module-level ``_REGISTRY`` for the duration of this assertion;
    # the finally-restore at the bottom keeps the registry clean for
    # subsequent tests.
    from unittest.mock import MagicMock

    # ``side_effect=real_handler`` is sufficient: Mock invokes
    # side_effect and returns its result. Round-2 F1: dropping the
    # redundant ``wraps=real_handler`` removes the ambiguous double-
    # invocation path (Mock would re-delegate to wraps only if
    # side_effect returned ``DEFAULT``, which handlers never do —
    # but the redundancy is one refactor away from a silent contract
    # mis-measure). The ``call_count`` assertion counts mock
    # invocations, not wrapped-object invocations.
    handler_spy = MagicMock(side_effect=real_handler)
    ingest_dispatcher._REGISTRY[key] = handler_spy
    try:
        # 1st dispatch — handler runs, side-effect lands.
        result = dispatch_envelope(envelope)
        assert result.outcome == DispatchOutcome.OK, (
            f"1st dispatch expected OK, got {result.outcome} (exception: {result.exception!r})"
        )
        side_effect_check()  # exactly one side-effect
        assert handler_spy.call_count == 1, (
            f"Handler must be called exactly once on 1st dispatch, got {handler_spy.call_count}"
        )

        # 2nd + 3rd dispatches — dedupe row blocks handler, DUPLICATE.
        for attempt in (2, 3):
            result = dispatch_envelope(envelope)
            assert result.outcome == DispatchOutcome.DUPLICATE, (
                f"{attempt}th dispatch expected DUPLICATE, got {result.outcome}"
            )
            side_effect_check()  # STILL exactly one side-effect
            # M2 fix: the dispatcher's dedupe row MUST short-circuit
            # BEFORE the handler is invoked. If the handler runs on
            # the replay (counter increments), the dispatcher contract
            # is broken regardless of any handler-internal short-circuit.
            assert handler_spy.call_count == 1, (
                f"Handler must NOT be re-invoked on replay #{attempt}; "
                f"got call_count={handler_spy.call_count}"
            )
    finally:
        # Restore the real handler so subsequent tests see a clean
        # registry.
        ingest_dispatcher._REGISTRY[key] = real_handler

    # IngestDedupe has exactly one row — the PK uniqueness pinned it.
    assert IngestDedupe.objects.filter(event_id=envelope.event_id).count() == 1, (
        "IngestDedupe must hold exactly one row per event_id after 3 dispatches"
    )


@pytest.fixture(autouse=True)
def _enable_tenant_verify_fail_open(settings) -> None:
    """Round-3 NEW-5 bridge — pre-#246 transition mode. Without this,
    handlers raise TenantAuthorizationError before reaching the
    dedupe layer and the test asserts the wrong outcome."""
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(
        id=TENANT_ID,
        slug="t-contract",
        name="Idempotency contract test tenant",
    )


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="9001",
        chat_id="chat-9001",
        ayla_user_id=AYLA_USER_ID,
    )


@pytest.fixture
def conversation(tenant: Tenant, bot_user: BotUser) -> Conversation:
    return Conversation.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        state=Conversation.State.IDLE,
        last_message_at=dt.datetime(2026, 5, 21, 10, 0, tzinfo=dt.timezone.utc),
    )


@pytest.fixture
def existing_proxy(tenant: Tenant, bot_user: BotUser) -> RemoteBookingProxy:
    """A pre-existing RemoteBookingProxy for handlers that mutate state
    on top of an existing row (cancelled, rescheduled, completed)."""
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=UUID(APPOINTMENT_ID),
        tenant=tenant,
        bot_user=bot_user,
        start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
        end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
        status="confirmed",
    )


# ─── Smoke: registry shape ─────────────────────────────────────────────────


class TestRegistryShape:
    """Sanity that the dispatcher registry contains the handlers we're
    about to exercise. If this fails, either ``apps/eventbus/apps.py``
    didn't register them OR the test suite booted with a partial Django
    init."""

    def test_booking_handlers_registered(self) -> None:
        registry = registered_handlers()
        for name in (
            "booking.created",
            "booking.cancelled",
            "booking.rescheduled",
            "booking.completed",
        ):
            assert (name, 1) in registry, f"{name}@v1 not registered"

    def test_payment_handlers_registered(self) -> None:
        registry = registered_handlers()
        for name in (
            "payment.authorized",
            "payment.captured",
            "payment.failed",
            "payment.refunded",
        ):
            assert (name, 1) in registry, f"{name}@v1 not registered"


# ─── booking.* (#442) ──────────────────────────────────────────────────────


class TestBookingIdempotency:
    def test_booking_created_idempotent(self, tenant: Tenant, bot_user: BotUser) -> None:
        env = _envelope(
            event_name="booking.created",
            data={
                "appointment_id": APPOINTMENT_ID,
                "specialist_id": SPECIALIST_ID,
                "service_id": SERVICE_ID,
                "start_at": "2026-05-22T15:00:00+03:00",
                "end_at": "2026-05-22T16:00:00+03:00",
                "status": "confirmed",
                "price_total": "1800.00",
                "source": "mobile_app",
            },
        )

        appointment_uuid = UUID(APPOINTMENT_ID)

        def check() -> None:
            # Exactly one proxy + exactly two reminders (T-24h + T-2h)
            # — no matter how many times the dispatch is replayed.
            assert (
                RemoteBookingProxy.all_tenants.filter(appointment_id=appointment_uuid).count() == 1
            )
            assert (
                BookingReminder.all_tenants.filter(ayla_appointment_id=appointment_uuid).count()
                == 2
            )

        _dispatch_3x_and_assert(env, side_effect_check=check)

    def test_booking_cancelled_idempotent(
        self, tenant: Tenant, existing_proxy: RemoteBookingProxy
    ) -> None:
        env = _envelope(
            event_name="booking.cancelled",
            data={
                "appointment_id": APPOINTMENT_ID,
                "cancelled_by": "user",
                "reason_code": "user_changed_plans",
                "cancelled_at": "2026-05-21T16:08:45.119Z",
            },
        )

        with patch("apps.eventbus.consumers.booking.emit_internal_event") as mock_emit:

            def check() -> None:
                existing_proxy.refresh_from_db()
                assert existing_proxy.status == "cancelled"
                # Emit fires exactly once across 3 dispatches.
                assert mock_emit.call_count == 1

            _dispatch_3x_and_assert(env, side_effect_check=check)

    def test_booking_rescheduled_idempotent(
        self, tenant: Tenant, existing_proxy: RemoteBookingProxy
    ) -> None:
        env = _envelope(
            event_name="booking.rescheduled",
            data={
                "appointment_id": APPOINTMENT_ID,
                "old_start_at": "2026-05-22T15:00:00+00:00",
                "new_start_at": "2026-05-23T11:00:00+00:00",
                "rescheduled_by": "admin",
            },
        )

        with patch("apps.eventbus.consumers.booking.emit_internal_event") as mock_emit:

            def check() -> None:
                existing_proxy.refresh_from_db()
                # New start_at applied exactly once.
                assert existing_proxy.start_at == dt.datetime(
                    2026, 5, 23, 11, 0, tzinfo=dt.timezone.utc
                )
                # Duration preserved (1h).
                assert existing_proxy.end_at - existing_proxy.start_at == (dt.timedelta(hours=1))
                assert mock_emit.call_count == 1

            _dispatch_3x_and_assert(env, side_effect_check=check)

    def test_booking_completed_idempotent(
        self, tenant: Tenant, existing_proxy: RemoteBookingProxy
    ) -> None:
        env = _envelope(
            event_name="booking.completed",
            data={
                "appointment_id": APPOINTMENT_ID,
                "completed_at": "2026-05-22T16:30:00.000Z",
            },
        )

        with patch("apps.eventbus.consumers.booking.emit_internal_event") as mock_emit:

            def check() -> None:
                existing_proxy.refresh_from_db()
                assert existing_proxy.status == "completed"
                assert mock_emit.call_count == 1

            _dispatch_3x_and_assert(env, side_effect_check=check)


# ─── payment.* (#443) ──────────────────────────────────────────────────────


class TestPaymentIdempotency:
    def test_payment_authorized_idempotent(
        self, tenant: Tenant, conversation: Conversation
    ) -> None:
        env = _envelope(
            event_name="payment.authorized",
            data={
                "payment_id": PAYMENT_ID,
                "appointment_id": APPOINTMENT_ID,
                "amount": "1800.00",
                "currency": "RUB",
                "provider": "yookassa",
                "external_id": "2d8e3c1f-7a9b-4d6e-c0f1-3b4a5c6d7e8f",
            },
        )

        def check() -> None:
            conversation.refresh_from_db()
            # Pending slot set exactly to this payment_id.
            assert conversation.pending_payment_id == UUID(PAYMENT_ID)
            # Event_id stamped exactly once.
            assert conversation.last_payment_event_id == env.event_id

        _dispatch_3x_and_assert(env, side_effect_check=check)

    def test_payment_captured_idempotent(self, tenant: Tenant, conversation: Conversation) -> None:
        env = _envelope(
            event_name="payment.captured",
            data={
                "payment_id": PAYMENT_ID,
                "appointment_id": APPOINTMENT_ID,
                "amount": "1800.00",
                "captured_at": "2026-05-22T16:31:14.205Z",
            },
        )

        with patch("apps.eventbus.consumers.payment.emit_internal_event") as mock_emit:

            def check() -> None:
                # PaymentTerminalDedupe table has exactly one row for
                # (tenant, payment_id, CAPTURED).
                assert (
                    PaymentTerminalDedupe.objects.filter(
                        tenant_id=tenant.id,
                        payment_id=UUID(PAYMENT_ID),
                        terminal_state=PaymentTerminalDedupe.TerminalState.CAPTURED,
                    ).count()
                    == 1
                )
                # Loyalty fan-out emitted exactly once.
                assert mock_emit.call_count == 1
                assert mock_emit.call_args[0][0] == "loyalty_bonus_eligible"

                conversation.refresh_from_db()
                assert conversation.last_payment_captured_at == env.occurred_at

            _dispatch_3x_and_assert(env, side_effect_check=check)

    def test_payment_failed_idempotent(
        self, tenant: Tenant, conversation: Conversation, settings
    ) -> None:
        # Threshold high so single replay batch doesn't trigger skill emit.
        settings.PAYMENT_FAILED_HANDOFF_THRESHOLD = 10

        env = _envelope(
            event_name="payment.failed",
            data={
                "payment_id": PAYMENT_ID,
                "appointment_id": APPOINTMENT_ID,
                "reason": "card_declined",
                "failed_at": "2026-05-21T14:48:02.503Z",
            },
        )

        with patch("apps.eventbus.consumers.payment.emit_internal_event") as mock_emit:

            def check() -> None:
                conversation.refresh_from_db()
                # Counter incremented exactly ONCE despite 3 dispatches.
                assert conversation.consecutive_payment_failures == 1
                assert conversation.last_payment_failure_code == "card_declined"
                # No skill emit (below threshold) — counter == 1 < 10.
                assert mock_emit.call_count == 0

            _dispatch_3x_and_assert(env, side_effect_check=check)

    def test_payment_refunded_idempotent(self, tenant: Tenant, conversation: Conversation) -> None:
        env = _envelope(
            event_name="payment.refunded",
            data={
                "payment_id": PAYMENT_ID,
                "appointment_id": APPOINTMENT_ID,
                "amount": "1800.00",
                "reason": "service_issue",
                "refunded_at": "2026-05-23T10:15:42.881Z",
            },
        )

        with patch("apps.eventbus.consumers.payment.emit_internal_event") as mock_emit:

            def check() -> None:
                # Terminal dedupe row for refund exists exactly once.
                assert (
                    PaymentTerminalDedupe.objects.filter(
                        tenant_id=tenant.id,
                        payment_id=UUID(PAYMENT_ID),
                        terminal_state=PaymentTerminalDedupe.TerminalState.REFUNDED,
                    ).count()
                    == 1
                )
                # Loyalty reverse emitted exactly once.
                assert mock_emit.call_count == 1
                assert mock_emit.call_args[0][0] == "loyalty_refund_reverse"

                conversation.refresh_from_db()
                assert conversation.last_payment_refunded_at == env.occurred_at

            _dispatch_3x_and_assert(env, side_effect_check=check)


# ─── catalog.service.updated (#444) ────────────────────────────────────────


class TestCatalogIdempotency:
    """The ``service.updated`` consumer mirrors Ayla service changes
    into ``CatalogService``. Idempotency contract: 3 dispatches of the
    same envelope bump ``cache_version`` exactly ONCE (the dispatcher's
    IngestDedupe blocks the 2nd and 3rd dispatches from reaching the
    handler)."""

    def test_service_updated_idempotent(self, tenant: Tenant) -> None:
        # Pre-existing mirror row for the Ayla service.
        mirror = CatalogService.all_tenants.create(
            tenant=tenant,
            external_id=42,
            ayla_service_id=UUID(SERVICE_ID),
            external_updated_at=dt.datetime(2026, 5, 20, 10, 0, tzinfo=dt.timezone.utc),
            slug="haircut",
            name="Стрижка",
            cache_version=0,
        )

        env = _envelope(
            event_name="service.updated",
            data={
                "service_id": SERVICE_ID,
                "changed_fields": ["price"],
                "previous_values": {"price": "1800.00"},
            },
        )

        def check() -> None:
            mirror.refresh_from_db()
            # Counter == 1 across all 3 dispatches (dispatcher
            # IngestDedupe short-circuits 2nd + 3rd before reaching
            # the handler).
            assert mirror.cache_version == 1

        _dispatch_3x_and_assert(env, side_effect_check=check)


# ─── identity.user.profile.updated (#446) ──────────────────────────────────


class TestIdentityIdempotency:
    """The ``user.profile.updated`` consumer fetches the PII subset
    (display_name + avatar_url) from Ayla REST and writes it to
    BotUser. Idempotency contract: 3 dispatches of the same envelope
    cause exactly ONE REST fetch + ONE DB write at the dispatcher
    layer (IngestDedupe blocks 2nd + 3rd).

    Note on tenant_id: ``user.profile.updated`` is the only event
    family where tenant_id MAY be null per §3.12. The contract test
    uses non-null TENANT_ID for fixture simplicity (matches the rest
    of the suite); the null-tenant case is covered separately in
    apps/eventbus/tests/test_identity_consumer.py.
    """

    def test_user_profile_updated_idempotent(self, tenant: Tenant) -> None:
        from unittest.mock import patch

        from apps.integrations.ayla.profile_client import ProfileFields

        bot_user = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="9001",
            chat_id="chat-9001",
            ayla_user_id=AYLA_USER_ID,
            display_name="Old",
        )

        env = _envelope(
            event_name="user.profile.updated",
            data={
                "user_id": AYLA_USER_ID,
                "changed_fields": ["display_name"],
            },
        )

        with patch(
            "apps.eventbus.consumers.identity.fetch_profile_fields",
            return_value=ProfileFields(display_name="New Name", avatar_url=""),
        ) as mock_fetch:

            def check() -> None:
                bot_user.refresh_from_db()
                # 1st dispatch wrote "New Name"; 2nd + 3rd
                # short-circuit at IngestDedupe BEFORE handler runs,
                # so REST is NOT called again and the value stays.
                assert bot_user.display_name == "New Name"
                # REST called exactly once across all 3 dispatches.
                assert mock_fetch.call_count == 1

            _dispatch_3x_and_assert(env, side_effect_check=check)


# ─── schedule.master.schedule.updated (#445) ───────────────────────────────


class TestScheduleIdempotency:
    """The ``master.schedule.updated`` consumer bumps CatalogMaster's
    cache_version. 3 dispatches of the same envelope cause exactly
    ONE bump (IngestDedupe blocks 2nd + 3rd before the handler runs)."""

    def test_master_schedule_updated_idempotent(self, tenant: Tenant) -> None:
        from apps.catalog.models import CatalogMaster

        master_id = "8d4e5f6a-7b8c-4d9e-0f1a-2b3c4d5e6f7a"
        mirror = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=99,
            ayla_user_id=UUID(master_id),
            external_updated_at=dt.datetime(2026, 5, 20, 10, 0, tzinfo=dt.timezone.utc),
            name="Анна",
            cache_version=0,
        )

        env = _envelope(
            event_name="master.schedule.updated",
            data={
                "master_id": master_id,
                "change_type": "vacation_set",
                "affected_all_dates": False,
                "affected_dates": ["2026-06-15"],
            },
        )

        def check() -> None:
            mirror.refresh_from_db()
            assert mirror.cache_version == 1

        _dispatch_3x_and_assert(env, side_effect_check=check)


# ─── reviews.review.created (#445) ─────────────────────────────────────────


class TestReviewsIdempotency:
    """The ``review.created`` consumer writes a ReviewProcessedDedupe
    row (per-(tenant, review_id) idempotency). 3 dispatches cause
    exactly ONE dedupe row (IngestDedupe blocks 2nd + 3rd)."""

    def test_review_created_idempotent(self, tenant: Tenant) -> None:
        from apps.eventbus.models import ReviewProcessedDedupe

        review_id = "6d7e8f9a-0b1c-4d2e-3f4a-5b6c7d8e9f0a"
        env = _envelope(
            event_name="review.created",
            data={
                "review_id": review_id,
                "appointment_id": APPOINTMENT_ID,
                "rating": 5,
                "has_text": True,
                "is_anonymous": False,
            },
        )

        def check() -> None:
            assert (
                ReviewProcessedDedupe.objects.filter(
                    tenant_id=tenant.id, review_id=UUID(review_id)
                ).count()
                == 1
            )

        _dispatch_3x_and_assert(env, side_effect_check=check)


# ─── All consumer families shipped — DLQ smoke retired ─────────────────────


class TestUnshippedConsumerNamesDeadLetter:
    """All 6 consumer families from #442-#446 are now shipped. The
    DLQ-smoke harness from Round-1 M1 (forcing-function for unshipped
    consumers) is retained as a single negative test that pins the
    «unknown event_name → UNKNOWN_EVENT_NAME via DLQ» dispatcher
    contract. When a NEW event family is added to the §3 catalog
    without a handler, this test would still pass (different code
    path: UNKNOWN_EVENT_NAME, not UNKNOWN_EVENT_VERSION) — which is
    correct, that's the dispatcher's responsibility, not this file's.
    """

    def test_truly_unknown_event_name_dlq(self) -> None:
        """An event name NOT in event-contract.md §3 catalog → DLQ via
        UNKNOWN_EVENT_NAME outcome."""
        env = _envelope(event_name="totally.fake.event", data={})
        result = dispatch_envelope(env)
        assert result.outcome == DispatchOutcome.UNKNOWN_EVENT_NAME
