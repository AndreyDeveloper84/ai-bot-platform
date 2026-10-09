"""DRF-2967 — клиент каталога: утверждение основания и два отказа второй линии.

Форма отказов — со слов окна каталога (09.10): открытая заявка на удаление —
423 ``DELETION_IN_PROGRESS``; основания нет или оно старше известного
отзыва — 422 ``CONSENT_REQUIRED`` с ``reason`` ``withdrawn`` /
``not_attested``. С живого ответа каталога форма не снята.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from apps.integrations.ayla.plan_engine_client import (
    PlanConsentRequiredError,
    PlanDeletionInProgressError,
    PlanEngineUnavailableError,
)
from apps.integrations.ayla.tests.test_plan_engine_client_2879 import _answering, _compose
from apps.integrations.ayla.tests.test_plan_engine_client_save_2885 import (
    COMMAND,
    _client,
    _refused,
)

BASIS = {
    "type": "personal_data",
    "document_version": "privacy-v2.0",
    "granted_at": "2026-10-01T09:00:00+00:00",
}


def test_a1_the_basis_rides_in_the_compose_body() -> None:
    client, seen = _answering(200, {"data": {"outcome": "NO_GOAL", "decision": None}})

    _compose(client, consent=BASIS)

    (request,) = seen
    assert json.loads(request.content)["consent"] == BASIS


def test_a1_without_a_basis_the_field_is_absent_not_null() -> None:
    """``null`` каталог читал бы как присланное и пустое; отсутствие поля —
    «утверждения нет»."""
    client, seen = _answering(200, {"data": {"outcome": "NO_GOAL", "decision": None}})

    _compose(client)

    (request,) = seen
    body = json.loads(request.content)
    assert body["safety_state"] == "UNKNOWN"  # тело то самое
    assert "consent" not in body


REFUSALS: list[tuple[httpx.Response, type[Exception]]] = [
    (_refused(423, "DELETION_IN_PROGRESS", "deletion_requested"), PlanDeletionInProgressError),
    (_refused(422, "CONSENT_REQUIRED", "withdrawn"), PlanConsentRequiredError),
    (_refused(422, "CONSENT_REQUIRED", "not_attested"), PlanConsentRequiredError),
    # Тот же статус с чужим кодом — не наш отказ: недоступность, а не «нет согласия».
    (_refused(423, "SOMETHING_ELSE"), PlanEngineUnavailableError),
    (_refused(422, "SOMETHING_ELSE"), PlanEngineUnavailableError),
]


@pytest.mark.parametrize(("response", "error"), REFUSALS)
def test_a2_the_second_line_refusals_are_named_on_compose(
    response: httpx.Response, error: type[Exception]
) -> None:
    with pytest.raises(error) as caught:
        _compose(_client(lambda r: response))

    assert type(caught.value) is error


@pytest.mark.parametrize(("response", "error"), REFUSALS)
def test_a2_the_second_line_refusals_are_named_on_save(
    response: httpx.Response, error: type[Exception]
) -> None:
    with pytest.raises(error) as caught:
        _client(lambda r: response).save_plan(external_user_id="bot:max:1", command=COMMAND)

    assert type(caught.value) is error


def test_a3_the_basis_in_a_save_command_reaches_the_wire() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"data": {"plan": {}, "created": True}})

    _client(handler).save_plan(external_user_id="bot:max:1", command={**COMMAND, "consent": BASIS})

    assert seen["body"]["consent"] == BASIS
