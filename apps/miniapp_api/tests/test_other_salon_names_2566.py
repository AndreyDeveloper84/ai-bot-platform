"""DRF-2566 — у записи чужого салона названы мастер и услуга.

После DRF-2436 (часть A) Mini App открывает запись человека в любом его салоне,
но ``_proxy_catalog_refs`` искал мастера и услугу в каталоге салона ЗАПРОСА (Mini
App узнаёт человека под одним салоном — ``MAX_BOT_TENANT_SLUG``). Запись в
другом салоне приходила с пустыми ``master_name`` и ``service_name``.

Пара, которая обязана различаться по салону и совпадать по результату: запись
СВОЕГО салона и запись ДРУГОГО — у обеих мастер и услуга названы именами своего
каталога. «Поле непустое» только на своей записи проходило и при дефекте.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.booking.models import RemoteBookingProxy
from apps.miniapp_api.tests.test_person_owns_booking_2436 import (  # noqa: F401 — autouse
    AYLA_UID,
    ME,
    _get,
    _identity,
    _settings,
)
from apps.miniapp_api.tests.test_recent_activity_mirror import _make_master, _make_service
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def home() -> Tenant:
    return Tenant.objects.create(slug="home-2436", name="Формула тела", address="ул. Домашняя, 1")


@pytest.fixture
def lumina() -> Tenant:
    return Tenant.objects.create(slug="lumina-2566", name="Люмина", address="ул. Светлая, 7")


def _visit(tenant: Tenant, bot_user, *, master, service) -> RemoteBookingProxy:
    start = timezone.now() + timedelta(days=4)
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=uuid.uuid4(),
        tenant=tenant,
        bot_user=bot_user,
        start_at=start,
        end_at=start + timedelta(hours=1),
        status="confirmed",
        service_id=service.ayla_service_id,
        specialist_id=master.id,
    )


class TestNamesComeFromTheBookingsOwnSalon:
    def test_own_and_other_salon_both_name_master_and_service(self, client, home, lumina) -> None:
        me_home = _identity(home, ME, AYLA_UID)
        me_lumina = _identity(lumina, ME, AYLA_UID)
        own = _visit(
            home,
            me_home,
            master=_make_master(home, name="Ольга"),
            service=_make_service(home, name="Маникюр", ayla_service_id=uuid.uuid4()),
        )
        other = _visit(
            lumina,
            me_lumina,
            master=_make_master(lumina, name="Марина"),
            service=_make_service(
                lumina, name="Массаж шейно-воротниковой зоны", ayla_service_id=uuid.uuid4()
            ),
        )

        got = {}
        for proxy in (own, other):
            resp = _get(client, f"/api/v1/customer/bookings/{proxy.appointment_id}")
            assert resp.status_code == 200, resp.content
            booking = resp.json()["booking"]
            got[str(proxy.tenant.slug)] = (booking["master_name"], booking["service_name"])

        assert got == {
            "home-2436": ("Ольга", "Маникюр"),
            "lumina-2566": ("Марина", "Массаж шейно-воротниковой зоны"),
        }

    def test_a_master_of_the_request_salon_is_not_borrowed(self, client, home, lumina) -> None:
        # Мастер с тем же каталожным id в салоне запроса не должен «подставиться»
        # чужой записи: имена берутся из каталога салона записи и только оттуда.
        me_home = _identity(home, ME, AYLA_UID)
        me_lumina = _identity(lumina, ME, AYLA_UID)
        lumina_master = _make_master(lumina, name="Марина")
        _make_master(home, name="Ольга")
        other = _visit(
            lumina,
            me_lumina,
            master=lumina_master,
            service=_make_service(lumina, name="Массаж", ayla_service_id=uuid.uuid4()),
        )
        assert me_home is not None

        booking = _get(client, f"/api/v1/customer/bookings/{other.appointment_id}").json()[
            "booking"
        ]

        assert booking["master_name"] == "Марина"
