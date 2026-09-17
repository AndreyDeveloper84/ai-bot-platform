"""Локальная запись и перенос: продаваемость спрашивает запись, не перенос (DRF-1989, R6).

* Новая локальная запись на непродаваемое ребро — ``offer_not_sellable`` с
  текстом причины; до правки она проходила (проверялось только наличие
  строки ``MasterService``). Статус в Mini App — 409.
* **Решение владельца R6** (``OWNER_QUESTIONS.md``): перенос существующей
  записи разрешён и при ``price_below_minimum``, снимок записи не меняется.
  Оба локальных переноса — ``commit_reschedule`` и
  ``reschedule_customer_booking`` (второй создаёт новую строку через
  ``create_customer_booking``) — закреплены тестом: это не изменение
  поведения, а его фиксация, чтобы проверка записи не задела перенос.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.booking.models import BookingRequest
from apps.booking.services.create import BookingCreateError
from apps.booking.services.reschedule import reschedule_customer_booking
from apps.booking.services.transitions import commit_reschedule, request_reschedule
from apps.booking.tests.test_master_sale_gate_drf1548 import (
    _create,
    _far_future_monday,
    _master,
    _offer,
)
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.integrations.ayla.tests.test_offer_refusal_1989 import CLIENT_PRICE
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="drf1989-local", name="DRF-1989", timezone="Europe/Moscow")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="1989",
        chat_id="1989",
        display_name="Мария",
    )


@pytest.fixture
def linked_master(tenant: Tenant) -> CatalogMaster:
    return _master(tenant, ext=1, name="Анна")


@pytest.fixture
def service(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=42,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        slug="manicure",
        name="Маникюр",
        duration_min=60,
        is_active=True,
    )


@pytest.fixture
def offered(tenant: Tenant, linked_master: CatalogMaster, service: CatalogService) -> None:
    _offer(tenant, linked_master, service)


def _stop_selling(tenant: Tenant, master: CatalogMaster, service: CatalogService) -> None:
    MasterService.all_tenants.filter(tenant=tenant, master=master, service=service).update(
        sellable=False, unsellable_reason="price_below_minimum"
    )


def _snapshot(row: BookingRequest) -> tuple:
    return (row.service_id, row.master_id, row.service_name, row.master_name, row.duration_min)


def test_local_create_on_an_unsellable_offer_is_a_named_refusal(
    tenant, bot_user, linked_master, service, offered
) -> None:
    _stop_selling(tenant, linked_master, service)

    with pytest.raises(BookingCreateError) as exc:
        _create(tenant, bot_user, service, linked_master)

    assert (exc.value.slug, exc.value.detail) == ("offer_not_sellable", CLIENT_PRICE)


def test_the_create_status_table_maps_the_refusal() -> None:
    from apps.miniapp_api.views import _ERROR_SLUG_TO_STATUS

    assert _ERROR_SLUG_TO_STATUS.get("offer_not_sellable") == 409


def test_commit_reschedule_onto_an_unsellable_offer_is_allowed(
    tenant, bot_user, linked_master, service, offered
) -> None:
    """R6: перенос не спрашивает продаваемость; снимок новой строки — прежний."""
    booking = _create(tenant, bot_user, service, linked_master)
    before = _snapshot(booking)
    _stop_selling(tenant, linked_master, service)
    request_reschedule(
        booking,
        actor=bot_user,
        new_master_id=str(linked_master.id),
        new_service_id=str(service.id),
        new_visit_at=_far_future_monday(16),
    )
    booking.refresh_from_db()

    old, new = commit_reschedule(booking, actor=bot_user)

    assert (old.status, new.status) == (
        BookingRequest.Status.RESCHEDULED,
        BookingRequest.Status.CONFIRMED,
    )
    assert _snapshot(new) == before


def test_reschedule_service_onto_an_unsellable_offer_is_allowed(
    tenant, bot_user, linked_master, service, offered
) -> None:
    """R6: второй перенос идёт через ``create_customer_booking`` — и не спрашивает тоже."""
    booking = _create(tenant, bot_user, service, linked_master)
    before = (_snapshot(booking), booking.commercial_identity_snapshot)
    _stop_selling(tenant, linked_master, service)

    new = reschedule_customer_booking(
        tenant=tenant,
        bot_user=bot_user,
        old_booking_id=str(booking.id),
        new_visit_at=_far_future_monday(16),
    )

    assert new.status == BookingRequest.Status.CONFIRMED
    assert (_snapshot(new), new.commercial_identity_snapshot) == before
