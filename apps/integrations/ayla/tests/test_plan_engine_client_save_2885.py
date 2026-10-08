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
