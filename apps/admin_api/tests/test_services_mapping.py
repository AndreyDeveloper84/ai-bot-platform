"""Integration tests for /api/v1/admin/services-mapping/* (PR 4 / MM4).

Covers:

* Auth matrix — customer / master / receptionist / admin / owner.
* GET — full shape, orphans, cross-tenant isolation, empty tenant.
* POST bulk — apply, atomicity, audit per master, snapshot conflicts,
  validation (cross-tenant, inactive entity, duplicates, empty list,
  missing token).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import (
    init_data_header,
    link_master_to_bot_user,
    make_master,
)
from apps.audit.models import AuditLog
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


# --- URL helpers ----------------------------------------------------------


def _get_url() -> str:
    return reverse("admin_api:services_mapping_get")


def _bulk_url() -> str:
    return reverse("admin_api:services_mapping_bulk")


def _post_bulk(client: Client, body: dict, *, header: str) -> Any:
    return client.post(
        _bulk_url(),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=header,
    )


def _make_service(
    tenant: Tenant,
    *,
    name: str = "Маникюр",
    external_id: int | None = None,
    is_active: bool = True,
    duration_min: int | None = 60,
    price_from: Decimal | None = Decimal("1500.00"),
) -> CatalogService:
    now = datetime.now(tz=timezone.utc)
    if external_id is None:
        external_id = CatalogService.all_tenants.filter(tenant=tenant).count() + 1000
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=external_id,
        external_updated_at=now,
        slug=f"svc-{external_id}",
        name=name,
        is_active=is_active,
        duration_min=duration_min,
        price_from=price_from,
    )


# =========================================================================
# AUTH MATRIX
# =========================================================================


class TestServicesMappingAuth:
    def test_customer_forbidden(self, client: Client, customer_bot_user: BotUser) -> None:
        resp = client.get(_get_url(), HTTP_AUTHORIZATION=init_data_header("5005"))
        assert resp.status_code == 403

    def test_receptionist_forbidden(self, client: Client, receptionist_bot_user: BotUser) -> None:
        resp = client.get(_get_url(), HTTP_AUTHORIZATION=init_data_header("5003"))
        assert resp.status_code == 403

    def test_master_only_forbidden(
        self,
        client: Client,
        master_only_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        link_master_to_bot_user(master, master_only_bot_user)
        resp = client.get(_get_url(), HTTP_AUTHORIZATION=init_data_header("5004"))
        assert resp.status_code == 403

    def test_admin_allowed(self, client: Client, admin_bot_user: BotUser) -> None:
        resp = client.get(_get_url(), HTTP_AUTHORIZATION=init_data_header("5002"))
        assert resp.status_code == 200

    def test_owner_allowed(self, client: Client, owner_bot_user: BotUser) -> None:
        resp = client.get(_get_url(), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200


# =========================================================================
# GET /api/v1/admin/services-mapping
# =========================================================================


class TestServicesMappingGet:
    def test_empty_tenant_returns_empty_arrays(
        self, client: Client, owner_bot_user: BotUser
    ) -> None:
        resp = client.get(_get_url(), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["services"] == []
        assert data["masters"] == []
        assert data["mapping"] == []
        assert data["orphans"] == {
            "services_without_masters": [],
            "masters_without_services": [],
        }
        assert isinstance(data["snapshot_token"], str)
        assert data["snapshot_token"]

    def test_full_shape(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        m1 = make_master(tenant, name="Анна", external_id=1)
        m2 = make_master(tenant, name="Мария", external_id=2)
        s1 = _make_service(tenant, name="Маникюр", external_id=100)
        s2 = _make_service(
            tenant, name="Гель-лак", external_id=101, duration_min=90, price_from=Decimal("2200")
        )
        MasterService.all_tenants.create(tenant=tenant, master=m1, service=s1)
        MasterService.all_tenants.create(tenant=tenant, master=m1, service=s2)
        MasterService.all_tenants.create(tenant=tenant, master=m2, service=s1)

        resp = client.get(_get_url(), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200, resp.content
        data = resp.json()

        # Services ordered by name; Decimal serialised as string.
        assert [s["name"] for s in data["services"]] == ["Гель-лак", "Маникюр"]
        for svc in data["services"]:
            assert isinstance(svc["id"], str)
            assert "duration_min" in svc and "price_from" in svc
            assert svc["is_active"] is True

        # Masters ordered by name.
        assert [m["name"] for m in data["masters"]] == ["Анна", "Мария"]
        for mst in data["masters"]:
            assert mst["is_active"] is True
            assert mst["invite_status"] == "accepted"

        # Mapping shape — 3 entries.
        assert len(data["mapping"]) == 3
        assert {"service_id", "master_id"} == set(data["mapping"][0].keys())

        # No orphans here.
        assert data["orphans"]["services_without_masters"] == []
        assert data["orphans"]["masters_without_services"] == []

    def test_orphan_detection(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        # m_orphan has zero services. s_orphan has zero masters.
        m_paired = make_master(tenant, name="Анна", external_id=1)
        m_orphan = make_master(tenant, name="Наталья", external_id=2)
        s_paired = _make_service(tenant, name="Маникюр", external_id=100)
        s_orphan = _make_service(tenant, name="Депиляция", external_id=101)
        MasterService.all_tenants.create(tenant=tenant, master=m_paired, service=s_paired)

        resp = client.get(_get_url(), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200
        data = resp.json()
        assert str(s_orphan.id) in data["orphans"]["services_without_masters"]
        assert str(s_paired.id) not in data["orphans"]["services_without_masters"]
        assert str(m_orphan.id) in data["orphans"]["masters_without_services"]
        assert str(m_paired.id) not in data["orphans"]["masters_without_services"]

    def test_inactive_master_excluded(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        make_master(tenant, name="Анна", external_id=1, is_active=True)
        make_master(tenant, name="Ирина", external_id=2, is_active=False)

        resp = client.get(_get_url(), HTTP_AUTHORIZATION=init_data_header("5001"))
        data = resp.json()
        assert [m["name"] for m in data["masters"]] == ["Анна"]

    def test_inactive_service_excluded(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        _make_service(tenant, name="Активная", external_id=100, is_active=True)
        _make_service(tenant, name="Деактивированная", external_id=101, is_active=False)

        resp = client.get(_get_url(), HTTP_AUTHORIZATION=init_data_header("5001"))
        data = resp.json()
        assert [s["name"] for s in data["services"]] == ["Активная"]

    def test_cross_tenant_isolation(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        other_tenant: Tenant,
    ) -> None:
        # Our tenant.
        m_ours = make_master(tenant, name="Анна", external_id=1)
        s_ours = _make_service(tenant, name="Маникюр", external_id=100)
        MasterService.all_tenants.create(tenant=tenant, master=m_ours, service=s_ours)

        # Other tenant — must NEVER leak.
        m_other = make_master(other_tenant, name="OtherMaster", external_id=2)
        s_other = _make_service(other_tenant, name="OtherService", external_id=200)
        MasterService.all_tenants.create(tenant=other_tenant, master=m_other, service=s_other)

        resp = client.get(_get_url(), HTTP_AUTHORIZATION=init_data_header("5001"))
        data = resp.json()
        master_ids = {m["id"] for m in data["masters"]}
        service_ids = {s["id"] for s in data["services"]}
        assert str(m_other.id) not in master_ids
        assert str(s_other.id) not in service_ids
        assert len(data["mapping"]) == 1


# =========================================================================
# POST /api/v1/admin/services-mapping/bulk
# =========================================================================


class TestServicesMappingBulkApply:
    def test_single_add_applied(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        m = make_master(tenant, name="Анна", external_id=1)
        s = _make_service(tenant, name="Маникюр", external_id=100)

        resp = _post_bulk(
            client,
            {"changes": [{"service_id": str(s.id), "master_id": str(m.id), "enabled": True}]},
            header=init_data_header("5001"),
        )
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["applied"] == 1
        assert body["conflicts"] == []
        assert isinstance(body["new_snapshot_token"], str)
        assert MasterService.all_tenants.filter(tenant=tenant, master=m, service=s).exists()

    def test_admin_can_apply(
        self,
        client: Client,
        admin_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        m = make_master(tenant, name="Анна", external_id=1)
        s = _make_service(tenant, name="Маникюр", external_id=100)
        resp = _post_bulk(
            client,
            {"changes": [{"service_id": str(s.id), "master_id": str(m.id), "enabled": True}]},
            header=init_data_header("5002"),
        )
        assert resp.status_code == 200, resp.content

    def test_multiple_changes_atomic(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        m1 = make_master(tenant, name="Анна", external_id=1)
        m2 = make_master(tenant, name="Мария", external_id=2)
        s1 = _make_service(tenant, name="Маникюр", external_id=100)
        s2 = _make_service(tenant, name="Гель-лак", external_id=101)
        # Seed one existing mapping that we'll remove.
        MasterService.all_tenants.create(tenant=tenant, master=m2, service=s2)

        resp = _post_bulk(
            client,
            {
                "changes": [
                    {"service_id": str(s1.id), "master_id": str(m1.id), "enabled": True},
                    {"service_id": str(s2.id), "master_id": str(m1.id), "enabled": True},
                    {"service_id": str(s2.id), "master_id": str(m2.id), "enabled": False},
                ]
            },
            header=init_data_header("5001"),
        )
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["applied"] == 3
        # m1 has both services now.
        assert MasterService.all_tenants.filter(tenant=tenant, master=m1).count() == 2
        # m2's pre-existing mapping was removed.
        assert MasterService.all_tenants.filter(tenant=tenant, master=m2).count() == 0

    def test_idempotent_already_enabled_not_double_counted(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        m = make_master(tenant, name="Анна", external_id=1)
        s = _make_service(tenant, name="Маникюр", external_id=100)
        MasterService.all_tenants.create(tenant=tenant, master=m, service=s)

        resp = _post_bulk(
            client,
            {"changes": [{"service_id": str(s.id), "master_id": str(m.id), "enabled": True}]},
            header=init_data_header("5001"),
        )
        assert resp.status_code == 200
        # No new rows; applied == 0 (caller's intent already matched DB).
        assert resp.json()["applied"] == 0
        assert MasterService.all_tenants.filter(tenant=tenant, master=m, service=s).count() == 1

    def test_empty_changes_list(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        resp = _post_bulk(
            client,
            {"changes": []},
            header=init_data_header("5001"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["applied"] == 0
        assert body["conflicts"] == []


class TestServicesMappingBulkAudit:
    def test_audit_row_per_master_not_per_change(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        m1 = make_master(tenant, name="Анна", external_id=1)
        m2 = make_master(tenant, name="Мария", external_id=2)
        s1 = _make_service(tenant, name="Маникюр", external_id=100)
        s2 = _make_service(tenant, name="Гель-лак", external_id=101)

        before = AuditLog.all_tenants.filter(action="master.services_changed").count()
        _post_bulk(
            client,
            {
                "changes": [
                    {"service_id": str(s1.id), "master_id": str(m1.id), "enabled": True},
                    {"service_id": str(s2.id), "master_id": str(m1.id), "enabled": True},
                    {"service_id": str(s1.id), "master_id": str(m2.id), "enabled": True},
                ]
            },
            header=init_data_header("5001"),
        )
        after = AuditLog.all_tenants.filter(action="master.services_changed").count()
        # 3 changes spread across 2 masters → exactly 2 audit rows.
        assert after - before == 2

    def test_audit_payload_carries_diff(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        m = make_master(tenant, name="Анна", external_id=1)
        s_add = _make_service(tenant, name="Маникюр", external_id=100)
        s_remove = _make_service(tenant, name="Гель-лак", external_id=101)
        MasterService.all_tenants.create(tenant=tenant, master=m, service=s_remove)

        _post_bulk(
            client,
            {
                "changes": [
                    {"service_id": str(s_add.id), "master_id": str(m.id), "enabled": True},
                    {"service_id": str(s_remove.id), "master_id": str(m.id), "enabled": False},
                ]
            },
            header=init_data_header("5001"),
        )
        row = AuditLog.all_tenants.filter(
            action="master.services_changed",
            target_id=m.id,
        ).first()
        assert row is not None
        assert row.payload["master_id"] == str(m.id)
        assert row.payload["actor_role"] == "owner"
        assert row.payload["tenant_id"] == str(tenant.id)
        assert str(s_add.id) in row.payload["services_added"]
        assert str(s_remove.id) in row.payload["services_removed"]
        assert "occurred_at" in row.payload

    def test_no_audit_when_no_real_diff(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """Calling enable=True on a pair that's already mapped is a no-op
        and MUST NOT produce an audit row (per spec ‘diff payload')."""

        m = make_master(tenant, name="Анна", external_id=1)
        s = _make_service(tenant, name="Маникюр", external_id=100)
        MasterService.all_tenants.create(tenant=tenant, master=m, service=s)

        before = AuditLog.all_tenants.filter(action="master.services_changed").count()
        _post_bulk(
            client,
            {"changes": [{"service_id": str(s.id), "master_id": str(m.id), "enabled": True}]},
            header=init_data_header("5001"),
        )
        after = AuditLog.all_tenants.filter(action="master.services_changed").count()
        assert after == before


class TestServicesMappingBulkConflict:
    def test_stale_snapshot_409(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        m = make_master(tenant, name="Анна", external_id=1)
        s = _make_service(tenant, name="Маникюр", external_id=100)

        # Get an initial snapshot, then mutate the matrix out-of-band to
        # invalidate the caller's token.
        first = client.get(_get_url(), HTTP_AUTHORIZATION=init_data_header("5001")).json()
        stale_token = first["snapshot_token"]
        MasterService.all_tenants.create(tenant=tenant, master=m, service=s)

        resp = _post_bulk(
            client,
            {
                "changes": [{"service_id": str(s.id), "master_id": str(m.id), "enabled": False}],
                "snapshot_token": stale_token,
            },
            header=init_data_header("5001"),
        )
        assert resp.status_code == 409, resp.content
        body = resp.json()
        assert body["applied"] == 0
        assert len(body["conflicts"]) == 1
        c = body["conflicts"][0]
        assert c["service_id"] == str(s.id)
        assert c["master_id"] == str(m.id)
        assert c["current_value"] is True
        assert c["your_value"] is False
        # Mapping was NOT mutated by the request.
        assert MasterService.all_tenants.filter(tenant=tenant, master=m, service=s).exists()

    def test_missing_token_is_ok(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """snapshot_token is optional for backward-compat; absence means
        no conflict check + best-effort apply."""

        m = make_master(tenant, name="Анна", external_id=1)
        s = _make_service(tenant, name="Маникюр", external_id=100)
        resp = _post_bulk(
            client,
            {"changes": [{"service_id": str(s.id), "master_id": str(m.id), "enabled": True}]},
            header=init_data_header("5001"),
        )
        assert resp.status_code == 200

    def test_fresh_token_applies(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        m = make_master(tenant, name="Анна", external_id=1)
        s = _make_service(tenant, name="Маникюр", external_id=100)
        snapshot = client.get(_get_url(), HTTP_AUTHORIZATION=init_data_header("5001")).json()[
            "snapshot_token"
        ]
        resp = _post_bulk(
            client,
            {
                "changes": [{"service_id": str(s.id), "master_id": str(m.id), "enabled": True}],
                "snapshot_token": snapshot,
            },
            header=init_data_header("5001"),
        )
        assert resp.status_code == 200
        # Returned token is fresh — applying again with the FIRST token
        # would 409, but the chained ``new_snapshot_token`` succeeds.
        next_token = resp.json()["new_snapshot_token"]
        resp2 = _post_bulk(
            client,
            {
                "changes": [{"service_id": str(s.id), "master_id": str(m.id), "enabled": False}],
                "snapshot_token": next_token,
            },
            header=init_data_header("5001"),
        )
        assert resp2.status_code == 200, resp2.content

    def test_stale_but_intent_matches_no_conflict(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """If the caller's stale-token request ALREADY matches the current
        DB state, that's idempotent — no conflict, applied=0."""

        m = make_master(tenant, name="Анна", external_id=1)
        s = _make_service(tenant, name="Маникюр", external_id=100)
        stale_token = client.get(_get_url(), HTTP_AUTHORIZATION=init_data_header("5001")).json()[
            "snapshot_token"
        ]
        # Someone else creates the mapping; caller asks for enabled=True
        # with a stale token — but that's already the current state.
        MasterService.all_tenants.create(tenant=tenant, master=m, service=s)

        resp = _post_bulk(
            client,
            {
                "changes": [{"service_id": str(s.id), "master_id": str(m.id), "enabled": True}],
                "snapshot_token": stale_token,
            },
            header=init_data_header("5001"),
        )
        # Caller's intent already matches DB → fall through to apply path,
        # which sees no diff and returns 200 applied=0.
        assert resp.status_code == 200
        assert resp.json()["applied"] == 0


class TestServicesMappingBulkValidation:
    def test_cross_tenant_service_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        other_tenant: Tenant,
    ) -> None:
        m = make_master(tenant, name="Анна", external_id=1)
        s_other = _make_service(other_tenant, name="OtherService", external_id=200)
        resp = _post_bulk(
            client,
            {
                "changes": [
                    {
                        "service_id": str(s_other.id),
                        "master_id": str(m.id),
                        "enabled": True,
                    }
                ]
            },
            header=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_cross_tenant_master_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        other_tenant: Tenant,
    ) -> None:
        m_other = make_master(other_tenant, name="OtherMaster", external_id=2)
        s = _make_service(tenant, name="Маникюр", external_id=100)
        resp = _post_bulk(
            client,
            {
                "changes": [
                    {
                        "service_id": str(s.id),
                        "master_id": str(m_other.id),
                        "enabled": True,
                    }
                ]
            },
            header=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_inactive_master_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        m = make_master(tenant, name="Ирина", external_id=1, is_active=False)
        s = _make_service(tenant, name="Маникюр", external_id=100)
        resp = _post_bulk(
            client,
            {"changes": [{"service_id": str(s.id), "master_id": str(m.id), "enabled": True}]},
            header=init_data_header("5001"),
        )
        assert resp.status_code == 400
        assert "inactive" in resp.json()["detail"].lower()

    def test_inactive_service_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        m = make_master(tenant, name="Анна", external_id=1)
        s = _make_service(tenant, name="Деактивированная", external_id=100, is_active=False)
        resp = _post_bulk(
            client,
            {"changes": [{"service_id": str(s.id), "master_id": str(m.id), "enabled": True}]},
            header=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_duplicate_pair_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        m = make_master(tenant, name="Анна", external_id=1)
        s = _make_service(tenant, name="Маникюр", external_id=100)
        resp = _post_bulk(
            client,
            {
                "changes": [
                    {"service_id": str(s.id), "master_id": str(m.id), "enabled": True},
                    {"service_id": str(s.id), "master_id": str(m.id), "enabled": False},
                ]
            },
            header=init_data_header("5001"),
        )
        assert resp.status_code == 400
        assert "duplicate" in resp.json()["detail"].lower()

    def test_bad_uuid_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        m = make_master(tenant, name="Анна", external_id=1)
        resp = _post_bulk(
            client,
            {"changes": [{"service_id": "not-a-uuid", "master_id": str(m.id), "enabled": True}]},
            header=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_enabled_not_bool_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        m = make_master(tenant, name="Анна", external_id=1)
        s = _make_service(tenant, name="Маникюр", external_id=100)
        resp = _post_bulk(
            client,
            {"changes": [{"service_id": str(s.id), "master_id": str(m.id), "enabled": "yes"}]},
            header=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_changes_not_list_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
    ) -> None:
        resp = _post_bulk(client, {"changes": "lol"}, header=init_data_header("5001"))
        assert resp.status_code == 400

    def test_missing_master_uuid_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        s = _make_service(tenant, name="Маникюр", external_id=100)
        # Random UUID — not in tenant. Expect 400.
        resp = _post_bulk(
            client,
            {
                "changes": [
                    {
                        "service_id": str(s.id),
                        "master_id": str(uuid.uuid4()),
                        "enabled": True,
                    }
                ]
            },
            header=init_data_header("5001"),
        )
        assert resp.status_code == 400
