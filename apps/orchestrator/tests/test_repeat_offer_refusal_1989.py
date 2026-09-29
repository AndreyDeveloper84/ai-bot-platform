"""«Записаться ещё» на непродаваемое предложение — причина словами, без обещаний (DRF-1989).

Статус ``offer_not_sellable`` раньше падал в последнюю ветку ответа — текст
про недоступность сервиса, то есть поломку, которой нет. «У мастера не
указана цена» говорится только для ``price_below_minimum``; ``inactive`` и
``unknown`` — нейтральный текст без причины.
"""

from __future__ import annotations

import pytest

from apps.booking.services.records import RepeatResult
from apps.integrations.ayla.tests.test_offer_refusal_1989 import CLIENT_NEUTRAL, CLIENT_PRICE
from apps.orchestrator.visits import _repeat_refusal


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("price_below_minimum", CLIENT_PRICE),
        ("inactive", CLIENT_NEUTRAL),
        ("unknown", CLIENT_NEUTRAL),
    ],
)
def test_repeat_of_an_unsellable_offer_says_why(reason: str, expected: str) -> None:
    text, _buttons = _repeat_refusal(
        RepeatResult(
            status="offer_not_sellable",  # type: ignore[arg-type]
            service_name="Маникюр",
            master_name="Инна",
            details={"reason": reason},
        )
    )

    assert text == expected
