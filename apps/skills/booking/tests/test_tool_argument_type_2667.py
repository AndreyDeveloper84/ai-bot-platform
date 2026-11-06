"""DRF-2667 — аргумент инструмента от модели не строкой не доезжает никуда.

Модель по построению может прислать словарь вместо строки. До правки
``str(arguments.get(...) or "")`` превращал ``{"a": 1}`` в ``"{'a': 1}"``:
телефон и причина ложились в отложенное действие (а оттуда — в запись и в
событие отмены), имя получателя — в платёж, id мастера и услуги уходили
параметрами в чтения каталога.

Узлы проверяют свойство, а не форму отказа: словарь не оказывается ни в
строке базы, ни в вызове наружу. Рядом с каждым — страж со строкой, чтобы
«ничего не легло» не зеленело на пустоте.
"""

# Фикстуры ``tenant`` / ``bot_user`` импортированы из test_tools и принимаются
# тестами параметрами — это не переопределение (F811).
# ruff: noqa: F811

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import patch

import pytest

from apps.booking.models import PendingBookingAction
from apps.catalog.models import CatalogMaster, CatalogService
from apps.integrations.ayla.booking_client import AylaBookingClient
from apps.integrations.ayla_payments import CreatePaymentResult, reset_ayla_payments_client
from apps.skills.booking.provider import AylaYClientsAdapter
from apps.skills.booking.tests.test_tools import FakeYClients, bot_user, tenant  # noqa: F401 — фикстуры
from apps.skills.booking.tests.test_tools_cancel import FakeClient, _make_booking
from apps.skills.booking.tools import (
    buy_certificate,
    calc_price,
    cancel_booking,
    confirm_booking,
    show_slots,
)
from apps.tenancy.context import tenant_scope
from tests.support.catalog_mirror import sync_shaped

pytestmark = pytest.mark.django_db

OBJECT = {"a": 1}
LEAK = "{'a': 1}"


def _rows_text() -> str:
    return " ".join(str(r.payload) for r in PendingBookingAction.all_tenants.all())


def _confirm(tenant, bot_user, **args: Any):
    arguments = {"master_id": 11, "service_id": 22, "slot_datetime": "2026-05-20T14:00:00"}
    arguments.update(args)
    with tenant_scope(tenant):
        return confirm_booking(
            client=FakeYClients(),
            arguments=arguments,
            tenant=tenant,
            bot_user=bot_user,
            allowed_master_ids={11},
            allowed_service_ids={22},
            master_lookup={},
            service_lookup={22: "Массаж"},
        )


class TestConfirmBooking:
    @pytest.mark.parametrize("field", ["client_phone", "slot_datetime"])
    def test_an_object_never_lands_in_the_pending_row(self, tenant, bot_user, field) -> None:
        """Сначала строкой — след есть; потом словарём — следа словаря нет."""
        _confirm(tenant, bot_user, client_phone="+79990000000")
        _confirm(tenant, bot_user, **{field: OBJECT})
        rows = _rows_text()
        assert "+79990000000" in rows
        assert LEAK not in rows


class TestCancelBooking:
    def _cancel(self, tenant, bot_user, reason: Any, *, yc_id: int) -> None:
        _make_booking(tenant, bot_user, yc_id=yc_id)
        with tenant_scope(tenant):
            cancel_booking(
                client=FakeClient(),
                arguments={"record_id": yc_id, "reason": reason},
                tenant=tenant,
                bot_user=bot_user,
            )

    def test_an_object_reason_never_lands_in_the_pending_row(self, tenant, bot_user) -> None:
        self._cancel(tenant, bot_user, "ребёнок заболел", yc_id=555)
        self._cancel(tenant, bot_user, OBJECT, yc_id=556)
        rows = _rows_text()
        assert "ребёнок заболел" in rows
        assert LEAK not in rows


class TestBuyCertificate:
    @pytest.fixture(autouse=True)
    def _payments(self, settings):
        settings.STRICT_TENANT_SCOPE = "audit"
        settings.AYLA_PAYMENTS_TEST_MODE = True
        settings.AYLA_BASE_URL = ""
        settings.AYLA_INTERNAL_API_TOKEN = ""
        settings.CERTIFICATE_PAYMENT_ENABLED = True
        reset_ayla_payments_client()
        yield
        reset_ayla_payments_client()

    def _buy(self, tenant, bot_user, **args: Any) -> list[dict[str, Any]]:
        sent: list[dict[str, Any]] = []

        def _create(**kwargs):
            sent.append(kwargs)
            return CreatePaymentResult(
                payment_id="pay-stub",
                checkout_url="https://yoomoney.test/checkout/x",
                status="pending",
                test=True,
            )

        arguments = {"amount_rub": 1500}
        arguments.update(args)
        with patch(
            "apps.integrations.ayla_payments.client.AylaPaymentsClient.create_payment",
            side_effect=_create,
        ):
            with tenant_scope(tenant):
                buy_certificate(tenant=tenant, bot_user=bot_user, arguments=arguments)
        return sent

    @pytest.mark.parametrize("field", ["recipient_name", "buyer_email"])
    def test_an_object_is_never_sent_to_payments(self, tenant, bot_user, field) -> None:
        sent = self._buy(tenant, bot_user, recipient_name="Оля")
        sent += self._buy(tenant, bot_user, **{field: OBJECT})
        assert sent[0]["recipient_name"] == "Оля"
        assert LEAK not in str(sent)


class _Catalog:
    """Каталог за адаптером: пишет, с чем к нему пришли."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_specialist_service_edges(self, **kw):
        self.calls.append(("edges", kw))
        return []

    def get_available_dates(self, **kw):
        self.calls.append(("dates", kw))
        return ["2026-10-01"]

    def get_available_times(self, **kw):
        self.calls.append(("slots", kw))
        return []


def _adapter(catalog: _Catalog, tenant) -> AylaYClientsAdapter:
    # cast: у заглушки только три чтения — те, до которых доходят calc_price и
    # show_slots. Запись, отмена, перенос и списки протокола ``AylaBookingClient``
    # не реализованы: позови их узел — упадёт AttributeError, а не пройдёт молча.
    return AylaYClientsAdapter(
        client=cast(AylaBookingClient, catalog), external_user_id="x", tenant=tenant
    )


class TestKeysOnTheLivePath:
    """BOOKING_VIA_AYLA_REST=true, как на пилоте: ``_coerce_id`` делает ``str()``."""

    @pytest.fixture(autouse=True)
    def _ayla(self, settings):
        settings.BOOKING_VIA_AYLA_REST = True

    def _calc(self, tenant, catalog: _Catalog, master_id: Any, *, n: int) -> None:
        sid = uuid.uuid4()
        CatalogService.all_tenants.create(
            tenant=tenant,
            external_id=n,
            external_updated_at=datetime.now(tz=timezone.utc),
            slug=f"s-2667-{n}",
            name="S",
            duration_min=60,
            is_active=True,
            ayla_service_id=sid,
        )
        adapter = _adapter(catalog, tenant)
        with tenant_scope(tenant):
            calc_price(
                tenant=tenant,
                arguments={"service_id": str(sid), "master_id": master_id},
                allowed_service_ids={str(sid)},
                service_lookup={},
                client=adapter,
            )

    def test_calc_price_never_sends_an_object_as_the_master(self, tenant) -> None:
        catalog = _Catalog()
        self._calc(tenant, catalog, str(uuid.uuid4()), n=1)
        self._calc(tenant, catalog, OBJECT, n=2)
        calls = catalog.calls
        assert [name for name, _ in calls][:1] == ["edges"]
        assert LEAK not in str(calls)

    def _slots(self, tenant, catalog: _Catalog, service_id: Any) -> None:
        adapter = _adapter(catalog, tenant)
        # Мастер из allow-set — мастер ростера, строка зеркала у него есть;
        # без неё адаптер спросил бы каталог, чей он (DRF-2677).
        master = str(
            sync_shaped(
                CatalogMaster.all_tenants.create(
                    tenant=tenant,
                    external_id=CatalogMaster.all_tenants.count() + 1,
                    external_updated_at=datetime.now(tz=timezone.utc),
                    name="M",
                )
            ).pk
        )
        with tenant_scope(tenant):
            show_slots(
                client=adapter,
                arguments={
                    "master_id": master,
                    "service_id": service_id,
                    "date_from": "2026-10-01",
                },
                tenant_id=str(tenant.id),
                allowed_master_ids={master},
            )

    def test_show_slots_never_sends_an_object_as_the_service(self, tenant) -> None:
        catalog = _Catalog()
        self._slots(tenant, catalog, str(uuid.uuid4()))
        self._slots(tenant, catalog, OBJECT)
        calls = catalog.calls
        assert calls
        assert LEAK not in str(calls)


class TestNumbersAreText:
    """Телефон JSON-числом — годное значение, не отказ: отказ только контейнерам."""

    def test_a_numeric_phone_is_kept(self, tenant, bot_user) -> None:
        _confirm(tenant, bot_user, client_phone=79990000000)
        rows = _rows_text()
        assert "79990000000" in rows
