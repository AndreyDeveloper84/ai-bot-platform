"""Integration tests for PATCH /api/v1/master/onboarding/profile + GET /me."""

from __future__ import annotations

import json

from django.test import Client
from django.urls import reverse

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
    # DRF-1813 (M21): запись «о себе» и фото ушла в каталог — сохранение,
    # лимит, аудит и загрузка фото теперь в test_profile_proxy_1813.py.
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
        # DRF-1805: права — из проводки. Ручки правки услуг (M10) ещё нет —
        # право ложно; заявка о недоступности и ответ клиенту есть.
        assert data["permissions"]["can_edit_schedule"] is True
        assert data["permissions"]["can_edit_services"] is False
        assert data["permissions"]["can_message_customers"] is True
        # Services list populated from the M2M fixture.
        assert len(data["master"]["services"]) >= 1

    def test_unauthenticated(self, client: Client, db) -> None:
        resp = client.get(reverse("master_api:me"))
        assert resp.status_code == 400  # malformed init-data
