"""Прокси сборки плана: бот приносит реестр правил и состояние безопасности (DRF-2879).

    POST /customer/plan/decision → PlanEngineHttpClient.compose_decision

До этого листа у загрузчика реестра правил не было ни одного потребителя, и
сборка плана в каталоге не могла быть вызвана: реестр она требует в теле.

* p1 — флаг выключен → 404 ``plan_engine_disabled`` ДО чтения реестра и ДО
  каталога;
* p2 — в каталог уходит НАСТОЯЩИЙ реестр в форме провода, субъект — из
  подписанных данных Mini App;
* p3 — реестр не загрузился → 503 ``plan_rules_unavailable``, запрос в каталог
  не отправлен, пустой реестр не подставлен;
* s1 — состояние безопасности: бот его не оценивал → уходит ``UNKNOWN`` с
  названной версией «не оценивалось»; ``NORMAL`` и ``NOT_APPLICABLE`` не
  уходят никогда;
* e1 — убранные способности передаются как есть; мусор → 400 до каталога;
* o1 — исходы без плана — ответы 200 с именем и без ``decision``; ``PLAN`` —
  с решением как есть;
* r1 — отказы каталога по именам.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from typing import Any
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.integrations.ayla.plan_engine_client import (
    PlanEngineAuthError,
    PlanEngineConfigError,
    PlanEngineContractError,
    PlanEngineDisabledError,
    PlanEngineUnavailableError,
)
from apps.planning_rules.registry import PlanningRegistryInvalidError, load_registry
from apps.planning_rules.wire import registry_wire_body
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-plan-engine-2879"  # noqa: S105 — test fixture  # pragma: allowlist secret
EXT = "bot:max:28790"
CLIENT = "apps.miniapp_api.views_plan_engine.PlanEngineHttpClient"
LOADER = "apps.miniapp_api.views_plan_engine.load_registry"

NO_PLAN = {"outcome": "NO_CURATED_DECOMPOSITION", "decision": None, "safety_state": "NORMAL"}
DECISION = {
    "decision_id": "0b6f3c2e-9d1a-4c55-8e2f-287900000001",
    "goal_ref": "0b6f3c2e-9d1a-4c55-8e2f-287900000002",
    "steps": [
        {"step_id": "s-1", "role": "OPTIONAL", "level": "CAPABILITY", "capability_ref": "relax"}
    ],
    "assertions": [],
    "validation": {"status": "INCOMPLETE", "step_validations": {"s-1": "INCOMPLETE"}},
}


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest})


def _auth(user_id: str = "28790") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Анна"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.AYLA_BASE_URL = "https://ayla.test"
    settings.AYLA_INTERNAL_API_TOKEN = "test-service-token"  # noqa: S105  # pragma: allowlist secret
    settings.PLAN_ENGINE_ENABLED = True


@pytest.fixture
def bot_user(db, settings) -> BotUser:
    tenant = Tenant.objects.create(slug="plan-engine-2879", name="Plan Engine")
    settings.MAX_BOT_TENANT_SLUG = "plan-engine-2879"
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="28790", display_name="Анна"
    )


def _post(client: Client, body: Any = None):
    return client.post(
        reverse("miniapp_api:customer_plan_decision"),
        data="" if body is None else json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=_auth(),
    )


def _sent(mocked) -> dict[str, Any]:
    return mocked.return_value.compose_decision.call_args.kwargs


class TestTheFlag:
    def test_p1_switched_off_nothing_is_read_or_called(self, client, bot_user, settings) -> None:
        settings.PLAN_ENGINE_ENABLED = False
        with patch(CLIENT) as mocked, patch(LOADER) as loader:
            resp = _post(client)

        assert resp.status_code == 404
        assert resp.json()["error"] == "plan_engine_disabled"
        loader.assert_not_called()
        mocked.assert_not_called()

    def test_p1_switched_on_the_same_request_reaches_the_catalog(self, client, bot_user) -> None:
        with patch(CLIENT) as mocked:
            mocked.return_value.compose_decision.return_value = NO_PLAN
            resp = _post(client)

        assert resp.status_code == 200, resp.content
        assert mocked.return_value.compose_decision.call_count == 1


class TestTheRegistry:
    def test_p2_the_real_registry_rides_in_wire_form(self, client, bot_user) -> None:
        with patch(CLIENT) as mocked:
            mocked.return_value.compose_decision.return_value = NO_PLAN
            _post(client)

        sent = _sent(mocked)
        assert sent["external_user_id"] == EXT
        assert sent["rules_registry"] == registry_wire_body(load_registry())
        # Не пусто и не частично: правил столько же, сколько в реестре.
        assert len(sent["rules_registry"]["rules"]) == len(load_registry().rules) >= 13

    def test_p3_a_registry_that_does_not_load_stops_the_request(self, client, bot_user) -> None:
        with (
            patch(CLIENT) as mocked,
            patch(LOADER, side_effect=PlanningRegistryInvalidError("broken")),
        ):
            resp = _post(client)

        assert resp.status_code == 503
        assert resp.json()["error"] == "plan_rules_unavailable"
        mocked.assert_not_called()

    def test_p3_the_same_request_with_a_healthy_registry_goes_through(
        self, client, bot_user
    ) -> None:
        """Положительная пара к p3: останавливает именно реестр."""
        with patch(CLIENT) as mocked:
            mocked.return_value.compose_decision.return_value = NO_PLAN
            resp = _post(client)

        assert resp.status_code == 200
        assert mocked.return_value.compose_decision.call_count == 1


class TestTheSafetyInput:
    def test_s1_not_evaluated_is_sent_as_unknown_and_named(self, client, bot_user) -> None:
        with patch(CLIENT) as mocked:
            mocked.return_value.compose_decision.return_value = {
                "outcome": "SAFETY_BLOCKED",
                "decision": None,
            }
            resp = _post(client)

        sent = _sent(mocked)
        assert (sent["safety_state"], sent["safety_policy_version"]) == (
            "UNKNOWN",
            "bot:not-evaluated",
        )
        assert resp.json() == {"outcome": "SAFETY_BLOCKED", "decision": None}


class TestTheExcludedCapabilities:
    def test_e1_an_empty_body_means_nothing_removed(self, client, bot_user) -> None:
        with patch(CLIENT) as mocked:
            mocked.return_value.compose_decision.return_value = NO_PLAN
            _post(client)

        assert _sent(mocked)["excluded_capability_refs"] == []

    def test_e1_the_removed_capabilities_ride_as_given(self, client, bot_user) -> None:
        with patch(CLIENT) as mocked:
            mocked.return_value.compose_decision.return_value = NO_PLAN
            _post(client, {"excluded_capability_refs": ["relax_back", " calm "]})

        assert _sent(mocked)["excluded_capability_refs"] == ["relax_back", "calm"]

    @pytest.mark.parametrize(
        "body",
        [
            {"excluded_capability_refs": "relax"},
            {"excluded_capability_refs": [1]},
            {"excluded_capability_refs": [""]},
            {"excluded_capability_refs": ["x" * 129]},
            {"excluded_capability_refs": ["k"] * 51},
            ["relax"],
        ],
    )
    def test_e1_junk_is_refused_before_the_catalog(self, client, bot_user, body) -> None:
        with patch(CLIENT) as mocked:
            resp = _post(client, body)

        assert resp.status_code == 400
        assert resp.json()["error"] == "malformed"
        mocked.assert_not_called()


class TestTheOutcomes:
    @pytest.mark.parametrize(
        "outcome",
        ["SAFETY_BLOCKED", "NO_GOAL", "NO_CURATED_DECOMPOSITION", "PLAN_NOT_JUSTIFIED"],
    )
    def test_o1_no_plan_is_an_answer_not_an_error(self, client, bot_user, outcome: str) -> None:
        with patch(CLIENT) as mocked:
            mocked.return_value.compose_decision.return_value = {
                "outcome": outcome,
                "decision": None,
                "details": {"internal": "x"},
            }
            resp = _post(client)

        assert resp.status_code == 200
        assert resp.json() == {"outcome": outcome, "decision": None}

    def test_o1_a_plan_carries_its_decision_as_given(self, client, bot_user) -> None:
        with patch(CLIENT) as mocked:
            mocked.return_value.compose_decision.return_value = {
                "outcome": "PLAN",
                "decision": DECISION,
            }
            resp = _post(client)

        assert resp.status_code == 200
        assert resp.json() == {"outcome": "PLAN", "decision": DECISION}


class TestTheRefusals:
    @pytest.mark.parametrize(
        ("exc", "status", "slug"),
        [
            (PlanEngineDisabledError("off"), 404, "plan_engine_disabled"),
            (PlanEngineContractError("rules_registry_incomplete"), 502, "ayla_unavailable"),
            (PlanEngineConfigError("no token"), 503, "not_configured"),
            (PlanEngineAuthError("403"), 502, "ayla_unavailable"),
            (PlanEngineUnavailableError("network"), 502, "ayla_unavailable"),
        ],
    )
    def test_r1_each_catalog_refusal_has_a_name(
        self, client, bot_user, exc: Exception, status: int, slug: str
    ) -> None:
        with patch(CLIENT) as mocked:
            mocked.return_value.compose_decision.side_effect = exc
            resp = _post(client)

        assert (resp.status_code, resp.json()["error"]) == (status, slug)

    def test_r1_without_init_data_is_401(self, client, bot_user) -> None:
        with patch(CLIENT) as mocked:
            resp = client.post(
                reverse("miniapp_api:customer_plan_decision"), content_type="application/json"
            )

        assert resp.status_code == 401
        mocked.assert_not_called()
