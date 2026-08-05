"""``reschedule_booking`` + ``execute_reschedule`` tool tests (B5 / DRF-841).

Reschedule is implemented as cancel-and-create (YClients has no native
reschedule endpoint). The partial-failure window — cancel succeeds,
create fails — is the destructive corner case: the test suite makes
sure the BookingRequest ends in ``CANCELLED`` (not ``RESCHEDULED``)
in that path, the caller gets a partial-failure error code, and no
retry of the create happens.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.test import override_settings
from django.utils import timezone as dj_timezone

from apps.booking.models import (
    BookingReminder,
    BookingRequest,
    PendingBookingAction,
    RemoteBookingProxy,
)
from apps.identity.models import BotUser
from apps.integrations.yclients import (
    AvailableTime,
    BookingRecord,
    UserRecord,
    YClientsAPIError,
    YClientsUnavailableError,
)
from apps.skills.booking.tools import execute_reschedule, reschedule_booking
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="resched-tools", name="Resched")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-r",
        chat_id="bu-r",
        phone="79991234567",
        client_name="Anna",
    )


class FakeClient:
    def __init__(self) -> None:
        self.cancel_calls: list[int] = []
        self.cancel_exc: Exception | None = None
        self.create_calls: list[dict[str, Any]] = []
        self.create_response: BookingRecord | None = None
        self.create_exc: Exception | None = None
        self.user_records: list[UserRecord] = []
        self.user_records_exc: Exception | None = None
        self.times: list[AvailableTime] = []
        self.times_exc: Exception | None = None
        self.reschedule_calls: list[dict[str, Any]] = []
        self.reschedule_response: BookingRecord | None = None
        self.reschedule_exc: Exception | None = None

    def cancel_record(self, *, record_id: int) -> bool:
        self.cancel_calls.append(record_id)
        if self.cancel_exc is not None:
            raise self.cancel_exc
        return True

    def create_record(self, **kwargs: Any) -> BookingRecord:
        self.create_calls.append(kwargs)
        if self.create_exc is not None:
            raise self.create_exc
        return self.create_response or BookingRecord(record_id=999, record_hash="h", raw={})

    def reschedule_record(
        self,
        *,
        record_id: int | str,
        datetime: str,
        expected_version: int | None = None,
    ) -> BookingRecord:
        self.reschedule_calls.append(
            {
                "record_id": record_id,
                "datetime": datetime,
                "expected_version": expected_version,
            }
        )
        if self.reschedule_exc is not None:
            raise self.reschedule_exc
        return self.reschedule_response or BookingRecord(
            record_id=0, record_hash="h", raw={"ayla_appointment_id": str(record_id)}
        )

    def get_user_records(self) -> list[UserRecord]:
        if self.user_records_exc is not None:
            raise self.user_records_exc
        return list(self.user_records)

    def get_available_times(self, **_: Any) -> list[AvailableTime]:
        if self.times_exc is not None:
            raise self.times_exc
        return list(self.times)


def _make_booking(
    tenant: Tenant,
    bot_user: BotUser,
    *,
    yc_id: int = 555,
) -> BookingRequest:
    with tenant_scope(tenant):
        return BookingRequest.objects.create(
            tenant=tenant,
            bot_user=bot_user,
            service_name="Массаж",
            master_name="Ольга",
            client_name="Anna",
            client_phone="79991234567",
            comment=f"Bot booking | yclients_record_id={yc_id}",
            source="bot",
            status=BookingRequest.Status.CONFIRMED,
        )


def _user_record(*, id_: int = 555, staff_id: int = 11, service_id: int = 22) -> UserRecord:
    return UserRecord(
        id=id_,
        services=[{"id": service_id, "title": "Массаж"}],
        company={},
        staff={"id": staff_id, "name": "Ольга"},
        date="2026-05-20T10:00:00",
        datetime="2026-05-20T10:00:00",
        seance_length=3600,
        raw={},
    )


def _future_iso(hours: int = 48) -> str:
    return (dj_timezone.now() + timedelta(hours=hours)).replace(microsecond=0).isoformat()


def _slot_at(new_dt: str) -> AvailableTime:
    """Build an :class:`AvailableTime` matching ``new_dt`` so the
    execute_reschedule slot-recheck (skills retro hotfix #4) finds the
    target slot still free.
    """
    return AvailableTime(
        time=new_dt.split("T", 1)[1][:5],
        datetime=new_dt,
        seance_length_s=3600,
    )


# ---------------------------------------------------------------------------
# reschedule_booking — preview path
# ---------------------------------------------------------------------------


class TestReschedulePreview:
    def test_happy_path_persists_pending(self, tenant: Tenant, bot_user: BotUser) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        client.user_records = [_user_record(id_=555, staff_id=11, service_id=22)]
        new_dt = _future_iso(48)
        # Slot the master has free at the new datetime.
        client.times = [
            AvailableTime(
                time=new_dt.split("T", 1)[1][:5],
                datetime=new_dt,
                seance_length_s=3600,
            )
        ]
        with tenant_scope(tenant):
            result = reschedule_booking(
                client=client,
                arguments={"record_id": 555, "new_datetime": new_dt},
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == ""
        assert result.pending is not None
        assert result.pending.kind == PendingBookingAction.Kind.RESCHEDULE
        # No destructive call yet.
        assert client.cancel_calls == []
        assert client.create_calls == []
        # Pending row carries staff/service we'll use on execute.
        row = PendingBookingAction.all_tenants.get(pk=result.pending.token)
        assert row.payload["master_id"] == 11
        assert row.payload["service_id"] == 22
        assert row.payload["new_datetime"] == new_dt

    def test_anti_hallucination_invalid_record(self, tenant: Tenant, bot_user: BotUser) -> None:
        client = FakeClient()
        with tenant_scope(tenant):
            result = reschedule_booking(
                client=client,
                arguments={
                    "record_id": 9999,
                    "new_datetime": _future_iso(48),
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "invalid_record_id"

    def test_past_datetime_rejected(self, tenant: Tenant, bot_user: BotUser) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        past = (dj_timezone.now() - timedelta(days=1)).replace(microsecond=0).isoformat()
        with tenant_scope(tenant):
            result = reschedule_booking(
                client=client,
                arguments={"record_id": 555, "new_datetime": past},
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "past_datetime"

    def test_invalid_datetime_format(self, tenant: Tenant, bot_user: BotUser) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        with tenant_scope(tenant):
            result = reschedule_booking(
                client=client,
                arguments={"record_id": 555, "new_datetime": "tomorrow at 2"},
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "invalid_datetime"

    def test_slot_unavailable_returns_clarification(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        client.user_records = [_user_record(id_=555)]
        # Master has no slot at the requested time.
        client.times = []
        with tenant_scope(tenant):
            result = reschedule_booking(
                client=client,
                arguments={
                    "record_id": 555,
                    "new_datetime": _future_iso(48),
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "slot_unavailable"

    def test_yclients_user_records_unreachable_returns_error(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        client.user_records_exc = YClientsUnavailableError("x")
        with tenant_scope(tenant):
            result = reschedule_booking(
                client=client,
                arguments={
                    "record_id": 555,
                    "new_datetime": _future_iso(48),
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "record_not_found"


# ---------------------------------------------------------------------------
# execute_reschedule — the actual cancel-and-create
# ---------------------------------------------------------------------------


class TestExecuteReschedule:
    def test_happy_path(self, tenant: Tenant, bot_user: BotUser) -> None:
        booking = _make_booking(tenant, bot_user, yc_id=555)
        # Pending reminder on the OLD record.
        visit_at = dj_timezone.now() + timedelta(days=1)
        BookingReminder.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id="555",
            chat_id="bu-r",
            visit_at=visit_at,
            kind=BookingReminder.Kind.DAY_BEFORE,
            status=BookingReminder.Status.PENDING,
            scheduled_at=visit_at - timedelta(hours=24),
            master_name="Ольга",
            service_name="Массаж",
        )
        client = FakeClient()
        client.create_response = BookingRecord(record_id=888, record_hash="h", raw={})
        new_dt = _future_iso(72)
        client.times = [_slot_at(new_dt)]
        with tenant_scope(tenant):
            result = execute_reschedule(
                client=client,
                payload={
                    "record_id": 555,
                    "new_datetime": new_dt,
                    "master_id": 11,
                    "service_id": 22,
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == ""
        assert result.confirmation is not None
        assert result.confirmation.record_id == 888

        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.RESCHEDULED

        # New BookingRequest created with the new record id marker.
        new_row = BookingRequest.all_tenants.filter(
            comment__contains="yclients_record_id=888",
        ).first()
        assert new_row is not None
        assert new_row.status == BookingRequest.Status.CONFIRMED
        assert "rescheduled_from=555" in new_row.comment

        # Old reminder cancelled, new reminders scheduled.
        old_reminder = BookingReminder.all_tenants.filter(
            yclients_record_id="555",
        ).first()
        assert old_reminder is not None
        assert old_reminder.status == BookingReminder.Status.CANCELLED

        new_reminder = BookingReminder.all_tenants.filter(
            yclients_record_id="888",
            kind=BookingReminder.Kind.DAY_BEFORE,
        ).first()
        assert new_reminder is not None
        assert new_reminder.status == BookingReminder.Status.PENDING

    def test_cancel_fails_keeps_old_booking(self, tenant: Tenant, bot_user: BotUser) -> None:
        booking = _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        client.cancel_exc = YClientsAPIError("http_404")
        new_dt = _future_iso(48)
        client.times = [_slot_at(new_dt)]
        with tenant_scope(tenant):
            result = execute_reschedule(
                client=client,
                payload={
                    "record_id": 555,
                    "new_datetime": new_dt,
                    "master_id": 11,
                    "service_id": 22,
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "yclients_cancel_failure"
        # Old booking left untouched.
        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.CONFIRMED
        # No create attempted.
        assert client.create_calls == []

    def test_partial_failure_old_cancelled_no_retry(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """The destructive corner case.

        Cancel succeeded but create failed. We MUST NOT retry the
        create — risk of double-booking. The old row flips to
        ``CANCELLED`` (not ``RESCHEDULED``) so the data layer reflects
        reality, and the error code lets the caller emit a manager
        notification.
        """
        booking = _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        client.create_exc = YClientsAPIError("http_400: slot collision")
        new_dt = _future_iso(48)
        client.times = [_slot_at(new_dt)]
        with tenant_scope(tenant):
            result = execute_reschedule(
                client=client,
                payload={
                    "record_id": 555,
                    "new_datetime": new_dt,
                    "master_id": 11,
                    "service_id": 22,
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "booking_reschedule_partial_failure"
        assert client.cancel_calls == [555]
        # Exactly ONE create attempt — no retry.
        assert len(client.create_calls) == 1

        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.CANCELLED

    def test_invalid_payload_short_circuits(self, tenant: Tenant, bot_user: BotUser) -> None:
        client = FakeClient()
        with tenant_scope(tenant):
            result = execute_reschedule(
                client=client,
                payload={"record_id": 555},
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "invalid_payload"
        assert client.cancel_calls == []

    def test_record_not_owned_rejected(self, tenant: Tenant, bot_user: BotUser) -> None:
        # No BookingRequest matches → ownership check fails at execute.
        client = FakeClient()
        with tenant_scope(tenant):
            result = execute_reschedule(
                client=client,
                payload={
                    "record_id": 555,
                    "new_datetime": _future_iso(48),
                    "master_id": 11,
                    "service_id": 22,
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "invalid_record_id"
        assert client.cancel_calls == []

    def test_new_row_carries_ai_direct_attribution(self, tenant: Tenant, bot_user: BotUser) -> None:
        """Phase 4a-IMPL1 closure: reschedule produces a NEW row with
        ai_direct attribution + reschedule-discriminator metadata.

        Without this the new row would default to booking_source='external'
        and fall out of the billing pipeline (and break LTV computation
        for Loyalty Phase 2.b).
        """
        from decimal import Decimal

        _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        client.create_response = BookingRecord(record_id=888, record_hash="h", raw={})
        new_dt = _future_iso(72)
        client.times = [_slot_at(new_dt)]
        with tenant_scope(tenant):
            execute_reschedule(
                client=client,
                payload={
                    "record_id": 555,
                    "new_datetime": new_dt,
                    "master_id": 11,
                    "service_id": 22,
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        new_row = BookingRequest.all_tenants.get(
            comment__contains="yclients_record_id=888",
        )
        assert new_row.booking_source == "ai_direct"
        assert new_row.billable is True
        assert new_row.ai_assist_score == Decimal("1.00")
        assert new_row.attribution_metadata["actor_type"] == "customer"
        assert new_row.attribution_metadata["created_by"] == "execute_reschedule"
        assert new_row.attribution_metadata["rescheduled_from_record_id"] == 555
        assert new_row.visit_at is not None

    def test_unparseable_new_datetime_rejects_before_cancel(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """Phase 4a-IMPL1 split-brain guard: unparseable new_datetime
        rejects BEFORE the cancel + create round-trip so we don't leave
        the old record cancelled and the new BookingRequest unwritten.
        """
        _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        with tenant_scope(tenant):
            result = execute_reschedule(
                client=client,
                payload={
                    "record_id": 555,
                    "new_datetime": "totally-not-iso",
                    "master_id": 11,
                    "service_id": 22,
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "invalid_payload"
        assert client.cancel_calls == []
        assert client.create_calls == []


# ──────────────────────────────────────────────────────────────────────────
# Skills retro hotfix #4 — TOCTOU recheck before destructive cancel
# ──────────────────────────────────────────────────────────────────────────


class TestRescheduleTocTouRecheck:
    """Between the preview's get_available_times call and the ✅ tap (up
    to 10 min of pending-action TTL) another customer can take the slot.
    Without the execute-time recheck, ``execute_reschedule`` would cancel
    the old record FIRST and only then learn the new slot is gone —
    leaving the user with no booking at all. The recheck guard makes
    the operation safely abort with ``slot_unavailable`` so the caller
    can ask for a different time, old booking intact.
    """

    def test_slot_taken_at_execute_aborts_before_cancel(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        booking = _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        # client.times left empty → recheck reports the slot taken.
        with tenant_scope(tenant):
            result = execute_reschedule(
                client=client,
                payload={
                    "record_id": 555,
                    "new_datetime": _future_iso(72),
                    "master_id": 11,
                    "service_id": 22,
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "slot_unavailable"
        # Critically: old booking is NOT touched. No destructive action.
        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.CONFIRMED
        assert client.cancel_calls == []
        assert client.create_calls == []

    def test_yclients_recheck_unreachable_aborts_before_cancel(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        # Recheck infrastructure failure must also fail closed — old
        # booking stays intact, surface yclients_unavailable so caller
        # can retry without data loss.
        booking = _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        client.times_exc = YClientsUnavailableError("network down")
        with tenant_scope(tenant):
            result = execute_reschedule(
                client=client,
                payload={
                    "record_id": 555,
                    "new_datetime": _future_iso(72),
                    "master_id": 11,
                    "service_id": 22,
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "yclients_unavailable"
        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.CONFIRMED
        assert client.cancel_calls == []


# ──────────────────────────────────────────────────────────────────────────
# Skills retro hotfix #1 — anchor idempotency lookup to documented prefix
# ──────────────────────────────────────────────────────────────────────────


class TestIdempotencyMarkerAnchored:
    """Substring-match on ``comment`` would let a spoofed service_name /
    client_name carrying ``yclients_record_id=<other-id>`` short-circuit
    a legitimate reschedule. The anchored ``startswith`` check on the
    documented ``"Bot booking | yclients_record_id="`` prefix is not
    spoofable via free-text payload.
    """

    def test_spoofed_substring_does_not_short_circuit_reschedule(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        # A pre-existing row with the spoofed marker in a NON-prefix
        # position (e.g. a free-text service_name carrying the string).
        # Without the anchor fix, this row would short-circuit
        # `existing_new` and skip writing the legitimate NEW row.
        _make_booking(tenant, bot_user, yc_id=555)
        with tenant_scope(tenant):
            BookingRequest.objects.create(
                tenant=tenant,
                bot_user=bot_user,
                service_name="totally legitimate",
                master_name="—",
                client_name="someone-else",
                client_phone="79990000000",
                # Marker buried mid-string, no "Bot booking | " prefix:
                comment="user note: see yclients_record_id=888 for prior visit",
                source="bot",
                status=BookingRequest.Status.CONFIRMED,
            )

        client = FakeClient()
        client.create_response = BookingRecord(record_id=888, record_hash="h", raw={})
        new_dt = _future_iso(72)
        client.times = [_slot_at(new_dt)]
        with tenant_scope(tenant):
            result = execute_reschedule(
                client=client,
                payload={
                    "record_id": 555,
                    "new_datetime": new_dt,
                    "master_id": 11,
                    "service_id": 22,
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == ""
        # The legitimate NEW row was written with the proper prefix
        # marker (substring spoof was correctly ignored).
        legit_new = BookingRequest.all_tenants.filter(
            comment__startswith="Bot booking | yclients_record_id=888",
        ).first()
        assert legit_new is not None
        assert legit_new.status == BookingRequest.Status.CONFIRMED
        # Hardened assertion (Code Reviewer Y2): exactly ONE anchored
        # row exists. The spoofed row carries the bare substring but no
        # prefix, so it MUST NOT count toward the anchored query — that
        # pins the contract: anchor is real, not coincidentally passing.
        assert (
            BookingRequest.all_tenants.filter(
                comment__startswith="Bot booking | yclients_record_id=888",
            ).count()
            == 1
        )


# ──────────────────────────────────────────────────────────────────────────
# Hotfix D — split-brain recovery between YClients-create and local-write
# ──────────────────────────────────────────────────────────────────────────


class TestLocalCreateSplitBrainRecovery:
    """The window between YClients.create_record succeeding and the
    local BookingRequest.all_tenants.create succeeding is the last
    split-brain gap. If the local write fails (DB transient, validator
    surprise), YClients has the new record but our DB doesn't.

    Recovery strategy:
    - Try to cancel the new YClients record (best-effort).
    - If compensate succeeded → flip old row to CANCELLED + emit
      booking.cancelled with shared correlation_id.
    - If compensate failed → leave old row RESCHEDULED, emit critical
      system.module.health.degraded alert for ops triage.
    - Always audit, always return error to caller.
    """

    def _payload(self, *, new_dt: str | None = None) -> dict[str, Any]:
        return {
            "record_id": 555,
            "new_datetime": new_dt or _future_iso(72),
            "master_id": 11,
            "service_id": 22,
            "master_name": "Ольга",
            "service_name": "Массаж",
            "client_phone": "79991234567",
            "client_name": "Anna",
        }

    def test_local_create_fails_compensate_succeeds_recovers_cleanly(
        self, tenant: Tenant, bot_user: BotUser, monkeypatch
    ) -> None:
        from apps.eventbus.models import DomainEvent

        booking = _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        client.create_response = BookingRecord(record_id=888, record_hash="h", raw={})
        new_dt = _future_iso(72)
        client.times = [_slot_at(new_dt)]

        # Force local BookingRequest.create to raise.
        from apps.skills.booking import tools as tools_module

        original_create = tools_module.BookingRequest.all_tenants.create

        def _crashing_create(*args, **kwargs):
            # Only fail the NEW create (suffix in comment marker), let the
            # _make_booking fixture's own create pass.
            if "rescheduled_from=555" in kwargs.get("comment", ""):
                raise RuntimeError("simulated DB transient")
            return original_create(*args, **kwargs)

        monkeypatch.setattr(tools_module.BookingRequest.all_tenants, "create", _crashing_create)

        with tenant_scope(tenant):
            result = execute_reschedule(
                client=client,
                payload=self._payload(new_dt=new_dt),
                tenant=tenant,
                bot_user=bot_user,
            )

        assert result.error == "booking_reschedule_local_create_failed"
        # YClients was told to cancel the new record (compensate).
        assert 888 in client.cancel_calls

        # Old row flipped from RESCHEDULED (set earlier) to CANCELLED.
        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.CANCELLED

        # booking.cancelled envelope emitted with reschedule_local_create_failed reason.
        cancel_ev = (
            DomainEvent.objects.filter(event_name="booking.cancelled")
            .order_by("-occurred_at")
            .first()
        )
        assert cancel_ev is not None
        assert cancel_ev.data["cancellation_reason"] == "reschedule_local_create_failed"

        # Warning-level health alert (compensate succeeded → not critical).
        alert = (
            DomainEvent.objects.filter(event_name="system.module.health.degraded")
            .order_by("-occurred_at")
            .first()
        )
        assert alert is not None
        assert alert.data["module_name"] == "booking.execute_reschedule"
        assert alert.data["severity"] == "warning"
        assert "split_brain_recovered" in alert.data["metric"]

    def test_local_create_fails_compensate_fails_alerts_critical(
        self, tenant: Tenant, bot_user: BotUser, monkeypatch
    ) -> None:
        from apps.eventbus.models import DomainEvent

        booking = _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        client.create_response = BookingRecord(record_id=888, record_hash="h", raw={})
        new_dt = _future_iso(72)
        client.times = [_slot_at(new_dt)]

        from apps.skills.booking import tools as tools_module

        original_create = tools_module.BookingRequest.all_tenants.create

        def _crashing_create(*args, **kwargs):
            if "rescheduled_from=555" in kwargs.get("comment", ""):
                raise RuntimeError("simulated DB transient")
            return original_create(*args, **kwargs)

        monkeypatch.setattr(tools_module.BookingRequest.all_tenants, "create", _crashing_create)

        # Compensate cancel ALSO fails — true unresolved split-brain.
        original_cancel = client.cancel_record

        def _failing_cancel(*, record_id: int):
            if record_id == 888:
                raise YClientsAPIError("simulated compensate cancel failure")
            return original_cancel(record_id=record_id)

        client.cancel_record = _failing_cancel  # type: ignore[method-assign]

        with tenant_scope(tenant):
            result = execute_reschedule(
                client=client,
                payload=self._payload(new_dt=new_dt),
                tenant=tenant,
                bot_user=bot_user,
            )

        assert result.error == "booking_reschedule_local_create_failed"

        # Old row STAYS in RESCHEDULED — we couldn't undo, so don't lie.
        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.RESCHEDULED

        # Critical-level health alert (compensate failed → ops paging).
        alert = (
            DomainEvent.objects.filter(event_name="system.module.health.degraded")
            .order_by("-occurred_at")
            .first()
        )
        assert alert is not None
        assert alert.data["severity"] == "critical"
        assert "split_brain_unresolved" in alert.data["metric"]
        assert "yc_id=888" in alert.data["metric"]


# ──────────────────────────────────────────────────────────────────────────
# RB1-D01 — Ayla native reschedule must send expected_version
# ──────────────────────────────────────────────────────────────────────────


class TestAylaRescheduleExpectedVersion:
    """Flag-ON path: optimistic concurrency version is captured from the
    mirror, stored in the pending action, and forwarded to the Ayla client.
    """

    def _make_ayla_booking(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        appt_id: uuid.UUID,
        version: int,
    ) -> tuple[RemoteBookingProxy, BookingRequest]:
        proxy = RemoteBookingProxy.all_tenants.create(
            appointment_id=appt_id,
            tenant=tenant,
            bot_user=bot_user,
            start_at=dj_timezone.now() + timedelta(days=1),
            end_at=dj_timezone.now() + timedelta(days=1, hours=1),
            status=RemoteBookingProxy.Status.CONFIRMED,
            service_id=uuid.uuid4(),
            specialist_id=uuid.uuid4(),
            last_applied_appointment_version=version,
        )
        booking = BookingRequest.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            service_name="Массаж",
            master_name="Ольга",
            client_name="Anna",
            client_phone="79991234567",
            comment=f"Bot booking | yclients_record_id={appt_id}",
            source="bot",
            status=BookingRequest.Status.CONFIRMED,
        )
        return proxy, booking

    def test_pending_action_stores_expected_version(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        appt_id = uuid.uuid4()
        self._make_ayla_booking(tenant, bot_user, appt_id, version=7)
        client = FakeClient()
        new_dt = _future_iso(72)
        client.times = [_slot_at(new_dt)]

        with tenant_scope(tenant), override_settings(BOOKING_VIA_AYLA_REST=True):
            result = reschedule_booking(
                client=client,
                arguments={
                    "record_id": str(appt_id),
                    "new_datetime": new_dt,
                },
                tenant=tenant,
                bot_user=bot_user,
            )

        assert result.error == ""
        assert result.pending is not None
        row = PendingBookingAction.all_tenants.get(pk=result.pending.token)
        assert row.payload["expected_version"] == 7
        assert row.payload["master_id"] == str(row.payload["master_id"])

    def test_execute_sends_expected_version_to_client(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        appt_id = uuid.uuid4()
        proxy, _ = self._make_ayla_booking(tenant, bot_user, appt_id, version=4)
        client = FakeClient()
        new_dt = _future_iso(72)
        client.times = [_slot_at(new_dt)]

        with tenant_scope(tenant), override_settings(BOOKING_VIA_AYLA_REST=True):
            result = execute_reschedule(
                client=client,
                payload={
                    "record_id": str(appt_id),
                    "new_datetime": new_dt,
                    "master_id": str(proxy.specialist_id),
                    "service_id": str(proxy.service_id),
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                    "expected_version": 4,
                },
                tenant=tenant,
                bot_user=bot_user,
            )

        assert result.error == ""
        assert len(client.reschedule_calls) == 1
        call = client.reschedule_calls[0]
        assert call["record_id"] == str(appt_id)
        assert call["expected_version"] == 4

    def test_stale_version_returns_error_not_success(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        appt_id = uuid.uuid4()
        proxy, booking = self._make_ayla_booking(tenant, bot_user, appt_id, version=2)
        client = FakeClient()
        client.reschedule_exc = YClientsAPIError("http_409_STALE_VERSION")
        new_dt = _future_iso(72)
        client.times = [_slot_at(new_dt)]

        with tenant_scope(tenant), override_settings(BOOKING_VIA_AYLA_REST=True):
            result = execute_reschedule(
                client=client,
                payload={
                    "record_id": str(appt_id),
                    "new_datetime": new_dt,
                    "master_id": str(proxy.specialist_id),
                    "service_id": str(proxy.service_id),
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                    "expected_version": 2,
                },
                tenant=tenant,
                bot_user=bot_user,
            )

        assert result.error == "stale_version"
        # The canonical appointment and local booking must NOT be mutated.
        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.CONFIRMED

    def test_missing_expected_version_does_not_block_execution(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """A pending row created before this fix (no expected_version) must
        still execute, sending ``None`` to the client rather than crashing.
        """
        appt_id = uuid.uuid4()
        proxy, _ = self._make_ayla_booking(tenant, bot_user, appt_id, version=None)
        client = FakeClient()
        new_dt = _future_iso(72)
        client.times = [_slot_at(new_dt)]

        with tenant_scope(tenant), override_settings(BOOKING_VIA_AYLA_REST=True):
            result = execute_reschedule(
                client=client,
                payload={
                    "record_id": str(appt_id),
                    "new_datetime": new_dt,
                    "master_id": str(proxy.specialist_id),
                    "service_id": str(proxy.service_id),
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                },
                tenant=tenant,
                bot_user=bot_user,
            )

        assert result.error == ""
        assert client.reschedule_calls[0]["expected_version"] is None
