"""Экран 07 — контракт бота (DRF-1814, часть A из трёх): карточка, бейдж, чипы, портфолио.

Что доказывается и чем:

* **владелец полей — каталог**: имя/«о себе»/фото карточки берутся из ответа
  ``GET …/profile/``, а не из зеркала — узел кладёт в зеркало ДРУГИЕ значения
  и ждёт каталожные; ``limits`` едут как есть (один источник, DRF-1960);
* **бейдж «Принимает сегодня» — только из реального слота**: ``true`` ровно
  при непустом ответе слотов на сегодня по первой продаваемой услуге с
  мостом; ``no_service`` — слоты не спрашиваются (счётчиком); ``no_slots``;
  ``catalog_unavailable`` — карточка при этом отдаётся (200), а не 503;
* **чипы — категории выбранных шаблонов** из живого состояния выбора
  (``category_name``, DRF-1912): уникальные, в порядке ответа, только активные;
  выбор недоступен — пусто с причиной;
* **портфолио — прокси, субъект по построению**: у маршрутов нет
  ``specialist_id``, клиент получает id профиля мастера из initData; 11-е
  фото — отказ каталога по имени, бот своего счётчика не ведёт.

Стенд — тот же, что у M21 (``test_profile_proxy_1813``): ``MagicMock`` на
месте ``get_ayla_booking_client``, initData подписана в ``conftest``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import (
    AylaSlot,
    BookingBadRequestError,
    BookingUnavailableError,
)
from apps.master_api import urls as master_urls
from apps.master_api import views_profile_card as card
from apps.master_api.tests.conftest import init_data_header
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

AVATAR = "https://catalog.test/media/avatars/anna.jpg"
LIMITS = {
    "bio": 500,
    "display_name_min": 2,
    "avatar_bytes": 5 * 1024 * 1024,
    "portfolio_bytes": 10 * 1024 * 1024,
    "portfolio_count": 10,
}


def _state(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "specialist_id": "spec",
        "display_name": "Анна из каталога",
        "bio": "О себе — из каталога",
        "avatar_url": AVATAR,
        "portfolio": {"count": 2, "limit": 10},
        "limits": dict(LIMITS),
    }
    base.update(over)
    return base


def _selection(*rows: tuple[str | None, bool]) -> dict[str, Any]:
    return {
        "specialist_id": "spec",
        "selected": sum(1 for _, active in rows if active),
        "configured": 0,
        "services": [
            {"salon_service_id": str(uuid.uuid4()), "category_name": name, "is_active": active}
            for name, active in rows
        ],
    }


@pytest.fixture
def ayla(monkeypatch, accepted_master: CatalogMaster) -> MagicMock:
    client = MagicMock()
    client.get_specialist_profile.return_value = _state()
    client.get_available_times.return_value = [
        AylaSlot(time="12:00", datetime=None, duration_s=None)
    ]
    client.get_service_selection.return_value = _selection(("Маникюр", True))
    client.list_specialist_portfolio.return_value = {"items": [], "count": 0, "limit": 10}
    client.upload_specialist_portfolio_item.return_value = {
        "id": "item-1",
        "image_url": "https://catalog.test/media/portfolio/1.jpg",
        "sort_order": 0,
        "created_at": "2026-09-17T12:00:00+00:00",
    }
    client.delete_specialist_portfolio_item.return_value = {"count": 0, "limit": 10}
    monkeypatch.setattr(card, "get_ayla_booking_client", lambda: client)
    return client


@pytest.fixture
def bridged_service(tenant: Tenant, accepted_master: CatalogMaster) -> CatalogService:
    service = CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=77,
        external_updated_at=datetime.now(tz=timezone.utc),
        slug="manicure-classic",
        name="Маникюр классический",
        duration_min=60,
        is_active=True,
        ayla_service_id=uuid.uuid4(),
    )
    MasterService.all_tenants.create(tenant=tenant, master=accepted_master, service=service)
    return service


def _auth() -> dict:
    return {"HTTP_AUTHORIZATION": init_data_header("12345")}


def _get_card(client: Client) -> Any:
    return client.get(reverse("master_api:profile_card"), **_auth())


# ─── карточка: владелец полей — каталог ─────────────────────────────────────


class TestCardIsTheCatalogsAnswer:
    def test_name_bio_photo_come_from_the_catalog_not_the_mirror(
        self,
        client: Client,
        accepted_master: CatalogMaster,
        bot_user: BotUser,
        ayla,
        bridged_service,
    ):
        CatalogMaster.all_tenants.filter(pk=accepted_master.pk).update(
            bio="зеркало отстало", photo_url="https://old/mirror.jpg"
        )

        resp = _get_card(client)

        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["master"] == {
            "id": str(accepted_master.id),
            "name": "Анна из каталога",
            "bio": "О себе — из каталога",
            "photo_url": AVATAR,
        }
        kwargs = ayla.get_specialist_profile.call_args.kwargs
        assert kwargs["specialist_id"] == str(accepted_master.catalog_specialist_id)
        assert kwargs["external_user_id"].endswith(str(bot_user.channel_user_id))

    def test_limits_and_portfolio_counts_are_the_catalogs_verbatim(
        self, client: Client, accepted_master, ayla, bridged_service
    ):
        body = _get_card(client).json()

        assert body["limits"] == LIMITS
        assert body["portfolio"] == {"count": 2, "limit": 10}

    def test_catalog_down_is_503_by_name(self, client: Client, accepted_master, ayla):
        ayla.get_specialist_profile.side_effect = BookingUnavailableError("down")

        resp = _get_card(client)

        assert resp.status_code == 503
        assert resp.json()["error"] == "catalog_unavailable"

    def test_unresolved_catalog_profile_is_409_before_any_call(
        self, client: Client, accepted_master, ayla
    ):
        CatalogMaster.all_tenants.filter(pk=accepted_master.pk).update(catalog_specialist_id=None)

        resp = _get_card(client)

        assert resp.status_code == 409
        assert resp.json()["error"] == "catalog_profile_unresolved"
        assert ayla.get_specialist_profile.call_count == 0


# ─── бейдж «Принимает сегодня» — только из реального слота ─────────────────


class TestAcceptsTodayComesFromARealSlot:
    def test_true_only_when_the_catalog_returns_a_slot_for_today(
        self, client: Client, accepted_master: CatalogMaster, ayla, bridged_service
    ):
        body = _get_card(client).json()

        assert body["accepts_today"] is True
        assert body["accepts_today_reason"] is None
        kwargs = ayla.get_available_times.call_args.kwargs
        assert kwargs["specialist_id"] == str(accepted_master.catalog_specialist_id)
        assert kwargs["service_id"] == str(bridged_service.ayla_service_id)
        assert kwargs["date"] == date.fromisoformat(kwargs["date"]).isoformat()  # сегодня, ISO

    def test_no_slots_today_is_false_with_the_reason(
        self, client: Client, accepted_master, ayla, bridged_service
    ):
        ayla.get_available_times.return_value = []

        body = _get_card(client).json()

        assert body["accepts_today"] is False
        assert body["accepts_today_reason"] == card.ACCEPTS_TODAY_NO_SLOTS

    def test_no_bridged_service_means_no_question_to_the_catalog(
        self, client: Client, accepted_master, ayla
    ):
        """Без услуги с мостом слотов не бывает — и запрос не делается (счётчик)."""

        body = _get_card(client).json()

        assert body["accepts_today"] is False
        assert body["accepts_today_reason"] == card.ACCEPTS_TODAY_NO_SERVICE
        assert ayla.get_available_times.call_count == 0

    def test_slots_outage_is_false_with_the_reason_and_the_card_still_renders(
        self, client: Client, accepted_master, ayla, bridged_service
    ):
        ayla.get_available_times.side_effect = BookingUnavailableError("down")

        resp = _get_card(client)

        assert resp.status_code == 200
        assert resp.json()["accepts_today"] is False
        assert resp.json()["accepts_today_reason"] == card.ACCEPTS_TODAY_CATALOG_UNAVAILABLE
        assert resp.json()["master"]["name"] == "Анна из каталога"


# ─── чипы — категории выбранных шаблонов ────────────────────────────────────


class TestCategoriesAreTheSelectedTemplatesCategories:
    def test_unique_active_in_response_order_from_the_live_selection(
        self, client: Client, accepted_master, ayla, bridged_service
    ):
        ayla.get_service_selection.return_value = _selection(
            ("Педикюр", True), ("Маникюр", True), ("Педикюр", True), ("Брови", False), (None, True)
        )

        body = _get_card(client).json()

        assert body["categories"] == ["Педикюр", "Маникюр"]
        assert body["categories_reason"] is None

    def test_selection_refused_is_an_empty_list_with_a_reason_not_a_guess(
        self, client: Client, accepted_master, ayla, bridged_service
    ):
        ayla.get_service_selection.side_effect = BookingBadRequestError(
            "x", status_code=409, code="SERVICE_SELECTION_REFUSED", details={"reason": "salon"}
        )

        body = _get_card(client).json()

        assert body["categories"] == []
        assert body["categories_reason"] == card.CATEGORIES_SELECTION_UNAVAILABLE


# ─── портфолио — прокси, субъект по построению ──────────────────────────────


class TestPortfolioIsAProxy:
    def test_list_is_the_catalogs_answer(self, client: Client, accepted_master, ayla):
        ayla.list_specialist_portfolio.return_value = {
            "items": [{"id": "a", "image_url": "u", "sort_order": 0, "created_at": "t"}],
            "count": 1,
            "limit": 10,
        }

        resp = client.get(reverse("master_api:profile_portfolio"), **_auth())

        assert resp.status_code == 200, resp.content
        assert resp.json()["count"] == 1
        assert resp.json()["items"][0]["id"] == "a"

    def test_upload_forwards_the_bytes_and_answers_201_with_the_item(
        self, client: Client, accepted_master: CatalogMaster, bot_user: BotUser, ayla
    ):
        upload = SimpleUploadedFile("work.jpg", b"\xff\xd8jpeg-bytes", content_type="image/jpeg")

        resp = client.post(reverse("master_api:profile_portfolio"), {"image": upload}, **_auth())

        assert resp.status_code == 201, resp.content
        assert resp.json()["id"] == "item-1"
        kwargs = ayla.upload_specialist_portfolio_item.call_args.kwargs
        assert kwargs["specialist_id"] == str(accepted_master.catalog_specialist_id)
        assert kwargs["external_user_id"].endswith(str(bot_user.channel_user_id))
        assert kwargs["content"] == b"\xff\xd8jpeg-bytes"
        assert kwargs["content_type"] == "image/jpeg"

    def test_the_eleventh_photo_is_the_catalogs_refusal_by_name(
        self, client: Client, accepted_master, ayla
    ):
        ayla.upload_specialist_portfolio_item.side_effect = BookingBadRequestError(
            "x",
            status_code=400,
            code="VALIDATION_ERROR",
            details={"reason": "portfolio_limit_exceeded", "limit": 10},
        )
        upload = SimpleUploadedFile("work.jpg", b"x", content_type="image/jpeg")

        resp = client.post(reverse("master_api:profile_portfolio"), {"image": upload}, **_auth())

        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"
        assert resp.json()["details"]["reason"] == "portfolio_limit_exceeded"

    def test_upload_without_the_field_is_400_and_the_catalog_is_not_called(
        self, client: Client, accepted_master, ayla
    ):
        resp = client.post(reverse("master_api:profile_portfolio"), {}, **_auth())

        assert resp.status_code == 400
        assert ayla.upload_specialist_portfolio_item.call_count == 0

    def test_delete_forwards_the_item_and_answers_the_new_count(
        self, client: Client, accepted_master: CatalogMaster, ayla
    ):
        item_id = uuid.uuid4()

        resp = client.delete(
            reverse("master_api:profile_portfolio_item", kwargs={"item_id": item_id}), **_auth()
        )

        assert resp.status_code == 200, resp.content
        assert resp.json() == {"count": 0, "limit": 10}
        kwargs = ayla.delete_specialist_portfolio_item.call_args.kwargs
        assert kwargs["item_id"] == str(item_id)
        assert kwargs["specialist_id"] == str(accepted_master.catalog_specialist_id)

    def test_subject_is_the_signed_master_by_construction(
        self, client: Client, accepted_master: CatalogMaster, ayla
    ):
        """Чужой профиль в пути невозможен: параметра нет — не «проверяется», а
        не существует. Утверждение о маршрутах + о том, кого получил клиент."""

        names = {"profile_card", "profile_portfolio", "profile_portfolio_item"}
        patterns = {p.name: str(p.pattern) for p in master_urls.urlpatterns if p.name in names}
        assert set(patterns) == names
        assert not any("specialist" in pattern for pattern in patterns.values()), patterns

        client.get(reverse("master_api:profile_portfolio"), **_auth())
        assert ayla.list_specialist_portfolio.call_args.kwargs["specialist_id"] == str(
            accepted_master.catalog_specialist_id
        )
