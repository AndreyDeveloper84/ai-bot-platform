"""invite_token не печатается на форме каталога ни для кого (DRF-1515).

Защита живёт у владельца поля — в ``CatalogMasterAdmin.exclude``. До этой
задачи токен прятала внешняя заплатка из ``apps/adminconsole/`` (DRF-1495),
которая держалась на том, что о ней помнят. Теперь заплатка снята, и эти
проверки доказывают, что форма каталога закрыта сама по себе.

Значение токена фиктивное: это случайный UUID, сгенерированный внутри
прогона, он нигде не используется и ни к чему не подходит.
"""

from __future__ import annotations

import secrets
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:  # noqa: ANN001
    return Tenant.objects.create(slug="mask-tenant", name="Mask tenant")


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.objects.create(
        tenant=tenant,
        external_updated_at=timezone.now(),
        external_id=990002,
        name="Мастер Маскировка",
        specialization="Парикмахер",
        invite_token=uuid.uuid4(),
    )


def _login(user) -> Client:  # noqa: ANN001, ANN202
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_superuser_does_not_see_invite_token(master: CatalogMaster) -> None:
    user = get_user_model().objects.create_superuser(
        username="mask.root", email="", password=secrets.token_urlsafe(24)
    )
    response = _login(user).get(f"/admin/catalog/catalogmaster/{master.pk}/change/")
    body = response.content.decode("utf-8")

    # Присутствие: карточка отрисовалась, разрешённые поля на месте.
    assert response.status_code == 200
    assert "Мастер Маскировка" in body
    assert "Парикмахер" in body

    assert str(master.invite_token) not in body
    assert "invite_token" not in body


@pytest.mark.django_db
def test_staff_viewer_does_not_see_invite_token(master: CatalogMaster) -> None:
    """Штатный смотрящий (view-only, без прав на правку) — та же картина."""
    user = get_user_model().objects.create_user(
        username="mask.viewer", email="", password=secrets.token_urlsafe(24), is_staff=True
    )
    user.user_permissions.add(Permission.objects.get(codename="view_catalogmaster"))

    response = _login(user).get(f"/admin/catalog/catalogmaster/{master.pk}/change/")
    body = response.content.decode("utf-8")

    # Присутствие: страница доступна и это карточка нужного мастера.
    assert response.status_code == 200
    assert "Мастер Маскировка" in body

    assert str(master.invite_token) not in body
    assert "invite_token" not in body
