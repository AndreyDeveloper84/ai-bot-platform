"""DRF-2705, мини-апп: слоты по услуге, чья длительность разрешается из шаблона.

Путь целиком, как он идёт в жизни: строка ``salon-services`` с провода →
``_parse_salon_service`` → ``upsert_salon_services`` → ``CatalogService`` →
``GET /customer/slots``.

До правки зеркало брало сырое ``duration_minutes``. У услуги, которая берёт
длительность из шаблона, оно ``null`` — в зеркало ложилось ``None``, и слоты
отвечали ``409 service_unbookable`` («service has no duration configured») на
услугу, которую каталог продаёт.

Сам отказ остаётся и здесь закреплён с обеих сторон: длительность есть — слоты
есть; разрешать нечем — ``409``. Раньше вторую сторону не держал ни один узел.
Намеренно ли этот отказ стоит раньше ветки пути через Ayla — открытый вопрос
листа, эти узлы его не решают: они идут локальным путём.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from urllib.parse import urlencode

import pytest
from django.urls import reverse

from apps.catalog.models import CatalogService, MasterService
from apps.catalog.services.http_client import _parse_salon_service
from apps.catalog.services.upserter import upsert_salon_services
from apps.miniapp_api.tests import test_views as _views_module

# Фикстуры и помощники соседнего модуля — присваиванием, как в узлах DRF-2678.
_init_data_header = _views_module._init_data_header
_bot_token = _views_module._bot_token
bot_user = _views_module.bot_user
master = _views_module.master
tenant = _views_module.tenant
working_hours = _views_module.working_hours

pytestmark = pytest.mark.django_db

AYLA_SERVICE_ID = "5a100000-0000-4000-8000-000000002705"
_ABSENT = object()


def _row(*, raw: Any, resolved: Any = _ABSENT) -> dict[str, Any]:
    """Строка по примеру §1 контракта каталога. Идентификаторы выдуманные."""
    row: dict[str, Any] = {
        "id": AYLA_SERVICE_ID,
        "tenant": "7e000000-0000-4000-8000-000000002705",
        "template": "7e3b0000-0000-4000-8000-000000002705",
        "category": None,
        "name": "Маникюр классический",
        "duration_minutes": raw,
        "base_price": None,
        "requires_health_check": False,
        "is_active": True,
        "source": "manual",
        "goals": [],
        "created_at": "2026-07-09T18:31:00Z",
        "updated_at": "2026-07-09T18:31:00Z",
    }
    if resolved is not _ABSENT:
        row["resolved_duration"] = resolved
    return row


def _mirror(tenant, master, row: dict[str, Any]) -> CatalogService:
    """Строка с провода → зеркало, теми же функциями, что у синхронизации."""
    result = upsert_salon_services(tenant, [_parse_salon_service(row)])
    assert result.errors == [], result.errors
    assert result.created == 1
    service = CatalogService.all_tenants.get(tenant=tenant, ayla_service_id=AYLA_SERVICE_ID)
    # Исполнитель есть: иначе отказ пришёл бы от другого гейта с тем же кодом.
    MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
    return service


def _slots(client, master, service):
    target = date.today() + timedelta(days=30)
    url = (
        reverse("miniapp_api:slots")
        + "?"
        + urlencode(
            {
                "master_id": str(master.id),
                "service_id": str(service.id),
                "date_from": target.isoformat(),
                "date_to": target.isoformat(),
            }
        )
    )
    return client.get(url, HTTP_AUTHORIZATION=_init_data_header("12345"))


def test_a_service_timed_by_its_template_gets_slots(
    client, bot_user, tenant, master, working_hours
):
    """Живой случай листа: у салона ``null``, каталог разрешил 60 из шаблона."""
    service = _mirror(tenant, master, _row(raw=None, resolved=60))
    assert service.duration_min == 60

    resp = _slots(client, master, service)

    assert resp.status_code == 200, resp.content
    assert len(resp.json()["slots"]) > 0


def test_the_resolved_duration_is_what_the_slots_are_cut_by(
    client, bot_user, tenant, master, working_hours
):
    """Разрешённое, а не сырое: при 60 мин в окно 10:00–19:00 влезает больше
    начал, чем при 240. Числа разные, чтобы узел не прошёл совпадением."""
    short = _mirror(tenant, master, _row(raw=240, resolved=60))
    assert short.duration_min == 60
    n_short = len(_slots(client, master, short).json()["slots"])

    CatalogService.all_tenants.filter(id=short.id).update(duration_min=240)
    n_long = len(_slots(client, master, short).json()["slots"])

    assert n_short > n_long > 0


def test_nothing_to_resolve_is_still_refused(client, bot_user, tenant, master, working_hours):
    """Обратная сторона, которую раньше не держал ни один узел: каталог прислал
    ключ и в нём ``null`` — разрешать нечем, длительности нет, слотов нет."""
    service = _mirror(tenant, master, _row(raw=None, resolved=None))
    assert service.duration_min is None

    resp = _slots(client, master, service)

    assert resp.status_code == 409, resp.content
    assert resp.json()["error"] == "service_unbookable"


def test_an_older_catalog_still_reads_the_raw_field(
    client, bot_user, tenant, master, working_hours
):
    """Каталог старше поля ключа не шлёт вовсе: читаем сырое, как раньше."""
    service = _mirror(tenant, master, _row(raw=45))
    assert service.duration_min == 45

    resp = _slots(client, master, service)

    assert resp.status_code == 200, resp.content
    assert len(resp.json()["slots"]) > 0


def test_an_older_catalog_with_no_salon_duration_is_refused(
    client, bot_user, tenant, master, working_hours
):
    """Мир до правки A, зафиксированный как есть: ключа нет, сырое ``null`` —
    бот разрешить не может и отказывает. Это и был дефект; чинит его каталог,
    прислав ключ, — а не бот, угадав длительность."""
    service = _mirror(tenant, master, _row(raw=None))
    assert service.duration_min is None

    resp = _slots(client, master, service)

    assert resp.status_code == 409, resp.content
    assert resp.json()["error"] == "service_unbookable"
