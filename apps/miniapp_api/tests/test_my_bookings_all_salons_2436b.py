"""DRF-2436 часть B — «Мои записи» единым списком по всем салонам человека.

Решение владельца п.15 (``docs/OWNER_DECISIONS_2026-09-28.md``): «„Мои записи“ —
единый личный список пользователя по всем салонам Ayla. У каждой записи должно
быть понятно, в каком салоне и у какого мастера она создана.»

Три поверхности читали по одной личности и одному салону — список Mini App,
«ближайшая запись» на Главной и «мои записи» в чате бота. Теперь все три — по
человеку (все личности подписанного аккаунта), и у каждой записи — её салон.

Сторож — пара, которая ОБЯЗАНА различаться: две записи одного человека из
РАЗНЫХ салонов в одном ответе, с разными ``salon_name``. «Список непустой»
проходил и тогда, когда второй салон отфильтрован.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.booking.models import BookingRequest, RemoteBookingProxy
from apps.identity.models import BotUser
from apps.miniapp_api.tests.test_person_owns_booking_2436 import (  # noqa: F401 — autouse
    AYLA_UID,
    ME,
    STRANGER,
    _get,
    _identity,
    _settings,
)
from apps.miniapp_api.tests.test_recent_activity_mirror import _make_master, _make_service
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/customer/bookings/list"
HOME_URL = "/api/v1/customer/recent-activity"


@pytest.fixture
def home() -> Tenant:
    return Tenant.objects.create(
        slug="home-2436", name="Формула тела", address="ул. Домашняя, 1", timezone="Europe/Moscow"
    )


@pytest.fixture
def lumina() -> Tenant:
    return Tenant.objects.create(
        slug="lumina-2436b", name="Люмина", address="ул. Светлая, 7", timezone="Europe/Moscow"
    )


def _visit(tenant: Tenant, bot_user: BotUser, *, days: int, master_name: str) -> RemoteBookingProxy:
    start = timezone.now() + timedelta(days=days)
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=uuid.uuid4(),
        tenant=tenant,
        bot_user=bot_user,
        start_at=start,
        end_at=start + timedelta(hours=1),
        status="confirmed",
        service_id=_make_service(
            tenant, name="Услуга", ayla_service_id=uuid.uuid4()
        ).ayla_service_id,
        specialist_id=_make_master(tenant, name=master_name).id,
    )


def _by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {i["id"]: i for i in items}


class TestListIsOnePersonalListAcrossSalons:
    def test_two_salons_in_one_list_each_named(self, client, home, lumina) -> None:
        own = _visit(home, _identity(home, ME, AYLA_UID), days=3, master_name="Ольга")
        other = _visit(lumina, _identity(lumina, ME, AYLA_UID), days=5, master_name="Марина")

        items = _by_id(_get(client, LIST_URL).json()["items"])

        # Пара, которая обязана различаться: обе записи в одном списке, салоны разные.
        assert set(items) == {str(own.appointment_id), str(other.appointment_id)}
        assert items[str(own.appointment_id)]["salon_name"] == "Формула тела"
        assert items[str(other.appointment_id)]["salon_name"] == "Люмина"
        # Мастер назван каталогом салона записи, адрес — салона записи.
        assert items[str(other.appointment_id)]["master_name"] == "Марина"
        assert items[str(other.appointment_id)]["address"] == "ул. Светлая, 7"

    def test_another_persons_booking_is_not_in_my_list(self, client, home, lumina) -> None:
        mine = _visit(lumina, _identity(lumina, ME, AYLA_UID), days=3, master_name="Марина")
        _identity(home, ME, AYLA_UID)
        theirs = _visit(lumina, _identity(lumina, STRANGER), days=4, master_name="Марина")

        ids = set(_by_id(_get(client, LIST_URL).json()["items"]))

        # Положительная пара впереди: своя запись того же салона — в списке.
        assert str(mine.appointment_id) in ids
        assert str(theirs.appointment_id) not in ids


class TestNearestBookingCarriesItsOwnSalon:
    def test_nearest_in_another_salon_is_named_by_that_salon(self, client, home, lumina) -> None:
        _visit(home, _identity(home, ME, AYLA_UID), days=6, master_name="Ольга")
        nearest = _visit(lumina, _identity(lumina, ME, AYLA_UID), days=2, master_name="Марина")

        nb = _get(client, HOME_URL).json()["next_booking"]

        assert nb["booking_id"] == str(nearest.appointment_id)
        # Салон и адрес — записи, а не того, под которым Mini App узнал человека.
        assert nb["salon_name"] == "Люмина"
        assert nb["address"] == "ул. Светлая, 7"
        assert nb["master_name"] == "Марина"


class TestChatMyBookingsReadsThePerson:
    def _book(self, tenant: Tenant, bot_user: BotUser, *, days: int, service: str) -> None:
        proxy = _visit(tenant, bot_user, days=days, master_name="Мастер")
        BookingRequest.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            service_name=service,
            master_name="Мастер",
            client_name="Анна",
            client_phone="79991234567",
            comment=f"Bot booking | yclients_record_id={proxy.appointment_id}",
            source="bot",
            status=BookingRequest.Status.CONFIRMED,
        )

    def test_chat_lists_bookings_of_every_salon_of_the_person(self, home, lumina) -> None:
        from apps.skills.booking.tools import show_my_bookings

        me_home = _identity(home, ME, AYLA_UID)
        me_lumina = _identity(lumina, ME, AYLA_UID)
        stranger = _identity(lumina, STRANGER)
        self._book(home, me_home, days=3, service="Маникюр")
        self._book(lumina, me_lumina, days=4, service="Массаж")
        self._book(lumina, stranger, days=5, service="Чужая")

        # Навык вызывают под личностью ОДНОГО салона — как handoff в салон.
        result = show_my_bookings(client=None, tenant=home, bot_user=me_home)

        services = sorted(b.service_name for b in result.bookings)
        assert services == ["Маникюр", "Массаж"]
        # DRF-2569 / слова владельца п.2: у каждой строки — салон ЕЁ записи.
        lines = result.text.splitlines()[1:]
        assert any("Маникюр — мастер Мастер · Формула тела, " in ln for ln in lines), lines
        assert any("Массаж — мастер Мастер · Люмина, " in ln for ln in lines), lines
