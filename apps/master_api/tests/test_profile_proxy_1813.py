"""Профиль мастера из кабинета — прокси в каталог (DRF-1813, M21, половина бота).

Каталожная половина — beautygo_backend #455 (выложен 15.09 05:46):
``PATCH /internal/specialists/{id}/profile/`` и ``POST …/media/avatar/`` под
субъектом. До неё бот писал «о себе» и фото в свои поля
(``CatalogMaster.bio`` / ``photo_url`` + файл в ``MEDIA_ROOT/master_photos``),
а синхронизация их перетирала. Теперь ``PATCH /api/v1/master/onboarding/profile``
— прокси:

- «о себе» уходит в каталог; зеркало берёт ответ каталога, а не запрос;
- фото уходит в каталог multipart-ом; зеркало берёт ``avatar_url`` каталога;
  своего файла бот больше не пишет;
- лимит «о себе» один — у каталога (500); бот своих 280 не режет;
- отказ каталога — по имени, данные под ``details``; зеркало и аудит не
  трогаются; каталог недоступен — 503;
- аудит ``master.profile_initialized`` — только после успешной записи.

Красный до правки: весь файл, кроме случаев, где бот и сейчас отказал бы
так же (401 ``not_a_master`` — не здесь, в ``test_profile.py``).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import BookingBadRequestError, BookingUnavailableError
from apps.master_api import views
from apps.master_api.tests.conftest import init_data_header

pytestmark = pytest.mark.django_db

AVATAR = "https://catalog.test/media/specialists/avatars/anna.jpg"
CATALOG_BIO = "Опыт 5 лет — как его сохранил каталог"


def _state(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "specialist_id": "spec",
        "display_name": "Анна Петрова",
        "bio": "",
        "avatar_url": "",
        "portfolio": {"count": 0, "limit": 10},
    }
    base.update(over)
    return base


@pytest.fixture
def ayla(monkeypatch, accepted_master: CatalogMaster) -> MagicMock:
    client = MagicMock()
    client.patch_specialist_profile.return_value = _state(bio=CATALOG_BIO)
    client.upload_specialist_avatar.return_value = _state(avatar_url=AVATAR)
    monkeypatch.setattr(views, "get_ayla_booking_client", lambda: client)
    return client


def _auth() -> dict:
    return {"HTTP_AUTHORIZATION": init_data_header("12345")}


def _patch_json(client: Client, body: dict) -> Any:
    return client.patch(
        reverse("master_api:onboarding_profile"),
        data=json.dumps(body),
        content_type="application/json",
        **_auth(),
    )


def _patch_photo(client: Client, *, bio: str | None = None) -> Any:
    fields: dict[str, Any] = {
        "photo": SimpleUploadedFile("selfie.jpg", b"\xff\xd8\xff\xd9", content_type="image/jpeg")
    }
    if bio is not None:
        fields["bio"] = bio
    return client.generic(
        "PATCH",
        reverse("master_api:onboarding_profile"),
        data=encode_multipart(BOUNDARY, fields),
        content_type=MULTIPART_CONTENT,
        **_auth(),
    )


def _audit_rows(master: CatalogMaster) -> Any:
    return AuditLog.all_tenants.filter(action="master.profile_initialized", target_id=master.id)


class TestBioGoesToTheCatalog:
    def test_the_catalog_writes_the_bio_and_the_mirror_takes_its_answer(
        self, client: Client, accepted_master, bot_user: BotUser, ayla
    ):
        resp = _patch_json(client, {"bio": "Опыт 5 лет"})

        assert resp.status_code == 200, resp.content
        kwargs = ayla.patch_specialist_profile.call_args.kwargs
        assert kwargs["specialist_id"] == str(accepted_master.id)
        assert kwargs["external_user_id"].endswith(str(bot_user.channel_user_id))
        assert kwargs["bio"] == "Опыт 5 лет"
        accepted_master.refresh_from_db()
        assert accepted_master.bio == CATALOG_BIO
        assert resp.json()["master"]["bio"] == CATALOG_BIO

    def test_the_bot_does_not_cut_the_bio_at_280(self, client: Client, accepted_master, ayla):
        resp = _patch_json(client, {"bio": "б" * 400})

        assert resp.status_code == 200, resp.content
        assert ayla.patch_specialist_profile.call_args.kwargs["bio"] == "б" * 400

    def test_a_catalog_refusal_is_named_and_nothing_local_changes(
        self, client: Client, accepted_master, ayla
    ):
        ayla.patch_specialist_profile.side_effect = BookingBadRequestError(
            "x",
            status_code=400,
            code="VALIDATION_ERROR",
            details={"bio": ["Ensure this field has no more than 500 characters."]},
        )
        before = accepted_master.bio

        resp = _patch_json(client, {"bio": "б" * 501})

        assert resp.status_code == 400, resp.content
        assert resp.json()["error"] == "validation_error"
        assert "bio" in resp.json()["details"]
        accepted_master.refresh_from_db()
        assert accepted_master.bio == before
        assert not _audit_rows(accepted_master).exists()

    def test_the_audit_row_follows_a_successful_write(self, client: Client, accepted_master, ayla):
        assert _patch_json(client, {"bio": "новый текст"}).status_code == 200

        row = _audit_rows(accepted_master).get()
        assert "bio" in row.payload["fields_populated"]


class TestPhotoGoesToTheCatalog:
    def test_the_photo_is_uploaded_and_the_mirror_takes_the_catalog_url(
        self, client: Client, accepted_master, ayla, settings, tmp_path
    ):
        settings.MEDIA_ROOT = str(tmp_path)

        resp = _patch_photo(client)

        assert resp.status_code == 200, resp.content
        kwargs = ayla.upload_specialist_avatar.call_args.kwargs
        assert kwargs["specialist_id"] == str(accepted_master.id)
        assert kwargs["filename"] == "selfie.jpg"
        assert kwargs["content_type"] == "image/jpeg"
        assert kwargs["content"] == b"\xff\xd8\xff\xd9"
        accepted_master.refresh_from_db()
        assert accepted_master.photo_url == AVATAR
        # Своего файла бот больше не пишет — владелец фото каталог.
        assert not (tmp_path / "master_photos").exists()
        assert "photo" in _audit_rows(accepted_master).get().payload["fields_populated"]

    def test_a_refused_photo_is_named_with_its_reason(self, client: Client, accepted_master, ayla):
        ayla.upload_specialist_avatar.side_effect = BookingBadRequestError(
            "x", status_code=400, code="VALIDATION_ERROR", details={"reason": "not_square"}
        )

        resp = _patch_photo(client)

        assert resp.status_code == 400, resp.content
        assert resp.json()["error"] == "validation_error"
        assert resp.json()["details"]["reason"] == "not_square"
        accepted_master.refresh_from_db()
        assert accepted_master.photo_url == ""

    def test_bio_and_photo_together_both_reach_the_catalog(
        self, client: Client, accepted_master, ayla
    ):
        resp = _patch_photo(client, bio="Опыт 5 лет")

        assert resp.status_code == 200, resp.content
        assert ayla.patch_specialist_profile.call_args.kwargs["bio"] == "Опыт 5 лет"
        assert ayla.upload_specialist_avatar.called
        accepted_master.refresh_from_db()
        assert accepted_master.bio == CATALOG_BIO
        assert accepted_master.photo_url == AVATAR


class TestCatalogRefusalsAndOutage:
    @pytest.mark.parametrize(
        ("status", "code", "http", "slug"),
        [
            (403, None, 403, "not_linked"),
            (404, "SPECIALIST_NOT_FOUND", 404, "specialist_not_found"),
        ],
    )
    def test_refusal_is_named(
        self, client: Client, accepted_master, ayla, status, code, http, slug
    ):
        ayla.patch_specialist_profile.side_effect = BookingBadRequestError(
            "x", status_code=status, code=code, details=None
        )

        resp = _patch_json(client, {"bio": "текст"})

        assert resp.status_code == http, resp.content
        assert resp.json()["error"] == slug
        assert not _audit_rows(accepted_master).exists()

    def test_unavailable_is_503_and_nothing_local_changes(
        self, client: Client, accepted_master, ayla
    ):
        ayla.patch_specialist_profile.side_effect = BookingUnavailableError("circuit_open")

        resp = _patch_json(client, {"bio": "текст"})

        assert resp.status_code == 503, resp.content
        assert resp.json()["error"] == "catalog_unavailable"
        accepted_master.refresh_from_db()
        assert accepted_master.bio == ""
        assert not _audit_rows(accepted_master).exists()


class TestTheClientSendsTheAvatarAsMultipart:
    """Настоящий код клиента через in-memory транспорт: тело — multipart с
    полем ``image``, заголовок субъекта и Bearer на месте, JSON-тип не
    навязан (иначе каталог не разберёт файл)."""

    def test_the_upload_is_multipart_with_the_subject_header(self, settings, monkeypatch):
        from apps.integrations.ayla.booking_client import (
            get_ayla_booking_client,
            reset_ayla_booking_client,
        )

        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "token-1813"  # noqa: S105
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"data": _state(avatar_url=AVATAR)})

        real_client = httpx.Client

        def factory(*args: Any, **kwargs: Any) -> httpx.Client:
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "Client", factory)
        reset_ayla_booking_client()
        try:
            data = get_ayla_booking_client().upload_specialist_avatar(
                specialist_id="spec",
                external_user_id="bot:max:1813",
                filename="a.jpg",
                content=b"\xff\xd8\xff\xd9",
                content_type="image/jpeg",
            )
        finally:
            reset_ayla_booking_client()

        assert data["avatar_url"] == AVATAR
        (request,) = seen
        assert request.method == "POST"
        assert request.url.path.endswith("/internal/specialists/spec/media/avatar/")
        assert request.headers["content-type"].startswith("multipart/form-data")
        assert request.headers["x-external-user-id"] == "bot:max:1813"
        assert request.headers["authorization"] == "Bearer token-1813"
        body = request.read()
        assert b'name="image"' in body
        assert b'filename="a.jpg"' in body
