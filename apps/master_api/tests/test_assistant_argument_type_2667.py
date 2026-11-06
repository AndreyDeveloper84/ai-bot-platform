"""DRF-2667 — аргумент модели не строкой не доезжает до человека.

Аргументы инструмента пишет модель, и словарь вместо строки для неё —
обычный исход, а не чужая ошибка. До правки ``str(arguments.get(...) or "")``
превращал ``{"a": 1}`` в текст ``"{'a': 1}"``: он ложился в заявку на
нерабочее время и показывался мастеру в сводке «Причина: отпуск ({'a': 1})».

Узлы проверяют свойство, а не форму отказа: после хода со словарём ни в
базе, ни в ответе нет ни ``{'a': 1}``, ни ``None`` текстом. Форма отказа
может смениться — это свойство нет. Каждая пара с положительной стражей:
то же действие со строкой проходит, чтобы «записи нет» не зеленело на
пустоте.
"""

# Фикстура ``llm`` импортирована из test_assistant_api и принимается тестами
# параметром — это не переопределение (F811).
# ruff: noqa: F811

from __future__ import annotations

import pytest

from apps.master_api.tests.conftest import init_data_header
from apps.master_api.tests.test_assistant_api import (  # noqa: F401 — фикстура llm
    CONFIRM_URL,
    FakeResult,
    _ask,
    _block_time_call,
    llm,
)
from apps.scheduling.models import ScheduleChangeRequest

pytestmark = pytest.mark.django_db

OBJECT = {"a": 1}
LEAKS = ("{'a': 1}", "None")


def _no_leak(text: str) -> None:
    for leak in LEAKS:
        assert leak not in text, f"{leak!r} доехал до человека: {text!r}"


def _stored_reasons() -> list[str]:
    return [r.reason_text for r in ScheduleChangeRequest.all_tenants.all()]


def _ask_and_confirm(client, call) -> tuple[dict, int | None]:
    body = _ask(client, "хочу выходной в пятницу").json()
    pending = body.get("pending_action")
    if not pending:
        return body, None
    resp = client.post(
        CONFIRM_URL,
        data={"token": pending["token"]},
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("12345"),
    )
    return body, resp.status_code


class TestReasonText:
    def test_a_string_reason_is_stored(self, client, bot_user, accepted_master, llm):
        """Страж: строка проходит и ложится — иначе узел ниже зеленеет на пустоте."""
        llm["script"].append(FakeResult(tool_calls=[_block_time_call()]))

        _body, status = _ask_and_confirm(client, None)

        assert status == 200
        assert _stored_reasons() == ["поездка"]

    def test_an_object_reason_never_reaches_the_master_or_the_row(
        self, client, bot_user, accepted_master, llm
    ):
        call = _block_time_call()
        call.arguments["reason_text"] = OBJECT
        llm["script"].append(FakeResult(tool_calls=[call]))

        body, _status = _ask_and_confirm(client, call)

        shown = (
            f"{body.get('answer') or ''} {(body.get('pending_action') or {}).get('summary') or ''}"
        )
        assert shown.strip()  # мастеру что-то сказано — иначе проверять нечего
        _no_leak(shown)
        for stored in _stored_reasons():
            _no_leak(stored)


class TestTimeArgument:
    def test_an_object_start_is_not_echoed_to_the_master(
        self, client, bot_user, accepted_master, llm
    ):
        """``_parse_dt`` отвечал «не понимаю время "{'a': 1}"» — эхо в ответ."""
        call = _block_time_call()
        call.arguments["start"] = OBJECT
        llm["script"].append(FakeResult(tool_calls=[call]))

        body = _ask(client, "хочу выходной в пятницу").json()

        assert body.get("pending_action") is None  # действие не предложено
        answer = body.get("answer") or ""
        assert answer  # отказ сказан словами — иначе проверять нечего
        _no_leak(answer)
        assert ScheduleChangeRequest.all_tenants.count() == 0
