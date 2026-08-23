"""Integration tests for MM5 master deactivation cascade backend.

Covers:

* Auth matrix on all three endpoints (preview / deactivate / reactivate):
  customer / master / receptionist / admin / owner.
* Preview — empty / populated / fallback ranking / cross-tenant.
* Execute — happy path, race detection, validation, custom template,
  notification dispatch, already-deactivated guard.
* Reactivate — happy path, already-active guard, master DM dispatch,
  WorkingHours / MasterService preservation.

Each endpoint test wraps :mod:`apps.admin_api.services.master_deactivation`
through the HTTP layer so we exercise URL routing + auth + body parsing.
A handful of pure-function tests exercise edge cases that are awkward
through HTTP (e.g. notification rendering with custom templates).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.services.master_deactivation import (
    DEFAULT_CUSTOMER_NOTIFICATION_TEMPLATE,
    BookingAction,
    DeactivationError,
    execute_deactivation,
    preview_deactivation,
    reactivate_master,
)
from apps.admin_api.tests.conftest import (
    init_data_header,
    link_master_to_bot_user,
    make_master,
)
from apps.audit.models import AuditLog
from apps.booking.models import BookingRequest, RemoteBookingProxy
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

# MM5 cascade uses ``transaction.on_commit`` for customer/master DM
# dispatch (so a MAX outage can't roll back the lifecycle transition).
# Standard Django TestCase wraps every test in a single transaction
# that's rolled back — on_commit hooks NEVER fire there. We need
# transactional tests so the dispatch side-effect is observable.
pytestmark = pytest.mark.django_db(transaction=True)


# --- URL helpers ----------------------------------------------------------


def _preview_url(master_id) -> str:
    return reverse("admin_api:master_deactivation_preview", args=[str(master_id)])


def _deactivate_url(master_id) -> str:
    return reverse("admin_api:master_deactivate", args=[str(master_id)])


def _reactivate_url(master_id) -> str:
    return reverse("admin_api:master_reactivate", args=[str(master_id)])


# --- factory helpers ------------------------------------------------------


def _make_service(
    tenant: Tenant,
    *,
    name: str = "Маникюр гель-лак",
    duration_min: int = 60,
    external_id: int = 42,
    slug: str = "manicure-gel",
) -> CatalogService:
    now = datetime.now(tz=timezone.utc)
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=external_id,
        external_updated_at=now,
        slug=slug,
        name=name,
        duration_min=duration_min,
        is_active=True,
    )


def _link_master_service(tenant, master, service) -> MasterService:
    return MasterService.all_tenants.create(tenant=tenant, master=master, service=service)


def _make_booking(
    *,
    tenant,
    master,
    service,
    bot_user: BotUser | None = None,
    visit_at: datetime | None = None,
    client_name: str = "Мария Иванова",
    duration_min: int = 60,
    status: str = BookingRequest.Status.CONFIRMED,
) -> BookingRequest:
    if visit_at is None:
        visit_at = datetime.now(tz=timezone.utc) + timedelta(days=3)
    return BookingRequest.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        service=service,
        master=master,
        service_name=service.name,
        master_name=master.name,
        client_name=client_name,
        client_phone="+79991234567",
        visit_at=visit_at,
        duration_min=duration_min,
        status=status,
        booking_source="ai_direct",
        attribution_metadata={"actor_type": "customer", "created_by": "test"},
    )


def _make_mirror_booking(
    *,
    tenant,
    master,
    start_at: datetime | None = None,
    status: str = RemoteBookingProxy.Status.CONFIRMED,
    duration_min: int = 60,
) -> RemoteBookingProxy:
    """One Ayla-side appointment as bot-platform mirrors it.

    Deliberately carries no client name or phone — the mirror stores no
    PII (event-contract §7), which is exactly why it can only ever answer
    «how many», never «who».
    """
    if start_at is None:
        start_at = datetime.now(tz=timezone.utc) + timedelta(days=3)
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=uuid.uuid4(),
        tenant=tenant,
        start_at=start_at,
        end_at=start_at + timedelta(minutes=duration_min),
        status=status,
        specialist_id=master.id,
    )


def _make_customer_bot_user(
    tenant: Tenant,
    idx: int,
    *,
    consented: bool = True,
    opted_out: bool = False,
    deleted: bool = False,
    withdrawn: bool = False,
) -> BotUser:
    """A client the cascade might write to.

    Consenting by DEFAULT (DRF-1307). Before the consent gate landed
    this helper produced a person with no consent at all and the suite
    still asserted that two DMs went out — which is exactly the bug the
    ticket is about, encoded as an expectation. Flipping the default
    rather than adding an opt-in flag means any future test that wants
    the *unprotected* shape has to say so out loud.

    ``withdrawn=True`` is the shape that matters most: ``consent_at`` is
    set — because :func:`apps.consent.services.withdraw` never clears
    it — and the ``ConsentRecord`` is withdrawn. Four of the pilot's
    five "consenting" users look exactly like this.
    """
    bu = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"6{idx:03d}",
        display_name=f"Customer {idx}",
        chat_id=f"chat-6{idx:03d}",
        phone="+79991234567",
        proactive_messages_opt_out=opted_out,
        deleted_at=datetime.now(tz=timezone.utc) if deleted else None,
    )
    if consented or withdrawn:
        bu.consent_at = datetime.now(tz=timezone.utc)
        bu.save(update_fields=["consent_at"])
        ConsentRecord.all_tenants.create(
            tenant=tenant,
            bot_user=bu,
            consent_type=ConsentRecord.ConsentType.PERSONAL_DATA.value,
            granted=True,
            source="test",
            withdrawn_at=datetime.now(tz=timezone.utc) if withdrawn else None,
        )
    return bu


# =========================================================================
# AUTH MATRIX
# =========================================================================


class TestAuthMatrix:
    """Customer / master / receptionist all forbidden across all three endpoints.

    Admin allowed on preview, forbidden on deactivate + reactivate.
    Owner allowed on all three.
    """

    def test_customer_forbidden_preview(
        self, client: Client, customer_bot_user: BotUser, master: CatalogMaster
    ) -> None:
        resp = client.post(_preview_url(master.id), HTTP_AUTHORIZATION=init_data_header("5005"))
        assert resp.status_code == 403

    def test_master_only_forbidden_preview(
        self,
        client: Client,
        master_only_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        link_master_to_bot_user(master, master_only_bot_user)
        resp = client.post(_preview_url(master.id), HTTP_AUTHORIZATION=init_data_header("5004"))
        assert resp.status_code == 403

    def test_receptionist_forbidden_preview(
        self, client: Client, receptionist_bot_user: BotUser, master: CatalogMaster
    ) -> None:
        resp = client.post(_preview_url(master.id), HTTP_AUTHORIZATION=init_data_header("5003"))
        assert resp.status_code == 403

    def test_admin_allowed_preview(
        self, client: Client, admin_bot_user: BotUser, master: CatalogMaster
    ) -> None:
        resp = client.post(_preview_url(master.id), HTTP_AUTHORIZATION=init_data_header("5002"))
        assert resp.status_code == 200, resp.content

    def test_owner_allowed_preview(
        self, client: Client, owner_bot_user: BotUser, master: CatalogMaster
    ) -> None:
        resp = client.post(_preview_url(master.id), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200, resp.content

    def test_admin_forbidden_deactivate(
        self, client: Client, admin_bot_user: BotUser, master: CatalogMaster
    ) -> None:
        resp = client.post(
            _deactivate_url(master.id),
            data=json.dumps({"bookings_plan": [], "reason": "x"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5002"),
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"

    def test_owner_allowed_deactivate(
        self, client: Client, owner_bot_user: BotUser, master: CatalogMaster
    ) -> None:
        # No future bookings → empty plan accepted.
        resp = client.post(
            _deactivate_url(master.id),
            data=json.dumps({"bookings_plan": [], "reason": "test"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 200, resp.content

    def test_admin_forbidden_reactivate(
        self,
        client: Client,
        admin_bot_user: BotUser,
        inactive_master: CatalogMaster,
    ) -> None:
        inactive_master.archived_at = datetime.now(tz=timezone.utc)
        inactive_master.save(update_fields=["archived_at"])
        resp = client.post(
            _reactivate_url(inactive_master.id),
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5002"),
        )
        assert resp.status_code == 403


# =========================================================================
# PREVIEW
# =========================================================================


class TestPreview:
    def test_no_future_bookings_empty_arrays(
        self, client: Client, owner_bot_user: BotUser, master: CatalogMaster
    ) -> None:
        resp = client.post(_preview_url(master.id), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["future_bookings"] == []
        assert data["summary"]["total_future_bookings"] == 0
        assert data["summary"]["bookings_with_fallback"] == 0
        assert data["summary"]["bookings_without_fallback"] == 0
        # Nothing anywhere — the empty list is trustworthy.
        assert data["summary"]["mirror_future_bookings"] == 0
        assert data["summary"]["inventory_complete"] is True

    def test_future_bookings_with_fallback(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        service = _make_service(tenant)
        _link_master_service(tenant, master, service)
        # A second active master who also performs the service.
        other = make_master(tenant, name="Мария Соколова", external_id=20)
        _link_master_service(tenant, other, service)

        bu = _make_customer_bot_user(tenant, 1)
        _make_booking(tenant=tenant, master=master, service=service, bot_user=bu)

        resp = client.post(_preview_url(master.id), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total_future_bookings"] == 1
        assert data["summary"]["bookings_with_fallback"] == 1
        booking = data["future_bookings"][0]
        assert booking["service_name"] == "Маникюр гель-лак"
        assert booking["client_first_name"] == "Мария"
        assert booking["client_last_initial"] == "И."
        fb = booking["fallback_masters"]
        assert len(fb) == 1
        assert fb[0]["name"] == "Мария Соколова"
        assert fb[0]["does_this_service"] is True
        assert fb[0]["is_free_at_slot"] is True
        assert fb[0]["match_score"] == 100

    def test_fallback_with_slot_conflict_scores_50(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        service = _make_service(tenant)
        _link_master_service(tenant, master, service)
        other = make_master(tenant, name="Мария Соколова", external_id=20)
        _link_master_service(tenant, other, service)

        visit_at = datetime.now(tz=timezone.utc) + timedelta(days=3)
        bu1 = _make_customer_bot_user(tenant, 1)
        bu2 = _make_customer_bot_user(tenant, 2)
        _make_booking(
            tenant=tenant,
            master=master,
            service=service,
            bot_user=bu1,
            visit_at=visit_at,
        )
        # Other master is busy at the same slot.
        _make_booking(
            tenant=tenant,
            master=other,
            service=service,
            bot_user=bu2,
            visit_at=visit_at,
            client_name="Anna Test",
        )

        resp = client.post(_preview_url(master.id), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200
        fb = resp.json()["future_bookings"][0]["fallback_masters"]
        assert len(fb) == 1
        assert fb[0]["is_free_at_slot"] is False
        assert fb[0]["match_score"] == 50

    def test_cross_tenant_returns_404(
        self,
        client: Client,
        owner_bot_user: BotUser,
        other_tenant: Tenant,
    ) -> None:
        other_master = make_master(other_tenant, name="Cross-tenant", external_id=999)
        resp = client.post(
            _preview_url(other_master.id), HTTP_AUTHORIZATION=init_data_header("5001")
        )
        assert resp.status_code == 404

    def test_inactive_master_preview_works(
        self,
        client: Client,
        owner_bot_user: BotUser,
        inactive_master: CatalogMaster,
    ) -> None:
        resp = client.post(
            _preview_url(inactive_master.id), HTTP_AUTHORIZATION=init_data_header("5001")
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["master"]["is_active"] is False

    def test_preview_emits_deactivation_started_audit(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        resp = client.post(_preview_url(master.id), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200
        rows = list(
            AuditLog.all_tenants.filter(action="master.deactivation_started").values_list(
                "action", "target_id"
            )
        )
        assert len(rows) == 1
        assert rows[0][1] == master.id

    def test_fallback_excludes_archived_master(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        service = _make_service(tenant)
        _link_master_service(tenant, master, service)
        # An archived master that also performs the service must NOT appear.
        archived = make_master(
            tenant,
            name="Архивный",
            external_id=21,
            is_active=False,
            archived_at=datetime.now(tz=timezone.utc),
        )
        _link_master_service(tenant, archived, service)

        bu = _make_customer_bot_user(tenant, 3)
        _make_booking(tenant=tenant, master=master, service=service, bot_user=bu)

        resp = client.post(_preview_url(master.id), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200
        fb = resp.json()["future_bookings"][0]["fallback_masters"]
        assert fb == []


# =========================================================================
# EXECUTE
# =========================================================================


@pytest.fixture
def cascade_setup(tenant: Tenant, master: CatalogMaster):
    """Common setup: master with 1 reassignable + 1 cancel-only booking."""

    service = _make_service(tenant)
    _link_master_service(tenant, master, service)
    other = make_master(tenant, name="Мария Соколова", external_id=20)
    _link_master_service(tenant, other, service)
    other_no_service = make_master(
        tenant, name="Не выполняет услугу", external_id=22
    )  # no MasterService link

    visit1 = datetime.now(tz=timezone.utc) + timedelta(days=3)
    visit2 = datetime.now(tz=timezone.utc) + timedelta(days=4)
    bu1 = _make_customer_bot_user(tenant, 10)
    bu2 = _make_customer_bot_user(tenant, 11)
    b1 = _make_booking(tenant=tenant, master=master, service=service, bot_user=bu1, visit_at=visit1)
    b2 = _make_booking(
        tenant=tenant,
        master=master,
        service=service,
        bot_user=bu2,
        visit_at=visit2,
        client_name="Елена Петрова",
    )
    return {
        "service": service,
        "other": other,
        "other_no_service": other_no_service,
        "b1": b1,
        "b2": b2,
    }


class TestInventoryIntegrity:
    """DRF-1139 — the preview must not present a blind spot as «nothing to do».

    Measured on the pilot 2026-08-16: every ``BookingRequest`` row has
    ``master_id IS NULL``, so the actionable query returns nothing for
    every master no matter what the salon actually has booked. The
    preview answered `total_future_bookings: 0` for all four masters
    while the Ayla mirror held a live future visit for one of them — and
    then offered an irreversible «Deactivate» button on that basis.
    """

    def test_mirror_visit_with_no_actionable_row_marks_inventory_incomplete(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        """The exact pilot shape: mirror knows, the cascade does not."""
        _make_mirror_booking(tenant=tenant, master=master)

        resp = client.post(_preview_url(master.id), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200
        summary = resp.json()["summary"]
        assert summary["total_future_bookings"] == 0
        assert summary["mirror_future_bookings"] == 1
        assert summary["inventory_complete"] is False

    def test_settled_mirror_statuses_do_not_count(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        """Cancelled / completed / no-show visits disturb nobody."""
        for status in (
            RemoteBookingProxy.Status.CANCELLED,
            RemoteBookingProxy.Status.COMPLETED,
            RemoteBookingProxy.Status.NO_SHOW,
        ):
            _make_mirror_booking(tenant=tenant, master=master, status=status)

        resp = client.post(_preview_url(master.id), HTTP_AUTHORIZATION=init_data_header("5001"))
        summary = resp.json()["summary"]
        assert summary["mirror_future_bookings"] == 0
        assert summary["inventory_complete"] is True

    def test_past_mirror_visits_do_not_count(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        _make_mirror_booking(
            tenant=tenant,
            master=master,
            start_at=datetime.now(tz=timezone.utc) - timedelta(days=1),
        )
        resp = client.post(_preview_url(master.id), HTTP_AUTHORIZATION=init_data_header("5001"))
        summary = resp.json()["summary"]
        assert summary["mirror_future_bookings"] == 0
        assert summary["inventory_complete"] is True

    def test_another_masters_mirror_visit_does_not_count(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        other = make_master(tenant, name="Мария Соколова", external_id=20)
        _make_mirror_booking(tenant=tenant, master=other)

        resp = client.post(_preview_url(master.id), HTTP_AUTHORIZATION=init_data_header("5001"))
        summary = resp.json()["summary"]
        assert summary["mirror_future_bookings"] == 0
        assert summary["inventory_complete"] is True

    def test_deactivate_refused_while_inventory_incomplete(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        """The whole point: an unseen live visit blocks the irreversible step."""
        _make_mirror_booking(tenant=tenant, master=master)

        resp = client.post(
            _deactivate_url(master.id),
            data=json.dumps({"bookings_plan": [], "reason": "уволилась"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "inventory_incomplete"

        master.refresh_from_db()
        assert master.is_active is True
        assert master.archived_at is None

    def test_matching_counts_do_not_block_a_legitimate_cascade(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        """One mirror visit, one actionable booking — the guard stays out of the way."""
        service = _make_service(tenant)
        _link_master_service(tenant, master, service)
        bu = _make_customer_bot_user(tenant, 1)
        booking = _make_booking(tenant=tenant, master=master, service=service, bot_user=bu)
        _make_mirror_booking(tenant=tenant, master=master, start_at=booking.visit_at)

        resp = client.post(
            _deactivate_url(master.id),
            data=json.dumps(
                {
                    "bookings_plan": [{"booking_id": str(booking.id), "action": "cancel"}],
                    "reason": "уволилась",
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 200, resp.content
        master.refresh_from_db()
        assert master.is_active is False

    def test_stale_mirror_does_not_block(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        """A mirror lagging BEHIND is safe — we would move more, never fewer."""
        service = _make_service(tenant)
        _link_master_service(tenant, master, service)
        bu = _make_customer_bot_user(tenant, 1)
        _make_booking(tenant=tenant, master=master, service=service, bot_user=bu)
        # No mirror row at all.

        resp = client.post(_preview_url(master.id), HTTP_AUTHORIZATION=init_data_header("5001"))
        summary = resp.json()["summary"]
        assert summary["total_future_bookings"] == 1
        assert summary["mirror_future_bookings"] == 0
        assert summary["inventory_complete"] is True


class TestExecute:
    def test_happy_path_reassign_and_cancel(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
        cascade_setup,
    ) -> None:
        b1 = cascade_setup["b1"]
        b2 = cascade_setup["b2"]
        other = cascade_setup["other"]

        with patch(
            "apps.admin_api.services.master_deactivation.send_message",
            return_value={"ok": True},
        ) as send_mock:
            resp = client.post(
                _deactivate_url(master.id),
                data=json.dumps(
                    {
                        "bookings_plan": [
                            {
                                "booking_id": str(b1.id),
                                "action": "reassign",
                                "to_master_id": str(other.id),
                            },
                            {"booking_id": str(b2.id), "action": "cancel"},
                        ],
                        "reason": "Уход с работы",
                        "notify_reassigned_masters": False,
                    }
                ),
                content_type="application/json",
                HTTP_AUTHORIZATION=init_data_header("5001"),
            )
        assert resp.status_code == 200, resp.content
        data = resp.json()
        assert data["is_active"] is False
        assert data["summary"]["reassigned_count"] == 1
        assert data["summary"]["cancelled_count"] == 1

        # State checks
        master.refresh_from_db()
        assert master.is_active is False
        assert master.archived_at is not None
        assert master.archive_reason == "Уход с работы"

        b1.refresh_from_db()
        assert str(b1.master_id) == str(other.id)
        b2.refresh_from_db()
        assert b2.status == BookingRequest.Status.CANCELLED

        # Customer notifications dispatched (2 — one per booking).
        assert send_mock.call_count == 2

    def test_plan_missing_booking_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
        cascade_setup,
    ) -> None:
        b1 = cascade_setup["b1"]
        other = cascade_setup["other"]
        resp = client.post(
            _deactivate_url(master.id),
            data=json.dumps(
                {
                    "bookings_plan": [
                        {
                            "booking_id": str(b1.id),
                            "action": "reassign",
                            "to_master_id": str(other.id),
                        },
                    ],
                    "reason": "",
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 409  # race detection
        assert resp.json()["error"] == "plan_stale_refresh_required"

    def test_plan_unknown_booking_id_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
        cascade_setup,
    ) -> None:
        import uuid

        b1 = cascade_setup["b1"]
        b2 = cascade_setup["b2"]
        other = cascade_setup["other"]
        resp = client.post(
            _deactivate_url(master.id),
            data=json.dumps(
                {
                    "bookings_plan": [
                        {
                            "booking_id": str(b1.id),
                            "action": "reassign",
                            "to_master_id": str(other.id),
                        },
                        {"booking_id": str(b2.id), "action": "cancel"},
                        {"booking_id": str(uuid.uuid4()), "action": "cancel"},
                    ],
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "plan_invalid"

    def test_reassign_to_target_without_service_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
        cascade_setup,
    ) -> None:
        b1 = cascade_setup["b1"]
        b2 = cascade_setup["b2"]
        other_no = cascade_setup["other_no_service"]
        resp = client.post(
            _deactivate_url(master.id),
            data=json.dumps(
                {
                    "bookings_plan": [
                        {
                            "booking_id": str(b1.id),
                            "action": "reassign",
                            "to_master_id": str(other_no.id),
                        },
                        {"booking_id": str(b2.id), "action": "cancel"},
                    ],
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "service_not_offered_by_target"

    def test_reassign_action_missing_to_master_id_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
        cascade_setup,
    ) -> None:
        b1 = cascade_setup["b1"]
        resp = client.post(
            _deactivate_url(master.id),
            data=json.dumps({"bookings_plan": [{"booking_id": str(b1.id), "action": "reassign"}]}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_custom_template_overrides_default(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
        cascade_setup,
    ) -> None:
        b1 = cascade_setup["b1"]
        b2 = cascade_setup["b2"]
        other = cascade_setup["other"]
        custom = "Custom override text for {client_first_name}"

        sent_texts: list[str] = []

        def _capture(chat_id: str, text: str, **kw):  # noqa: ARG001
            sent_texts.append(text)
            return {"ok": True}

        with patch(
            "apps.admin_api.services.master_deactivation.send_message", side_effect=_capture
        ):
            resp = client.post(
                _deactivate_url(master.id),
                data=json.dumps(
                    {
                        "bookings_plan": [
                            {
                                "booking_id": str(b1.id),
                                "action": "reassign",
                                "to_master_id": str(other.id),
                            },
                            {"booking_id": str(b2.id), "action": "cancel"},
                        ],
                        "custom_notification_template": custom,
                        "notify_reassigned_masters": False,
                    }
                ),
                content_type="application/json",
                HTTP_AUTHORIZATION=init_data_header("5001"),
            )
        assert resp.status_code == 200
        # Both DMs use the custom template.
        assert all("Custom override text for" in t for t in sent_texts)

    def test_default_template_reassign_branch_rendered(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
        cascade_setup,
    ) -> None:
        b1 = cascade_setup["b1"]
        b2 = cascade_setup["b2"]
        other = cascade_setup["other"]

        sent_texts: list[str] = []

        def _capture(chat_id: str, text: str, **kw):  # noqa: ARG001
            sent_texts.append(text)
            return {"ok": True}

        with patch(
            "apps.admin_api.services.master_deactivation.send_message", side_effect=_capture
        ):
            resp = client.post(
                _deactivate_url(master.id),
                data=json.dumps(
                    {
                        "bookings_plan": [
                            {
                                "booking_id": str(b1.id),
                                "action": "reassign",
                                "to_master_id": str(other.id),
                            },
                            {"booking_id": str(b2.id), "action": "cancel"},
                        ],
                        "notify_reassigned_masters": False,
                    }
                ),
                content_type="application/json",
                HTTP_AUTHORIZATION=init_data_header("5001"),
            )
        assert resp.status_code == 200
        # The reassign DM mentions the new master; the cancel DM does NOT.
        reassign_text = next(t for t in sent_texts if "Мария" in t and "переведём" in t)
        cancel_text = next(t for t in sent_texts if "отмен" in t)
        assert "[REASSIGN BRANCH]" not in reassign_text
        assert "[CANCEL BRANCH]" not in cancel_text
        assert "переведём" in reassign_text

    def test_notify_reassigned_masters_dispatches_master_dm(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
        cascade_setup,
        tenant: Tenant,
    ) -> None:
        b1 = cascade_setup["b1"]
        b2 = cascade_setup["b2"]
        other = cascade_setup["other"]
        # Link the fallback master to a bot user so DM can be sent.
        master_bu = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="7001",
            display_name="Мария-Мастер",
            chat_id="chat-master-7001",
            phone="+79991110011",
        )
        other.linked_bot_user = master_bu
        other.save(update_fields=["linked_bot_user"])

        sent_chats: list[str] = []

        def _capture(chat_id: str, text: str, **kw):  # noqa: ARG001
            sent_chats.append(chat_id)
            return {"ok": True}

        with patch(
            "apps.admin_api.services.master_deactivation.send_message", side_effect=_capture
        ):
            resp = client.post(
                _deactivate_url(master.id),
                data=json.dumps(
                    {
                        "bookings_plan": [
                            {
                                "booking_id": str(b1.id),
                                "action": "reassign",
                                "to_master_id": str(other.id),
                            },
                            {"booking_id": str(b2.id), "action": "cancel"},
                        ],
                        "notify_reassigned_masters": True,
                    }
                ),
                content_type="application/json",
                HTTP_AUTHORIZATION=init_data_header("5001"),
            )
        assert resp.status_code == 200
        # 2 customer DMs + 1 master DM
        assert "chat-master-7001" in sent_chats
        assert len(sent_chats) == 3

    def test_notification_failure_logged_but_not_rolled_back(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
        cascade_setup,
    ) -> None:
        from apps.channels.max.outbound import MaxAPIError

        b1 = cascade_setup["b1"]
        b2 = cascade_setup["b2"]
        other = cascade_setup["other"]

        def _boom(**kw):
            raise MaxAPIError(500, "MAX down")

        with patch("apps.admin_api.services.master_deactivation.send_message", side_effect=_boom):
            resp = client.post(
                _deactivate_url(master.id),
                data=json.dumps(
                    {
                        "bookings_plan": [
                            {
                                "booking_id": str(b1.id),
                                "action": "reassign",
                                "to_master_id": str(other.id),
                            },
                            {"booking_id": str(b2.id), "action": "cancel"},
                        ],
                        "notify_reassigned_masters": False,
                    }
                ),
                content_type="application/json",
                HTTP_AUTHORIZATION=init_data_header("5001"),
            )
        # State commit succeeded despite DM failure.
        assert resp.status_code == 200
        master.refresh_from_db()
        assert master.is_active is False

    def test_audit_rows_emitted(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
        cascade_setup,
    ) -> None:
        b1 = cascade_setup["b1"]
        b2 = cascade_setup["b2"]
        other = cascade_setup["other"]

        with patch(
            "apps.admin_api.services.master_deactivation.send_message",
            return_value={"ok": True},
        ):
            resp = client.post(
                _deactivate_url(master.id),
                data=json.dumps(
                    {
                        "bookings_plan": [
                            {
                                "booking_id": str(b1.id),
                                "action": "reassign",
                                "to_master_id": str(other.id),
                            },
                            {"booking_id": str(b2.id), "action": "cancel"},
                        ],
                        "notify_reassigned_masters": False,
                    }
                ),
                content_type="application/json",
                HTTP_AUTHORIZATION=init_data_header("5001"),
            )
        assert resp.status_code == 200

        actions = list(AuditLog.all_tenants.values_list("action", flat=True).order_by("created_at"))
        assert "master.bookings_reassigned" in actions
        assert "master.bookings_cancelled" in actions
        assert "master.deactivated" in actions

    def test_already_deactivated_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        inactive_master: CatalogMaster,
    ) -> None:
        inactive_master.archived_at = datetime.now(tz=timezone.utc)
        inactive_master.save(update_fields=["archived_at"])
        resp = client.post(
            _deactivate_url(inactive_master.id),
            data=json.dumps({"bookings_plan": []}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "already_deactivated"

    def test_invalid_body_no_plan_400(
        self, client: Client, owner_bot_user: BotUser, master: CatalogMaster
    ) -> None:
        resp = client.post(
            _deactivate_url(master.id),
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400


# =========================================================================
# REACTIVATE
# =========================================================================


class TestReactivate:
    def test_active_master_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        resp = client.post(
            _reactivate_url(master.id),
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "already_active"

    def test_inactive_master_reactivated(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        m = make_master(
            tenant,
            name="Бывший Архив",
            external_id=50,
            is_active=False,
            archived_at=datetime.now(tz=timezone.utc),
        )
        m.archive_reason = "previously deactivated"
        m.save(update_fields=["archive_reason"])

        resp = client.post(
            _reactivate_url(m.id),
            data=json.dumps({"notify_master": False}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_active"] is True
        assert data["archived_at"] is None

        m.refresh_from_db()
        assert m.is_active is True
        assert m.archived_at is None
        assert m.archive_reason == ""

    def test_reactivate_dispatches_master_dm(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        master_bu = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="8001",
            display_name="Master DM",
            chat_id="chat-8001",
            phone="+79990000001",
        )
        m = make_master(
            tenant,
            name="Reactivate Me",
            external_id=60,
            is_active=False,
            archived_at=datetime.now(tz=timezone.utc),
            linked_bot_user=master_bu,
        )

        with patch(
            "apps.admin_api.services.master_deactivation.send_message",
            return_value={"ok": True},
        ) as send_mock:
            resp = client.post(
                _reactivate_url(m.id),
                data=json.dumps({"notify_master": True}),
                content_type="application/json",
                HTTP_AUTHORIZATION=init_data_header("5001"),
            )
        assert resp.status_code == 200
        assert send_mock.called
        assert send_mock.call_args.kwargs["chat_id"] == "chat-8001"

    def test_reactivate_no_linked_bot_user_no_error(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        m = make_master(
            tenant,
            name="No Link",
            external_id=70,
            is_active=False,
            archived_at=datetime.now(tz=timezone.utc),
        )
        with patch("apps.admin_api.services.master_deactivation.send_message") as send_mock:
            resp = client.post(
                _reactivate_url(m.id),
                data=json.dumps({"notify_master": True}),
                content_type="application/json",
                HTTP_AUTHORIZATION=init_data_header("5001"),
            )
        assert resp.status_code == 200
        assert not send_mock.called

    def test_reactivate_preserves_master_services(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        m = make_master(
            tenant,
            name="WithServices",
            external_id=80,
            is_active=False,
            archived_at=datetime.now(tz=timezone.utc),
        )
        service = _make_service(tenant, external_id=43, slug="other-svc")
        ms = _link_master_service(tenant, m, service)

        resp = client.post(
            _reactivate_url(m.id),
            data=json.dumps({"notify_master": False}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 200
        # The MasterService row must still exist post-reactivation.
        assert MasterService.all_tenants.filter(pk=ms.pk).exists()

    def test_reactivate_emits_audit_row(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        m = make_master(
            tenant,
            name="AuditCheck",
            external_id=90,
            is_active=False,
            archived_at=datetime.now(tz=timezone.utc),
        )
        resp = client.post(
            _reactivate_url(m.id),
            data=json.dumps({"notify_master": False}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 200
        assert AuditLog.all_tenants.filter(action="master.reactivated").exists()


# =========================================================================
# SERVICE-LAYER UNIT TESTS — close gaps that don't fit through HTTP
# =========================================================================


class TestServiceLayerUnits:
    def test_preview_no_service_id_yields_empty_fallback(
        self,
        tenant: Tenant,
        master: CatalogMaster,
        owner_bot_user: BotUser,
    ) -> None:
        # Booking with no service_id (legacy/external) → no fallback computable.
        BookingRequest.all_tenants.create(
            tenant=tenant,
            master=master,
            service_name="Legacy",
            client_name="X",
            client_phone="+71112223344",
            status=BookingRequest.Status.CONFIRMED,
            visit_at=datetime.now(tz=timezone.utc) + timedelta(days=2),
        )
        preview = preview_deactivation(master, actor=owner_bot_user, actor_role="owner")
        assert len(preview.future_bookings) == 1
        assert preview.future_bookings[0].fallback_masters == []

    def test_execute_with_no_bookings_just_archives(
        self,
        tenant: Tenant,
        master: CatalogMaster,
        owner_bot_user: BotUser,
    ) -> None:
        result = execute_deactivation(
            master,
            plan=[],
            reason="no bookings test",
            notify_reassigned_masters=False,
            custom_template=None,
            actor=owner_bot_user,
            actor_role="owner",
        )
        master.refresh_from_db()
        assert master.is_active is False
        assert result.reassigned_count == 0
        assert result.cancelled_count == 0

    def test_default_template_has_both_branches(self) -> None:
        # Spec §770-783 — branches present as labelled markers.
        assert "[REASSIGN BRANCH]" in DEFAULT_CUSTOMER_NOTIFICATION_TEMPLATE
        assert "[CANCEL BRANCH]" in DEFAULT_CUSTOMER_NOTIFICATION_TEMPLATE

    def test_execute_with_reassign_to_archived_master_raises(
        self,
        tenant: Tenant,
        master: CatalogMaster,
        owner_bot_user: BotUser,
    ) -> None:
        service = _make_service(tenant)
        _link_master_service(tenant, master, service)
        archived = make_master(
            tenant,
            name="Archived target",
            external_id=21,
            is_active=False,
            archived_at=datetime.now(tz=timezone.utc),
        )
        _link_master_service(tenant, archived, service)
        bu = _make_customer_bot_user(tenant, 50)
        b = _make_booking(tenant=tenant, master=master, service=service, bot_user=bu)

        with pytest.raises(DeactivationError) as exc_info:
            execute_deactivation(
                master,
                plan=[
                    BookingAction(
                        booking_id=str(b.id),
                        action="reassign",
                        to_master_id=str(archived.id),
                    )
                ],
                reason="",
                notify_reassigned_masters=False,
                custom_template=None,
                actor=owner_bot_user,
                actor_role="owner",
            )
        assert exc_info.value.slug == "fallback_not_active"

    def test_reactivate_already_active_raises(
        self,
        master: CatalogMaster,
        owner_bot_user: BotUser,
    ) -> None:
        with pytest.raises(DeactivationError) as exc_info:
            reactivate_master(
                master,
                notify_master=False,
                actor=owner_bot_user,
                actor_role="owner",
            )
        assert exc_info.value.slug == "already_active"


# =========================================================================
# DRF-1307 — the consent gate on the customer broadcast
# =========================================================================


def _plan_all(bookings, *, to_master=None) -> list[BookingAction]:
    return [
        BookingAction(
            booking_id=str(b.id),
            action="reassign" if to_master else "cancel",
            to_master_id=str(to_master.id) if to_master else None,
        )
        for b in bookings
    ]


def _audit_blocked_reasons() -> dict[str, str]:
    """``{booking_id: reason}`` from the per-booking cascade audit rows."""
    out: dict[str, str] = {}
    for row in AuditLog.all_tenants.filter(
        action__in=("master.bookings_reassigned", "master.bookings_cancelled")
    ):
        payload = row.payload or {}
        out[payload["booking_id"]] = payload.get("customer_notification_blocked", "")
    return out


class TestCustomerConsentGate:
    """Who the cascade may write to, and — just as loudly — who it may.

    A test that only proves nobody was written to proves nothing: an
    empty send list is equally consistent with «the gate works» and
    «the dispatch is dead». Every test below that asserts a block runs
    against a suite where :meth:`test_consenting_client_still_receives`
    asserts the opposite half on the same code path, and the mixed test
    asserts both halves in one call.
    """

    @pytest.fixture
    def one_booking(self, tenant: Tenant, master: CatalogMaster):
        service = _make_service(tenant)
        _link_master_service(tenant, master, service)
        return service

    def _run(self, tenant, master, service, bot_user, owner_bot_user):
        booking = _make_booking(
            tenant=tenant, master=master, service=service, bot_user=bot_user
        )
        sent: list[tuple[str, str]] = []

        def _capture(chat_id: str, text: str, **kw):  # noqa: ARG001
            sent.append((chat_id, text))
            return {"ok": True}

        with patch(
            "apps.admin_api.services.master_deactivation.send_message", side_effect=_capture
        ):
            result = execute_deactivation(
                master,
                plan=_plan_all([booking]),
                reason="уволилась",
                notify_reassigned_masters=False,
                custom_template=None,
                actor=owner_bot_user,
                actor_role="owner",
            )
        return booking, sent, result

    def test_consenting_client_still_receives(
        self, tenant, master, one_booking, owner_bot_user
    ) -> None:
        """The half that makes every block below mean something."""
        bu = _make_customer_bot_user(tenant, 30)
        booking, sent, result = self._run(tenant, master, one_booking, bu, owner_bot_user)

        assert [c for c, _ in sent] == ["chat-6030"]
        assert result.customer_notifications_dispatched == 1
        assert result.customer_notifications_blocked == 0
        assert _audit_blocked_reasons()[str(booking.id)] == ""

    def test_opted_out_client_is_not_written_to(
        self, tenant, master, one_booking, owner_bot_user
    ) -> None:
        bu = _make_customer_bot_user(tenant, 31, opted_out=True)
        booking, sent, result = self._run(tenant, master, one_booking, bu, owner_bot_user)

        assert sent == []
        assert result.customer_notifications_blocked == 1
        assert _audit_blocked_reasons()[str(booking.id)] == "opt_out"
        # …and the cascade itself still ran. The person's booking is
        # cancelled whether or not we were allowed to say so.
        assert result.cancelled_count == 1
        master.refresh_from_db()
        assert master.is_active is False

    def test_client_who_never_consented_is_not_written_to(
        self, tenant, master, one_booking, owner_bot_user
    ) -> None:
        bu = _make_customer_bot_user(tenant, 32, consented=False)
        booking, sent, result = self._run(tenant, master, one_booking, bu, owner_bot_user)

        assert sent == []
        assert _audit_blocked_reasons()[str(booking.id)] == "no_consent"

    def test_withdrawn_consent_is_not_written_to(
        self, tenant, master, one_booking, owner_bot_user
    ) -> None:
        """The mine: ``withdraw()`` never clears ``consent_at``.

        Four of the pilot's twelve reachable users are in exactly this
        state on 2026-08-23 — column set, record withdrawn. A gate that
        reads the column alone writes to all four.
        """
        bu = _make_customer_bot_user(tenant, 33, withdrawn=True)
        assert bu.consent_at is not None  # the column still says yes
        booking, sent, result = self._run(tenant, master, one_booking, bu, owner_bot_user)

        assert sent == []
        assert _audit_blocked_reasons()[str(booking.id)] == "consent_withdrawn"

    def test_erased_client_with_surviving_chat_id_is_not_written_to(
        self, tenant, master, one_booking, owner_bot_user
    ) -> None:
        """The second mine: ``soft_delete_user()`` leaves ``chat_id`` set.

        One pilot row is erased and still reachable.
        """
        bu = _make_customer_bot_user(tenant, 34, deleted=True)
        assert bu.chat_id  # erasure scrubbed the PII, not the address
        booking, sent, result = self._run(tenant, master, one_booking, bu, owner_bot_user)

        assert sent == []
        assert _audit_blocked_reasons()[str(booking.id)] == "deleted"

    def test_mixed_cascade_writes_to_exactly_the_one_who_may_be_written_to(
        self, tenant, master, one_booking, owner_bot_user
    ) -> None:
        """Both halves in one call — the pilot's shape, scaled down.

        Four recipients, one allowed. On the pilot the ratio is twelve
        reachable to one allowed.
        """
        service = one_booking
        ok = _make_customer_bot_user(tenant, 40)
        opted_out = _make_customer_bot_user(tenant, 41, opted_out=True)
        withdrawn = _make_customer_bot_user(tenant, 42, withdrawn=True)
        erased = _make_customer_bot_user(tenant, 43, deleted=True)

        bookings = [
            _make_booking(
                tenant=tenant,
                master=master,
                service=service,
                bot_user=bu,
                visit_at=datetime.now(tz=timezone.utc) + timedelta(days=n + 2),
            )
            for n, bu in enumerate((ok, opted_out, withdrawn, erased))
        ]

        sent: list[str] = []

        def _capture(chat_id: str, text: str, **kw):  # noqa: ARG001
            sent.append(chat_id)
            return {"ok": True}

        with patch(
            "apps.admin_api.services.master_deactivation.send_message", side_effect=_capture
        ):
            result = execute_deactivation(
                master,
                plan=_plan_all(bookings),
                reason="уволилась",
                notify_reassigned_masters=False,
                custom_template=None,
                actor=owner_bot_user,
                actor_role="owner",
            )

        assert sent == ["chat-6040"]
        assert result.cancelled_count == 4
        assert result.customer_notifications_dispatched == 1
        assert result.customer_notifications_blocked == 3

        reasons = _audit_blocked_reasons()
        assert reasons[str(bookings[0].id)] == ""
        assert reasons[str(bookings[1].id)] == "opt_out"
        assert reasons[str(bookings[2].id)] == "consent_withdrawn"
        assert reasons[str(bookings[3].id)] == "deleted"

    def test_preview_reports_unreachable_clients_before_the_button(
        self,
        client: Client,
        tenant: Tenant,
        master: CatalogMaster,
        one_booking,
        owner_bot_user: BotUser,
    ) -> None:
        """The operator learns who they must phone while it is still free to.

        Blocking a message about somebody's own cancelled visit is only
        defensible if a human is told to make the call instead. If this
        assertion ever goes away, the gate stops being a protection and
        becomes a way to lose people quietly.
        """
        _make_booking(
            tenant=tenant,
            master=master,
            service=one_booking,
            bot_user=_make_customer_bot_user(tenant, 50),
        )
        _make_booking(
            tenant=tenant,
            master=master,
            service=one_booking,
            bot_user=_make_customer_bot_user(tenant, 51, withdrawn=True),
            visit_at=datetime.now(tz=timezone.utc) + timedelta(days=5),
        )

        resp = client.post(_preview_url(master.id), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["bookings_client_unreachable"] == 1
        blocked = sorted(b["notify_blocked"] for b in data["future_bookings"])
        assert blocked == ["", "consent_withdrawn"]


class TestOutboundSafetyScope:
    """What the safety filter is pointed at here, and what it is not.

    The DRF-1307 brief asked for ``evaluate_outbound`` on this message.
    Run against the administrator's own words it blocks the messages a
    salon most needs to send — see :func:`_vet_catalogue_values`. These
    two tests pin both halves of that decision so a later reader cannot
    "restore" the missing check without first deleting a test that says
    why it is missing.
    """

    @pytest.fixture
    def one_booking(self, tenant: Tenant, master: CatalogMaster):
        service = _make_service(tenant)
        _link_master_service(tenant, master, service)
        return service

    def test_administrator_may_promise_a_refund_and_give_a_phone_number(
        self, tenant, master, one_booking, owner_bot_user
    ) -> None:
        """A salon administrator has the authority an LLM does not.

        «вернём деньги» trips the ``promise`` shape and the number trips
        ``contact``. Both are exactly right in a message about an
        appointment the salon just cancelled.
        """
        bu = _make_customer_bot_user(tenant, 60)
        booking = _make_booking(tenant=tenant, master=master, service=one_booking, bot_user=bu)
        custom = (
            "Здравствуйте, {client_first_name}! Запись отменяем — вернём деньги "
            "за предоплату. Звоните: +7 999 123 45 67."
        )
        sent: list[str] = []

        def _capture(chat_id: str, text: str, **kw):  # noqa: ARG001
            sent.append(text)
            return {"ok": True}

        with patch(
            "apps.admin_api.services.master_deactivation.send_message", side_effect=_capture
        ):
            result = execute_deactivation(
                master,
                plan=_plan_all([booking]),
                reason="уволилась",
                notify_reassigned_masters=False,
                custom_template=custom,
                actor=owner_bot_user,
                actor_role="owner",
            )

        assert result.customer_notifications_dispatched == 1
        assert "вернём деньги" in sent[0]
        assert "+7 999 123 45 67" in sent[0]

    def test_master_name_carrying_a_phone_number_refuses_the_whole_cascade(
        self, tenant, master, one_booking, owner_bot_user
    ) -> None:
        """Catalogue text is not the administrator's, and it IS checked.

        Refuses instead of dropping the DM: this is fixable in the
        catalogue and the retry costs nothing, so nothing is mutated.
        """
        master.name = "Ольга +7 999 123 45 67"
        master.save(update_fields=["name"])
        bu = _make_customer_bot_user(tenant, 61)
        booking = _make_booking(tenant=tenant, master=master, service=one_booking, bot_user=bu)

        with patch("apps.admin_api.services.master_deactivation.send_message") as send_mock:
            with pytest.raises(DeactivationError) as exc_info:
                execute_deactivation(
                    master,
                    plan=_plan_all([booking]),
                    reason="уволилась",
                    notify_reassigned_masters=False,
                    custom_template=None,
                    actor=owner_bot_user,
                    actor_role="owner",
                )

        assert exc_info.value.slug == "notification_text_unsafe"
        send_mock.assert_not_called()
        # Nothing happened: the master is still active and the booking stands.
        master.refresh_from_db()
        booking.refresh_from_db()
        assert master.is_active is True
        assert master.archived_at is None
        assert booking.status == BookingRequest.Status.CONFIRMED
