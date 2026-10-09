"""DRF-2885 — провод «шаг плана → услуга → запись»: клиент плана и клиент записи.

* w1 — кандидаты: тело запроса (четвёрка и основание) и ответ как есть;
* w2 — пустой список без причины из закрытого перечня — «недоступно», не «услуг нет»;
* w3 — выбор услуги: уровень, обе ссылки, идентификатор подбора;
* w4 — отказы шага несут причину каталога;
* w5 — запись от шага: блок происхождения и основание РЯДОМ с ним, не внутри;
* w6 — обычная запись без блока шлёт прежнее тело.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from apps.integrations.ayla import booking_client as bc
from apps.integrations.ayla.plan_engine_client import (
    PlanEngineHttpClient,
    PlanEngineUnavailableError,
    PlanStepNotExecutableError,
    PlanStepResolutionRefusedError,
)

FOUR: dict[str, Any] = {
    "safety_state": "NORMAL",
    "safety_policy_version": "pre_check-abc",
    "evaluated_at_revision": 9,
    "s1_restriction": "none",
}
BASIS = {
    "type": "personal_data",
    "document_version": "v1",
    "granted_at": "2026-10-09T10:00:00+00:00",
}
CANDIDATE = {"tenant_offer_ref": "offer", "canonical_service_ref": "canon", "display": {}}


def _plan_client(handler) -> PlanEngineHttpClient:
    return PlanEngineHttpClient(
        base_url="https://catalog.example",
        token="t",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _ok(data: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"success": True, "data": data})


def _refused(code: str, reason: str) -> httpx.Response:
    return httpx.Response(
        409, json={"success": False, "error": {"code": code, "details": {"reason": reason}}}
    )


def test_w1_candidates_send_the_four_and_the_basis_and_return_the_answer_as_it_is() -> None:
    seen: dict[str, Any] = {}
    answer = {"candidates": [CANDIDATE], "search_id": "s-1", "nothing_because": None}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return _ok(answer)

    found = _plan_client(handler).step_candidates(
        external_user_id="bot:max:1", plan_id="p", step_id="s", consent=BASIS, **FOUR
    )

    assert seen["url"] == "https://catalog.example/api/v1/internal/me/plan/steps/candidates/"
    assert seen["body"] == {"plan_id": "p", "step_id": "s", **FOUR, "consent": BASIS}
    assert found == answer


@pytest.mark.parametrize(
    "answer",
    [
        {"candidates": [], "search_id": "s", "nothing_because": None},
        {"candidates": [], "search_id": "s", "nothing_because": "ЧТО-ТО_НОВОЕ"},
        {"search_id": "s", "nothing_because": "NO_OFFER"},
        {"candidates": ["мусор"], "search_id": "s"},
        {"candidates": [CANDIDATE], "nothing_because": None},
    ],
)
def test_w2_an_answer_that_cannot_be_read_is_unavailable_not_no_services(answer: Any) -> None:
    with pytest.raises(PlanEngineUnavailableError):
        _plan_client(lambda r: _ok(answer)).step_candidates(
            external_user_id="bot:max:1", plan_id="p", step_id="s", **FOUR
        )


def test_w2_an_empty_list_with_a_known_reason_is_an_answer() -> None:
    answer = {"candidates": [], "search_id": "s", "nothing_because": "NO_OFFER"}

    found = _plan_client(lambda r: _ok(answer)).step_candidates(
        external_user_id="bot:max:1", plan_id="p", step_id="s", **FOUR
    )

    assert found["nothing_because"] == "NO_OFFER"


def test_w3_the_choice_names_the_level_both_refs_and_the_search() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return _ok({"plan": {}, "created": True}, 201)

    _plan_client(handler).resolve_step(
        external_user_id="bot:max:1",
        plan_id="p",
        step_id="s",
        canonical_service_ref="canon",
        tenant_offer_ref="offer",
        resolver_decision_id="search-1",
        consent=BASIS,
        **FOUR,
    )

    assert seen["url"] == "https://catalog.example/api/v1/internal/me/plan/steps/resolution/"
    assert seen["body"] == {
        "plan_id": "p",
        "step_id": "s",
        "level": "OFFER",
        "canonical_service_ref": "canon",
        "tenant_offer_ref": "offer",
        "resolver_decision_id": "search-1",
        **FOUR,
        "consent": BASIS,
    }


@pytest.mark.parametrize(
    ("code", "reason", "error"),
    [
        ("PLAN_STEP_NOT_EXECUTABLE", "s1_restriction_stop", PlanStepNotExecutableError),
        ("PLAN_STEP_NOT_EXECUTABLE", "safety_blocked", PlanStepNotExecutableError),
        ("PLAN_STEP_RESOLUTION_REFUSED", "offer_not_a_candidate", PlanStepResolutionRefusedError),
    ],
)
def test_w4_a_step_refusal_carries_the_catalogs_reason(code: str, reason: str, error: Any) -> None:
    with pytest.raises(error) as caught:
        _plan_client(lambda r: _refused(code, reason)).step_candidates(
            external_user_id="bot:max:1", plan_id="p", step_id="s", **FOUR
        )

    assert caught.value.reason == reason


def _booking_client(handler) -> bc.AylaBookingHTTPClient:
    return bc.AylaBookingHTTPClient(
        base_url="https://ayla.test", api_token="secret-tok", transport=httpx.MockTransport(handler)
    )


def _created(captured: dict[str, Any]):
    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(201, json={"data": {"id": "appt-uuid", "status": "confirmed"}})

    return handler


def test_w5_a_step_booking_carries_the_block_and_the_basis_beside_it(db) -> None:
    captured: dict[str, Any] = {}
    block = {"entry_point": "PLAN_STEP", "plan_id": "p", "step_id": "s", **FOUR}

    _booking_client(_created(captured)).create_appointment(
        external_user_id="bot:max:1",
        client_id="client",
        specialist_id="spec",
        service_id="offer",
        start_datetime="2026-10-12T10:00:00+03:00",
        idempotency_key="k",
        payment_required=False,
        provenance=block,
        consent=BASIS,
    )

    assert captured["body"]["provenance"] == block
    assert captured["body"]["consent"] == BASIS
    assert "consent" not in captured["body"]["provenance"]
    assert captured["body"]["payment_required"] is False


def test_w6_an_ordinary_booking_sends_the_old_body(db) -> None:
    captured: dict[str, Any] = {}

    _booking_client(_created(captured)).create_appointment(
        external_user_id="bot:max:1",
        client_id="client",
        specialist_id="spec",
        service_id="svc",
        start_datetime="2026-10-12T10:00:00+03:00",
        idempotency_key="k",
    )

    assert captured["body"]["service_id"] == "svc"  # положительный контроль: тело разобрано
    assert "provenance" not in captured["body"]
    assert "consent" not in captured["body"]
