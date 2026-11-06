"""``appointment.rescheduled`` canonical DER contract coverage
(AYLA-DEC-0022, AYLA-DEC-0036 — fix for P1 review findings on commit
26bc616, AGENT_BOT_FIX_CANONICAL_RESCHEDULE_CONSUMER).

Exercises the canonical handler (:func:`handle_appointment_rescheduled_canonical`)
through the REAL dispatcher (not handler-direct calls like
``test_booking_consumer.py``) so dedupe/DLQ/registry behaviour is
covered, not just the handler body. Also covers that the legacy
``booking.rescheduled`` contract (:func:`handle_booking_rescheduled`)
is unaffected — the two are SEPARATE handlers with distinct wire
payloads (see ``apps.eventbus.consumers.booking`` module docstring).

Fixtures build the REAL DER wire shape (``version``/``previous_version``/
``revision_id``/``changed_fields``/``actor``, optional ``starts_at``/
``previous_starts_at``) — NOT the legacy payload
(``new_start_at``/``old_start_at``/``rescheduled_by``) under the new
event name, which is what the original (pre-fix) version of this file
incorrectly did.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest
from django.db import connection

from apps.booking.models import BookingReminder, RemoteBookingProxy
from apps.conversations.models import Conversation
from apps.eventbus import ingest_dispatcher as dispatcher_module
from apps.eventbus.consumers.booking import (
    _CANONICAL_RESCHEDULE_ACTORS,
    CanonicalReschedulePayloadError,
    CanonicalReschedulePendingProxyError,
    CanonicalRescheduleVersionGapError,
    _parse_canonical_reschedule_data,
    register_booking_handlers,
)
from apps.eventbus.ingest_dispatcher import DispatchOutcome, dispatch_envelope
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.models import HandlerFailureTracker, IngestDedupe, IngestDLQ
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant
from tests.fixtures.contracts import load_contract


pytestmark = pytest.mark.django_db

TENANT_ID = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"
AYLA_USER_ID = "f1a2b3c4-d5e6-4789-9abc-def012345678"
APPOINTMENT_ID = "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"


@pytest.fixture(autouse=True)
def _enable_tenant_verify_fail_open(settings) -> None:
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True


@pytest.fixture(autouse=True)
def _wired_production_registry():
    """Register the REAL booking handlers — a fresh snapshot/restore per
    test so this module can't leak state into (or inherit it from)
    sibling test modules that also mutate the process-local registry."""
    snapshot = dict(dispatcher_module._REGISTRY)
    dispatcher_module._REGISTRY.clear()
    register_booking_handlers()
    try:
        yield
    finally:
        dispatcher_module._REGISTRY.clear()
        dispatcher_module._REGISTRY.update(snapshot)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(id=TENANT_ID, slug="t-alias", name="Alias test tenant")


@pytest.fixture
def bot_user_linked(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="9001",
        chat_id="chat-9001",
        ayla_user_id=AYLA_USER_ID,
    )


@pytest.fixture
def existing_proxy(tenant: Tenant) -> RemoteBookingProxy:
    """Bootstrap state: ``last_applied_appointment_version`` is NULL,
    as for any proxy written only via legacy booking.created/.rescheduled
    that has never seen a canonical event."""
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=UUID(APPOINTMENT_ID),
        tenant=tenant,
        bot_user=None,
        start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
        end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
        status="confirmed",
    )


@pytest.fixture
def existing_proxy_v5_linked(tenant: Tenant, bot_user_linked: BotUser) -> RemoteBookingProxy:
    """A proxy that has already applied canonical version 5 — the
    baseline for version-ordering tests (apply/skip/gap)."""
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=UUID(APPOINTMENT_ID),
        tenant=tenant,
        bot_user=bot_user_linked,
        start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
        end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
        status="confirmed",
        last_applied_appointment_version=5,
        last_synced_event_id="evt-seed-v5",
    )


def _legacy_envelope(
    *,
    event_id: str,
    new_start_at: str = "2026-05-23T11:00:00+00:00",
    appointment_id: str = APPOINTMENT_ID,
    tenant_id: str | None = TENANT_ID,
) -> IngestEnvelope:
    """Legacy ``booking.rescheduled`` wire payload — NOT the DER shape."""
    data: dict[str, Any] = {
        "appointment_id": appointment_id,
        "old_start_at": "2026-05-22T15:00:00+00:00",
        "new_start_at": new_start_at,
        "rescheduled_by": "admin",
    }
    return IngestEnvelope(
        event_id=event_id,
        event_name="booking.rescheduled",
        event_version=1,
        occurred_at=dt.datetime(2026, 5, 21, 14, 32, 11, tzinfo=dt.timezone.utc),
        tenant_id=tenant_id,
        user_id=AYLA_USER_ID,
        actor="user",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        causation_id=None,
        data=data,
    )


def _canonical_envelope(
    *,
    event_id: str,
    version: int,
    previous_version: int,
    appointment_id: str = APPOINTMENT_ID,
    tenant_id: str | None = TENANT_ID,
    starts_at: str | None = "2026-05-23T11:00:00+00:00",
    previous_starts_at: str | None = "2026-05-22T15:00:00+00:00",
    revision_id: str = "rev-1",
    changed_fields: list[str] | None = None,
    actor: Any = "admin",
    omit_fields: tuple[str, ...] = (),
) -> IngestEnvelope:
    """Real ``appointment.rescheduled`` DER wire payload."""
    data: dict[str, Any] = {
        "appointment_id": appointment_id,
        "version": version,
        "previous_version": previous_version,
        "revision_id": revision_id,
        "changed_fields": changed_fields if changed_fields is not None else ["starts_at"],
        "actor": actor,
    }
    if starts_at is not None:
        data["starts_at"] = starts_at
    if previous_starts_at is not None:
        data["previous_starts_at"] = previous_starts_at
    for field_name in omit_fields:
        data.pop(field_name, None)
    return IngestEnvelope(
        event_id=event_id,
        event_name="appointment.rescheduled",
        event_version=1,
        occurred_at=dt.datetime(2026, 5, 21, 14, 32, 11, tzinfo=dt.timezone.utc),
        tenant_id=tenant_id,
        user_id=AYLA_USER_ID,
        actor="user",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        causation_id=None,
        data=data,
    )


class TestCanonicalPayloadHandling:
    def test_canonical_with_starts_at_updates_schedule(
        self, tenant: Tenant, existing_proxy: RemoteBookingProxy
    ) -> None:
        result = dispatch_envelope(
            _canonical_envelope(event_id="evt-c1", version=1, previous_version=0)
        )
        assert result.outcome is DispatchOutcome.OK

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.start_at == dt.datetime(2026, 5, 23, 11, 0, tzinfo=dt.timezone.utc)
        # Duration (1h) preserved, same arithmetic as the legacy handler.
        assert proxy.end_at == dt.datetime(2026, 5, 23, 12, 0, tzinfo=dt.timezone.utc)
        assert proxy.last_applied_appointment_version == 1
        assert proxy.last_synced_event_id == "evt-c1"

    def test_canonical_without_starts_at_leaves_schedule_untouched(
        self, tenant: Tenant, existing_proxy: RemoteBookingProxy
    ) -> None:
        """A canonical event MAY only touch non-schedule fields — repo
        policy: mark the version transition applied, don't touch
        start_at/end_at/reminders, don't route to reconciliation (only
        version GAPS are reconciled)."""
        result = dispatch_envelope(
            _canonical_envelope(
                event_id="evt-c2",
                version=1,
                previous_version=0,
                starts_at=None,
                previous_starts_at=None,
                changed_fields=["specialist_id"],
            )
        )
        assert result.outcome is DispatchOutcome.OK

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.start_at == dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc)
        assert proxy.end_at == dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc)
        assert proxy.last_applied_appointment_version == 1
        assert proxy.last_synced_event_id == "evt-c2"

    @pytest.mark.parametrize(
        "omit",
        ["appointment_id", "version", "previous_version", "revision_id", "changed_fields", "actor"],
    )
    def test_canonical_missing_required_field_is_controlled_validation_failure(
        self, tenant: Tenant, existing_proxy: RemoteBookingProxy, omit: str
    ) -> None:
        result = dispatch_envelope(
            _canonical_envelope(
                event_id=f"evt-missing-{omit}",
                version=1,
                previous_version=0,
                omit_fields=(omit,),
            )
        )
        # DRF-2302 — битый payload на повторе так же битый: постоянный отказ.
        assert result.outcome is DispatchOutcome.REJECTED
        assert isinstance(result.exception, CanonicalReschedulePayloadError)

        # No side effect — proxy untouched.
        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.last_applied_appointment_version is None
        assert proxy.start_at == dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc)

    def test_canonical_out_of_order_no_proxy_retried_not_dead_lettered_silently(
        self, tenant: Tenant
    ) -> None:
        """Canonical reschedule arriving before the appointment's proxy
        exists must NOT be silently acknowledged (P1 review finding on
        commit 26bc616: a plain OK-return here would let the dispatcher
        commit an IngestDedupe row and permanently swallow the
        reschedule once booking.created later creates the proxy at its
        own pre-reschedule time). It must also not fabricate a proxy
        row."""
        result = dispatch_envelope(
            _canonical_envelope(
                event_id="evt-no-proxy",
                version=1,
                previous_version=0,
                appointment_id="deadbeef-2222-2222-2222-000000000000",
            )
        )
        assert result.outcome is DispatchOutcome.HANDLER_EXCEPTION
        assert isinstance(result.exception, CanonicalReschedulePendingProxyError)
        assert (
            RemoteBookingProxy.all_tenants.filter(
                appointment_id="deadbeef-2222-2222-2222-000000000000"
            ).count()
            == 0
        )
        # The handler's transaction rolled back — no IngestDedupe row
        # was committed for this event_id, so it is NOT lost: Ayla's
        # retry of the same event_id is not a duplicate (see the next
        # test). Observable via the existing DLQ/tracker mechanism too.
        assert not IngestDedupe.objects.filter(event_id="evt-no-proxy").exists()
        tracker = HandlerFailureTracker.objects.get(event_id="evt-no-proxy")
        assert tracker.attempt_count == 1

    def test_canonical_pending_proxy_retry_succeeds_once_proxy_exists(self, tenant: Tenant) -> None:
        """Once booking.created lands (proxy exists), Ayla's retry of
        the SAME event_id that previously failed with
        CanonicalReschedulePendingProxyError must apply cleanly — proof
        the earlier failure did not get silently dead-lettered."""
        appointment_id = "deadbeef-3333-3333-3333-000000000000"
        env = _canonical_envelope(
            event_id="evt-pending-retry",
            version=1,
            previous_version=0,
            appointment_id=appointment_id,
        )

        first = dispatch_envelope(env)
        assert first.outcome is DispatchOutcome.HANDLER_EXCEPTION
        assert isinstance(first.exception, CanonicalReschedulePendingProxyError)

        # booking.created lands (simulated directly via ORM — the
        # handler itself is exercised in test_booking_consumer.py).
        RemoteBookingProxy.all_tenants.create(
            appointment_id=UUID(appointment_id),
            tenant=tenant,
            bot_user=None,
            start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
            end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
            status="confirmed",
        )

        retry = dispatch_envelope(env)
        assert retry.outcome is DispatchOutcome.OK

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(appointment_id))
        assert proxy.start_at == dt.datetime(2026, 5, 23, 11, 0, tzinfo=dt.timezone.utc)
        assert proxy.last_applied_appointment_version == 1


# DRF-2673 — the registry's six, spelled out HERE and not read from the
# consumer's constant: a node built from the constant would stay green
# while the constant lost ``external_system``.
REGISTRY_ACTORS = ("user", "specialist", "admin", "owner", "system", "external_system")

# A value that must never surface anywhere. Shaped like what a wrong
# producer would put in an initiator field: a person.
LEAK_SENTINEL = "+70000000000 sentinel-leak-2673"


class TestActorClosedList:
    """DRF-2673 — payload ``actor`` is checked against the closed list of
    ``Ayla Domain Event Registry`` §6.3, not for presence alone, and a
    refused value is not echoed (``pseudonymous_identifiers_only``).

    «Событие принято» is not this class's subject: with ``actor="admin"``
    it passes on a consumer that checks nothing.
    """

    def test_consumer_list_is_the_registrys_six(self) -> None:
        assert _CANONICAL_RESCHEDULE_ACTORS == frozenset(REGISTRY_ACTORS)
        assert len(REGISTRY_ACTORS) == 6

    @pytest.mark.parametrize("actor", REGISTRY_ACTORS)
    def test_each_registry_actor_is_accepted_and_applied(
        self, tenant: Tenant, existing_proxy: RemoteBookingProxy, actor: str
    ) -> None:
        """Positive control on EVERY value: a list one short would refuse
        a lawful event — permanently, 422 + DLQ, the reschedule lost."""
        result = dispatch_envelope(
            _canonical_envelope(
                event_id=f"evt-actor-{actor}", version=1, previous_version=0, actor=actor
            )
        )
        assert result.outcome is DispatchOutcome.OK

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.start_at == dt.datetime(2026, 5, 23, 11, 0, tzinfo=dt.timezone.utc)
        assert proxy.last_applied_appointment_version == 1
        assert not IngestDLQ.objects.filter(event_id=f"evt-actor-{actor}").exists()

    @pytest.mark.parametrize(
        ("case", "actor", "cause"),
        [
            # Neighbouring vocabularies — each a real word one field over.
            ("master", "master", "actor_not_in_registry_list"),  # §3.2 cancelled_by
            ("client", "client", "actor_not_in_registry_list"),  # producer's internal name
            ("salon", "salon", "actor_not_in_registry_list"),  # producer's internal name
            ("human", "human", "actor_not_in_registry_list"),  # outbound envelope actor.type
            # The list is exact: no case folding, no trimming.
            ("uppercase", "Admin", "actor_not_in_registry_list"),
            ("padded", " admin", "actor_not_in_registry_list"),
            ("free-text", LEAK_SENTINEL, "actor_not_in_registry_list"),
            # Not a string. The object is the shape this repository's own
            # contract fixture carried until DRF-2673.
            ("object", {"type": "system"}, "actor_not_a_string"),
            ("list", ["admin"], "actor_not_a_string"),
            ("int", 7, "actor_not_a_string"),
            ("bool", True, "actor_not_a_string"),
        ],
    )
    def test_actor_outside_the_list_is_rejected_with_its_cause(
        self,
        tenant: Tenant,
        existing_proxy: RemoteBookingProxy,
        caplog: pytest.LogCaptureFixture,
        case: str,
        actor: Any,
        cause: str,
    ) -> None:
        event_id = f"evt-actor-bad-{case}"
        with (
            caplog.at_level(logging.WARNING, logger="apps.eventbus"),
            patch("apps.eventbus.consumers.booking.emit_internal_event") as mock_emit,
        ):
            result = dispatch_envelope(
                _canonical_envelope(event_id=event_id, version=1, previous_version=0, actor=actor)
            )

        assert result.outcome is DispatchOutcome.REJECTED
        assert isinstance(result.exception, CanonicalReschedulePayloadError)
        # §8.12 slug — what the 422 body, the audit row and the DLQ carry.
        assert result.exception.reason == "invalid_payload"
        assert IngestDLQ.objects.filter(event_id=event_id, reason="invalid_payload").count() == 1
        # The cause is told apart in the consumer's own log line.
        assert f"actor_rejected cause={cause}" in caplog.text

        # Nothing applied, nothing reported to analytics.
        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.last_applied_appointment_version is None
        assert proxy.start_at == dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc)
        assert not IngestDedupe.objects.filter(event_id=event_id).exists()
        mock_emit.assert_not_called()

    @pytest.mark.parametrize("actor", [None, ""])
    def test_empty_actor_is_rejected_as_missing(
        self, tenant: Tenant, existing_proxy: RemoteBookingProxy, actor: Any
    ) -> None:
        """``required`` in the registry: present-but-empty is absent."""
        result = dispatch_envelope(
            _canonical_envelope(
                event_id=f"evt-actor-empty-{actor!r}", version=1, previous_version=0, actor=actor
            )
        )
        assert result.outcome is DispatchOutcome.REJECTED
        assert isinstance(result.exception, CanonicalReschedulePayloadError)

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.last_applied_appointment_version is None

    @pytest.mark.parametrize(
        ("case", "actor"),
        [
            ("string", LEAK_SENTINEL),
            ("object", {"type": LEAK_SENTINEL}),
            ("list", [LEAK_SENTINEL]),
        ],
    )
    def test_refused_actor_value_is_not_echoed(
        self,
        tenant: Tenant,
        existing_proxy: RemoteBookingProxy,
        caplog: pytest.LogCaptureFixture,
        case: str,
        actor: Any,
    ) -> None:
        """The raw value reaches neither the log, nor the exception text
        (what an error tracker would show), nor analytics, nor the DLQ."""
        event_id = f"evt-actor-leak-{case}"
        with (
            caplog.at_level(logging.DEBUG),
            patch("apps.eventbus.consumers.booking.emit_internal_event") as mock_emit,
        ):
            result = dispatch_envelope(
                _canonical_envelope(event_id=event_id, version=1, previous_version=0, actor=actor)
            )

        # Presence first: the refusal happened and WAS logged — an empty
        # capture would make the absence below a hope, not a check.
        assert result.outcome is DispatchOutcome.REJECTED
        assert "eventbus.consumer.appointment_rescheduled.actor_rejected" in caplog.text
        assert "eventbus.ingest.rejected" in caplog.text
        rendered = caplog.text + "".join(repr(r.args) for r in caplog.records)
        assert event_id in rendered

        assert "sentinel-leak-2673" not in rendered
        assert "actor rejected" in repr(result.exception)
        assert "sentinel-leak-2673" not in repr(result.exception)
        mock_emit.assert_not_called()

        # The DLQ row is there and holds the payload's keys — and not the
        # value: ingest_redaction collapses a string outside its allowlist.
        dlq = IngestDLQ.objects.get(event_id=event_id, reason="invalid_payload")
        assert dlq.raw_body["data"]["appointment_id"] == APPOINTMENT_ID
        assert "actor" in dlq.raw_body["data"]
        assert "sentinel-leak-2673" not in repr(dlq.raw_body)

    def test_contract_fixture_passes_the_consumer(self) -> None:
        """The shared fixture and the consumer agree on ``actor``. Until
        DRF-2673 nothing fed one to the other: the fixture carried an
        object while the producer writes a bare string."""
        data = load_contract("appointment.rescheduled.v1.json")["data"]
        parsed = _parse_canonical_reschedule_data(data)
        assert parsed.actor == "system"


class TestLegacyContractUnaffected:
    def test_legacy_rescheduled_still_works_with_old_payload(
        self, tenant: Tenant, existing_proxy: RemoteBookingProxy
    ) -> None:
        result = dispatch_envelope(
            _legacy_envelope(event_id="evt-legacy-1", new_start_at="2026-05-23T11:00:00+00:00")
        )
        assert result.outcome is DispatchOutcome.OK

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.start_at == dt.datetime(2026, 5, 23, 11, 0, tzinfo=dt.timezone.utc)
        assert proxy.last_synced_event_id == "evt-legacy-1"
        # Legacy contract carries no version — never sets this field.
        assert proxy.last_applied_appointment_version is None


class TestVersionAwareOrdering:
    def test_version_applies_when_previous_version_matches_last_applied(
        self, tenant: Tenant, existing_proxy_v5_linked: RemoteBookingProxy
    ) -> None:
        result = dispatch_envelope(
            _canonical_envelope(event_id="evt-v6", version=6, previous_version=5)
        )
        assert result.outcome is DispatchOutcome.OK

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.last_applied_appointment_version == 6
        assert proxy.start_at == dt.datetime(2026, 5, 23, 11, 0, tzinfo=dt.timezone.utc)

    def test_duplicate_version_is_skipped(
        self, tenant: Tenant, existing_proxy_v5_linked: RemoteBookingProxy
    ) -> None:
        """A NEW event_id carrying an already-applied version (version ==
        last_applied) — event_id dedupe alone would not catch this since
        it's a distinct event_id; the version check must."""
        result = dispatch_envelope(
            _canonical_envelope(
                event_id="evt-dup-v5",
                version=5,
                previous_version=4,
                starts_at="2026-06-01T00:00:00+00:00",
            )
        )
        assert (
            result.outcome is DispatchOutcome.OK
        )  # handler ran, no-op'd (not dispatcher DUPLICATE)

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.last_applied_appointment_version == 5
        assert proxy.start_at == dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc)
        assert proxy.last_synced_event_id == "evt-seed-v5"

    def test_stale_version_after_newer_does_not_roll_back_proxy_or_reminders(
        self,
        tenant: Tenant,
        existing_proxy_v5_linked: RemoteBookingProxy,
        bot_user_linked: BotUser,
    ) -> None:
        for kind in (BookingReminder.Kind.DAY_BEFORE, BookingReminder.Kind.TWO_HOURS):
            BookingReminder.all_tenants.create(
                ayla_appointment_id=existing_proxy_v5_linked.appointment_id,
                tenant=tenant,
                kind=kind,
                bot_user=bot_user_linked,
                yclients_record_id=None,
                chat_id="chat-9001",
                visit_at=existing_proxy_v5_linked.start_at,
                status=BookingReminder.Status.PENDING,
                scheduled_at=existing_proxy_v5_linked.start_at - dt.timedelta(hours=1),
            )

        apply_result = dispatch_envelope(
            _canonical_envelope(
                event_id="evt-v6-apply",
                version=6,
                previous_version=5,
                starts_at="2026-05-23T11:00:00+00:00",
            )
        )
        assert apply_result.outcome is DispatchOutcome.OK

        stale_result = dispatch_envelope(
            _canonical_envelope(
                event_id="evt-v5-stale-retry",
                version=5,
                previous_version=4,
                starts_at="2026-05-20T09:00:00+00:00",
            )
        )
        assert stale_result.outcome is DispatchOutcome.OK

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.last_applied_appointment_version == 6
        assert proxy.start_at == dt.datetime(2026, 5, 23, 11, 0, tzinfo=dt.timezone.utc)
        assert proxy.last_synced_event_id == "evt-v6-apply"

        rems = BookingReminder.all_tenants.filter(
            ayla_appointment_id=existing_proxy_v5_linked.appointment_id
        )
        assert rems.count() == 2
        for r in rems:
            assert r.visit_at == dt.datetime(2026, 5, 23, 11, 0, tzinfo=dt.timezone.utc)

    def test_stale_version_does_not_roll_back_conversation(
        self,
        tenant: Tenant,
        existing_proxy_v5_linked: RemoteBookingProxy,
        bot_user_linked: BotUser,
    ) -> None:
        dispatch_envelope(
            _canonical_envelope(
                event_id="evt-v6-apply-conv",
                version=6,
                previous_version=5,
                starts_at="2026-05-23T11:00:00+00:00",
            )
        )
        stale_result = dispatch_envelope(
            _canonical_envelope(
                event_id="evt-v5-stale-conv",
                version=5,
                previous_version=4,
                starts_at="2026-05-20T09:00:00+00:00",
            )
        )
        assert stale_result.outcome is DispatchOutcome.OK

        conv = Conversation.all_tenants.get(tenant=tenant, bot_user=bot_user_linked)
        assert conv.last_booking_at == dt.datetime(2026, 5, 23, 11, 0, tzinfo=dt.timezone.utc)

    def test_version_gap_not_applied_and_observable(
        self, tenant: Tenant, existing_proxy_v5_linked: RemoteBookingProxy
    ) -> None:
        result = dispatch_envelope(
            _canonical_envelope(
                event_id="evt-gap",
                version=9,
                previous_version=8,
                starts_at="2026-05-25T09:00:00+00:00",
            )
        )
        assert result.outcome is DispatchOutcome.HANDLER_EXCEPTION
        assert isinstance(result.exception, CanonicalRescheduleVersionGapError)

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.last_applied_appointment_version == 5
        assert proxy.start_at == dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc)

        # Observable via the EXISTING retry/DLQ mechanism (#433) — no new
        # reconciliation infrastructure introduced.
        tracker = HandlerFailureTracker.objects.get(event_id="evt-gap")
        assert tracker.attempt_count == 1

    def test_bootstrap_accepts_first_canonical_event_regardless_of_previous_version(
        self, tenant: Tenant, existing_proxy: RemoteBookingProxy
    ) -> None:
        """``existing_proxy`` has last_applied_appointment_version=NULL
        (written only via legacy booking.created) — the first canonical
        event must be accepted unconditionally and seed the baseline,
        even though its previous_version (41) can't be verified against
        any local canonical history."""
        result = dispatch_envelope(
            _canonical_envelope(
                event_id="evt-bootstrap",
                version=42,
                previous_version=41,
                starts_at="2026-05-23T11:00:00+00:00",
            )
        )
        assert result.outcome is DispatchOutcome.OK

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.last_applied_appointment_version == 42
        assert proxy.start_at == dt.datetime(2026, 5, 23, 11, 0, tzinfo=dt.timezone.utc)


class TestDualDeliveryAndReplay:
    def test_canonical_and_legacy_dual_delivery_no_duplicate_reminders(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        """A transition period could plausibly see Ayla emit BOTH the
        legacy and canonical contracts for what is conceptually one
        reschedule. Two DIFFERENT event_ids means the dedupe layer
        treats them as two deliveries — but both route to idempotent
        (update-shaped) handlers, so the final state must converge with
        no duplicate BookingReminder rows."""
        proxy = RemoteBookingProxy.all_tenants.create(
            appointment_id=UUID(APPOINTMENT_ID),
            tenant=tenant,
            bot_user=bot_user_linked,
            start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
            end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
            status="confirmed",
        )
        for kind in (BookingReminder.Kind.DAY_BEFORE, BookingReminder.Kind.TWO_HOURS):
            BookingReminder.all_tenants.create(
                ayla_appointment_id=proxy.appointment_id,
                tenant=tenant,
                kind=kind,
                bot_user=bot_user_linked,
                yclients_record_id=None,
                chat_id="chat-9001",
                visit_at=proxy.start_at,
                status=BookingReminder.Status.PENDING,
                scheduled_at=proxy.start_at - dt.timedelta(hours=1),
            )

        legacy = dispatch_envelope(
            _legacy_envelope(event_id="evt-legacy-name", new_start_at="2026-05-23T10:00:00+00:00")
        )
        canonical = dispatch_envelope(
            _canonical_envelope(
                event_id="evt-canonical-name",
                version=1,
                previous_version=0,
                starts_at="2026-05-23T11:00:00+00:00",
            )
        )

        assert legacy.outcome is DispatchOutcome.OK
        assert canonical.outcome is DispatchOutcome.OK

        final = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        # Canonical delivered last — its value wins. The two contracts
        # are arbitrated by delivery order between each other (the
        # legacy contract carries no version to arbitrate with); version
        # ordering only governs canonical-vs-canonical deliveries.
        assert final.start_at == dt.datetime(2026, 5, 23, 11, 0, tzinfo=dt.timezone.utc)
        assert final.last_applied_appointment_version == 1
        assert final.last_synced_event_id == "evt-canonical-name"

        rems = BookingReminder.all_tenants.filter(ayla_appointment_id=proxy.appointment_id)
        assert rems.count() == 2
        for r in rems:
            assert r.visit_at == dt.datetime(2026, 5, 23, 11, 0, tzinfo=dt.timezone.utc)
            assert r.status == BookingReminder.Status.PENDING

    def test_canonical_replay_3x_via_dispatcher_single_side_effect(
        self, tenant: Tenant, existing_proxy: RemoteBookingProxy
    ) -> None:
        env = _canonical_envelope(event_id="evt-replay", version=1, previous_version=0)

        outcomes = [dispatch_envelope(env).outcome for _ in range(3)]

        assert outcomes == [
            DispatchOutcome.OK,
            DispatchOutcome.DUPLICATE,
            DispatchOutcome.DUPLICATE,
        ]
        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.start_at == dt.datetime(2026, 5, 23, 11, 0, tzinfo=dt.timezone.utc)
        assert proxy.last_applied_appointment_version == 1


class TestConcurrentCanonicalDelivery:
    """P1 review finding on the first cut of this handler (commit
    26bc616): an unlocked proxy read + a separate later UPDATE let two
    concurrent deliveries (different ``event_id``s — e.g. a redelivery
    race, or genuine concurrent transport) both read the same
    ``last_applied_appointment_version`` and both apply, defeating
    version-based idempotency. Fix: ``select_for_update()`` on the
    proxy fetch (apps/eventbus/consumers/booking.py) serializes
    concurrent handler executions for the SAME ``appointment_id``.

    ``select_for_update()`` is a documented no-op on SQLite — this
    repo's local/default test backend (see config/settings/base.py;
    also noted in ``test_booking_consumer.py`` for the analogous
    ``get_or_create`` IntegrityError race). A row-lock assertion on
    SQLite would either pass by accident (Python GIL / thread
    scheduling happening to serialize the race) or flake — neither is
    real proof. SKIP rather than falsely pass; this test has teeth
    once run against Postgres (CI / ``POSTGRES_HOST`` set).
    """

    @pytest.mark.django_db(transaction=True)
    def test_concurrent_same_version_delivery_applies_exactly_once(
        self, tenant: Tenant, existing_proxy: RemoteBookingProxy
    ) -> None:
        if connection.vendor != "postgresql":
            pytest.skip(
                "select_for_update() row-locking is only meaningfully "
                f"testable against Postgres; this backend is {connection.vendor!r}."
            )

        barrier = threading.Barrier(2, timeout=10)
        outcomes: dict[str, DispatchOutcome] = {}
        errors: list[BaseException] = []

        def worker(name: str, event_id: str) -> None:
            from django.db import connection as thread_connection

            try:
                barrier.wait(timeout=10)
                result = dispatch_envelope(
                    _canonical_envelope(event_id=event_id, version=1, previous_version=0)
                )
                outcomes[name] = result.outcome
            except BaseException as exc:  # noqa: BLE001 — surfaced in the main thread below
                errors.append(exc)
            finally:
                thread_connection.close()

        t1 = threading.Thread(target=worker, args=("a", "evt-race-a"))
        t2 = threading.Thread(target=worker, args=("b", "evt-race-b"))
        with patch("apps.eventbus.consumers.booking.emit_internal_event") as mock_emit:
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

        assert not errors, errors
        assert outcomes == {"a": DispatchOutcome.OK, "b": DispatchOutcome.OK}

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.last_applied_appointment_version == 1

        # The row lock serializes the two deliveries: whichever thread
        # commits first advances last_applied_appointment_version to 1;
        # the other — blocked by select_for_update() until that commit
        # — re-reads the POST-commit state and takes the idempotent-
        # skip branch (version <= last_applied) instead of double-
        # applying. Exactly ONE emit_internal_event call proves this —
        # without the row lock, both threads could read the stale
        # last_applied=None and both apply, emitting twice.
        assert mock_emit.call_count == 1
