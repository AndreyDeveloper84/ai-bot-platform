"""Готовность к публикации, «Опубликовать» и «Проверить статус» — прокси в каталог (DRF-1797, M5).

Каталожная половина — beautygo_backend #453 (M4, выложен 15.09):
``/internal/specialists/{id}/publication/readiness/``, ``…/publication/``
``{command_id}``, ``…/publication/status/`` под субъектом. Здесь — то, что
заперто в боте:

- мастер читает и публикует СВОЙ профиль через ``/api/v1/master/publication…``;
  субъект — его bot-личность, профиль — его ``CatalogMaster.id``;
- ответы каталога проходят как есть: готовность бот не пересчитывает;
- «Опубликовать» требует ``command_id`` (UUID) от экрана — повтор с тем же
  ключом безопасен; без ключа — 400 без вызова каталога; 201 — только когда
  каталог сделал переход, повтор и «уже на проверке» — 200;
- каждый отказ каталога — по имени: неготовность со списком ``missing``,
  чужой ключ, салонный workspace, не связан;
- недоступность каталога — 503 ``catalog_unavailable``;
- **витрина и публикация дают один ответ**: зеркало ставит ``is_active``
  только при ``status == active`` каталога, а ACTIVE соло-мастер получает
  лишь через гейт модератора M4 (связь ∧ готовность ∧ модератор).
  ``sale_block`` не расширяется — решение главного окна 15.09 (DRF-1797):
  «без цены и фото не продаётся» держит статус профиля в каталоге.

Красный до правки: все прокси-тесты (маршрутов нет → 404) и тесты одного
ответа (они читают статус через прокси).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
import uuid
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.catalog.master_state import sale_block
from apps.catalog.models import CatalogMaster
from apps.catalog.services.http_client import _parse_specialist
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import BookingBadRequestError, BookingUnavailableError
from apps.master_api import views
from apps.master_api.tests.conftest import init_data_header

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse

pytestmark = pytest.mark.django_db

READINESS_URL = "/api/v1/master/publication/readiness"
PUBLISH_URL = "/api/v1/master/publication"
STATUS_URL = "/api/v1/master/publication/status"

CMD = str(uuid.uuid4())

NOT_READY = {
    "specialist_id": "spec",
    "status": "NOT_READY",
    "missing": [{"code": "photo_missing", "section": "profile", "detail": {}}],
}
READY = {"specialist_id": "spec", "status": "READY", "missing": []}
STATUS = {
    "specialist_id": "spec",
    "profile_status": "pending",
    "readiness": READY,
    "last_request": None,
}
PUBLISHED = {
    "specialist_id": "spec",
    "profile_status": "pending",
    "replayed": False,
    "request": {
        "id": "r1",
        "command_id": CMD,
        "outcome": "submitted",
        "from_status": "draft",
        "to_status": "pending",
        "created_at": "2026-09-15T05:00:00+00:00",
    },
}


@pytest.fixture
def ayla(monkeypatch, accepted_master: CatalogMaster) -> MagicMock:
    client = MagicMock()
    client.get_publication_readiness.return_value = dict(NOT_READY)
    client.publish.return_value = {**PUBLISHED, "created": True}
    client.get_publication_status.return_value = dict(STATUS)
    monkeypatch.setattr(views, "get_ayla_booking_client", lambda: client)
    return client


def _auth() -> dict:
    return {"HTTP_AUTHORIZATION": init_data_header("12345")}


def _post(client: Client, url: str, body: object) -> "_MonkeyPatchedWSGIResponse":
    return client.post(url, data=json.dumps(body), content_type="application/json", **_auth())


class TestReadiness:
    def test_get_passes_the_catalog_readiness_through_untouched(
        self, client: Client, accepted_master, bot_user: BotUser, ayla
    ):
        resp = client.get(READINESS_URL, **_auth())

        assert resp.status_code == 200, resp.content
        assert resp.json() == NOT_READY
        kwargs = ayla.get_publication_readiness.call_args.kwargs
        assert kwargs["specialist_id"] == str(accepted_master.id)
        assert kwargs["external_user_id"].endswith(str(bot_user.channel_user_id))


class TestPublish:
    @pytest.mark.parametrize(("created", "http"), [(True, 201), (False, 200)])
    def test_status_follows_the_catalogs_transition(
        self, client: Client, accepted_master, ayla, created, http
    ):
        ayla.publish.return_value = {**PUBLISHED, "created": created}

        resp = _post(client, PUBLISH_URL, {"command_id": CMD})

        assert resp.status_code == http, resp.content
        assert resp.json()["request"]["command_id"] == CMD
        kwargs = ayla.publish.call_args.kwargs
        assert kwargs["specialist_id"] == str(accepted_master.id)
        assert kwargs["command_id"] == CMD

    @pytest.mark.parametrize(
        "body",
        [{}, {"command_id": "not-a-uuid"}, {"command_id": 5}],
        ids=["missing", "garbage", "number"],
    )
    def test_without_a_command_key_the_catalog_is_not_called(
        self, client: Client, accepted_master, ayla, body
    ):
        resp = _post(client, PUBLISH_URL, body)

        assert resp.status_code == 400, resp.content
        assert resp.json()["error"] == "validation_error"
        ayla.publish.assert_not_called()


class TestStatus:
    def test_get_passes_the_catalog_status_through_untouched(
        self, client: Client, accepted_master, ayla
    ):
        resp = client.get(STATUS_URL, **_auth())

        assert resp.status_code == 200, resp.content
        assert resp.json() == STATUS


class TestRefusalsByName:
    @pytest.mark.parametrize(
        ("status", "code", "details", "http", "slug", "extra"),
        [
            (403, None, None, 403, "not_linked", {}),
            (404, "SPECIALIST_NOT_FOUND", None, 404, "specialist_not_found", {}),
            (
                409,
                "PUBLICATION_NOT_READY",
                NOT_READY,
                409,
                "not_ready",
                {"status": "NOT_READY", "missing": NOT_READY["missing"]},
            ),
            (
                409,
                "PUBLICATION_REFUSED",
                {"reason": "command_id_reused"},
                409,
                "command_id_reused",
                {"reason": "command_id_reused"},
            ),
            (
                409,
                "PUBLICATION_REFUSED",
                {"reason": "salon_publication_owner_managed"},
                409,
                "salon_publication_owner_managed",
                {"reason": "salon_publication_owner_managed"},
            ),
            (
                409,
                "PUBLICATION_REFUSED",
                {"reason": "no_workspace_tenant"},
                409,
                "no_workspace_tenant",
                {"reason": "no_workspace_tenant"},
            ),
            (400, "VALIDATION_ERROR", {"command_id": ["bad"]}, 400, "validation_error", {}),
        ],
    )
    def test_catalog_refusal_is_named_with_its_details(
        self, client: Client, accepted_master, ayla, status, code, details, http, slug, extra
    ):
        ayla.publish.side_effect = BookingBadRequestError(
            "x", status_code=status, code=code, details=details
        )

        resp = _post(client, PUBLISH_URL, {"command_id": CMD})

        assert resp.status_code == http
        data = resp.json()
        assert data["error"] == slug
        # Данные отказа — под ``details``, как их читает ApiError Mini App (DRF-1708).
        for key, value in extra.items():
            assert data["details"][key] == value

    @pytest.mark.parametrize(
        ("method", "call"),
        [
            ("get_publication_readiness", lambda c: c.get(READINESS_URL, **_auth())),
            ("publish", lambda c: _post(c, PUBLISH_URL, {"command_id": CMD})),
            ("get_publication_status", lambda c: c.get(STATUS_URL, **_auth())),
        ],
        ids=["readiness", "publish", "status"],
    )
    def test_unavailable_is_503(self, client: Client, accepted_master, ayla, method, call):
        getattr(ayla, method).side_effect = BookingUnavailableError("circuit_open")

        resp = call(client)

        assert resp.status_code == 503
        assert resp.json()["error"] == "catalog_unavailable"

    def test_wrong_methods_are_405(self, client: Client, accepted_master, ayla):
        assert client.post(READINESS_URL, **_auth()).status_code == 405
        assert client.get(PUBLISH_URL, **_auth()).status_code == 405
        assert client.post(STATUS_URL, **_auth()).status_code == 405


class TestStorefrontAndPublicationGiveOneAnswer:
    """Одна фикстура, два ответа: «продаётся ли мастер» (витрина, ``sale_block``)
    и «что сказал каталог о публикации» (статус через прокси).

    Зеркало берёт ``is_active`` тем же разбором строки каталога, что и
    синхронизация (``_parse_specialist``): активен только ``status == active``.
    Поэтому соло-мастер, которого M4 не пропустил через гейт модератора, не
    продаётся ни при каком наборе полей бота — и прокси статуса говорит то же.
    Положительная половина — ``active``: продаётся, и статус тот же.
    """

    @pytest.mark.parametrize(
        ("catalog_status", "sold"), [("draft", False), ("pending", False), ("active", True)]
    )
    def test_the_storefront_sells_exactly_what_the_catalog_published(
        self, client: Client, accepted_master, ayla, catalog_status, sold
    ):
        row = {
            "id": str(uuid.uuid4()),
            "display_name": accepted_master.name,
            "status": catalog_status,
            "is_available": True,
            "is_booking_enabled": True,
        }
        dto = _parse_specialist(row)
        # Связь с Ayla есть — иначе витрина отказала бы по другой причине
        # (``ayla_unlinked``) и тест доказывал бы не то.
        CatalogMaster.all_tenants.filter(pk=accepted_master.pk).update(
            is_active=dto.is_active, ayla_user_id=uuid.uuid4()
        )
        accepted_master.refresh_from_db()
        ayla.get_publication_status.return_value = {**STATUS, "profile_status": catalog_status}

        published = client.get(STATUS_URL, **_auth())

        assert published.status_code == 200, published.content
        assert published.json()["profile_status"] == catalog_status
        assert dto.is_active is sold
        assert (sale_block(accepted_master) is None) is sold
