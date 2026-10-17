"""DRF-2701 — модель узнаёт, что запись не сверена с расписанием.

Старый путь расписания (``BOOKING_VIA_AYLA_REST`` выключен — аварийный
откат): ``show_my_bookings`` берёт записи из своей базы, а время визита —
из живого чтения расписания. Запись доходила до модели с пустым временем
в двух разных случаях:

1. чтение расписания упало — исключение проглатывалось;
2. чтение удалось, но такой записи в расписании нет.

В обоих блок записей был байт-в-байт тем же, и ничто в нём не говорило,
что запись не подтверждена. Теперь оба случая названы модели, каждый
своими словами. Список при этом не меняется: запись не скрывается.

Узлы гонят ход через настоящий ``BookingSkill.handle`` и читают
сообщения второго вызова модели. Ожидаемые слова записаны литералом.
Данные синтетические.
"""

# Фикстуры импортированы из test_skill и принимаются параметрами (F811).
# ruff: noqa: F811

from __future__ import annotations

from typing import Any

import pytest

from apps.integrations.yclients import UserRecord, YClientsAPIError, YClientsUnavailableError
from apps.llm.protocol import ToolCall
from apps.skills.booking.skill import BookingSkill
from apps.skills.booking.tests.test_skill import (  # noqa: F401 — фикстуры
    BOOKING_DATE,
    FakeYClients,
    _completion,
    _isolated_env,
    _patch_provider_complete,
    _patch_yclients,
    bot_user,
    context,
    tenant,
)
from apps.skills.booking.tests.test_tools_cancel import _make_booking
from apps.tenancy.context import tenant_scope

pytestmark = pytest.mark.django_db

CHECK_FAILED = "Сверить записи с расписанием не удалось"
NOT_FOUND = "в расписании не найдена"
VISIT_AT = f"{BOOKING_DATE}T10:00:00"


@pytest.fixture(autouse=True)
def _legacy_schedule_path(settings) -> None:
    """Оба случая живут только на старом пути расписания."""
    settings.BOOKING_VIA_AYLA_REST = False


def _live_record(record_id: int) -> UserRecord:
    return UserRecord(
        id=record_id,
        services=[{"id": 22, "title": "Массаж"}],
        company={},
        staff={"id": 11, "name": "Ольга"},
        date=VISIT_AT,
        datetime=VISIT_AT,
        seance_length=3600,
        raw={},
    )


class _Schedule(FakeYClients):
    """Расписание: чтение записей можно уронить или наполнить."""

    def __init__(
        self, *, records: list[Any] | None = None, records_exc: Exception | None = None
    ) -> None:
        super().__init__()
        self.records = records or []
        self.records_exc = records_exc
        self.records_calls = 0

    def get_user_records(self) -> list[Any]:
        self.records_calls += 1
        if self.records_exc is not None:
            raise self.records_exc
        return list(self.records)


def _bookings_block(context, tenant, client: _Schedule) -> str:
    """Блок записей из промпта второго захода модели."""
    call = ToolCall(id="c1", name="show_my_bookings", arguments={})
    completions = [_completion(tool_calls=[call]), _completion(text="ответ модели")]
    with _patch_yclients(client), _patch_provider_complete(completions) as complete:
        with tenant_scope(tenant):
            result = BookingSkill().handle(context)
    # Ход дошёл до второго захода и расписание действительно спросили:
    # иначе читать нечего, и узел не о том.
    assert complete.call_count == 2
    assert result.should_handoff is False
    assert client.records_calls == 1
    system = complete.call_args_list[1].args[0][0]["content"]
    blocks = [s for s in system.split("\n\n") if s.startswith("ПРЕДСТОЯЩИЕ ЗАПИСИ")]
    assert len(blocks) == 1
    return blocks[0]


FAILURES = [
    pytest.param(lambda: YClientsUnavailableError("circuit_open"), id="unavailable"),
    pytest.param(lambda: YClientsAPIError("http_500"), id="api_error"),
]


@pytest.mark.parametrize("exc", FAILURES)
def test_a_failed_check_is_told_to_the_model(context, tenant, bot_user, exc) -> None:
    _make_booking(tenant, bot_user, yc_id=555)

    block = _bookings_block(context, tenant, _Schedule(records_exc=exc()))

    assert "Массаж" in block, "запись из своей базы по-прежнему показана"
    assert CHECK_FAILED in block, "модель не знает, что сверка с расписанием не удалась"
    # Расписание не читали — значит и «не найдена» сказать нельзя.
    assert block.count(NOT_FOUND) == 0


@pytest.mark.parametrize("exc", FAILURES)
def test_a_failed_check_with_no_local_rows_is_still_told(context, tenant, exc) -> None:
    block = _bookings_block(context, tenant, _Schedule(records_exc=exc()))

    assert block.startswith("ПРЕДСТОЯЩИЕ ЗАПИСИ: нет.")
    assert CHECK_FAILED in block


def test_a_record_the_schedule_does_not_hold_is_marked(context, tenant, bot_user) -> None:
    _make_booking(tenant, bot_user, yc_id=555)

    block = _bookings_block(context, tenant, _Schedule(records=[]))

    assert "Массаж" in block, "запись не скрыта"
    assert NOT_FOUND in block, "модель не знает, что записи в расписании нет"
    # Сверка удалась — о её провале сказать нельзя.
    assert block.count(CHECK_FAILED) == 0


def test_a_confirmed_record_carries_no_mark(context, tenant, bot_user) -> None:
    _make_booking(tenant, bot_user, yc_id=555)

    block = _bookings_block(context, tenant, _Schedule(records=[_live_record(555)]))

    assert VISIT_AT in block, "время взято из расписания"
    assert block.count(CHECK_FAILED) == 0
    assert block.count(NOT_FOUND) == 0


def test_only_the_unconfirmed_row_is_marked(context, tenant, bot_user) -> None:
    _make_booking(tenant, bot_user, yc_id=555)
    _make_booking(tenant, bot_user, yc_id=556)

    block = _bookings_block(context, tenant, _Schedule(records=[_live_record(555)]))

    rows = [line for line in block.splitlines() if line.startswith("• ")]
    assert len(rows) == 2
    assert sorted(NOT_FOUND in row for row in rows) == [False, True]
    assert [VISIT_AT in row for row in rows] == [NOT_FOUND not in row for row in rows]


def test_the_three_states_no_longer_look_the_same(context, tenant, bot_user) -> None:
    """Сам дефект: три разных состояния давали один и тот же блок."""
    _make_booking(tenant, bot_user, yc_id=555)

    failed = _bookings_block(
        context, tenant, _Schedule(records_exc=YClientsUnavailableError("circuit_open"))
    )
    absent = _bookings_block(context, tenant, _Schedule(records=[]))
    confirmed = _bookings_block(context, tenant, _Schedule(records=[_live_record(555)]))

    assert len({failed, absent, confirmed}) == 3
