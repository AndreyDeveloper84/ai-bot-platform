"""Integration tests for /api/v1/admin/availability-requests/* (M3-admin / Bundle B).

Covers:

* Auth matrix — customer / master / receptionist / admin / owner.
* GET list — pending vs decided vs all, master_id filter, pagination,
  cross-tenant isolation.
* POST approve — happy path (owner + admin), already-decided (409),
  overlap conflict (409), ScheduleException materialised (one per
  covered date, type=day_off), update_or_create idempotency, audit
  row + master DM dispatched, cross-tenant 404, bad UUID 404,
  legacy row (no typed window) 400, MAX DM failure non-fatal.
* POST reject — happy path, blank reason 400, oversize reason 400,
  already-decided 409, audit row + master DM dispatched.

Each endpoint test wraps :mod:`apps.admin_api.services.availability`
through the HTTP layer so we exercise URL routing + auth + body parsing.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import (
    init_data_header,
    link_master_to_bot_user,
    make_master,
)
from apps.audit.models import AuditLog
from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.scheduling.models import (
    ScheduleChangeRequest,
    ScheduleException,
)
from apps.tenancy.models import Tenant


# Decision flow uses ``transaction.on_commit`` for master DM dispatch —
# transactional tests so the hook fires + we can observe the side effect.
pytestmark = pytest.mark.django_db(transaction=True)


# --- URL helpers ----------------------------------------------------------


def _list_url() -> str:
    return reverse("admin_api:availability_requests_list")


def _approve_url(request_id) -> str:
    return reverse("admin_api:availability_request_approve", args=[str(request_id)])


def _reject_url(request_id) -> str:
    return reverse("admin_api:availability_request_reject", args=[str(request_id)])


# --- factory helpers ------------------------------------------------------


def _utc(dt_naive: datetime) -> datetime:
    return dt_naive.replace(tzinfo=timezone.utc)


def _make_pending_request(
    tenant: Tenant,
    master: CatalogMaster,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    reason_class: str = "vacation",
    reason_text: str = "семейная поездка",
    requested_by: BotUser | None = None,
) -> ScheduleChangeRequest:
    if start is None:
        start = _utc(datetime(2026, 6, 1, 9, 0))
    if end is None:
        end = _utc(datetime(2026, 6, 1, 18, 0))
    return ScheduleChangeRequest.all_tenants.create(
        tenant=tenant,
        master=master,
        requested_start=start,
        requested_end=end,
        reason_class=reason_class,
        reason_text=reason_text,
        requested_by=requested_by,
        status=ScheduleChangeRequest.Status.PENDING,
    )


# =========================================================================
# AUTH MATRIX
# =========================================================================


class TestAuthMatrix:
    """All three endpoints share require_admin_role — customer / master /
    receptionist forbidden across the board. Admin + Owner allowed."""

    def test_customer_forbidden_list(
        self, client: Client, customer_bot_user: BotUser, master: CatalogMaster
    ) -> None:
        resp = client.get(_list_url(), HTTP_AUTHORIZATION=init_data_header("5005"))
        assert resp.status_code == 403

    def test_master_only_forbidden_list(
        self,
        client: Client,
        master_only_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        link_master_to_bot_user(master, master_only_bot_user)
        resp = client.get(_list_url(), HTTP_AUTHORIZATION=init_data_header("5004"))
        assert resp.status_code == 403

    def test_receptionist_forbidden_approve(
        self,
        client: Client,
        receptionist_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        req = _make_pending_request(tenant, master)
        resp = client.post(_approve_url(req.id), HTTP_AUTHORIZATION=init_data_header("5003"))
        assert resp.status_code == 403

    def test_admin_allowed_list(
        self, client: Client, admin_bot_user: BotUser, master: CatalogMaster
    ) -> None:
        resp = client.get(_list_url(), HTTP_AUTHORIZATION=init_data_header("5002"))
        assert resp.status_code == 200

    def test_owner_allowed_list(
        self, client: Client, owner_bot_user: BotUser, master: CatalogMaster
    ) -> None:
        resp = client.get(_list_url(), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200

    def test_unauthenticated_400_or_401(
        self, client: Client, tenant: Tenant, master: CatalogMaster
    ) -> None:
        # No Authorization header.
        req = _make_pending_request(tenant, master)
        resp = client.post(_approve_url(req.id))
        assert resp.status_code in (400, 401)


# =========================================================================
# GET LIST
# =========================================================================


class TestList:
    def test_list_empty(
        self, client: Client, owner_bot_user: BotUser, master: CatalogMaster
    ) -> None:
        resp = client.get(_list_url(), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["next_cursor"] is None

    def test_list_pending_only_by_default(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        pending = _make_pending_request(tenant, master)
        decided = _make_pending_request(
            tenant,
            master,
            start=_utc(datetime(2026, 7, 1, 9, 0)),
            end=_utc(datetime(2026, 7, 1, 18, 0)),
        )
        decided.status = ScheduleChangeRequest.Status.APPROVED
        decided.resolved_at = datetime.now(tz=timezone.utc)
        decided.save(update_fields=["status", "resolved_at"])

        resp = client.get(_list_url(), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200
        ids = [item["request_id"] for item in resp.json()["items"]]
        assert str(pending.id) in ids
        assert str(decided.id) not in ids

    def test_list_decided_filter(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        pending = _make_pending_request(tenant, master)
        decided = _make_pending_request(
            tenant,
            master,
            start=_utc(datetime(2026, 7, 1, 9, 0)),
            end=_utc(datetime(2026, 7, 1, 18, 0)),
        )
        decided.status = ScheduleChangeRequest.Status.REJECTED
        decided.resolved_at = datetime.now(tz=timezone.utc)
        decided.resolution_note = "не время для отпуска"
        decided.save(update_fields=["status", "resolved_at", "resolution_note"])

        resp = client.get(
            _list_url() + "?status=decided", HTTP_AUTHORIZATION=init_data_header("5001")
        )
        assert resp.status_code == 200
        ids = [item["request_id"] for item in resp.json()["items"]]
        assert str(decided.id) in ids
        assert str(pending.id) not in ids

    def test_list_all_filter_returns_both(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        pending = _make_pending_request(tenant, master)
        decided = _make_pending_request(
            tenant,
            master,
            start=_utc(datetime(2026, 7, 1, 9, 0)),
            end=_utc(datetime(2026, 7, 1, 18, 0)),
        )
        decided.status = ScheduleChangeRequest.Status.APPROVED
        decided.resolved_at = datetime.now(tz=timezone.utc)
        decided.save(update_fields=["status", "resolved_at"])

        resp = client.get(_list_url() + "?status=all", HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200
        ids = {item["request_id"] for item in resp.json()["items"]}
        assert {str(pending.id), str(decided.id)}.issubset(ids)

    def test_list_master_id_filter(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        other = make_master(tenant, name="Other Master", external_id=88)
        req_a = _make_pending_request(tenant, master)
        req_b = _make_pending_request(tenant, other)

        resp = client.get(
            _list_url() + f"?master_id={master.id}",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 200
        ids = [item["request_id"] for item in resp.json()["items"]]
        assert str(req_a.id) in ids
        assert str(req_b.id) not in ids

    def test_list_pagination_cursor_round_trip(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        # Create 3 requests; page through 2 + 1 with limit=2.
        reqs = [
            _make_pending_request(
                tenant,
                master,
                start=_utc(datetime(2026, 8, i + 1, 9, 0)),
                end=_utc(datetime(2026, 8, i + 1, 18, 0)),
                reason_text=f"req-{i}",
            )
            for i in range(3)
        ]
        resp = client.get(_list_url() + "?limit=2", HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["next_cursor"] is not None

        resp2 = client.get(
            _list_url() + f"?limit=2&cursor={body['next_cursor']}",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert len(body2["items"]) == 1
        assert body2["next_cursor"] is None
        all_ids = {i["request_id"] for i in body["items"] + body2["items"]}
        assert {str(r.id) for r in reqs} == all_ids

    def test_list_cross_tenant_isolation(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        other_tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        # A request lives on the other tenant — must NOT surface.
        other_master = make_master(other_tenant, name="Cross", external_id=777)
        ScheduleChangeRequest.all_tenants.create(
            tenant=other_tenant,
            master=other_master,
            requested_start=_utc(datetime(2026, 9, 1, 9, 0)),
            requested_end=_utc(datetime(2026, 9, 1, 18, 0)),
            reason_class="vacation",
            status=ScheduleChangeRequest.Status.PENDING,
        )
        resp = client.get(_list_url() + "?status=all", HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_list_bad_status_400(
        self, client: Client, owner_bot_user: BotUser, master: CatalogMaster
    ) -> None:
        resp = client.get(
            _list_url() + "?status=garbage",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"


# =========================================================================
# POST APPROVE
# =========================================================================


class TestApprove:
    def test_owner_approve_happy_path(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        # 3-day window — should materialise 3 ScheduleException day_off rows.
        req = _make_pending_request(
            tenant,
            master,
            start=_utc(datetime(2026, 6, 10, 9, 0)),
            end=_utc(datetime(2026, 6, 12, 18, 0)),
        )
        resp = client.post(
            _approve_url(req.id),
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 200, resp.content
        body = resp.json()["request"]
        assert body["status"] == ScheduleChangeRequest.Status.APPROVED
        assert len(body["materialised_dates"]) == 3

        req.refresh_from_db()
        assert req.status == ScheduleChangeRequest.Status.APPROVED
        assert req.resolved_at is not None

        # ScheduleException rows landed.
        excs = list(
            ScheduleException.all_tenants.filter(tenant=tenant, master=master).order_by("date")
        )
        assert len(excs) == 3
        assert all(e.type == ScheduleException.Type.DAY_OFF for e in excs)

        # Audit row.
        log = AuditLog.all_tenants.filter(
            action="admin.availability_approved", target_id=req.id
        ).first()
        assert log is not None
        assert log.payload["actor_role"] == "owner"
        assert len(log.payload["materialised_dates"]) == 3

    def test_admin_approve_allowed(
        self,
        client: Client,
        admin_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        req = _make_pending_request(tenant, master)
        resp = client.post(
            _approve_url(req.id),
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5002"),
        )
        assert resp.status_code == 200, resp.content
        log = AuditLog.all_tenants.filter(
            action="admin.availability_approved", target_id=req.id
        ).first()
        assert log is not None
        assert log.payload["actor_role"] == "admin"

    def test_approve_already_approved_409(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        req = _make_pending_request(tenant, master)
        req.status = ScheduleChangeRequest.Status.APPROVED
        req.resolved_at = datetime.now(tz=timezone.utc)
        req.save(update_fields=["status", "resolved_at"])

        resp = client.post(
            _approve_url(req.id),
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "already_decided"

    def test_approve_already_rejected_409(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        req = _make_pending_request(tenant, master)
        req.status = ScheduleChangeRequest.Status.REJECTED
        req.resolved_at = datetime.now(tz=timezone.utc)
        req.resolution_note = "no"
        req.save(update_fields=["status", "resolved_at", "resolution_note"])

        resp = client.post(
            _approve_url(req.id),
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "already_decided"

    def test_approve_overlap_conflict_409(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        # Pre-existing VACATION exception on a covered date — conflict.
        ScheduleException.all_tenants.create(
            tenant=tenant,
            master=master,
            date=datetime(2026, 6, 11).date(),
            type=ScheduleException.Type.VACATION,
        )
        req = _make_pending_request(
            tenant,
            master,
            start=_utc(datetime(2026, 6, 10, 9, 0)),
            end=_utc(datetime(2026, 6, 12, 18, 0)),
        )
        resp = client.post(
            _approve_url(req.id),
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == "overlap_conflict"
        assert "2026-06-11" in body["detail"]

        # Request stays PENDING (no partial mutation).
        req.refresh_from_db()
        assert req.status == ScheduleChangeRequest.Status.PENDING

    def test_approve_idempotent_day_off_overwrite(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        # Pre-seed a day_off on one of the dates — must overwrite cleanly.
        ScheduleException.all_tenants.create(
            tenant=tenant,
            master=master,
            date=datetime(2026, 6, 11).date(),
            type=ScheduleException.Type.DAY_OFF,
            reason="prior",
        )
        req = _make_pending_request(
            tenant,
            master,
            start=_utc(datetime(2026, 6, 10, 9, 0)),
            end=_utc(datetime(2026, 6, 12, 18, 0)),
            reason_text="отпуск с семьёй",
        )
        resp = client.post(
            _approve_url(req.id),
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 200, resp.content
        excs = list(
            ScheduleException.all_tenants.filter(tenant=tenant, master=master).order_by("date")
        )
        assert len(excs) == 3
        # The previously-seeded row was overwritten with the new reason.
        target = next(e for e in excs if e.date.isoformat() == "2026-06-11")
        assert target.reason == "отпуск с семьёй"

    def test_approve_cross_tenant_404(
        self,
        client: Client,
        owner_bot_user: BotUser,
        other_tenant: Tenant,
    ) -> None:
        other_master = make_master(other_tenant, name="X", external_id=555)
        other_req = _make_pending_request(other_tenant, other_master)
        resp = client.post(
            _approve_url(other_req.id),
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 404

    def test_approve_bad_uuid_404(
        self, client: Client, owner_bot_user: BotUser, master: CatalogMaster
    ) -> None:
        resp = client.post(
            _approve_url("not-a-uuid"),
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 404

    def test_approve_legacy_row_no_typed_window_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        # Legacy row with requested_change JSON only, no typed window.
        req = ScheduleChangeRequest.all_tenants.create(
            tenant=tenant,
            master=master,
            requested_change={"type": "exception_add", "date": "2026-06-11"},
            requested_start=None,
            requested_end=None,
            status=ScheduleChangeRequest.Status.PENDING,
        )
        resp = client.post(
            _approve_url(req.id),
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"

    def test_approve_dispatches_master_dm(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        # Create a master with a linked BotUser that has a chat_id.
        master_bu = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="9001",
            display_name="Анна-мастер",
            chat_id="9001-chat",
            phone="+79991111111",
        )
        m = make_master(tenant, name="Анна", external_id=44, linked_bot_user=master_bu)
        req = _make_pending_request(tenant, m)

        with patch("apps.channels.max.outbound.send_message") as send:
            resp = client.post(
                _approve_url(req.id),
                data="{}",
                content_type="application/json",
                HTTP_AUTHORIZATION=init_data_header("5001"),
            )
            assert resp.status_code == 200, resp.content
            assert send.called
            _args, kwargs = send.call_args
            assert kwargs["chat_id"] == "9001-chat"
            assert "одобрен" in kwargs["text"]

    def test_approve_dm_failure_does_not_fail_decision(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        from apps.channels.max.outbound import MaxAPIError

        master_bu = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="9002",
            display_name="Анна-мастер-2",
            chat_id="9002-chat",
            phone="+79992222222",
        )
        m = make_master(tenant, name="Анна2", external_id=45, linked_bot_user=master_bu)
        req = _make_pending_request(tenant, m)

        with patch(
            "apps.channels.max.outbound.send_message",
            side_effect=MaxAPIError(500, "boom"),
        ):
            resp = client.post(
                _approve_url(req.id),
                data="{}",
                content_type="application/json",
                HTTP_AUTHORIZATION=init_data_header("5001"),
            )
            assert resp.status_code == 200, resp.content
            req.refresh_from_db()
            assert req.status == ScheduleChangeRequest.Status.APPROVED

    def test_approve_no_linked_bot_user_skips_dm(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        # ``master`` fixture has no linked_bot_user.
        req = _make_pending_request(tenant, master)
        with patch("apps.channels.max.outbound.send_message") as send:
            resp = client.post(
                _approve_url(req.id),
                data="{}",
                content_type="application/json",
                HTTP_AUTHORIZATION=init_data_header("5001"),
            )
            assert resp.status_code == 200, resp.content
            assert not send.called


# =========================================================================
# POST REJECT
# =========================================================================


class TestReject:
    def test_owner_reject_happy_path(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        req = _make_pending_request(tenant, master)
        resp = client.post(
            _reject_url(req.id),
            data=json.dumps({"rejection_reason": "не время для отпуска"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 200, resp.content
        body = resp.json()["request"]
        assert body["status"] == ScheduleChangeRequest.Status.REJECTED
        assert body["resolution_note"] == "не время для отпуска"

        # No ScheduleException materialised.
        assert ScheduleException.all_tenants.filter(tenant=tenant, master=master).count() == 0

        log = AuditLog.all_tenants.filter(
            action="admin.availability_rejected", target_id=req.id
        ).first()
        assert log is not None
        assert log.payload["rejection_reason"] == "не время для отпуска"

    def test_admin_reject_allowed(
        self,
        client: Client,
        admin_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        req = _make_pending_request(tenant, master)
        resp = client.post(
            _reject_url(req.id),
            data=json.dumps({"rejection_reason": "переноси на след. месяц"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5002"),
        )
        assert resp.status_code == 200

    def test_reject_blank_reason_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        req = _make_pending_request(tenant, master)
        resp = client.post(
            _reject_url(req.id),
            data=json.dumps({"rejection_reason": "   "}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"

    def test_reject_missing_reason_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        req = _make_pending_request(tenant, master)
        resp = client.post(
            _reject_url(req.id),
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_reject_oversize_reason_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        req = _make_pending_request(tenant, master)
        resp = client.post(
            _reject_url(req.id),
            data=json.dumps({"rejection_reason": "x" * 501}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_reject_already_decided_409(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        req = _make_pending_request(tenant, master)
        req.status = ScheduleChangeRequest.Status.APPROVED
        req.resolved_at = datetime.now(tz=timezone.utc)
        req.save(update_fields=["status", "resolved_at"])

        resp = client.post(
            _reject_url(req.id),
            data=json.dumps({"rejection_reason": "поздно"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 409

    def test_reject_dispatches_master_dm(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        master_bu = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="9003",
            display_name="Анна-мастер-3",
            chat_id="9003-chat",
            phone="+79993333333",
        )
        m = make_master(tenant, name="Анна3", external_id=46, linked_bot_user=master_bu)
        req = _make_pending_request(tenant, m)

        with patch("apps.channels.max.outbound.send_message") as send:
            resp = client.post(
                _reject_url(req.id),
                data=json.dumps({"rejection_reason": "уже есть выходной"}),
                content_type="application/json",
                HTTP_AUTHORIZATION=init_data_header("5001"),
            )
            assert resp.status_code == 200, resp.content
            assert send.called
            _args, kwargs = send.call_args
            assert kwargs["chat_id"] == "9003-chat"
            assert "отклонён" in kwargs["text"]
            assert "уже есть выходной" in kwargs["text"]

    def test_reject_cross_tenant_404(
        self,
        client: Client,
        owner_bot_user: BotUser,
        other_tenant: Tenant,
    ) -> None:
        other_master = make_master(other_tenant, name="X2", external_id=556)
        other_req = _make_pending_request(other_tenant, other_master)
        resp = client.post(
            _reject_url(other_req.id),
            data=json.dumps({"rejection_reason": "no"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 404
