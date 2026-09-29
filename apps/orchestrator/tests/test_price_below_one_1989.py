"""Витрина услуг в чате: цена ниже 1 ₽ не рисуется (DRF-1989).

Правило было «> 0»: 0.00 не рисовалось, а 0.50 уходило строкой «от 0.5 ₽».
Каталог не продаёт предложение дешевле 1 ₽ (DRF-1962), и такая цена не
цена, а незаполненное поле. Только показ: поле ``price_from`` не меняется.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.discovery import execute_catalog_tool
from apps.orchestrator.tests.test_catalog_surface import _salon, _service

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_price_below_one_rouble_is_not_shown() -> None:
    tenant = _salon("nord-1989a", "Nord1989a", city="Пенза")
    _service(tenant, "Пилинг 1989", price="0.50", duration=30)

    reply = execute_catalog_tool("show_services", {"salon": "nord1989a"}, said="что в Nord1989a")

    assert reply is not None
    assert "Пилинг 1989" in reply.text
    assert "от 0.5 ₽" not in reply.text
    assert "от 0 ₽" not in reply.text


def test_a_real_price_is_still_shown() -> None:
    """Положительная стража: цена от 1 ₽ — прежняя строка."""
    tenant = _salon("nord-1989b", "Nord1989b", city="Пенза")
    _service(tenant, "Массаж 1989", price="1500", duration=45)

    reply = execute_catalog_tool("show_services", {"salon": "nord1989b"}, said="что в Nord1989b")

    assert reply is not None
    assert "Массаж 1989 — от 1500 ₽ · 45 мин" in reply.text
