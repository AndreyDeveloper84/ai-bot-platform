"""Integration tests for PATCH /api/v1/master/onboarding/profile + GET /me."""

from __future__ import annotations

import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.catalog.models import CatalogMaster, MasterService
from apps.identity.models import BotUser
from apps.master_api.tests.conftest import init_data_header


def _patch_json(client: Client, body: dict, *, header: str | None):
    if header:
        return client.patch(
            reverse("master_api:onboarding_profile"),
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )
    return client.patch(
        reverse("master_api:onboarding_profile"),
        data=json.dumps(body),
        content_type="application/json",
    )


class TestOnboardingProfile:
    def test_bio_update_idempotent(
        self,
        client: Client,
        accepted_master: CatalogMaster,
    ) -> None:
        resp = _patch_json(
            client,
            {"bio": "Опыт 5 лет, гель-лак и наращивание"},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content
        accepted_master.refresh_from_db()
        assert "5 лет" in accepted_master.bio

        # Second call same payload → same result, no error.
        resp2 = _patch_json(
            client,
            {"bio": "Опыт 5 лет, гель-лак и наращивание"},
            header=init_data_header("12345"),
        )
        assert resp2.status_code == 200

    def test_bio_too_long_rejected(
        self,
        client: Client,
        accepted_master: CatalogMaster,
    ) -> None:
        resp = _patch_json(client, {"bio": "x" * 281}, header=init_data_header("12345"))
        assert resp.status_code == 400

    def test_unauthenticated_rejected(self, client: Client, db) -> None:
        resp = client.patch(
            reverse("master_api:onboarding_profile"),
            data=json.dumps({"bio": "hi"}),
            content_type="application/json",
        )
        assert resp.status_code == 400  # missing init-data

    def test_non_master_rejected(
        self,
        client: Client,
        bot_user: BotUser,
    ) -> None:
        """BotUser exists but no linked master → 401 not_a_master."""

        resp = _patch_json(client, {"bio": "hi"}, header=init_data_header("12345"))
        assert resp.status_code == 401
        assert resp.json()["error"] == "not_a_master"

    def test_audit_row_emitted(
        self,
        client: Client,
        accepted_master: CatalogMaster,
    ) -> None:
        _patch_json(client, {"bio": "новый текст"}, header=init_data_header("12345"))
        row = AuditLog.all_tenants.filter(
            action="master.profile_initialized",
            target_id=accepted_master.id,
        ).first()
        assert row is not None
        assert "bio" in row.payload["fields_populated"]

    def test_photo_upload(
        self,
        client: Client,
        accepted_master: CatalogMaster,
        tmp_path,
        settings,
    ) -> None:
        """Photo upload accepted; photo_url populated (no resize pipeline yet)."""

        from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart

        settings.MEDIA_ROOT = str(tmp_path)
        settings.MEDIA_URL = "/media/"
        upload = SimpleUploadedFile(
            "selfie.jpg",
            b"\xff\xd8\xff\xd9",  # minimal JPEG marker bytes
            content_type="image/jpeg",
        )
        # Django Client.patch URL-encodes by default; encode_multipart +
        # generic() is the canonical way to PATCH a multipart payload.
        data = encode_multipart(BOUNDARY, {"photo": upload})
        resp = client.generic(
            "PATCH",
            reverse("master_api:onboarding_profile"),
            data=data,
            content_type=MULTIPART_CONTENT,
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content
        accepted_master.refresh_from_db()
        assert accepted_master.photo_url
        assert "master_photos" in accepted_master.photo_url


class TestMe:
    def test_returns_master_shape(
        self,
        client: Client,
        accepted_master: CatalogMaster,
        master_service: MasterService,
    ) -> None:
        resp = client.get(
            reverse("master_api:me"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content
        data = resp.json()
        assert data["master"]["id"] == str(accepted_master.id)
        assert data["salon"]["tenant_id"] == str(accepted_master.tenant_id)
        # Spec: hardcode all three True in PR 1.
        assert data["permissions"]["can_edit_schedule"] is True
        assert data["permissions"]["can_edit_services"] is True
        assert data["permissions"]["can_message_customers"] is True
        # Services list populated from the M2M fixture.
        assert len(data["master"]["services"]) >= 1

    def test_unauthenticated(self, client: Client, db) -> None:
        resp = client.get(reverse("master_api:me"))
        assert resp.status_code == 400  # malformed init-data
