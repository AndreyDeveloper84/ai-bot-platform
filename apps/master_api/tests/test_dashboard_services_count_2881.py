"""DRF-2881 — «Сегодня» знает число услуг мастера, а не гадает по специализации.

Живой проход владельца 07.10: у solo-мастера две настроенные услуги, а «Сегодня»
говорит «Вам ещё не назначили услуги». Экран выводил «услуг нет» из пустой
``CatalogMaster.specialization`` — поля, которое не заполняет ни один путь
синхронизации (на пилоте пусто у 41 мастера из 41). Фразу видел каждый мастер
без записей на сегодня.

Теперь бот отдаёт ``master.services_count`` — тот же счёт, что у списка услуг
кабинета: связи с действующей услугой своего салона.

* n1 — услуг нет → 0;
* n2 — действующая услуга считается, и специализация на счёт не влияет;
* n3 — выключенная услуга не считается;
* n4 — услуга другого мастера и другого салона не считается;
* n5 — число доезжает в ответ ручки дашборда.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from django.test import Client
from django.urls import reverse
from django.utils import timezone as dj_timezone

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.master_api.services import dashboard as ds
from apps.master_api.tests.conftest import init_data_header, make_master
from apps.tenancy.models import Tenant

NOW = datetime(2026, 10, 7, 14, 0, tzinfo=timezone.utc)


def _service(tenant: Tenant, name: str, *, is_active: bool = True) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        ayla_service_id=uuid4(),
        external_id=None,
        external_updated_at=dj_timezone.now(),
        name=name,
        duration_min=60,
        is_active=is_active,
    )


def _assign(master: CatalogMaster, service: CatalogService) -> MasterService:
    return MasterService.all_tenants.create(tenant=master.tenant, master=master, service=service)


def _count(master: CatalogMaster) -> int:
    return ds.build_dashboard(master, NOW).to_dict()["master"]["services_count"]


class TestTheServicesCount:
    def test_n1_no_services_is_zero(self, accepted_master: CatalogMaster) -> None:
        assert _count(accepted_master) == 0

    def test_n2_an_active_service_counts_and_specialization_does_not_matter(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        """Ровно случай владельца: услуги есть, специализация пуста."""
        CatalogMaster.all_tenants.filter(pk=accepted_master.pk).update(specialization="")
        accepted_master.refresh_from_db()
        _assign(accepted_master, _service(tenant, "Классический массаж"))
        _assign(accepted_master, _service(tenant, "Массаж спины"))

        assert accepted_master.specialization == ""
        assert _count(accepted_master) == 2

    def test_n2_a_filled_specialization_does_not_invent_services(
        self, accepted_master: CatalogMaster
    ) -> None:
        CatalogMaster.all_tenants.filter(pk=accepted_master.pk).update(specialization="Массаж")
        accepted_master.refresh_from_db()

        assert _count(accepted_master) == 0

    def test_n3_a_switched_off_service_does_not_count(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        _assign(accepted_master, _service(tenant, "Классический массаж"))
        _assign(accepted_master, _service(tenant, "Снятая услуга", is_active=False))

        assert _count(accepted_master) == 1

    def test_n4_other_masters_and_other_salons_do_not_count(
        self, tenant: Tenant, other_tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        colleague = make_master(tenant, name="Коллега")
        _assign(colleague, _service(tenant, "Услуга коллеги"))
        stranger = make_master(other_tenant, name="Чужой")
        _assign(stranger, _service(other_tenant, "Услуга чужого салона"))
        _assign(accepted_master, _service(tenant, "Своя услуга"))

        # Положительная пара: чужие услуги существуют и посчитаны у своих хозяев.
        assert (_count(colleague), _count(stranger)) == (1, 1)
        assert _count(accepted_master) == 1


class TestTheEndpoint:
    def test_n5_the_count_reaches_the_dashboard_answer(
        self, client: Client, bot_user: BotUser, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        _assign(accepted_master, _service(tenant, "Классический массаж"))

        resp = client.get(
            reverse("master_api:dashboard"), HTTP_AUTHORIZATION=init_data_header("12345")
        )

        assert resp.status_code == 200, resp.content
        assert resp.json()["master"]["services_count"] == 1
