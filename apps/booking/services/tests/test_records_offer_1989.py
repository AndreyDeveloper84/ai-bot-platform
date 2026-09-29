"""Повтор визита: непродаваемое предложение — свой статус с причиной (DRF-1989).

До DRF-1989 ребро с ``sellable=false`` давало два неправдивых ответа:

* слоты отказали → «Эту услугу сейчас не оказывают» — услугу оказывают, её
  нельзя купить, пока у мастера нет цены;
* слоты открыты (каталог до полной выкладки) → «сейчас — 0 ₽».

Теперь оба пути — ``offer_not_sellable`` с причиной ребра, и цены 0 ₽ нет.
"""

from __future__ import annotations

import pytest

from apps.booking.services import records
from apps.booking.services.tests.test_records import FakeClient, _BotUser, _intent, _record
from apps.integrations.ayla.booking_client import BookingBadRequestError

UNSELLABLE_EDGE = {"price": "0.00", "sellable": False, "unsellable_reason": "price_below_minimum"}


@pytest.fixture
def patch_client(monkeypatch):
    def _install(client: FakeClient) -> FakeClient:
        monkeypatch.setattr(records, "get_ayla_booking_client", lambda: client)
        return client

    return _install


def test_refused_slots_with_an_unsellable_edge_name_the_offer(patch_client) -> None:
    patch_client(
        FakeClient(
            intent=_intent(),
            detail=_record(),
            slots_error=BookingBadRequestError(
                "http_404_NOT_FOUND", status_code=404, code="NOT_FOUND"
            ),
            edges=[dict(UNSELLABLE_EDGE)],
        )
    )

    result = records.prepare_repeat(bot_user=_BotUser(), appointment_id="a1")

    assert (result.status, result.details.get("reason")) == (
        "offer_not_sellable",
        "price_below_minimum",
    )


def test_open_slots_with_an_unsellable_edge_do_not_quote_zero(patch_client) -> None:
    patch_client(
        FakeClient(intent=_intent(2500.0), detail=_record(), edges=[dict(UNSELLABLE_EDGE)])
    )

    result = records.prepare_repeat(bot_user=_BotUser(), appointment_id="a1")

    assert (result.status, result.details.get("reason")) == (
        "offer_not_sellable",
        "price_below_minimum",
    )
    assert result.current_price is None
