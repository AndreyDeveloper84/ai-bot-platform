"""Мастерская шлёт в каталог id его профиля, а не первичный ключ зеркала (DRF-1933, часть 1).

У строки зеркала ``CatalogMaster`` первичный ключ совпадает с каталожным
``SpecialistProfile.id`` только когда строку завела синхронизация. Две живые
дороги дают другой ключ:

* **соло-мастер** — ``solo_onboarding.create_solo_provider`` заводит строку с
  ``uuid4``, а каталог заводит свой профиль (``tenants/solo_provisioning.py``);
* **салонное приглашение до синка** — после склейки DRF-1507 синхронизация
  обновляет строку приглашения, и её ``uuid4`` остаётся первичным ключом.

До правки все прокси мастерской слали ``str(master.id)`` — у таких мастеров
каталог отвечал бы 404 на экранах 04–08. Решение (A) главного окна 15.09:
колонка ``CatalogMaster.catalog_specialist_id`` — одно представление факта;
прокси читают её, пустая колонка — именованный отказ, а не первичный ключ.

Красный до правки: случаи «соло» и «приглашение» (каталог получает pk) и
«отказ» (каталог всё равно зовут). Зелёный в обе стороны: салонная строка
синка, где pk == id каталога — её поведение не меняется.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.master_api import views
from apps.master_api.tests.conftest import init_data_header

pytestmark = pytest.mark.django_db

RID = "0c5f0000-0000-4000-8000-000000001933"
SID = "0c5f0000-0000-4000-8000-000000000404"
TID = "0c5f0000-0000-4000-8000-000000000303"
CMD = "0c5f0000-0000-4000-8000-000000000808"

_HAS_COLUMN = any(f.name == "catalog_specialist_id" for f in CatalogMaster._meta.get_fields())


# (id, HTTP-метод, имя маршрута, kwargs маршрута, вид тела, тело, метод клиента)
SITES: list[tuple[str, str, str, dict[str, str], str | None, Any, str]] = [
    (
        "profile_bio",
        "patch",
        "onboarding_profile",
        {},
        "json",
        {"bio": "Опыт"},
        "patch_specialist_profile",
    ),
    (
        "profile_photo",
        "patch",
        "onboarding_profile",
        {},
        "multipart",
        None,
        "upload_specialist_avatar",
    ),
    ("hours_get", "get", "working_hours", {}, None, None, "get_working_hours"),
    ("hours_put", "put", "working_hours", {}, "json", {"schedule": []}, "put_working_hours"),
    ("gap_list", "get", "canon_gap_requests", {}, None, None, "list_canon_gap_requests"),
    (
        "gap_create",
        "post",
        "canon_gap_requests",
        {},
        "json",
        {"name": "Массаж", "duration_minutes": 60, "price": "1500"},
        "create_canon_gap_request",
    ),
    (
        "gap_similar",
        "get",
        "canon_gap_similar",
        {},
        "query",
        {"name": "Массаж"},
        "similar_canon_templates",
    ),
    (
        "gap_detail",
        "get",
        "canon_gap_request_detail",
        {"request_id": RID},
        None,
        None,
        "get_canon_gap_request",
    ),
    ("accepting_get", "get", "accepting_bookings", {}, None, None, "get_accepting_bookings"),
    (
        "accepting_set",
        "patch",
        "accepting_bookings",
        {},
        "json",
        {"accepting_bookings": False},
        "set_accepting_bookings",
    ),
    ("readiness", "get", "publication_readiness", {}, None, None, "get_publication_readiness"),
    ("publish", "post", "publication", {}, "json", {"command_id": CMD}, "publish"),
    ("pub_status", "get", "publication_status", {}, None, None, "get_publication_status"),
    ("reviews", "get", "reviews", {}, None, None, "get_specialist_reviews"),
    ("selection_get", "get", "service_selection", {}, None, None, "get_service_selection"),
    (
        "selection_post",
        "post",
        "service_selection",
        {},
        "json",
        {"template_ids": [TID]},
        "select_services",
    ),
    (
        "offer",
        "put",
        "service_offer",
        {"salon_service_id": SID},
        "json",
        {"price": "1500", "duration_minutes": 60},
        "put_service_offer",
    ),
    (
        "remove",
        "delete",
        "selected_service",
        {"salon_service_id": SID},
        None,
        None,
        "remove_service",
    ),
]
SITE_IDS = [s[0] for s in SITES]


def test_the_table_names_every_workshop_call_site():
    """Перепись 15.09 (бот dev f3fa4275): 17 вызовов клиента каталога в
    ``master_api/views.py``; после #1755 (K14b, «Мои отзывы») — 18.
    Таблица выше — ровно они, по одному на метод."""
    assert len(SITES) == 18
    assert len({s[6] for s in SITES}) == 18


def _set_catalog_id(master: CatalogMaster, *, raw_id: str | None, column: str | None) -> None:
    CatalogMaster.all_tenants.filter(pk=master.pk).update(raw={"id": raw_id} if raw_id else {})
    if _HAS_COLUMN:
        CatalogMaster.all_tenants.filter(pk=master.pk).update(catalog_specialist_id=column)


@pytest.fixture
def ayla(monkeypatch) -> MagicMock:
    client = MagicMock()
    monkeypatch.setattr(views, "get_ayla_booking_client", lambda: client)
    return client


def _call(site: tuple, http: Client) -> Any:
    _id, method, name, kwargs, kind, body, _client_method = site
    url = reverse(f"master_api:{name}", kwargs=kwargs or None)
    auth = {"HTTP_AUTHORIZATION": init_data_header("12345")}
    if kind == "json":
        return getattr(http, method)(
            url, data=json.dumps(body), content_type="application/json", **auth
        )
    if kind == "multipart":
        photo = SimpleUploadedFile("selfie.jpg", b"\xff\xd8\xff\xd9", content_type="image/jpeg")
        return http.generic(
            method.upper(),
            url,
            data=encode_multipart(BOUNDARY, {"photo": photo}),
            content_type=MULTIPART_CONTENT,
            **auth,
        )
    if kind == "query":
        return http.get(url, data=body, **auth)
    return getattr(http, method)(url, **auth)


def _sent_specialist_id(ayla: MagicMock, client_method: str) -> str:
    method = getattr(ayla, client_method)
    assert method.called, f"{client_method}: запрос не дошёл до клиента каталога"
    return str(method.call_args.kwargs["specialist_id"])


@pytest.fixture
def http() -> Client:
    # Ответ прокси здесь не предмет: подмена клиента отдаёт MagicMock, и
    # сборка ответа может упасть. Предмет — с каким id позвали каталог.
    return Client(raise_request_exception=False)


class TestMirrorRowWhosePkIsNotTheCatalogId:
    @pytest.mark.parametrize("kind", ["solo", "invite_glued"])
    @pytest.mark.parametrize("site", SITES, ids=SITE_IDS)
    def test_the_catalog_receives_its_own_specialist_id(
        self, http, accepted_master, ayla, site, kind
    ):
        catalog_id = str(uuid.uuid4())
        assert catalog_id != str(accepted_master.pk)
        # Соло: у строки нет ответа синка, id каталога — из провижининга.
        # Приглашение после склейки DRF-1507: raw["id"] — канонический id.
        _set_catalog_id(
            accepted_master,
            raw_id=None if kind == "solo" else catalog_id,
            column=catalog_id,
        )

        _call(site, http)

        assert _sent_specialist_id(ayla, site[6]) == catalog_id


class TestUnresolvedCatalogIdIsANamedRefusal:
    @pytest.mark.parametrize("site", SITES, ids=SITE_IDS)
    def test_no_catalog_call_and_the_refusal_is_named(self, http, accepted_master, ayla, site):
        _set_catalog_id(accepted_master, raw_id=None, column=None)

        resp = _call(site, http)

        assert not getattr(ayla, site[6]).called, "без id каталога каталог не зовут (pk — не id)"
        assert resp.status_code == 409, resp.content
        assert resp.json()["error"] == "catalog_profile_unresolved"


class TestSalonRowFromSyncIsUnchanged:
    @pytest.mark.parametrize("site", SITES, ids=SITE_IDS)
    def test_a_synced_row_still_sends_its_primary_key(self, http, accepted_master, ayla, site):
        pk = str(accepted_master.pk)
        _set_catalog_id(accepted_master, raw_id=pk, column=pk)

        _call(site, http)

        assert _sent_specialist_id(ayla, site[6]) == pk
