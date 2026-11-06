"""Клиент сборки плана — что уходит каталогу и как читается ответ (DRF-2879).

Ответы каталога здесь СИНТЕТИЧЕСКИЕ — по коду ручки ``plan/decision/`` на дату
листа (каталог PR #679, не слит). Фикстура с живого ответа снимается после
слияния и одного сквозного прогона на стенде.

* c1 — тело запроса: безопасность, реестр, убранные способности; субъект —
  только в заголовке;
* c2 — любой исход с именем возвращается как есть;
* c3 — ответ без имени исхода — нарушение договора, а не «плана нет»;
* c4 — отказы по именам: выключено, неконформный запрос, авторизация, 5xx,
  сеть, не-JSON;
* c5 — без адреса или токена запрос не уходит.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from apps.integrations.ayla.plan_engine_client import (
    PlanEngineAuthError,
    PlanEngineConfigError,
    PlanEngineContractError,
    PlanEngineDisabledError,
    PlanEngineHttpClient,
    PlanEngineUnavailableError,
)

REGISTRY = {"registry_version": "0.1", "rules": [{"rule_id": "PR-X-0001"}]}


def _client(handler) -> PlanEngineHttpClient:
    return PlanEngineHttpClient(
        base_url="https://ayla.test",
        token="t",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _compose(client: PlanEngineHttpClient, **over: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "external_user_id": "bot:max:2879",
        "safety_state": "UNKNOWN",
        "safety_policy_version": "bot:not-evaluated",
        "rules_registry": REGISTRY,
        "excluded_capability_refs": ["calm"],
    }
    kwargs.update(over)
    return client.compose_decision(**kwargs)


def _answering(status: int, body: Any) -> tuple[PlanEngineHttpClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    return _client(handler), seen


class TestTheRequest:
    def test_c1_the_body_and_the_subject(self) -> None:
        client, seen = _answering(200, {"data": {"outcome": "NO_GOAL", "decision": None}})

        _compose(client)

        (request,) = seen
        assert request.method == "POST"
        assert request.url.path.endswith("/internal/me/plan/decision/")
        assert request.headers["X-External-User-ID"] == "bot:max:2879"
        assert request.headers["Authorization"] == "Bearer t"
        assert json.loads(request.content) == {
            "safety_state": "UNKNOWN",
            "safety_policy_version": "bot:not-evaluated",
            "rules_registry": REGISTRY,
            "excluded_capability_refs": ["calm"],
        }


class TestTheAnswer:
    @pytest.mark.parametrize(
        "outcome",
        ["PLAN", "SAFETY_BLOCKED", "NO_GOAL", "NO_CURATED_DECOMPOSITION", "PLAN_NOT_JUSTIFIED"],
    )
    def test_c2_a_named_outcome_comes_back_as_given(self, outcome: str) -> None:
        document: dict[str, Any] = {
            "outcome": outcome,
            "decision": {"steps": []} if outcome == "PLAN" else None,
        }
        client, _ = _answering(200, {"data": document})

        assert _compose(client) == document

    @pytest.mark.parametrize("body", [{"data": {}}, {"data": {"outcome": None}}, {}, {"data": []}])
    def test_c3_an_answer_without_an_outcome_is_not_a_no_plan(self, body: Any) -> None:
        client, seen = _answering(200, body)

        with pytest.raises(PlanEngineUnavailableError, match="outcome_missing"):
            _compose(client)
        assert len(seen) == 1


class TestTheRefusals:
    def test_c4_switched_off_in_the_catalog(self) -> None:
        client, _ = _answering(404, {"error": {"code": "PLAN_ENGINE_DISABLED"}})

        with pytest.raises(PlanEngineDisabledError):
            _compose(client)

    def test_c4_a_rejected_request_names_the_violation(self) -> None:
        client, _ = _answering(
            400,
            {
                "error": {
                    "code": "PLAN_CONTRACT_VIOLATION",
                    "details": {"reason": "rules_registry_incomplete"},
                }
            },
        )

        with pytest.raises(PlanEngineContractError) as caught:
            _compose(client)
        assert caught.value.reason == "rules_registry_incomplete"

    @pytest.mark.parametrize("status", [401, 403])
    def test_c4_auth(self, status: int) -> None:
        client, _ = _answering(status, {})

        with pytest.raises(PlanEngineAuthError):
            _compose(client)

    @pytest.mark.parametrize(
        ("status", "body"), [(500, {}), (503, {}), (200, "not json"), (418, {})]
    )
    def test_c4_everything_else_is_unavailable(self, status: int, body: Any) -> None:
        client, _ = _answering(status, body)

        with pytest.raises(PlanEngineUnavailableError):
            _compose(client)

    def test_c4_a_network_failure_is_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        with pytest.raises(PlanEngineUnavailableError, match="network"):
            _compose(_client(handler))


class TestTheConfiguration:
    @pytest.mark.parametrize(
        ("base_url", "token"), [("https://ayla.test", ""), ("", "t"), ("not a url", "t")]
    )
    def test_c5_nothing_is_sent_without_an_address_or_a_token(
        self, base_url: str, token: str
    ) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"data": {"outcome": "NO_GOAL"}})

        client = PlanEngineHttpClient(
            base_url=base_url,
            token=token,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        with pytest.raises(PlanEngineConfigError):
            _compose(client)
        # Положительная пара: тот же обработчик отвечает, когда настройки есть.
        assert _compose(_client(handler)) == {"outcome": "NO_GOAL"}
        assert len(seen) == 1
