"""Фильтр «Мастер» в админке записей — не пустой при непустой таблице (DRF-1608).

ЧТО ИМЕННО ЛОВИТ ЭТОТ ТЕСТ

Django строит боковой фильтр по FK через ``rel_model._default_manager``,
у ``CatalogMaster`` это ``objects`` = ``TenantScopedManager``. У запроса
в ``/admin/`` тенантного контекста нет, поэтому до правки DRF-1608:

  * ``STRICT_TENANT_SCOPE=strict`` (режим пилота) → ``CrossTenantError``
    внутри ``django/contrib/admin/filters.py`` → страница **500**;
  * ``STRICT_TENANT_SCOPE=audit``                → ``.none()`` → страница
    **200**, а фильтр «Мастер» пуст при непустой таблице мастеров.

Оба режима проверяются явно и не зависят от того, какой ``conftest.py``
оказался в пути: ``tests/conftest.py`` включает strict только для
``tests/``, а тесты в ``apps/`` идут на умолчании настроек (``audit``).
Молчаливая зависимость от окружающего conftest как раз и прятала бы
половину дефекта.

СТРАЖА НУЛЯ. «Список вариантов пуст» само по себе не улика: пустым он
бывает и на пустой таблице. Поэтому каждый прогон сначала утверждает
положительное число мастеров в базе и затем сверяет число вариантов
фильтра **с ним**, а не с ``> 0``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone as dj_timezone

from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogMaster, CatalogService
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

MSK = ZoneInfo("Europe/Moscow")
CHANGELIST_URL = "/admin/booking/bookingrequest/"

pytestmark = pytest.mark.django_db


@pytest.fixture
def superuser_client(client: Client) -> Client:
    user = get_user_model().objects.create_superuser(
        username="drf1608-root",
        email="drf1608@example.com",
        password="x",  # pragma: allowlist secret
    )
    client.force_login(user)
    return client


def _seed_two_salons() -> tuple[CatalogMaster, CatalogMaster]:
    """Два салона, по мастеру в каждом, одна запись — как на пилоте.

    Два тенанта нарочно: односалонная фикстура зеленела бы и на
    коде, который просто подставил бы «текущего» тенанта.
    """

    masters: list[CatalogMaster] = []
    for idx, (slug, master_name) in enumerate((("s1", "Анна"), ("s2", "Борис"))):
        tenant = Tenant.objects.create(
            slug=f"drf1608-{slug}", name=f"Салон {slug}", timezone="Europe/Moscow"
        )
        bot_user = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id=f"drf1608-{slug}",
            chat_id=f"drf1608-{slug}",
        )
        master = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=1608 + idx,
            external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
            name=master_name,
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        service = CatalogService.all_tenants.create(
            tenant=tenant,
            external_id=1608 + idx,
            external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
            slug=f"srv-{slug}",
            name="Маникюр",
            duration_min=60,
            is_active=True,
        )
        visit_at = (dj_timezone.now().astimezone(MSK) + timedelta(days=14, hours=idx)).replace(
            minute=0, second=0, microsecond=0
        )
        BookingRequest.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            service=service,
            master=master,
            service_name=service.name,
            master_name=master.name,
            client_name="Клиент",
            client_phone="+7-000",
            visit_at=visit_at,
            duration_min=60,
            status=BookingRequest.Status.CONFIRMED,
            source="bot",
            booking_source="ai_direct",
            billable=True,
            billing_reason="ai_direct + confirmed",
            attribution_metadata={"actor_type": "customer", "created_by": "execute_confirm"},
        )
        masters.append(master)
    return masters[0], masters[1]


def _master_filter_option_ids(body: str) -> set[str]:
    """Идентификаторы мастеров, предложенные боковым фильтром «Мастер»."""

    return set(re.findall(r"master__id__exact=([0-9a-fA-F-]+)", body))


@pytest.mark.parametrize("mode", ["strict", "audit"])
def test_master_filter_lists_every_master(superuser_client: Client, settings, mode: str) -> None:
    """Фильтр «Мастер» показывает всех мастеров базы в обоих режимах."""

    settings.STRICT_TENANT_SCOPE = "strict"
    anna, boris = _seed_two_salons()

    # Положительная стража: пустой фильтр ниже — улика только потому,
    # что мастера в базе заведомо есть.
    masters_in_db = CatalogMaster.all_tenants.count()
    assert masters_in_db == 2, "фикстура обязана завести ровно двух мастеров"

    settings.STRICT_TENANT_SCOPE = mode
    response = superuser_client.get(CHANGELIST_URL)

    assert response.status_code == 200, (
        f"режим {mode}: страница записей должна открываться, получено {response.status_code}"
    )
    body = response.content.decode()

    option_ids = _master_filter_option_ids(body)
    assert option_ids == {str(anna.pk), str(boris.pk)}, (
        f"режим {mode}: в базе {masters_in_db} мастеров, "
        f"фильтр «Мастер» предложил {len(option_ids)}"
    )
    # Имена — чтобы вариант был именно мастером, а не пустой ссылкой.
    assert "Анна" in body and "Борис" in body


def test_master_filter_actually_filters(superuser_client: Client, settings) -> None:
    """Выбранный в фильтре мастер сужает список, а не обнуляет его.

    Парная положительная проверка к тесту выше: непустой список
    вариантов бесполезен, если переход по варианту отдаёт пусто.
    """

    settings.STRICT_TENANT_SCOPE = "strict"
    anna, boris = _seed_two_salons()

    response = superuser_client.get(CHANGELIST_URL, {"master__id__exact": str(anna.pk)})

    assert response.status_code == 200
    body = response.content.decode()
    assert "Анна" in body
    assert f"Борис ({boris.external_id})" not in body
