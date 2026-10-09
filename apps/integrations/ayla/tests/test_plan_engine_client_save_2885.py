"""DRF-2885 — клиент Plan Engine: сохранение плана и подписи способностей.

Форма запросов и имена отказов — по коду каталога (ветка PR
beautygo_backend#700, чтение); ответы здесь синтетические, с живого каталога
фикстура не снята — снять после первого прогона на стенде.

* k1 — сохранение шлёт команду как есть и субъекта заголовком;
* k2 — ``created`` доезжает; ответ без плана — не «сохранено»;
* k3 — отказы каталога названы: безопасность, идемпотентность, цель, контракт;
* k4 — подписи: только подписанные способности, без ключей вместо слов.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from apps.integrations.ayla.plan_engine_client import (
    PlanCapabilityNotConfirmedError,
    PlanEngineContractError,
    PlanEngineDisabledError,
    PlanEngineHttpClient,
    PlanEngineUnavailableError,
    PlanGoalNotFoundError,
    PlanIdempotencyConflictError,
    PlanSaveSafetyBlockedError,
)

COMMAND = {"decision_id": "d-1", "goal_ref": "g-1", "safety_state": "NORMAL"}


def _client(handler) -> PlanEngineHttpClient:
    return PlanEngineHttpClient(
        base_url="https://catalog.example",
        token="t",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _ok(data: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"success": True, "data": data})


def _refused(status: int, code: str, reason: str = "") -> httpx.Response:
    return httpx.Response(
        status, json={"success": False, "error": {"code": code, "details": {"reason": reason}}}
    )


class TestSave:
    def test_k1_the_command_and_the_subject(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["subject"] = request.headers["X-External-User-ID"]
            seen["body"] = json.loads(request.content)
            return _ok({"plan": {"plan_id": "p-1"}, "created": True}, 201)

        data = _client(handler).save_plan(external_user_id="bot:max:1", command=COMMAND)

        assert seen["url"] == "https://catalog.example/api/v1/internal/me/plan/"
        assert seen["subject"] == "bot:max:1"
        assert seen["body"] == COMMAND
        assert data == {"plan": {"plan_id": "p-1"}, "created": True}

    def test_k2_a_replay_says_created_false(self) -> None:
        data = _client(lambda r: _ok({"plan": {}, "created": False})).save_plan(
            external_user_id="bot:max:1", command=COMMAND
        )

        assert data["created"] is False

    @pytest.mark.parametrize("data", [{}, {"plan": None, "created": True}, {"plan": {}}])
    def test_k2_an_answer_without_a_plan_is_not_a_save(self, data: Any) -> None:
        with pytest.raises(PlanEngineUnavailableError):
            _client(lambda r: _ok(data)).save_plan(external_user_id="bot:max:1", command=COMMAND)

    @pytest.mark.parametrize(
        ("response", "error"),
        [
            (_refused(409, "PLAN_SAVE_SAFETY_BLOCKED"), PlanSaveSafetyBlockedError),
            (_refused(409, "PLAN_IDEMPOTENCY_CONFLICT"), PlanIdempotencyConflictError),
            (_refused(409, "PLAN_CAPABILITY_NOT_CONFIRMED"), PlanCapabilityNotConfirmedError),
            (_refused(404, "NOT_FOUND", "goal_not_found"), PlanGoalNotFoundError),
            (_refused(404, "PLAN_ENGINE_DISABLED"), PlanEngineDisabledError),
            (
                _refused(400, "PLAN_CONTRACT_VIOLATION", "clarify_without_restriction"),
                PlanEngineContractError,
            ),
            (_refused(409, "SOMETHING_ELSE"), PlanEngineUnavailableError),
        ],
    )
    def test_k3_refusals_are_named(self, response: httpx.Response, error: type[Exception]) -> None:
        with pytest.raises(error) as caught:
            _client(lambda r: response).save_plan(external_user_id="bot:max:1", command=COMMAND)

        assert type(caught.value) is error

    def test_k3_the_contract_violation_carries_its_reason(self) -> None:
        response = _refused(400, "PLAN_CONTRACT_VIOLATION", "clarify_without_restriction")

        with pytest.raises(PlanEngineContractError) as caught:
            _client(lambda r: response).save_plan(external_user_id="bot:max:1", command=COMMAND)

        assert caught.value.reason == "clarify_without_restriction"


class TestLabels:
    def test_k4_only_labelled_capabilities_come_back(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _ok(
                {
                    "labels": {
                        "cap.a": {"state": "labelled", "label": " Режим сна "},
                        "cap.b": {"state": "unlabelled", "label": ""},
                        "cap.c": {"state": "labelled", "label": "   "},
                        "cap.d": "мусор",
                    }
                }
            )

        labels = _client(handler).capability_labels(
            external_user_id="bot:max:1", keys=["cap.a", "cap.b", "cap.c", "cap.d"]
        )

        assert seen["url"].endswith("/api/v1/internal/me/plan/capability-labels/")
        assert seen["body"] == {"keys": ["cap.a", "cap.b", "cap.c", "cap.d"]}
        assert labels == {"cap.a": "Режим сна"}

    def test_k4_an_answer_without_labels_is_unavailable(self) -> None:
        with pytest.raises(PlanEngineUnavailableError):
            _client(lambda r: _ok({})).capability_labels(external_user_id="bot:max:1", keys=["x"])


class TestRead:
    """Шаг 3: сохранённый план по «мой план»."""

    def test_r1_the_plan_document_comes_back(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["subject"] = request.headers["X-External-User-ID"]
            return _ok({"plan": {"plan_id": "p-1", "revision": {"steps": []}}})

        plan = _client(handler).get_plan(external_user_id="bot:max:1")

        assert (seen["method"], seen["subject"]) == ("GET", "bot:max:1")
        assert seen["url"] == "https://catalog.example/api/v1/internal/me/plan/"
        assert plan == {"plan_id": "p-1", "revision": {"steps": []}}

    def test_r2_no_plan_is_none_not_an_error(self) -> None:
        assert _client(lambda r: _ok({"plan": None})).get_plan(external_user_id="bot:max:1") is None

    @pytest.mark.parametrize("data", [{}, {"plan": "мусор"}, {"plan": []}])
    def test_r3_an_answer_without_the_plan_key_is_not_no_plan(self, data: Any) -> None:
        with pytest.raises(PlanEngineUnavailableError):
            _client(lambda r: _ok(data)).get_plan(external_user_id="bot:max:1")

    @pytest.mark.parametrize("status", [404, 409, 500, 503])
    def test_r3_a_refusal_is_not_no_plan(self, status: int) -> None:
        with pytest.raises(PlanEngineUnavailableError):
            _client(lambda r: httpx.Response(status, json={})).get_plan(
                external_user_id="bot:max:1"
            )


class TestCapabilityDetails:
    """«Зачем шаг» — поле ``expected_effect`` рядом с подписью (DRF-2876)."""

    @staticmethod
    def _answer(**row: Any) -> Any:
        return lambda r: _ok(
            {"labels": {"cap.a": {"state": "labelled", "label": "Режим сна", **row}}}
        )

    def test_d1_the_effect_comes_with_the_label(self) -> None:
        details = _client(
            self._answer(expected_effect="  Помогает высыпаться.  ")
        ).capability_details(external_user_id="bot:max:1", keys=["cap.a"])

        assert details == {
            "cap.a": {"label": "Режим сна", "expected_effect": "Помогает высыпаться."}
        }

    @pytest.mark.parametrize(
        "row", [{}, {"expected_effect": None}, {"expected_effect": "  "}, {"expected_effect": 7}]
    )
    def test_d2_no_effect_is_none_and_the_label_stays(self, row: dict[str, Any]) -> None:
        """Каталог ещё не отдаёт поле, или текста нет — подпись остаётся, «зачем» пусто."""
        details = _client(self._answer(**row)).capability_details(
            external_user_id="bot:max:1", keys=["cap.a"]
        )

        assert details == {"cap.a": {"label": "Режим сна", "expected_effect": None}}

    def test_d3_a_capability_without_a_label_brings_no_effect_either(self) -> None:
        """Каталог может прислать эффект и без подписи (``no_text``) — шаг без
        подписи показать нечем, и его «зачем» не нужен."""
        answer = {
            "labels": {"cap.a": {"state": "no_text", "label": None, "expected_effect": "Текст."}}
        }

        details = _client(lambda r: _ok(answer)).capability_details(
            external_user_id="bot:max:1", keys=["cap.a"]
        )

        assert details == {}

    def test_d4_labels_are_the_same_call_without_the_effect(self) -> None:
        labels = _client(self._answer(expected_effect="Помогает высыпаться.")).capability_labels(
            external_user_id="bot:max:1", keys=["cap.a"]
        )

        assert labels == {"cap.a": "Режим сна"}

    @pytest.mark.parametrize("state", ["ambiguous", "unknown", "no_text"])
    def test_d5_only_a_confirmed_label_counts_whatever_text_came_with_it(self, state: str) -> None:
        """Состояние решает, а не наличие строки: подпись неподтверждённой
        способности человеку не показывается, и её «зачем» — тоже."""
        confirmed = _client(self._answer(expected_effect="Текст.")).capability_details(
            external_user_id="bot:max:1", keys=["cap.a"]
        )
        assert "cap.a" in confirmed  # положительный контроль: с «labelled» строка есть

        answer = {
            "labels": {"cap.a": {"state": state, "label": "Режим сна", "expected_effect": "Текст."}}
        }
        details = _client(lambda r: _ok(answer)).capability_details(
            external_user_id="bot:max:1", keys=["cap.a"]
        )

        assert details == {}
