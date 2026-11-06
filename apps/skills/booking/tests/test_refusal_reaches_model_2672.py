"""DRF-2672 — отказ инструмента доходит до модели, которая отвечает человеку.

Навык записи зовёт модель дважды: первый заход выбирает инструмент,
второй — пишет ответ по его результату. До правки второй заход получал
в промпт только успех (мастера, время, предпросмотр, цена). Отказ, не
ставший передачей менеджеру, не оставлял в промпте ничего: промпт был
тем же, что при результате без единого поля, и модель отвечала вслепую.
Единственным исключением был сертификат — его блок «НЕУДАЧА / Причина».

Каждый узел гонит отказ через настоящий ``BookingSkill.handle`` и читает
сообщения ВТОРОГО вызова модели. Имя инструмента и причина отказа
записаны литералом: узел, берущий их из константы навыка, не заметил бы
её смены.
"""

# Фикстуры ``tenant`` / ``bot_user`` / ``context`` / ``_isolated_env``
# импортированы из test_skill и принимаются тестами параметрами — это не
# переопределение (F811).
# ruff: noqa: F811

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone as dj_timezone

from apps.llm.protocol import ToolCall
from apps.skills.booking.skill import BookingSkill
from apps.skills.booking.tests.test_skill import (  # noqa: F401 — фикстуры
    BOOKING_DATE,
    FakeYClients,
    _completion,
    _isolated_env,
    _patch_provider_complete,
    _patch_yclients,
    _service,
    _staff,
    bot_user,
    context,
    tenant,
)
from apps.skills.booking.tests.test_tools_cancel import _make_booking
from apps.skills.booking.tests.test_tools_reschedule import _user_record
from apps.tenancy.context import tenant_scope

pytestmark = pytest.mark.django_db

OBJECT = {"a": 1}
MODEL_REPLY = "ответ модели"


class _Client(FakeYClients):
    """Провайдер расписания: одна запись клиента, свободного времени нет."""

    def __init__(self) -> None:
        super().__init__()
        self.services_rows = [_service(22)]
        self.staff_rows = [_staff(11)]
        self.user_records: list[Any] = [_user_record(id_=555)]

    def get_user_records(self) -> list[Any]:
        return list(self.user_records)


def _iso(hours: int) -> str:
    return (dj_timezone.now() + timedelta(hours=hours)).replace(microsecond=0).isoformat()


def _second_prompt(
    context, tenant, tool: str, arguments: dict[str, Any], *, client: _Client | None = None
) -> tuple[Any, str]:
    """Прогнать ход с вызовом ``tool`` и вернуть (результат, промпт 2-го захода)."""
    call = ToolCall(id="c1", name=tool, arguments=arguments)
    completions = [_completion(tool_calls=[call]), _completion(text=MODEL_REPLY)]
    with _patch_yclients(client or _Client()), _patch_provider_complete(completions) as complete:
        with tenant_scope(tenant):
            result = BookingSkill().handle(context)
    # Отказ дошёл до второго захода, а не свернул в передачу менеджеру или
    # в готовый текст: иначе читать нечего, и узел не о том.
    assert complete.call_count == 2, f"{tool}: второго захода модели не было"
    assert result.should_handoff is False
    assert result.reply_text == MODEL_REPLY
    messages = complete.call_args_list[1].args[0]
    return result, "\n".join(str(m.get("content", "")) for m in messages)


# (инструмент, аргументы, причина отказа) — причина литералом.
REFUSALS = [
    pytest.param(
        "reschedule_booking",
        lambda: {"record_id": 555, "new_datetime": _iso(48)},
        "slot_unavailable",
        id="reschedule-slot_unavailable",
    ),
    pytest.param(
        "reschedule_booking",
        lambda: {"record_id": 555, "new_datetime": "завтра в два"},
        "invalid_datetime",
        id="reschedule-invalid_datetime",
    ),
    pytest.param(
        "reschedule_booking",
        lambda: {"record_id": 555, "new_datetime": _iso(-24)},
        "past_datetime",
        id="reschedule-past_datetime",
    ),
    pytest.param(
        "show_slots",
        lambda: {"master_id": 11, "service_id": 22, "date_from": OBJECT},
        "invalid_argument",
        id="show_slots-invalid_argument",
    ),
    pytest.param(
        "confirm_booking",
        lambda: {
            "master_id": 11,
            "service_id": 22,
            "slot_datetime": _iso(48),
            "client_phone": OBJECT,
        },
        "invalid_argument",
        id="confirm_booking-invalid_argument",
    ),
    pytest.param(
        "cancel_booking",
        lambda: {"record_id": 555, "reason": OBJECT},
        "invalid_argument",
        id="cancel_booking-invalid_argument",
    ),
    pytest.param(
        "calc_price",
        lambda: {"service_id": 22, "promo_code": OBJECT},
        "invalid_argument",
        id="calc_price-invalid_argument",
    ),
]


@pytest.mark.parametrize(("tool", "arguments", "reason"), REFUSALS)
def test_a_refusal_is_told_to_the_model(context, tenant, bot_user, tool, arguments, reason) -> None:
    _make_booking(tenant, bot_user, yc_id=555)

    _result, prompt = _second_prompt(context, tenant, tool, arguments())

    # Заголовок целиком, а не имя инструмента: имена всех инструментов и так
    # перечислены в каждом промпте, одно имя ничего бы не доказало.
    assert f"ИНСТРУМЕНТ {tool} — НЕУДАЧА" in prompt, "модель не знает, что инструмент отказал"
    assert f"Причина: {reason}" in prompt, "модель не знает, почему инструмент отказал"
    # Слов для человека код здесь не держит — и в промпт их не подкладывает.
    assert "Готовый текст" not in prompt


def test_the_certificate_refusal_was_always_told(context, tenant) -> None:
    """Образец, по которому сделаны остальные: он не меняется и не дублируется."""
    _result, prompt = _second_prompt(context, tenant, "buy_certificate", {"amount_rub": 1})

    assert "СЕРТИФИКАТ (buy_certificate) — НЕУДАЧА" in prompt
    assert "Причина: amount_out_of_range" in prompt
    assert prompt.count("НЕУДАЧА") == 1


class TestNoFreeTime:
    """``show_slots`` отработал, времени нет — это ответ, а не отказ."""

    def _prompt(self, context, tenant, client: _Client) -> str:
        call = ToolCall(id="c1", name="show_slots", arguments={"master_id": 11, "service_id": 22})
        completions = [_completion(tool_calls=[call]), _completion(text=MODEL_REPLY)]
        with _patch_yclients(client), _patch_provider_complete(completions) as complete:
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert complete.call_count == 2
        assert result.reply_text == MODEL_REPLY
        messages = complete.call_args_list[1].args[0]
        return "\n".join(str(m.get("content", "")) for m in messages)

    def test_no_dates_at_all(self, context, tenant) -> None:
        prompt = self._prompt(context, tenant, _Client())

        assert "СВОБОДНОЕ ВРЕМЯ (show_slots): нет." in prompt
        assert "Нет свободных дат у мастера в ближайшее время." in prompt
        assert prompt.count("НЕУДАЧА") == 0

    def test_a_day_with_no_time(self, context, tenant) -> None:
        client = _Client()
        client.dates = [BOOKING_DATE]

        prompt = self._prompt(context, tenant, client)

        assert "СВОБОДНОЕ ВРЕМЯ (show_slots): нет." in prompt
        assert f"На {BOOKING_DATE} свободного времени нет." in prompt


def test_a_success_is_not_called_a_refusal(context, tenant, bot_user) -> None:
    """Успешный инструмент: его данные в промпте есть, слова «НЕУДАЧА» — нет."""
    _make_booking(tenant, bot_user, yc_id=555)
    # Без живой записи провайдера: её время в фикстуре прошедшее, и запись
    # отфильтровалась бы — контроль проверял бы пустой список.
    client = _Client()
    client.user_records = []

    _result, prompt = _second_prompt(context, tenant, "show_my_bookings", {}, client=client)

    assert "ПРЕДСТОЯЩИЕ ЗАПИСИ:" in prompt
    assert "Массаж" in prompt
    assert prompt.count("НЕУДАЧА") == 0
    assert prompt.count("СВОБОДНОЕ ВРЕМЯ (show_slots)") == 0
