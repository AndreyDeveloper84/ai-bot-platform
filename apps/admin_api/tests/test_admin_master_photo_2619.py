"""DRF-2619 — фото мастера рукой администратора: байты у каталога, не на диске бота.

Было: загрузка писала файл в ``MEDIA_ROOT/master_photos/`` и адрес
``/media/master_photos/…`` в зеркало. Этот адрес не отдавал никто (у бота
нет ни ``MEDIA_URL``, ни маршрута ``/media/``), а ближайшая синхронизация
затирала его ``avatar_url`` каталога — кнопка делала вид, что работает.

Пары, которые обязаны различаться:

* загрузка, принятая каталогом, — байты ушли в каталог ровно те, зеркало
  держит адрес каталога, на диск бота не легло ничего; загрузка, которой
  каталог отказал (``not_square``), — зеркало прежнее, аудита нет, причина
  отказа дошла до экрана;
* загрузка → синхронизация → показ: синхронизация приносит то же фото, и
  карточка показывает его; мастер без загрузки после той же синхронизации —
  без фото (инициалы).

Что каталог хранит ровно загруженные байты — узел каталога
(``users/tests/test_salon_admin_master_avatar_2619.py``); здесь — что бот их
отдал без изменений и не держит у себя.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone as dt_timezone

import httpx
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.catalog.models import CatalogMaster
from apps.catalog.services.http_client import CatalogSpecialistDTO
from apps.catalog.services.upserter import upsert_specialists
from apps.channels.bot_registry import SALON_STREAM, BotEntry
from apps.identity.models import BotUser
from apps.integrations.ayla.person_token import PersonToken
from apps.integrations.ayla.salon_client import AylaSalonClient
from apps.miniapp_api.master_media import master_photo_path
from apps.tenancy.models import Tenant
from tests.support.catalog_mirror import sync_shaped

from .conftest import BOT_TOKEN, init_data_header

pytestmark = pytest.mark.django_db

SERVICE_TOKEN = "service-token-under-test-2619"  # pragma: allowlist secret
PERSON_TOKEN = "person-token-under-test-2619"  # pragma: allowlist secret
WRITES = "apps.admin_api.views_salon_schedule_writes"
CATALOG_AVATAR = "https://catalog.test/media/specialists/avatars/olga-2619.png"
PHOTO = b"\x89PNG\r\n\x1a\n" + b"2619-photo-bytes" * 8


@pytest.fixture(autouse=True)
def _salon_bot_signs(settings, tenant: Tenant, tmp_path) -> None:
    settings.MAX_BOT_REGISTRY = (
        BotEntry(
            slug="signer",
            webhook_secret="",
            api_token=BOT_TOKEN,
            tenant_slug=tenant.slug,
            stream=SALON_STREAM,
        ),
    )
    # Если бот снова начнёт писать на диск — он напишет сюда, и узел это увидит.
    settings.MEDIA_ROOT = str(tmp_path)


@pytest.fixture(autouse=True)
def _empty_token_cache():
    from apps.integrations.ayla.person_token import _CACHE

    _CACHE.clear()
    yield
    _CACHE.clear()


@pytest.fixture
def synced_master(tenant: Tenant) -> CatalogMaster:
    return sync_shaped(
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=2619,
            external_updated_at=datetime.now(tz=dt_timezone.utc),
            name="Ольга",
            ayla_user_id=uuid.uuid4(),
        )
    )


class _Catalog:
    """Обмен токена и каталог — оба записаны."""

    def __init__(self, monkeypatch, *, refuse_reason: str | None = None):
        self.sent: list[httpx.Request] = []

        def exchange(*, init_data: str, tenant_slug: str, **_kw) -> PersonToken:
            return PersonToken(access_token=PERSON_TOKEN, tenant_slug=tenant_slug, expires_in=600)

        def handler(request: httpx.Request) -> httpx.Response:
            self.sent.append(request)
            if refuse_reason is not None:
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "code": "VALIDATION_ERROR",
                            "message": "File not accepted.",
                            "details": {"reason": refuse_reason},
                        }
                    },
                )
            return httpx.Response(200, json={"data": {"avatar_url": CATALOG_AVATAR}})

        client = AylaSalonClient(
            base_url="https://ayla.example",
            service_token=SERVICE_TOKEN,
            transport=httpx.MockTransport(handler),
        )
        monkeypatch.setattr(f"{WRITES}.obtain_person_token", exchange)
        monkeypatch.setattr(f"{WRITES}.get_salon_client", lambda: client)


def _upload(client: Client, master: CatalogMaster, content: bytes = PHOTO):
    return client.post(
        reverse("admin_api:master_photo_upload", args=[str(master.id)]),
        data={"photo": SimpleUploadedFile("olga.png", content, content_type="image/png")},
        HTTP_AUTHORIZATION=init_data_header("5001"),
    )


def _audit_rows(master: CatalogMaster) -> int:
    return AuditLog.all_tenants.filter(
        action="master.photo_updated_by_admin", target_id=master.id
    ).count()


def _sync(tenant: Tenant, master: CatalogMaster, avatar_url: str) -> None:
    upsert_specialists(
        tenant,
        [
            CatalogSpecialistDTO(
                ayla_master_id=str(master.id),
                user_id=str(master.ayla_user_id),
                name=master.name,
                external_updated_at=datetime.now(tz=dt_timezone.utc),
                bio="",
                avatar_url=avatar_url,
                raw={"id": str(master.id)},
            )
        ],
    )


class TestTheCatalogOwnsTheBytes:
    def test_an_accepted_upload_sends_the_exact_bytes_and_keeps_nothing_on_the_bot(
        self,
        client: Client,
        owner_bot_user: BotUser,
        synced_master: CatalogMaster,
        monkeypatch,
        tmp_path,
    ) -> None:
        catalog = _Catalog(monkeypatch)

        resp = _upload(client, synced_master)

        assert resp.status_code == 200, resp.content
        (sent,) = catalog.sent
        assert sent.url.path == f"/api/v1/tenants/me/masters/{synced_master.id}/media/avatar/"
        headers = {k.lower(): v for k, v in sent.headers.items()}
        assert headers["authorization"] == f"Bearer {PERSON_TOKEN}"
        assert SERVICE_TOKEN not in " ".join(headers.values())
        assert PHOTO in sent.read()
        synced_master.refresh_from_db()
        assert synced_master.photo_url == CATALOG_AVATAR
        assert resp.json() == {"photo_url": master_photo_path(synced_master.id, CATALOG_AVATAR)}
        assert _audit_rows(synced_master) == 1
        assert list(tmp_path.iterdir()) == []  # диск бота пуст

    def test_a_refused_upload_changes_nothing_and_says_which_rule(
        self,
        client: Client,
        owner_bot_user: BotUser,
        synced_master: CatalogMaster,
        monkeypatch,
    ) -> None:
        _Catalog(monkeypatch, refuse_reason="not_square")

        resp = _upload(client, synced_master)

        assert resp.status_code == 400
        assert resp.json()["details"] == {"reason": "not_square"}
        synced_master.refresh_from_db()
        assert synced_master.photo_url == ""
        assert _audit_rows(synced_master) == 0

    def test_without_the_file_nothing_leaves(
        self,
        client: Client,
        owner_bot_user: BotUser,
        synced_master: CatalogMaster,
        monkeypatch,
    ) -> None:
        catalog = _Catalog(monkeypatch)
        resp = client.post(
            reverse("admin_api:master_photo_upload", args=[str(synced_master.id)]),
            data={},
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400
        assert catalog.sent == []


class TestUploadThenSyncThenShow:
    def test_the_sync_brings_the_same_photo_and_a_master_without_upload_has_none(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        synced_master: CatalogMaster,
        monkeypatch,
    ) -> None:
        other = sync_shaped(
            CatalogMaster.all_tenants.create(
                tenant=tenant,
                external_id=26190,
                external_updated_at=datetime.now(tz=dt_timezone.utc),
                name="Вера",
                ayla_user_id=uuid.uuid4(),
            )
        )
        _Catalog(monkeypatch)
        assert _upload(client, synced_master).status_code == 200

        # Каталог отдаёт синхронизации то, что хранит: у одной — фото, у другой — нет.
        _sync(tenant, synced_master, CATALOG_AVATAR)
        _sync(tenant, other, "")

        synced_master.refresh_from_db()
        other.refresh_from_db()
        assert synced_master.photo_url == CATALOG_AVATAR
        assert master_photo_path(synced_master.id, synced_master.photo_url) != ""
        assert master_photo_path(other.id, other.photo_url) == ""  # инициалы
