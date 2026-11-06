"""DRF-2822 — причина «числа нет» доезжает до Mini App тем же ключом.

Статус общий с чатом: из одного ответа каталога. Экран текстового ввода
режима «Без чисел» не знает, поэтому в этом режиме причину не отдаёт сам бот —
о калориях там не говорим.
"""

# ruff: noqa: F811 -- fixtures are imported by name from the neighbouring test module
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pytest

from apps.integrations.ayla import DishEstimate
from apps.miniapp_api.tests.test_food_text_2091 import (  # noqa: F401 — фикстуры
    _bot_token,
    _diary_on,
    _patch_client,
    _post,
    bot_user,
    diary_consent,
    personal_consent,
    tenant,
)


def _estimate(status: str | None) -> DishEstimate:
    return DishEstimate(
        matched_dish="зыбзик",
        portion_g=300.0,
        portion_estimated=False,
        kcal=None,
        protein_g=None,
        fat_g=None,
        carbs_g=None,
        raw={},
        kcal_ai_status=status,
    )


@dataclass
class _OldEstimate:
    """Двойник без поля — ответ клиента до листа."""

    matched_dish: str = "зыбзик"
    portion_g: float = 300.0
    portion_estimated: bool = False
    kcal: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    carbs_g: float | None = None


def _status_on_the_wire(client, bot_user, estimate) -> object:
    patcher, _ = _patch_client(estimate=estimate)
    with patcher:
        resp = _post(client, bot_user, "customer_food_estimate", {"text": "зыбзик 300"})
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["matched_dish"] == "зыбзик"
    assert "kcal_ai_status" in body
    return body["kcal_ai_status"]


@pytest.mark.parametrize("status", ["unavailable", "disabled", "declined", None])
def test_the_status_reaches_the_mini_app(client, bot_user, status):
    assert _status_on_the_wire(client, bot_user, _estimate(status)) == status


def test_an_estimate_without_the_field_is_not_an_error(client, bot_user):
    assert _status_on_the_wire(client, bot_user, _OldEstimate()) is None


@pytest.mark.parametrize("status", ["unavailable", "disabled"])
def test_numbers_hidden_carries_no_reason(client, bot_user, status):
    with patch("apps.miniapp_api.views.numbers_hidden_for", return_value=True):
        assert _status_on_the_wire(client, bot_user, _estimate(status)) is None
