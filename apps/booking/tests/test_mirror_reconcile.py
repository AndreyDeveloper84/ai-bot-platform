"""Mirror ↔ canon reconciliation detector (DRF-1111 + DRF-1161).

The mirror (``RemoteBookingProxy``) is filled by events out of Ayla, and a
booking created by a path that emits no event never reaches it — the salon
day then silently disagrees with reality and nobody can see it from inside.
This pins the detector: periodic, identifier-based comparison of live
bookings on both sides; any divergence is a log event, a persistent one is
a page. Never an autofix.
"""

from __future__ import annotations

import datetime as dt
import logging
from io import StringIO
from uuid import uuid4

import pytest
from django.core.management import call_command

from apps.booking.mirror_status import LIVE_STATUSES, TERMINAL_STATUSES
from apps.booking.models import RemoteBookingProxy
from apps.identity.models import BotUser
from apps.integrations.ayla.salon_client import SalonUnavailable
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2026, 8, 25, 12, 0, tzinfo=dt.timezone.utc)


def _proxy(tenant: Tenant, **overrides) -> RemoteBookingProxy:
    defaults = {
        "tenant": tenant,
        "start_at": NOW + dt.timedelta(days=2),
        "end_at": NOW + dt.timedelta(days=2, hours=1),
        "status": RemoteBookingProxy.Status.CONFIRMED,
        "source": RemoteBookingProxy.Source.AUTOMATION,
    }
    defaults.update(overrides)
    return RemoteBookingProxy.all_tenants.create(appointment_id=uuid4(), **defaults)


def _booking(
    appointment_id,
    *,
    status: str = "confirmed",
    start: dt.datetime | None = None,
) -> dict:
    start = start or (NOW + dt.timedelta(days=2))
    return {
        "appointment_id": str(appointment_id),
        "version": 1,
        "status": status,
        "start_at": start.isoformat(),
        "end_at": (start + dt.timedelta(hours=1)).isoformat(),
        "start_at_local": start.isoformat(),
        "end_at_local": (start + dt.timedelta(hours=1)).isoformat(),
        "service_name": "Стрижка",
        "duration_minutes": 60,
        "price": "0",
        "client_id": str(uuid4()),
        "client_name": "",
        "completed_by": "",
        "no_show_marked_by": "",
    }


class FakeSalonClient:
    """Answers the tenant-day fan-out from a flat {date: [bookings]} map."""

    def __init__(self, bookings_by_date: dict[dt.date, list[dict]] | None = None) -> None:
        self.bookings_by_date = bookings_by_date or {}
        self.calls: list[dict] = []

    def get_tenant_day(self, *, actor_external_id: str, tenant_slug: str, date: dt.date) -> dict:
        self.calls.append({"actor": actor_external_id, "tenant": tenant_slug, "date": date})
        return {
            "date": date.isoformat(),
            "masters": [{"bookings": self.bookings_by_date.get(date, [])}],
        }


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="reconcile-salon", name="Reconcile", timezone="Europe/Moscow")


@pytest.fixture
def owner(tenant: Tenant) -> BotUser:
    user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="owner-1",
        chat_id="chat-owner-1",
        display_name="Вера Владелец",
    )
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=user, role=TenantStaff.Role.OWNER)
    return user


def _run(tenant, client, **kwargs):
    from apps.booking import mirror_reconcile

    return mirror_reconcile.reconcile_tenant(
        tenant,
        client=client,
        actor_external_id="bot:max:owner-1",
        now=NOW,
        **kwargs,
    )


# ─── comparison core ─────────────────────────────────────────────────────────


class TestComparison:
    def test_in_sync_is_clean(self, tenant: Tenant, owner: BotUser) -> None:
        row = _proxy(tenant)
        client = FakeSalonClient(
            {NOW.date() + dt.timedelta(days=2): [_booking(row.appointment_id)]}
        )

        report = _run(tenant, client)

        assert report.is_clean()
        assert report.fingerprint() == ""

    def test_booking_live_in_ayla_missing_from_mirror(self, tenant: Tenant) -> None:
        """The DRF-1111 primary case: a creation path that emitted no event."""
        ghost = uuid4()
        client = FakeSalonClient({NOW.date() + dt.timedelta(days=2): [_booking(ghost)]})

        report = _run(tenant, client)

        assert not report.is_clean()
        assert report.ayla_only == (str(ghost),)

    def test_mirror_row_absent_from_ayla(self, tenant: Tenant) -> None:
        """DRF-1034: hard-deleted in the backend, no event exists at all."""
        row = _proxy(tenant)

        report = _run(tenant, FakeSalonClient())

        assert not report.is_clean()
        assert report.mirror_only == (str(row.appointment_id),)

    def test_cancelled_in_ayla_still_live_in_mirror(self, tenant: Tenant) -> None:
        row = _proxy(tenant, status="confirmed")
        client = FakeSalonClient(
            {NOW.date() + dt.timedelta(days=2): [_booking(row.appointment_id, status="cancelled")]}
        )

        report = _run(tenant, client)

        assert not report.is_clean()
        assert report.status_mismatch == ((str(row.appointment_id), "cancelled", "confirmed"),)

    def test_live_in_ayla_terminal_in_mirror(self, tenant: Tenant) -> None:
        row = _proxy(tenant, status="cancelled")
        client = FakeSalonClient(
            {NOW.date() + dt.timedelta(days=2): [_booking(row.appointment_id)]}
        )

        report = _run(tenant, client)

        assert not report.is_clean()
        assert report.status_mismatch == ((str(row.appointment_id), "confirmed", "cancelled"),)

    def test_moved_start_is_divergence(self, tenant: Tenant) -> None:
        row = _proxy(tenant)
        moved = NOW + dt.timedelta(days=2, hours=3)
        client = FakeSalonClient(
            {NOW.date() + dt.timedelta(days=2): [_booking(row.appointment_id, start=moved)]}
        )

        report = _run(tenant, client)

        assert not report.is_clean()
        assert report.start_mismatch == (
            (str(row.appointment_id), moved.isoformat(), row.start_at.isoformat()),
        )

    def test_terminal_and_past_rows_are_ignored(self, tenant: Tenant) -> None:
        # Terminal mirror row, terminal in Ayla — history, not divergence.
        done = _proxy(tenant, status="completed")
        # Past visit still 'confirmed' yesterday — before the window on both sides.
        past = _proxy(
            tenant,
            status="confirmed",
            start_at=NOW - dt.timedelta(days=1),
            end_at=NOW - dt.timedelta(days=1, hours=-1),
        )
        client = FakeSalonClient(
            {
                NOW.date() + dt.timedelta(days=2): [
                    _booking(done.appointment_id, status="completed")
                ],
                # An overnight row bleeding into today from yesterday must not
                # fire: it started before the window on both sides.
                NOW.date(): [
                    _booking(
                        past.appointment_id,
                        start=NOW - dt.timedelta(days=1),
                    )
                ],
            }
        )

        report = _run(tenant, client)

        assert report.is_clean()

    def test_unknown_status_counts_as_live_on_both_sides(self, tenant: Tenant) -> None:
        """A status slug neither side has seen yet must not read as terminal."""
        assert "reschedule_pending" not in LIVE_STATUSES | TERMINAL_STATUSES
        row = _proxy(tenant, status="reschedule_pending")
        client = FakeSalonClient(
            {
                NOW.date() + dt.timedelta(days=2): [
                    _booking(row.appointment_id, status="reschedule_pending")
                ]
            }
        )

        report = _run(tenant, client)

        assert report.is_clean()

    def test_rows_beyond_the_window_are_excluded_on_both_sides(self, tenant: Tenant) -> None:
        far = NOW + dt.timedelta(days=80)
        _proxy(tenant, start_at=far, end_at=far + dt.timedelta(hours=1))

        report = _run(tenant, FakeSalonClient(), window_days=45)

        assert report.is_clean()


# ─── actor resolution + sweep wiring ─────────────────────────────────────────


class TestSweep:
    def test_actor_is_the_active_owner(self, tenant: Tenant, owner: BotUser) -> None:
        from apps.booking import mirror_reconcile

        assert mirror_reconcile.find_reconcile_actor(tenant) == owner

    def test_falls_back_to_an_admin(self, tenant: Tenant) -> None:
        from apps.booking import mirror_reconcile

        admin = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="admin-1", chat_id="c-a1"
        )
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=admin, role=TenantStaff.Role.ADMIN)

        assert mirror_reconcile.find_reconcile_actor(tenant) == admin

    def test_deactivated_owner_is_not_the_actor(self, tenant: Tenant, owner: BotUser) -> None:
        from apps.booking import mirror_reconcile

        TenantStaff.all_tenants.filter(bot_user=owner).update(deactivated_at=NOW)

        assert mirror_reconcile.find_reconcile_actor(tenant) is None

    def test_tenant_without_actor_is_skipped_with_a_warning(
        self, tenant: Tenant, caplog: pytest.LogCaptureFixture
    ) -> None:
        from apps.booking import mirror_reconcile

        _proxy(tenant)  # the tenant participates in the flow, so it IS swept
        pages: list[tuple] = []
        with caplog.at_level(logging.WARNING):
            summary = mirror_reconcile.run_mirror_reconciliation(
                client_factory=lambda: FakeSalonClient(),
                now=NOW,
                page=lambda *a, **k: pages.append((a, k)),
            )

        assert summary["skipped_no_actor"] == [tenant.slug]
        assert pages == []
        assert any("no_actor" in r.message for r in caplog.records)

    def test_tenant_with_neither_mirror_nor_staff_is_not_swept(self, tenant: Tenant) -> None:
        """The platform-global tenant and bare shells: nothing to diverge
        from, nobody to name to Ayla — sweeping them is load without
        signal."""
        from apps.booking import mirror_reconcile

        client = FakeSalonClient()
        summary = mirror_reconcile.run_mirror_reconciliation(
            client_factory=lambda: client, now=NOW, page=lambda *a, **k: None
        )

        assert client.calls == []
        assert summary["checked"] == []

    def test_staffed_tenant_with_empty_mirror_is_still_swept(self, tenant: Tenant, owner) -> None:
        """A tenant whose events ALL failed has an empty mirror — excluding
        it would blind the detector in exactly the failure it exists for."""
        from apps.booking import mirror_reconcile

        ghost = str(uuid4())
        client = FakeSalonClient({NOW.date() + dt.timedelta(days=2): [_booking(ghost)]})
        summary = mirror_reconcile.run_mirror_reconciliation(
            client_factory=lambda: client, now=NOW, page=lambda *a, **k: None
        )

        assert summary["diverged"] == [tenant.slug]

    def test_ayla_outage_marks_the_tenant_unchecked(
        self, tenant: Tenant, owner: BotUser, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A failed read is NOT a divergence — the detector must never page
        «расхождение» off an unreachable canon."""

        class DownClient(FakeSalonClient):
            def get_tenant_day(self, **kwargs):
                raise SalonUnavailable("network: boom")

        from apps.booking import mirror_reconcile

        _proxy(tenant)
        pages: list[tuple] = []
        with caplog.at_level(logging.WARNING):
            summary = mirror_reconcile.run_mirror_reconciliation(
                client_factory=lambda: DownClient(),
                now=NOW,
                page=lambda *a, **k: pages.append((a, k)),
            )

        assert summary["unchecked"] == [tenant.slug]
        assert summary["diverged"] == []
        assert pages == []


# ─── log + page behaviour (the detector contract) ────────────────────────────


class TestAlerting:
    def _client_with_ghost(self) -> tuple[FakeSalonClient, str]:
        ghost = str(uuid4())
        return (
            FakeSalonClient({NOW.date() + dt.timedelta(days=2): [_booking(ghost)]}),
            ghost,
        )

    def test_divergence_is_a_log_event_on_every_tick(
        self, tenant: Tenant, owner: BotUser, caplog: pytest.LogCaptureFixture
    ) -> None:
        from apps.booking import mirror_reconcile

        client, ghost = self._client_with_ghost()
        with caplog.at_level(logging.WARNING, logger="apps.booking.mirror_reconcile"):
            mirror_reconcile.run_mirror_reconciliation(
                client_factory=lambda: client, now=NOW, page=lambda *a, **k: None
            )

        record = next(r for r in caplog.records if "mirror_reconcile.diverged" in r.message)
        assert tenant.slug in record.message
        assert ghost in caplog.text

    def test_first_tick_does_not_page_second_identical_tick_does(
        self, tenant: Tenant, owner: BotUser
    ) -> None:
        """The threshold: a divergence must persist across two consecutive
        ticks before it pages — a booking whose event is still in flight
        between Ayla and the consumer must never wake anyone up."""
        from apps.booking import mirror_reconcile

        client, _ = self._client_with_ghost()
        pages: list[tuple] = []
        page = lambda *a, **k: pages.append((a, k))  # noqa: E731

        mirror_reconcile.run_mirror_reconciliation(
            client_factory=lambda: client, now=NOW, page=page
        )
        assert pages == []

        mirror_reconcile.run_mirror_reconciliation(
            client_factory=lambda: client, now=NOW + dt.timedelta(hours=1), page=page
        )
        assert len(pages) == 1
        assert pages[0][0][0] == "error"

    def test_changed_fingerprint_does_not_page(self, tenant: Tenant, owner: BotUser) -> None:
        from apps.booking import mirror_reconcile

        client, _ = self._client_with_ghost()
        pages: list[tuple] = []
        page = lambda *a, **k: pages.append((a, k))  # noqa: E731

        mirror_reconcile.run_mirror_reconciliation(
            client_factory=lambda: client, now=NOW, page=page
        )
        # Next tick: a DIFFERENT divergence (the first one resolved, a new
        # one appeared) — the two-tick clock starts over.
        other = FakeSalonClient({NOW.date() + dt.timedelta(days=3): [_booking(uuid4())]})
        mirror_reconcile.run_mirror_reconciliation(
            client_factory=lambda: other, now=NOW + dt.timedelta(hours=1), page=page
        )

        assert pages == []

    def test_recovery_is_logged_and_state_cleared(
        self, tenant: Tenant, owner: BotUser, caplog: pytest.LogCaptureFixture
    ) -> None:
        from apps.booking import mirror_reconcile

        client, _ = self._client_with_ghost()
        page = lambda *a, **k: None  # noqa: E731
        mirror_reconcile.run_mirror_reconciliation(
            client_factory=lambda: client, now=NOW, page=page
        )

        with caplog.at_level(logging.INFO, logger="apps.booking.mirror_reconcile"):
            mirror_reconcile.run_mirror_reconciliation(
                client_factory=lambda: FakeSalonClient(),
                now=NOW + dt.timedelta(hours=1),
                page=page,
            )

        assert any("recovered" in r.message for r in caplog.records)


# ─── management command ──────────────────────────────────────────────────────


class TestCommand:
    def test_prints_summary_and_never_pages(
        self, tenant: Tenant, owner: BotUser, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ghost = str(uuid4())
        client = FakeSalonClient({NOW.date() + dt.timedelta(days=2): [_booking(ghost)]})
        monkeypatch.setattr("apps.booking.mirror_reconcile.get_salon_client", lambda: client)
        pages: list[tuple] = []
        monkeypatch.setattr(
            "apps.booking.mirror_reconcile.alert_page",
            lambda *a, **k: pages.append((a, k)),
        )

        out = StringIO()
        call_command("reconcile_ayla_mirror", stdout=out)

        assert tenant.slug in out.getvalue()
        assert "ayla_only=1" in out.getvalue()
        assert pages == []

    def test_writes_nothing(self, tenant: Tenant, owner: BotUser) -> None:
        row = _proxy(tenant)
        out = StringIO()
        call_command("reconcile_ayla_mirror", stdout=out)
        row.refresh_from_db()
        assert row.status == "confirmed"


# ─── celery wiring ───────────────────────────────────────────────────────────


class TestWiring:
    def test_beat_schedule_runs_the_sweep_hourly(self) -> None:
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE.get("booking.reconcile_ayla_mirror")
        assert entry is not None
        assert entry["task"] == "apps.booking.tasks.reconcile_ayla_mirror"

    def test_task_delegates_to_the_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from apps.booking import tasks

        seen: dict = {}
        monkeypatch.setattr(
            "apps.booking.tasks.run_mirror_reconciliation",
            lambda: seen.setdefault("called", True) and {"checked": []},
        )

        result = tasks.reconcile_ayla_mirror()

        assert seen.get("called") is True
        assert result == {"checked": []}
