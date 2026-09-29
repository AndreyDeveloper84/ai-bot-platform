"""Адаптер Ayla шлёт в каталог id профиля каталога (DRF-1933, часть 2).

Внутри бота мастер остаётся по первичному ключу зеркала: консьерж кладёт pk
карточки в ``native_master_id`` (``orchestrator/handoff.py``), колбэк
``book:pick_master:<pk>:<service>`` несёт его дальше, и локальные поиски
(рёбра, котировки, ворота здоровья) ищут по нему. Переводить этот ключ в
каталожный внутри бота — перенести дефект из исходящих во входящие.

Поэтому id каталога резолвится на границе — в ``AylaYClientsAdapter``, пятью
вызовами, которые ходят в каталог с ``specialist_id``: даты, окна, создание,
котировка, цена ребра. Решение главного окна 15.09.

* строка зеркала найдена по pk → её ``catalog_specialist_id``;
* id уже каталожный (так отдаёт ``get_staff``) → строка находится по колонке,
  уходит он же;
* колонка пуста → нейтральный ``YClientsSpecialistUnavailableError``
  (подкласс ``YClientsAPIError``: инструменты его ловят, человеку не 500),
  каталог не зовётся;
* строки нет вовсе → id уходит как есть: бот его не выдумывал, его прислал
  каталог.

Красный до правки: склеенное приглашение и соло (уходит pk), отказ (каталог
зовут). Зелёный в обе стороны: строка синка, каталожный id на входе,
неизвестный id.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, cast

import pytest

from apps.catalog.models import CatalogMaster
from apps.integrations.ayla.booking_client import AylaBookingRecord
from apps.skills.booking.provider import AylaYClientsAdapter, YClientsSpecialistUnavailableError
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

SERVICE = "0c5f0000-0000-4000-8000-00000000b001"


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="adapter-1933", name="Adapter 1933")


class _Fake:
    """Клиент Ayla: записывает, с каким ``specialist_id`` его позвали."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def get_available_dates(self, *, specialist_id: str, service_id: str) -> list[str]:
        self.sent.append(("get_available_dates", specialist_id))
        return []

    def get_available_times(self, *, specialist_id: str, date: str, service_id: str) -> list[Any]:
        self.sent.append(("get_available_times", specialist_id))
        return []

    def create_appointment(self, **kwargs: Any) -> AylaBookingRecord:
        self.sent.append(("create_appointment", kwargs["specialist_id"]))
        return AylaBookingRecord(appointment_id=str(uuid.uuid4()), raw={})

    def get_specialist_service_edges(self, *, specialist_id: str, service_id: str) -> list[dict]:
        self.sent.append(("get_specialist_service_edges", specialist_id))
        return [{"price": "1500.00", "duration_minutes": 60}]


def _adapter(fake: _Fake, tenant: Tenant | None) -> AylaYClientsAdapter:
    return AylaYClientsAdapter(
        client=cast(Any, fake),
        external_user_id="bot:max:1933",
        client_id="client-1933",
        tenant=tenant,
    )


CALLS = {
    "dates": lambda a, staff: a.get_available_dates(staff_id=staff, service_ids=[SERVICE]),
    "times": lambda a, staff: a.get_available_times(
        staff_id=staff, date="2026-09-20", service_ids=[SERVICE]
    ),
    "create": lambda a, staff: a.create_record(
        staff_id=staff,
        services=[SERVICE],
        datetime="2026-09-20T12:00:00+03:00",
        client_phone="",
        client_name="Клиент",
    ),
    "quote": lambda a, staff: a.get_specialist_service_quote(staff_id=staff, service_id=SERVICE),
    "price": lambda a, staff: a.get_specialist_service_price(staff_id=staff, service_id=SERVICE),
}


def _row(tenant: Tenant, *, column: uuid.UUID | None, raw_id: uuid.UUID | None) -> CatalogMaster:
    row = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=CatalogMaster.all_tenants.filter(tenant=tenant).count() + 1,
        external_updated_at=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
        name="Анна",
        raw={"id": str(raw_id)} if raw_id else {},
    )
    CatalogMaster.all_tenants.filter(pk=row.pk).update(catalog_specialist_id=column)
    row.refresh_from_db()
    return row


class TestPkOfTheMirrorRowIsTranslatedAtTheBoundary:
    @pytest.mark.parametrize("kind", ["invite_glued", "solo"])
    @pytest.mark.parametrize("call", sorted(CALLS))
    def test_the_catalog_receives_the_catalog_id(self, tenant, call, kind):
        catalog_id = uuid.uuid4()
        row = _row(tenant, column=catalog_id, raw_id=catalog_id if kind == "invite_glued" else None)
        assert row.pk != catalog_id
        fake = _Fake()

        CALLS[call](_adapter(fake, tenant), str(row.pk))

        assert [sid for _name, sid in fake.sent] == [str(catalog_id)]


class TestAnEmptyColumnIsANeutralRefusal:
    @pytest.mark.parametrize("call", sorted(CALLS))
    def test_no_catalog_call_and_a_neutral_error(self, tenant, call):
        row = _row(tenant, column=None, raw_id=None)
        fake = _Fake()

        with pytest.raises(YClientsSpecialistUnavailableError):
            CALLS[call](_adapter(fake, tenant), str(row.pk))
        assert fake.sent == []


class TestIdsTheBotDidNotMintPassUnchanged:
    @pytest.mark.parametrize("call", sorted(CALLS))
    def test_a_synced_row_still_sends_its_primary_key(self, tenant, call):
        row = _row(tenant, column=None, raw_id=None)
        CatalogMaster.all_tenants.filter(pk=row.pk).update(catalog_specialist_id=row.pk)
        fake = _Fake()

        CALLS[call](_adapter(fake, tenant), str(row.pk))

        assert [sid for _name, sid in fake.sent] == [str(row.pk)]

    @pytest.mark.parametrize("call", sorted(CALLS))
    def test_a_catalog_id_from_get_staff_is_sent_as_is(self, tenant, call):
        catalog_id = uuid.uuid4()
        _row(tenant, column=catalog_id, raw_id=catalog_id)
        fake = _Fake()

        CALLS[call](_adapter(fake, tenant), str(catalog_id))

        assert [sid for _name, sid in fake.sent] == [str(catalog_id)]

    def test_an_id_no_mirror_row_knows_is_sent_as_is(self, tenant):
        unknown = str(uuid.uuid4())
        fake = _Fake()

        CALLS["times"](_adapter(fake, tenant), unknown)

        assert fake.sent == [("get_available_times", unknown)]


def test_the_quote_still_reads_the_edge_after_translation(tenant):
    """Положительная стража к «отказу» на тех же данных: котировка склеенной
    строки доходит до ребра и возвращает его цену и длительность."""
    catalog_id = uuid.uuid4()
    row = _row(tenant, column=catalog_id, raw_id=catalog_id)
    fake = _Fake()

    price, duration = _adapter(fake, tenant).get_specialist_service_quote(
        staff_id=str(row.pk), service_id=SERVICE
    )

    assert (price, duration) == (Decimal("1500.00"), 60)
    assert fake.sent == [("get_specialist_service_edges", str(catalog_id))]


class TestTheAdapterKnowsItsSalon:
    """MKT1: строка зеркала читается только в пределах салона. Фабрика
    привязывает адаптер к салону пользователя записи; без салона адаптер
    не ищет строку и отдаёт id как есть (названный предел, не умолчание)."""

    def test_the_factory_binds_the_booking_users_tenant(self, tenant, settings, monkeypatch):
        from apps.identity.models import BotUser
        from apps.skills.booking import provider

        settings.BOOKING_VIA_AYLA_REST = True
        monkeypatch.setattr(
            "apps.identity.services.ayla_link.ensure_ayla_link", lambda bot_user, trigger: None
        )
        # Клиент здесь не предмет: проверяется привязка к салону, а настоящий
        # клиент не стартует без AYLA_BASE_URL.
        monkeypatch.setattr(
            "apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: _Fake()
        )
        bot_user = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="1933", chat_id="1933"
        )

        adapter = provider.get_booking_provider(bot_user=bot_user)

        assert isinstance(adapter, AylaYClientsAdapter)
        assert adapter._tenant == tenant  # noqa: SLF001

    def test_an_unbound_adapter_does_not_look_the_row_up(self, tenant):
        catalog_id = uuid.uuid4()
        row = _row(tenant, column=catalog_id, raw_id=catalog_id)
        fake = _Fake()

        CALLS["times"](_adapter(fake, None), str(row.pk))

        assert fake.sent == [("get_available_times", str(row.pk))]
