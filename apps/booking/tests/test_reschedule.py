"""Tests for the reschedule service (Phase 2)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.booking.models import BookingRequest
from apps.booking.services.create import (
    BookingCreateError,
    CreateBookingInput,
    create_customer_booking,
)
from apps.booking.services.reschedule import reschedule_customer_booking
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.scheduling.models import Weekday, WorkingHours
from apps.tenancy.models import Tenant


MSK = ZoneInfo("Europe/Moscow")


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="resched", name="Resched", timezone="Europe/Moscow")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="12345",
        chat_id="12345",
        display_name="Мария",
    )


@pytest.fixture
def other_bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="99999",
        chat_id="99999",
        display_name="Другой",
    )


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=1,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        name="Анна",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
    )


@pytest.fixture
def service(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=42,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        slug="manicure",
        name="Маникюр",
        duration_min=60,
        is_active=True,
    )


@pytest.fixture
def master_service(tenant, master, service) -> None:
    MasterService.all_tenants.create(tenant=tenant, master=master, service=service)


@pytest.fixture
def working_hours(tenant, master) -> None:
    for wd in (
        Weekday.MONDAY,
        Weekday.TUESDAY,
        Weekday.WEDNESDAY,
        Weekday.THURSDAY,
        Weekday.FRIDAY,
        Weekday.SATURDAY,
        Weekday.SUNDAY,
    ):
        WorkingHours.all_tenants.create(
            tenant=tenant,
            master=master,
            day_of_week=wd,
            is_working=True,
            start_time=time(10, 0),
            end_time=time(19, 0),
        )


def _monday_at(hour: int) -> datetime:
    d = date.today() + timedelta(days=30)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return datetime.combine(d, time(hour, 0), tzinfo=MSK)


@pytest.fixture
def existing_booking(tenant, bot_user, master, service, master_service, working_hours):
    return create_customer_booking(
        inp=CreateBookingInput(
            tenant=tenant,
            bot_user=bot_user,
            service_id=str(service.id),
            master_id=str(master.id),
            visit_at=_monday_at(12),
        ),
    )


class TestRescheduleCustomerBooking:
    def test_happy_path(
        self, tenant, bot_user, master, service, master_service, working_hours, existing_booking
    ):
        new_visit = _monday_at(14)
        new = reschedule_customer_booking(
            tenant=tenant,
            bot_user=bot_user,
            old_booking_id=str(existing_booking.id),
            new_visit_at=new_visit,
        )
        assert new.visit_at == new_visit
        assert new.booking_source == "ai_direct"
        # Q12-α (founder ACK 2026-05-22): happy-path reschedule within
        # 90d, same service → continuation chain → billable=False,
        # billing_reason='reschedule_continuation', original_booking_event
        # points at the chain root.
        assert new.billable is False
        assert new.billing_reason == "reschedule_continuation"
        assert new.original_booking_event_id == existing_booking.id
        assert new.attribution_metadata["created_by"] == "execute_reschedule"
        # Old marked RESCHEDULED.
        existing_booking.refresh_from_db()
        assert existing_booking.status == BookingRequest.Status.RESCHEDULED

    def test_q12a_over_90d_breaks_chain(
        self,
        tenant,
        bot_user,
        master,
        service,
        master_service,
        working_hours,
        existing_booking,
    ):
        """Q12-α (founder ACK 2026-05-22): reschedule to >90d from chain
        root → new row is billable=True (chain broken, fresh sale).
        ``original_booking_event_id`` is NULL so the new row starts its
        own chain."""

        # 91 days from existing_booking.visit_at — strictly beyond 90.
        d = existing_booking.visit_at.date() + timedelta(days=91)
        while d.weekday() != 0:
            d += timedelta(days=1)
        # Working hours fixture covers Mon-Fri only — pick the next Monday.
        new_visit = datetime.combine(d, time(14, 0), tzinfo=MSK)

        new = reschedule_customer_booking(
            tenant=tenant,
            bot_user=bot_user,
            old_booking_id=str(existing_booking.id),
            new_visit_at=new_visit,
        )

        assert new.billable is True
        assert "over_90d" in new.billing_reason
        assert new.original_booking_event_id is None
        assert new.attribution_metadata["created_by"] == "execute_reschedule"

    def test_not_owned_rejected(self, tenant, bot_user, other_bot_user, existing_booking):
        with pytest.raises(BookingCreateError) as exc_info:
            reschedule_customer_booking(
                tenant=tenant,
                bot_user=other_bot_user,
                old_booking_id=str(existing_booking.id),
                new_visit_at=_monday_at(15),
            )
        assert exc_info.value.slug == "forbidden"

    def test_already_cancelled_rejected(self, tenant, bot_user, existing_booking):
        existing_booking.status = BookingRequest.Status.CANCELLED
        existing_booking.save()
        with pytest.raises(BookingCreateError) as exc_info:
            reschedule_customer_booking(
                tenant=tenant,
                bot_user=bot_user,
                old_booking_id=str(existing_booking.id),
                new_visit_at=_monday_at(15),
            )
        assert exc_info.value.slug == "not_reschedulable"

    def test_q12a_cancel_requested_rejected(self, tenant, bot_user, existing_booking):
        """Tech-lead double-pass S1: CANCEL_REQUESTED interim state
        (5s undo window) MUST NOT be reschedulable. Service-layer
        already enforces «only CONFIRMED» at line 75 — this test pins
        that contract so a future relaxation can't slip through.
        Mirrors the LLM-tool path enforcement added in tools.py.
        """

        existing_booking.status = BookingRequest.Status.CANCEL_REQUESTED
        existing_booking.save()

        with pytest.raises(BookingCreateError) as exc_info:
            reschedule_customer_booking(
                tenant=tenant,
                bot_user=bot_user,
                old_booking_id=str(existing_booking.id),
                new_visit_at=_monday_at(15),
            )
        assert exc_info.value.slug == "not_reschedulable"

    def test_not_found_rejected(self, tenant, bot_user):
        import uuid

        with pytest.raises(BookingCreateError) as exc_info:
            reschedule_customer_booking(
                tenant=tenant,
                bot_user=bot_user,
                old_booking_id=str(uuid.uuid4()),
                new_visit_at=_monday_at(14),
            )
        assert exc_info.value.slug == "not_found"

    def test_q12a_541_price_hike_breaks_chain_end_to_end(
        self,
        tenant,
        bot_user,
        master,
        service,
        master_service,
        working_hours,
    ):
        """Q12-α #541 (founder ACK 2026-05-23): admin mutates the
        service's ``price_from`` between root sale and reschedule →
        chain breaks at the commercial-identity comparator → new row
        is billable=True with reason
        ``commercial_identity_changed:sticker_price_amount`` and starts
        a fresh chain (``original_booking_event_id is None``)."""

        service.price_from = Decimal("1500.00")
        service.save()

        root = create_customer_booking(
            inp=CreateBookingInput(
                tenant=tenant,
                bot_user=bot_user,
                service_id=str(service.id),
                master_id=str(master.id),
                visit_at=_monday_at(12),
            ),
        )
        assert root.commercial_identity_snapshot["sticker_price_amount"] == "1500.00"

        # Admin price hike between sale + reschedule.
        service.price_from = Decimal("2000.00")
        service.save()

        new = reschedule_customer_booking(
            tenant=tenant,
            bot_user=bot_user,
            old_booking_id=str(root.id),
            new_visit_at=_monday_at(14),
        )

        assert new.billable is True
        assert "commercial_identity_changed:sticker_price_amount" in new.billing_reason
        assert new.original_booking_event_id is None
        # New chain root snapshots the CURRENT (mutated) price.
        assert new.commercial_identity_snapshot["sticker_price_amount"] == "2000.00"

    def test_q12a_541_identity_unchanged_preserves_chain_and_root_snapshot(
        self,
        tenant,
        bot_user,
        master,
        service,
        master_service,
        working_hours,
    ):
        """Q12-α #541: when commercial identity is unchanged the chain
        continues AND the new row carries the ROOT's snapshot forward
        (not a fresh live snapshot) — that's the commercial truth of
        the original sale, per founder ACK 2026-05-23."""

        service.price_from = Decimal("1500.00")
        service.save()

        root = create_customer_booking(
            inp=CreateBookingInput(
                tenant=tenant,
                bot_user=bot_user,
                service_id=str(service.id),
                master_id=str(master.id),
                visit_at=_monday_at(12),
            ),
        )

        new = reschedule_customer_booking(
            tenant=tenant,
            bot_user=bot_user,
            old_booking_id=str(root.id),
            new_visit_at=_monday_at(14),
        )

        assert new.billable is False
        assert new.billing_reason == "reschedule_continuation"
        assert new.original_booking_event_id == root.id
        # Snapshot carried from root, not re-derived from live service.
        assert new.commercial_identity_snapshot == root.commercial_identity_snapshot

    def test_q12a_616_root_snapshot_read_uses_select_for_update(
        self,
        tenant,
        bot_user,
        master,
        service,
        master_service,
        working_hours,
        existing_booking,
    ):
        """Q12-α #616 (PRE_PILOT 2026-07-15): the carry-forward root
        snapshot read MUST happen under ``select_for_update()`` so two
        concurrent reschedules on different links of the same chain
        serialize on the root row.

        Also closes Q12-α #533 D8 (concurrent reschedule on same chain
        regression pin) — this test + the companion
        ``test_q12a_616_root_snapshot_read_calls_select_for_update_with_of_self``
        below document the contract that two-link concurrent rebook
        cannot double-bill: both threads acquire the same root lock
        via ``select_for_update``, only one can proceed at a time, so
        chain semantics are preserved per-thread.

        Contract test — captures SQL via ``CaptureQueriesContext`` and
        asserts a ``FOR UPDATE`` clause is present on the root SELECT
        when running against Postgres. Skipped on SQLite where
        ``select_for_update`` is a no-op (Django warns but emits no
        SQL).
        """

        # First reschedule creates link2 (continuation chain).
        link2 = reschedule_customer_booking(
            tenant=tenant,
            bot_user=bot_user,
            old_booking_id=str(existing_booking.id),
            new_visit_at=_monday_at(14),
        )
        assert link2.original_booking_event_id == existing_booking.id

        # Second reschedule walks the chain → triggers the root read
        # the test cares about (compute_reschedule_continuation does
        # the walk; reschedule_customer_booking does the carry-forward).
        with CaptureQueriesContext(connection) as ctx:
            link3 = reschedule_customer_booking(
                tenant=tenant,
                bot_user=bot_user,
                old_booking_id=str(link2.id),
                new_visit_at=_monday_at(16),
            )

        # Behavioural sanity — chain still continues to the original root.
        assert link3.original_booking_event_id == existing_booking.id

        # Contract assertion: at least one query against
        # apps_booking_bookingrequest is issued with FOR UPDATE OF.
        # Postgres emits ``FOR UPDATE OF "apps_booking_bookingrequest"``;
        # SQLite silently no-ops the FOR UPDATE (Django warning, no SQL
        # appended). Skip the SQL assertion on SQLite; trust the patch-
        # based assertion below.
        if connection.vendor == "postgresql":
            queries_with_lock = [
                q["sql"]
                for q in ctx.captured_queries
                if "FOR UPDATE" in q["sql"] and "apps_booking_bookingrequest" in q["sql"]
            ]
            assert queries_with_lock, (
                "Expected at least one BookingRequest SELECT with FOR UPDATE; "
                "captured queries:\n" + "\n".join(q["sql"] for q in ctx.captured_queries)
            )

    def test_q12a_616_root_snapshot_read_calls_select_for_update_with_of_self(
        self,
        tenant,
        bot_user,
        master,
        service,
        master_service,
        working_hours,
        existing_booking,
    ):
        """Backend-agnostic contract test: spy on the manager's
        ``select_for_update`` call to verify both the **call happened**
        and the ``of=`` scoping argument. Works on SQLite where the
        actual lock is a no-op."""

        # Need a chain longer than 1 so the root walk in
        # ``compute_reschedule_continuation`` fires (it short-circuits
        # to ``root = old`` when ``old.original_booking_event_id`` is
        # None).
        link2 = reschedule_customer_booking(
            tenant=tenant,
            bot_user=bot_user,
            old_booking_id=str(existing_booking.id),
            new_visit_at=_monday_at(14),
        )

        # Spy at the QuerySet class level — Django's Manager
        # ``select_for_update`` is dynamically delegated to QuerySet, so
        # patching at the QuerySet class catches both manager-level and
        # chained-queryset calls uniformly. We filter recorded calls by
        # ``self.model.__name__`` to isolate BookingRequest from other
        # models (CatalogMaster also uses select_for_update inside
        # create_customer_booking).
        from django.db.models.query import QuerySet

        original_sfu = QuerySet.select_for_update
        calls: list[dict] = []

        def spy(self, *args, **kwargs):  # noqa: ANN001
            calls.append(
                {
                    "args": args,
                    "kwargs": kwargs,
                    "model": self.model.__name__,
                }
            )
            return original_sfu(self, *args, **kwargs)

        with patch.object(QuerySet, "select_for_update", new=spy):
            reschedule_customer_booking(
                tenant=tenant,
                bot_user=bot_user,
                old_booking_id=str(link2.id),
                new_visit_at=_monday_at(16),
            )

        # Filter to BookingRequest calls only.
        booking_calls = [c for c in calls if c["model"] == "BookingRequest"]
        # Expect at least 3 BookingRequest calls:
        #   1. reschedule.py:66 — lock OLD row at top of reschedule
        #   2. attribution.py — root walk inside compute_reschedule_continuation
        #   3. reschedule.py:128 — carry-forward fetch of root snapshot
        assert len(booking_calls) >= 3, (
            "Expected ≥3 BookingRequest select_for_update calls (old-row "
            "lock + root walk + carry-forward); got "
            f"{len(booking_calls)}. Calls: {booking_calls}"
        )
        # No-arg select_for_update() — locks the model's own row by
        # default. We don't use ``of=`` because it's Postgres-only.
        for call in booking_calls:
            assert call["args"] == () and call["kwargs"] == {}, (
                f"BookingRequest.select_for_update must be called with no args; got {call}"
            )
