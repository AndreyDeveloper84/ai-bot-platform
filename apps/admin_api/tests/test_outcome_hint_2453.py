"""Исход разведён: ``detail`` — нам, ``hint`` — человеку (DRF-2453).

## Что чинится

Девять мест на админских экранах печатали человеку `res.detail`, а в этом
канале лежало всё сразу:

* согласованная русская фраза владельца («перенос не настроен»);
* внутренний английский («cancellation is not configured»);
* `str(exc)` — а это **текст чужого сервиса**: `salon_client.py` кладёт в
  исключение `detail` ответа каталога, и ни язык его, ни согласованность
  владельцем этому репозиторию не известны.

Клиентского решения, которое было бы верным, там не существовало: снять
`detail` — потерять слова владельца; оставить — показывать человеку и
английское, и чужое.

## Полку не изобретали

`hint` рядом с внутренней причиной уже отдаёт `views_staff_role.py:178`
(`details={"hint": exc.hint}`), клиент объявил `details` в DRF-2273, а
`tools/lint/api_error_details_guard.py` эту полку **охраняет**. Четвёртый
конверт был бы не только дублем, но и дублем вне охраны.
"""

from __future__ import annotations

import json

import pytest

from apps.admin_api.views_booking_cancel import _outcome as cancel_outcome
from apps.admin_api.views_booking_complete import _error as complete_error
from apps.admin_api.views_booking_complete import _outcome as complete_outcome


def _body(response) -> dict:
    return json.loads(response.content)


class TestTheOutcomeCarriesBothHalvesApart:
    def test_a_hint_rides_beside_the_internal_reason(self) -> None:
        """Наличие раньше отсутствия: обе половины есть и они разные."""
        body = _body(
            complete_outcome(
                "blocked",
                "reschedule write is not configured for this tenant",
                503,
                hint="перенос не настроен",
            )
        )

        assert body["hint"] == "перенос не настроен"
        assert body["detail"] == "reschedule write is not configured for this tenant"
        assert body["hint"] != body["detail"]

    def test_without_a_hint_the_key_is_absent_not_empty(self) -> None:
        """Нет согласованной фразы — ключа нет, и экран скажет своё.

        Пустая строка была бы хуже отсутствия: экран прочитал бы её как
        «слова есть» и показал человеку пустое место.
        """
        body = _body(cancel_outcome("blocked", "salon said no", 403))

        assert body["detail"] == "salon said no"
        assert "hint" not in body

    def test_the_foreign_text_never_becomes_a_hint_by_itself(self) -> None:
        """`str(exc)` каталога остаётся внутренней причиной.

        Это главный узел листа: текст чужого сервиса не становится словами
        для человека только потому, что он оказался русским.
        """
        body = _body(cancel_outcome("blocked", "Визит уже завершён.", 403))

        assert body["detail"] == "Визит уже завершён."
        assert "hint" not in body

    @pytest.mark.parametrize("status", [409, 503, 504])
    def test_the_status_is_not_disturbed_by_the_hint(self, status: int) -> None:
        """Подсказка едет рядом со статусом, а не вместо него.

        Узел дешёвый и не случайный: разводя хелпер, я в первом заходе
        вписал `hint=` ПЕРЕД позиционным статусом и получил синтаксическую
        ошибку в шестнадцати местах.
        """
        response = complete_outcome("conflict", "internal", status, hint="слова")

        assert response.status_code == status


class TestTheErrorEnvelopeUsesTheSameShelf:
    def test_a_hint_goes_into_details(self) -> None:
        """В конверте ошибки подсказка живёт в `details.hint` — как в staff_role."""
        body = _body(
            complete_error(
                "unavailable",
                "booking version unavailable upstream",
                503,
                hint="расписание не ответило — попробуйте ещё раз",
            )
        )

        assert body["details"] == {"hint": "расписание не ответило — попробуйте ещё раз"}
        assert body["detail"] == "booking version unavailable upstream"

    def test_no_hint_means_no_details_key(self) -> None:
        body = _body(complete_error("not_found", "booking not found", 404))

        assert body["error"] == "not_found"
        assert "details" not in body
