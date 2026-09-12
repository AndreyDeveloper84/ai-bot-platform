"""Один владелец фото/bio — каталог (DRF-1812, M20).

Два хранилища фото (``CatalogMaster.photo_url`` бот против
``SpecialistProfile.avatar`` каталог) давали два разных «фото для
публикации». Теперь зеркало берёт ``avatar`` из ``/internal/specialists/``,
и ``photo_url`` — зеркальное поле.

Что заперто:

- парсер: ``avatar`` трёхзначен — нет ключа → ``None``, ``""`` → ``""``,
  URL → дословно (относительный тоже дословно, хост не выдумывается);
- upsert: фото каталога переписывает платформенное; повторный sync с новым
  фото — новое; ``bio`` как раньше — зеркало целиком;
- переходное правило до M21: у каталога фото нет (``""``) или ключа нет
  (``None``) — загруженное в кабинете фото не стирается (иначе оно
  пропадало бы при каждой синхронизации, а положить его в каталог пока
  некуда);
- readiness читает то же поле — фото из каталога закрывает пункт
  ``profile``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from apps.catalog.models import CatalogMaster
from apps.catalog.services.http_client import CatalogSpecialistDTO, _parse_specialist
from apps.catalog.services.upserter import upsert_specialists
from apps.tenancy.models import Tenant

AVATAR = "https://catalog.test/media/specialists/avatars/anna.jpg"


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="photo-owner", name="Photo Owner")


def _row(**overrides) -> dict:
    row = {
        "id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "display_name": "Анна Иванова",
        "updated_at": "2026-09-12T12:00:00Z",
        "status": "active",
        "is_available": True,
        "bio": "Топ-мастер",
    }
    row.update(overrides)
    return row


def _dto(mid: str, *, avatar_url: str | None, bio: str = "Топ-мастер") -> CatalogSpecialistDTO:
    return CatalogSpecialistDTO(
        ayla_master_id=mid,
        user_id=str(uuid.uuid4()),
        name="Анна Иванова",
        external_updated_at=datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc),
        bio=bio,
        avatar_url=avatar_url,
        raw={"id": mid},
    )


class TestParser:
    def test_absent_key_is_none_empty_is_empty_url_is_verbatim(self):
        assert _parse_specialist(_row()).avatar_url is None
        assert _parse_specialist(_row(avatar=None)).avatar_url is None
        assert _parse_specialist(_row(avatar="")).avatar_url == ""
        assert _parse_specialist(_row(avatar=AVATAR)).avatar_url == AVATAR
        # Относительный путь — дословно: хост здесь не выдумывается.
        assert _parse_specialist(_row(avatar="/media/a.jpg")).avatar_url == "/media/a.jpg"


@pytest.mark.django_db
class TestCatalogOwnsThePhoto:
    def test_catalog_avatar_overwrites_the_platform_photo(self, tenant: Tenant):
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid, avatar_url=None)])
        m = CatalogMaster.all_tenants.get(id=mid)
        m.photo_url = "https://bot.test/master_photos/old.jpg"
        m.save(update_fields=["photo_url"])

        upsert_specialists(tenant, [_dto(mid, avatar_url=AVATAR)])
        m.refresh_from_db()
        assert m.photo_url == AVATAR

        # Новое фото в каталоге — новое в зеркале.
        upsert_specialists(tenant, [_dto(mid, avatar_url=AVATAR + "?v=2")])
        m.refresh_from_db()
        assert m.photo_url == AVATAR + "?v=2"

    def test_bio_is_a_mirror_wholesale(self, tenant: Tenant):
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid, avatar_url=AVATAR, bio="Старое")])
        upsert_specialists(tenant, [_dto(mid, avatar_url=AVATAR, bio="")])
        assert CatalogMaster.all_tenants.get(id=mid).bio == ""

    @pytest.mark.parametrize("avatar_url", ["", None])
    def test_until_m21_a_platform_photo_survives_when_the_catalog_has_none(
        self, tenant: Tenant, avatar_url
    ):
        """Переходное правило: положить фото в каталог пока некуда (M21), и
        стирать загруженное в кабинете при каждом sync было бы регрессом."""
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid, avatar_url=None)])
        m = CatalogMaster.all_tenants.get(id=mid)
        m.photo_url = "https://bot.test/master_photos/mine.jpg"
        m.save(update_fields=["photo_url"])

        upsert_specialists(tenant, [_dto(mid, avatar_url=avatar_url)])
        m.refresh_from_db()
        assert m.photo_url == "https://bot.test/master_photos/mine.jpg"

    def test_readiness_profile_item_reads_the_mirrored_photo(self, tenant: Tenant):
        from apps.master_api.services.onboarding_readiness import _profile_item

        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid, avatar_url=None)])
        m = CatalogMaster.all_tenants.get(id=mid)
        assert _profile_item(m).detail["photo"] is False

        upsert_specialists(tenant, [_dto(mid, avatar_url=AVATAR)])
        m.refresh_from_db()
        assert _profile_item(m).detail["photo"] is True
