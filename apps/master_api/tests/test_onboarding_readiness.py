"""DRF-1794 (M2) — GET /api/v1/master/onboarding/readiness: проекция по фактам.

Что здесь стережётся:

* пункты считаются из строк — услуга с ценой закрывает ``services``,
  рабочий день закрывает ``hours``, имя+фото закрывают ``profile``;
* незнание — не «нет»: Ayla не отвечает → ``hours`` ``unknown`` с именем
  причины, а не ``missing``;
* несуществующая возможность — ``unavailable``, и ``ready`` из-за неё
  ложно с названной причиной, а не истинно по умолчанию;
* состояние публикации — тот же ``sale_block``, что у витрины, и оно же
  приходит в ``/me``;
* хранилища нет: сохранил часы — пункт закрылся, никакого «completed».
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser, SoloIdentityLink
from apps.integrations.ayla.salon_client import SalonUnavailable
from apps.master_api.services.onboarding_readiness import REQUIRED_ITEMS, build_readiness
from apps.master_api.tests.conftest import init_data_header
from apps.scheduling.models import WorkingHours
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

URL_NAME = "master_api:onboarding_readiness"


def _get(client: Client):
    return client.get(reverse(URL_NAME), HTTP_AUTHORIZATION=init_data_header("12345"))


def _items(body: dict) -> dict[str, dict]:
    return {item["key"]: item for item in body["items"]}


def _priced_service(
    tenant: Tenant, master: CatalogMaster, *, price: int | None, duration: int
) -> None:
    svc = CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=4242,
        external_updated_at=datetime.now(tz=timezone.utc),
        slug="readiness-svc",
        name="Маникюр (readiness)",
        duration_min=duration,
        price_from=price,
        is_active=True,
    )
    MasterService.all_tenants.create(tenant=tenant, master=master, service=svc)


class TestAuthBoundary:
    def test_without_init_data_rejected(self, client: Client, db) -> None:
        assert client.get(reverse(URL_NAME)).status_code == 400

    def test_non_master_rejected(self, client: Client, bot_user: BotUser) -> None:
        resp = _get(client)
        assert resp.status_code == 401
        assert resp.json()["error"] == "not_a_master"


class TestItemsAreComputedFromRows:
    def test_fresh_master_has_every_item_open_and_names_why(
        self, client: Client, accepted_master: CatalogMaster
    ) -> None:
        resp = _get(client)
        assert resp.status_code == 200, resp.content
        body = resp.json()
        items = _items(body)

        assert body["ready"] is False
        assert items["services"]["state"] == "missing"
        assert items["services"]["detail"] == {"selected": 0, "configured": 0}
        assert items["hours"]["state"] == "missing"
        assert items["profile"]["state"] == "missing"
        assert items["profile"]["detail"] == {"name": True, "photo": False, "bio": False}
        # Несуществующая возможность — не «не сделано».
        assert items["location"]["state"] == "unavailable"
        assert items["location"]["reason"] == "capability_not_built"
        assert set(body["blocking"]) == {
            "services:missing",
            "location:unavailable",
            "hours:missing",
            "profile:missing",
        }
        # Каждый пункт ведёт куда-то — экран 01 без тупиков.
        for item in body["items"]:
            assert item["deep_link"].startswith("/solo/")
        # Ни процентов, ни «N из M» — числа только из строк.
        assert "percent" not in body and "step" not in body

    def test_a_priced_service_closes_services_and_an_unpriced_one_does_not(
        self, client: Client, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        _priced_service(tenant, accepted_master, price=None, duration=60)
        before = _items(_get(client).json())["services"]
        assert before["state"] == "missing"
        assert before["detail"] == {"selected": 1, "configured": 0}

        CatalogService.all_tenants.filter(slug="readiness-svc").update(price_from=1500)
        after = _items(_get(client).json())["services"]
        assert after["state"] == "done"
        assert after["detail"] == {"selected": 1, "configured": 1}

    def test_a_working_day_closes_hours_and_nothing_is_stored(
        self, client: Client, tenant: Tenant, accepted_master: CatalogMaster, settings
    ) -> None:
        settings.BOOKING_VIA_AYLA_REST = False
        assert _items(_get(client).json())["hours"]["state"] == "missing"

        WorkingHours.all_tenants.create(
            tenant=tenant,
            master=accepted_master,
            day_of_week=1,
            is_working=True,
            start_time="10:00",
            end_time="19:00",
        )
        hours = _items(_get(client).json())["hours"]
        assert hours["state"] == "done"
        assert hours["detail"] == {"working_days": [1]}

        # Проекция, не хранилище: снял день (строка выходного — без времён,
        # как велит констрейнт ``working_hours_times_match_is_working``) —
        # пункт снова открыт.
        WorkingHours.all_tenants.filter(master=accepted_master).update(
            is_working=False,
            start_time=None,
            end_time=None,
        )
        assert _items(_get(client).json())["hours"]["state"] == "missing"

    def test_name_and_photo_close_profile(
        self, client: Client, accepted_master: CatalogMaster
    ) -> None:
        accepted_master.photo_url = "/media/master_photos/x.jpg"
        accepted_master.save(update_fields=["photo_url"])
        profile = _items(_get(client).json())["profile"]
        assert profile["state"] == "done"
        assert profile["detail"]["photo"] is True


class TestNotKnowingIsNotNo:
    def test_ayla_unavailable_makes_hours_unknown_not_missing(
        self, client: Client, accepted_master: CatalogMaster, settings
    ) -> None:
        settings.BOOKING_VIA_AYLA_REST = True
        with patch(
            "apps.master_api.services.onboarding_readiness.load_day_frame",
            side_effect=SalonUnavailable("down"),
        ):
            hours = _items(_get(client).json())["hours"]
        assert hours["state"] == "unknown"
        assert hours["reason"] == "SalonUnavailable"
        # Положительная стража той же подмены: с ответом канона пункт считается.
        with patch(
            "apps.master_api.services.onboarding_readiness.load_day_frame",
            return_value=({}, {}, {}),
        ):
            assert _items(_get(client).json())["hours"]["state"] == "missing"


class TestPublicationStateIsTheOneSaleGate:
    def test_readiness_and_me_carry_the_same_sale_block(
        self, client: Client, accepted_master: CatalogMaster
    ) -> None:
        # Фикстура — приглашённый мастер без ключа Ayla: гейт говорит ayla_unlinked.
        readiness = _get(client).json()
        me = client.get(
            reverse("master_api:me"), HTTP_AUTHORIZATION=init_data_header("12345")
        ).json()
        assert readiness["sale_block"] == "ayla_unlinked"
        assert readiness["setup_state"] == "SETUP_PENDING"
        assert me["sale_block"] == "ayla_unlinked"
        assert me["setup_state"] == "SETUP_PENDING"
        assert me["identity"] == readiness["identity"] == {"state": "unlinked", "link_status": None}

    def test_identity_facts_follow_the_link_row(
        self, client: Client, accepted_master: CatalogMaster, bot_user: BotUser
    ) -> None:
        link = SoloIdentityLink.objects.create(
            master=accepted_master,
            status=SoloIdentityLink.Status.PENDING,
            solo_registration_id=accepted_master.tenant_id,
            channel=bot_user.channel,
            channel_user_id=bot_user.channel_user_id,
            tenant_id_snapshot=accepted_master.tenant_id,
        )
        assert _get(client).json()["identity"]["state"] == "pending"

        link.status = SoloIdentityLink.Status.REJECTED
        link.save(update_fields=["status"])
        assert _get(client).json()["identity"]["state"] == "rejected"

    def test_ready_is_never_true_while_a_capability_is_unavailable(
        self, tenant: Tenant, accepted_master: CatalogMaster, settings
    ) -> None:
        """Всё, что мастер может сделать сегодня, сделано — и всё равно не READY:
        причина названа, не спрятана за истиной по умолчанию."""

        settings.BOOKING_VIA_AYLA_REST = False
        _priced_service(tenant, accepted_master, price=1500, duration=60)
        WorkingHours.all_tenants.create(
            tenant=tenant,
            master=accepted_master,
            day_of_week=2,
            is_working=True,
            start_time="10:00",
            end_time="19:00",
        )
        accepted_master.photo_url = "/media/master_photos/y.jpg"
        accepted_master.save(update_fields=["photo_url"])

        readiness = build_readiness(accepted_master)
        states = {item.key: item.state for item in readiness.items}
        assert states == {
            "services": "done",
            "hours": "done",
            "profile": "done",
            "location": "unavailable",
        }
        assert readiness.ready is False
        assert readiness.blocking == ["location:unavailable"]
        assert "location" in REQUIRED_ITEMS
