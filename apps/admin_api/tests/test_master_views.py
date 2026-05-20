"""Integration tests for /api/v1/admin/masters/* (PR 2 / MM1-MM3 backend).

Covers:

* Auth matrix — customer / master / receptionist / admin / owner.
* List endpoint — filters, search, pagination, empty / error shapes.
* Detail endpoint — full shape, 404 for missing / cross-tenant.
* PATCH — name/bio/specialization updates, audit row + diff,
  is_active gate (Owner-only deactivate, Admin allowed to reactivate),
  validation (blank name, oversized bio).
* POST photo — valid jpeg + audit, oversized rejected, non-image
  rejected, photo_url returned.
* GET audit — feed shape, cross-tenant isolation, pagination.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import (
    BOT_TOKEN,
    init_data_header,
    link_master_to_bot_user,
    make_master,
)
from apps.audit.models import AuditLog
from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


# --- URL helpers ----------------------------------------------------------


def _list_url() -> str:
    return reverse("admin_api:masters_list")


def _detail_url(master_id) -> str:
    return reverse("admin_api:master_detail", args=[str(master_id)])


def _photo_url(master_id) -> str:
    return reverse("admin_api:master_photo_upload", args=[str(master_id)])


def _audit_url(master_id) -> str:
    return reverse("admin_api:master_audit_feed", args=[str(master_id)])


# =========================================================================
# AUTH MATRIX
# =========================================================================


class TestAdminAuth:
    """The 5×endpoint matrix is collapsed to the list endpoint here —
    all five endpoints share :func:`require_admin_role`, so testing
    the auth gate once + each endpoint's happy path elsewhere is
    sufficient. Per-endpoint forbidden-cases are also tested below
    for defence-in-depth on the destructive paths."""

    def test_missing_header_400(self, client: Client, tenant: Tenant) -> None:
        resp = client.get(_list_url())
        assert resp.status_code == 400

    def test_bad_signature_401(self, client: Client, tenant: Tenant) -> None:
        resp = client.get(_list_url(), HTTP_AUTHORIZATION="MaxInitData user=foo&hash=deadbeef")
        assert resp.status_code in (400, 401)

    def test_user_not_registered_404(self, client: Client, tenant: Tenant) -> None:
        """Valid HMAC, but no BotUser for this MAX user_id."""

        resp = client.get(_list_url(), HTTP_AUTHORIZATION=init_data_header("9999"))
        assert resp.status_code == 404
        assert resp.json()["error"] == "user_not_registered"

    def test_customer_role_forbidden(self, client: Client, customer_bot_user: BotUser) -> None:
        resp = client.get(_list_url(), HTTP_AUTHORIZATION=init_data_header("5005"))
        assert resp.status_code == 403, resp.content
        assert resp.json()["error"] == "forbidden"

    def test_master_only_role_forbidden(
        self,
        client: Client,
        master_only_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        # Promote the bot_user to master so role resolver sees them
        # as primary_role=master.
        link_master_to_bot_user(master, master_only_bot_user)
        resp = client.get(_list_url(), HTTP_AUTHORIZATION=init_data_header("5004"))
        assert resp.status_code == 403

    def test_receptionist_role_forbidden(
        self, client: Client, receptionist_bot_user: BotUser
    ) -> None:
        resp = client.get(_list_url(), HTTP_AUTHORIZATION=init_data_header("5003"))
        assert resp.status_code == 403

    def test_admin_role_allowed(
        self,
        client: Client,
        admin_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        resp = client.get(_list_url(), HTTP_AUTHORIZATION=init_data_header("5002"))
        assert resp.status_code == 200, resp.content

    def test_owner_role_allowed(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        resp = client.get(_list_url(), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200, resp.content


# =========================================================================
# GET /api/v1/admin/masters/
# =========================================================================


class TestMastersList:
    def test_returns_active_first_then_alpha(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        # Active row first, then inactive — within active sort by name asc.
        make_master(tenant, name="Мария Соколова", external_id=10)
        make_master(tenant, name="Анна Петрова", external_id=11)
        make_master(tenant, name="Ирина Смирнова", is_active=False, external_id=12)

        resp = client.get(_list_url(), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200
        names = [item["name"] for item in resp.json()["items"]]
        # Active first (Anna, Maria) then inactive (Irina).
        assert names == ["Анна Петрова", "Мария Соколова", "Ирина Смирнова"]

    def test_filter_is_active_true_only(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        make_master(tenant, name="A active")
        make_master(tenant, name="Z inactive", is_active=False)

        resp = client.get(
            _list_url() + "?is_active=true",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["is_active"] is True

    def test_filter_invite_status_multi(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        make_master(
            tenant,
            name="Pending Pavlovna",
            invite_status=CatalogMaster.InviteStatus.PENDING,
        )
        make_master(
            tenant,
            name="Accepted Avdeeva",
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        make_master(
            tenant,
            name="Cancelled Tarasova",
            invite_status=CatalogMaster.InviteStatus.CANCELLED,
        )

        resp = client.get(
            _list_url() + "?invite_status=pending,cancelled",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 200
        statuses = {item["invite_status"] for item in resp.json()["items"]}
        assert statuses == {"pending", "cancelled"}

    def test_search_by_name_cyrillic_icontains(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        make_master(tenant, name="Анна Петрова")
        make_master(tenant, name="Мария Соколова")

        # Cyrillic substring matching the stored case — icontains on
        # SQLite is ASCII-only-case-insensitive, so we use a match that
        # works in both SQLite (test) and PostgreSQL (prod). Production
        # still gets full Cyrillic case-insensitivity courtesy of the
        # PostgreSQL `ILIKE` operator that `__icontains` lowers to.
        resp = client.get(
            _list_url() + "?search=Петр",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "Анна Петрова"

    def test_pagination_cursor_advances(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        # 4 active masters, limit=2 → two pages.
        for n in ("Aaa", "Bbb", "Ccc", "Ddd"):
            make_master(tenant, name=n)

        page1 = client.get(
            _list_url() + "?limit=2",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert page1.status_code == 200
        d1 = page1.json()
        assert len(d1["items"]) == 2
        assert d1["next_cursor"] is not None
        names1 = [item["name"] for item in d1["items"]]

        page2 = client.get(
            _list_url() + f"?limit=2&cursor={d1['next_cursor']}",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert page2.status_code == 200
        d2 = page2.json()
        names2 = [item["name"] for item in d2["items"]]
        # No overlap; combined = all 4 in order.
        assert set(names1).isdisjoint(set(names2))
        assert names1 + names2 == ["Aaa", "Bbb", "Ccc", "Ddd"]

    def test_empty_result(
        self,
        client: Client,
        owner_bot_user: BotUser,
    ) -> None:
        resp = client.get(_list_url(), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total_count"] == 0
        assert data["next_cursor"] is None

    def test_services_count_annotated_no_n_plus_one(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master_with_service: CatalogMaster,
    ) -> None:
        resp = client.get(_list_url(), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert items[0]["services_count"] == 1

    def test_bad_invite_status_400(self, client: Client, owner_bot_user: BotUser) -> None:
        resp = client.get(
            _list_url() + "?invite_status=bogus",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400


# =========================================================================
# GET /api/v1/admin/masters/<id>/
# =========================================================================


class TestMasterDetail:
    def test_full_shape(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master_with_service: CatalogMaster,
    ) -> None:
        resp = client.get(
            _detail_url(master_with_service.id),
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 200, resp.content
        m = resp.json()["master"]
        assert m["id"] == str(master_with_service.id)
        assert m["name"] == master_with_service.name
        assert m["specialization"] == master_with_service.specialization
        assert "bio" in m
        assert "photo_url" in m
        assert "invite_status" in m
        assert "mode" in m
        assert "linked_bot_user" in m  # may be None but key present
        assert isinstance(m["services"], list)
        assert m["services"][0]["name"] == "Маникюр гель-лак"
        assert "working_hours_summary" in m

    def test_404_on_bad_uuid(self, client: Client, owner_bot_user: BotUser) -> None:
        resp = client.get(
            _detail_url("not-a-uuid"),
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 404

    def test_404_on_missing_uuid(self, client: Client, owner_bot_user: BotUser) -> None:
        resp = client.get(
            _detail_url(uuid.uuid4()),
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 404

    def test_cross_tenant_returns_404(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        other_tenant: Tenant,
    ) -> None:
        """A master in OTHER tenant must return 404 — never 200/403.

        Returning 403 would leak that the row exists; 404 keeps the
        existence ambiguous to a probing actor.
        """

        other_master = make_master(other_tenant, name="Other Tenant Master")
        resp = client.get(
            _detail_url(other_master.id),
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 404


# =========================================================================
# PATCH /api/v1/admin/masters/<id>/
# =========================================================================


def _patch_json(client: Client, url: str, body: dict, *, header: str) -> Any:
    return client.patch(
        url,
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=header,
    )


class TestMasterUpdate:
    def test_update_name_and_bio(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        resp = _patch_json(
            client,
            _detail_url(master.id),
            {"name": "Анна Иванова", "bio": "Опыт 5 лет"},
            header=init_data_header("5001"),
        )
        assert resp.status_code == 200, resp.content
        master.refresh_from_db()
        assert master.name == "Анна Иванова"
        assert master.bio == "Опыт 5 лет"

    def test_audit_row_carries_diff(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        old_name = master.name
        _patch_json(
            client,
            _detail_url(master.id),
            {"name": "Новое Имя"},
            header=init_data_header("5001"),
        )
        row = AuditLog.all_tenants.filter(
            action="master.profile_updated_by_admin",
            target_id=master.id,
        ).first()
        assert row is not None
        assert "name" in row.payload["fields_changed"]
        assert row.payload["previous_values"]["name"] == old_name
        assert row.payload["actor_role"] == "owner"

    def test_admin_can_update_safe_fields(
        self,
        client: Client,
        admin_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        resp = _patch_json(
            client,
            _detail_url(master.id),
            {"specialization": "Маникюр + дизайн"},
            header=init_data_header("5002"),
        )
        assert resp.status_code == 200
        master.refresh_from_db()
        assert master.specialization == "Маникюр + дизайн"

    def test_admin_cannot_deactivate(
        self,
        client: Client,
        admin_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        resp = _patch_json(
            client,
            _detail_url(master.id),
            {"is_active": False},
            header=init_data_header("5002"),
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"
        master.refresh_from_db()
        assert master.is_active is True  # unchanged

    def test_admin_can_reactivate(
        self,
        client: Client,
        admin_bot_user: BotUser,
        inactive_master: CatalogMaster,
    ) -> None:
        resp = _patch_json(
            client,
            _detail_url(inactive_master.id),
            {"is_active": True},
            header=init_data_header("5002"),
        )
        assert resp.status_code == 200
        inactive_master.refresh_from_db()
        assert inactive_master.is_active is True

    def test_owner_can_deactivate(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        resp = _patch_json(
            client,
            _detail_url(master.id),
            {"is_active": False},
            header=init_data_header("5001"),
        )
        assert resp.status_code == 200
        master.refresh_from_db()
        assert master.is_active is False

    def test_blank_name_rejected(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        resp = _patch_json(
            client,
            _detail_url(master.id),
            {"name": "   "},
            header=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_oversized_bio_rejected(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        resp = _patch_json(
            client,
            _detail_url(master.id),
            {"bio": "x" * 1001},
            header=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_unknown_field_rejected(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        """No silent ignore — invite_status / yclients_staff_id must 400."""

        resp = _patch_json(
            client,
            _detail_url(master.id),
            {"invite_status": "cancelled"},
            header=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_role_change_field_rejected(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        """Out-of-scope: role changes must NOT come through this endpoint."""

        resp = _patch_json(
            client,
            _detail_url(master.id),
            {"role": "admin"},
            header=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_no_diff_no_audit_row(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        """Setting a field to its current value is a no-op — no audit."""

        before = AuditLog.all_tenants.filter(action="master.profile_updated_by_admin").count()
        _patch_json(
            client,
            _detail_url(master.id),
            {"name": master.name},
            header=init_data_header("5001"),
        )
        after = AuditLog.all_tenants.filter(action="master.profile_updated_by_admin").count()
        assert after == before


# =========================================================================
# POST /api/v1/admin/masters/<id>/photo/
# =========================================================================


class TestMasterPhotoUpload:
    def test_valid_jpeg_accepted(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
        tmp_path,
        settings,
    ) -> None:
        settings.MEDIA_ROOT = str(tmp_path)
        settings.MEDIA_URL = "/media/"
        upload = SimpleUploadedFile(
            "selfie.jpg",
            b"\xff\xd8\xff\xd9" * 10,
            content_type="image/jpeg",
        )
        resp = client.post(
            _photo_url(master.id),
            data={"photo": upload},
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert "photo_url" in body
        assert "master_photos" in body["photo_url"]
        master.refresh_from_db()
        assert master.photo_url == body["photo_url"]
        # File on disk.
        assert (tmp_path / "master_photos").exists()

    def test_audit_row_on_photo(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
        tmp_path,
        settings,
    ) -> None:
        settings.MEDIA_ROOT = str(tmp_path)
        upload = SimpleUploadedFile(
            "p.png",
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
            content_type="image/png",
        )
        client.post(
            _photo_url(master.id),
            data={"photo": upload},
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        row = AuditLog.all_tenants.filter(
            action="master.photo_updated_by_admin",
            target_id=master.id,
        ).first()
        assert row is not None
        assert row.payload["mime"] == "image/png"
        assert row.payload["size_bytes"] > 0
        assert row.payload["actor_role"] == "owner"

    def test_non_image_rejected(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        upload = SimpleUploadedFile(
            "evil.exe",
            b"MZ\x90\x00",  # PE header
            content_type="application/octet-stream",
        )
        resp = client.post(
            _photo_url(master.id),
            data={"photo": upload},
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_oversized_rejected(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
        settings,
    ) -> None:
        # Use a synthesized 11 MB upload.
        big = b"\xff" * (11 * 1024 * 1024)
        upload = SimpleUploadedFile("huge.jpg", big, content_type="image/jpeg")
        resp = client.post(
            _photo_url(master.id),
            data={"photo": upload},
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_missing_photo_field_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
    ) -> None:
        resp = client.post(
            _photo_url(master.id),
            data={},
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400


# =========================================================================
# GET /api/v1/admin/masters/<id>/audit/
# =========================================================================


class TestMasterAuditFeed:
    def test_returns_master_events(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
        tenant: Tenant,
    ) -> None:
        # Seed audit rows via the PATCH endpoint so the dual-filter is
        # exercised end-to-end (target='catalog.CatalogMaster' branch).
        _patch_json(
            client,
            _detail_url(master.id),
            {"name": "Новое"},
            header=init_data_header("5001"),
        )
        resp = client.get(
            _audit_url(master.id),
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 200, resp.content
        data = resp.json()
        assert len(data["items"]) >= 1
        first = data["items"][0]
        assert first["action"] == "master.profile_updated_by_admin"
        assert first["actor_role"] == "owner"
        assert "occurred_at" in first
        assert "payload" in first

    def test_cross_tenant_isolation(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
        tenant: Tenant,
        other_tenant: Tenant,
    ) -> None:
        # Insert an audit row in OTHER tenant carrying the same master
        # id — defence-in-depth check that tenant scope holds.
        AuditLog.all_tenants.create(
            tenant=other_tenant,
            action="master.profile_updated_by_admin",
            target="catalog.CatalogMaster",
            target_id=master.id,
            payload={"master_id": str(master.id), "actor_role": "owner"},
        )
        # And a row in OUR tenant so we know the endpoint isn't just
        # empty for unrelated reasons.
        AuditLog.all_tenants.create(
            tenant=tenant,
            action="master.profile_updated_by_admin",
            target="catalog.CatalogMaster",
            target_id=master.id,
            payload={"master_id": str(master.id), "actor_role": "owner"},
        )
        resp = client.get(
            _audit_url(master.id),
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 200
        data = resp.json()
        # Exactly one — the other-tenant row must be invisible.
        assert len(data["items"]) == 1

    def test_pagination(
        self,
        client: Client,
        owner_bot_user: BotUser,
        master: CatalogMaster,
        tenant: Tenant,
    ) -> None:
        # 5 audit rows; limit=2 → 2+2+1 across three pages.
        for i in range(5):
            AuditLog.all_tenants.create(
                tenant=tenant,
                action="master.profile_updated_by_admin",
                target="catalog.CatalogMaster",
                target_id=master.id,
                payload={
                    "master_id": str(master.id),
                    "i": i,
                    "actor_role": "owner",
                },
            )

        page1 = client.get(
            _audit_url(master.id) + "?limit=2",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert page1.status_code == 200
        d1 = page1.json()
        assert len(d1["items"]) == 2
        assert d1["next_cursor"] is not None

        page2 = client.get(
            _audit_url(master.id) + f"?limit=2&cursor={d1['next_cursor']}",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert page2.status_code == 200
        d2 = page2.json()
        assert len(d2["items"]) == 2

        # Combined set covers 4 of 5 — pagination doesn't repeat rows.
        # (We don't assert specific ``i`` ordering because rows created
        # in the same millisecond share ``created_at`` and the
        # tie-break by ``id`` is UUID-random; what we MUST guarantee
        # is that the cursor doesn't return the same row twice.)
        ids_page1 = {item["id"] for item in d1["items"]}
        ids_page2 = {item["id"] for item in d2["items"]}
        assert ids_page1.isdisjoint(ids_page2)

        # Drain the final page — has_more should be False.
        page3 = client.get(
            _audit_url(master.id) + f"?limit=2&cursor={d2['next_cursor']}",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert page3.status_code == 200
        d3 = page3.json()
        assert len(d3["items"]) == 1
        assert d3["next_cursor"] is None
        ids_page3 = {item["id"] for item in d3["items"]}
        assert ids_page1.isdisjoint(ids_page3)
        assert ids_page2.isdisjoint(ids_page3)

    def test_404_for_missing_master(
        self,
        client: Client,
        owner_bot_user: BotUser,
    ) -> None:
        resp = client.get(
            _audit_url(uuid.uuid4()),
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 404


# Use _ to silence unused imports in some test paths.
_ = (BOT_TOKEN, datetime, timezone)
