"""Провижининг соло пишет id профиля каталога и на строку мастера (DRF-1933, часть 1).

До правки id DRAFT-профиля из ответа каталога жил только на
``SoloIdentityLink.catalog_specialist_id``, и никто его не читал: кабинет
слал в каталог ``uuid4`` строки зеркала. Теперь тот же readback пишется в
``CatalogMaster.catalog_specialist_id`` — колонку, которую читают прокси.

Красный до правки: колонки нет — ``getattr`` возвращает ``None``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from apps.catalog.services.http_client import ProvisionedSoloWorkspaceDTO
from apps.channels.max import salon_handler
from apps.identity.models import SoloIdentityLink
from apps.identity.services import solo_catalog_provisioning

pytestmark = pytest.mark.django_db

CHANNEL_USER_ID = "solo-cat-1933"
SPECIALIST = uuid.UUID("0c5f0000-0000-4000-8000-00000000c1a0")


class _Event:
    def __init__(self) -> None:
        self.text = salon_handler.SOLO_CONFIRM_CALLBACK
        self.chat_id = "556"
        self.channel = "max"
        self.channel_user_id = CHANNEL_USER_ID
        self.raw = {"message": {"sender": {"name": "Ольга"}}}


class _FakeCatalog:
    def __enter__(self) -> "_FakeCatalog":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def provision_solo_workspace(self, **kwargs: Any) -> ProvisionedSoloWorkspaceDTO:
        return ProvisionedSoloWorkspaceDTO(
            tenant_id=uuid.UUID(str(kwargs["tenant_id"])),
            slug=kwargs["slug"],
            specialist_id=SPECIALIST,
            user_id=uuid.UUID("0c5f0000-0000-4000-8000-00000000c1a1"),
            status="draft",
            created=True,
        )


def test_the_catalog_profile_id_lands_on_the_master_row(monkeypatch):
    monkeypatch.setattr(salon_handler, "_reply", lambda event, text, attachments=None: None)
    monkeypatch.setattr(solo_catalog_provisioning, "CatalogHttpClient", lambda: _FakeCatalog())

    salon_handler._register_solo_provider(_Event(), entry=None, display_name="Ольга", city="Пенза")

    link = SoloIdentityLink.objects.select_related("master").get(channel_user_id=CHANNEL_USER_ID)
    assert link.catalog_specialist_id == SPECIALIST
    assert link.master.pk != SPECIALIST  # строка зеркала — со своим uuid4
    assert getattr(link.master, "catalog_specialist_id", None) == SPECIALIST
