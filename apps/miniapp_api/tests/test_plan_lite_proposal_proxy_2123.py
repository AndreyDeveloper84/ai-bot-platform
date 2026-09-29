"""План-A в Mini App — предложение Plan Lite из шаблона (DRF-2123, бот).

    GET  /customer/plan-lite/proposal → get_plan_lite_proposal
    POST /customer/plan-lite          → create_plan_lite(..., template_version)

Субъект — ``external_user_id_for(bot_user)`` из подписанного initData. Флаг
``PLAN_LITE_ENABLED`` выключен → 404 ``plan_lite_disabled`` ДО вызова
каталога. Отказы каталога — своими слагами: 404 ``no_active_goal``, 404
``no_template``, 404 ``plan_lite_disabled``; 502 ``ayla_unavailable``, 503
``not_configured`` — как у соседей. POST прокидывает ``template_version``
как есть (целое ≥ 1), без него — ``None``; не целое → 400 ``malformed``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.integrations.ayla.wellness_context_client import (
    PlanLite,
    PlanLiteAction,
    PlanLiteDisabledError,
    PlanLiteGoalNotFoundError,
    PlanLiteNoTemplateError,
    PlanLiteProposal,
    PlanLiteProposalAction,
    WellnessContextConfigError,
    WellnessContextUnavailableError,
)
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-plan-lite-2123"  # noqa: S105 — test fixture  # pragma: allowlist secret
EXT = "bot:max:21230"
PROPOSAL = PlanLiteProposal(
    goal_key="self_care",
    why="Забота о себе — это регулярность, а не подвиг.",
    template_version=2,
    actions=(
        PlanLiteProposalAction("book_service", "per_2_weeks", 1),
        PlanLiteProposalAction("log_food", "per_week", 3),
        PlanLiteProposalAction("log_water", "per_day", 6),
    ),
)
PROPOSAL_JSON = {
    "goal_key": "self_care",
    "why": "Забота о себе — это регулярность, а не подвиг.",
    "template_version": 2,
    "actions": [
        {"action_type": "book_service", "cadence": "per_2_weeks", "target_count": 1},
        {"action_type": "log_food", "cadence": "per_week", "target_count": 3},
        {"action_type": "log_water", "cadence": "per_day", "target_count": 6},
    ],
}
PLAN = PlanLite(
    plan_id="0b6f3c2e-9d1a-4c55-8e2f-2123bbbb0001",
    goal_key="self_care",
    actions=(PlanLiteAction("book_service", "per_2_weeks", 1, 0, "2026-09-19", "2026-10-03"),),
)


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _auth(user_id: str) -> str:
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
    settings.PLAN_LITE_ENABLED = True


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(slug="plan-lite-2123", name="Plan Lite", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "plan-lite-2123"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="21230", display_name="Анна"
    )


def _proposal_url() -> str:
    return reverse("miniapp_api:customer_plan_lite_proposal")


def _plan_url() -> str:
    return reverse("miniapp_api:customer_plan_lite")


class _FakeClient:
    def __init__(self, *, proposal=None, create=None):
        self.proposal = proposal
        self.create = create
        self.calls: list[tuple] = []

    def get_plan_lite_proposal(self, *, external_user_id):
        self.calls.append(("proposal", external_user_id))
        if isinstance(self.proposal, Exception):
            raise self.proposal
        return self.proposal

    def create_plan_lite(self, *, external_user_id, goal_id, actions, template_version):
        self.calls.append(("create", external_user_id, goal_id, actions, template_version))
        if isinstance(self.create, Exception):
            raise self.create
        return self.create


def _patch(fake: _FakeClient):
    return patch("apps.miniapp_api.views_plan_lite.WellnessContextHttpClient", return_value=fake)


class TestProposalGet:
    def test_returns_the_proposal_under_the_callers_external_id(
        self, client: Client, bot_user: BotUser
    ) -> None:
        fake = _FakeClient(proposal=PROPOSAL)
        with _patch(fake):
            resp = client.get(_proposal_url(), HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id))
        assert resp.status_code == 200, resp.content
        assert resp.json() == {"proposal": PROPOSAL_JSON}
        assert fake.calls == [("proposal", EXT)]

    def test_route_is_get_only(self, client: Client, bot_user: BotUser) -> None:
        fake = _FakeClient(proposal=PROPOSAL)
        with _patch(fake):
            resp = client.post(
                _proposal_url(),
                data="{}",
                content_type="application/json",
                HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id),
            )
        assert resp.status_code == 405
        assert fake.calls == []

    def test_flag_off_is_404_plan_lite_disabled_and_ayla_not_called(
        self, client: Client, bot_user: BotUser, settings
    ) -> None:
        settings.PLAN_LITE_ENABLED = False
        fake = _FakeClient(proposal=PROPOSAL)
        with _patch(fake):
            resp = client.get(_proposal_url(), HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id))
        assert resp.status_code == 404, resp.content
        assert resp.json()["error"] == "plan_lite_disabled"
        assert fake.calls == []

    @pytest.mark.parametrize(
        ("exc", "status", "slug"),
        [
            (PlanLiteGoalNotFoundError("no_active_goal"), 404, "no_active_goal"),
            (PlanLiteNoTemplateError("no_template"), 404, "no_template"),
            (PlanLiteDisabledError("plan_lite_disabled"), 404, "plan_lite_disabled"),
            (WellnessContextUnavailableError("down"), 502, "ayla_unavailable"),
            (WellnessContextConfigError("no token"), 503, "not_configured"),
        ],
        ids=["no-active-goal", "no-template", "catalog-disabled", "unavailable", "not-configured"],
    )
    def test_refusals_by_slug(
        self, client: Client, bot_user: BotUser, exc: Exception, status: int, slug: str
    ) -> None:
        fake = _FakeClient(proposal=exc)
        with _patch(fake):
            resp = client.get(_proposal_url(), HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id))
        assert resp.status_code == status, resp.content
        assert resp.json()["error"] == slug

    def test_without_init_data_is_401_before_the_catalog(self, client: Client, tenant) -> None:
        fake = _FakeClient(proposal=PROPOSAL)
        with _patch(fake):
            resp = client.get(_proposal_url())
        assert resp.status_code == 401
        assert fake.calls == []


class TestCreateTemplateVersion:
    def _post(self, client: Client, bot_user: BotUser, body: dict):
        return client.post(
            _plan_url(),
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id),
        )

    def test_template_version_is_passed_to_the_catalog(
        self, client: Client, bot_user: BotUser
    ) -> None:
        fake = _FakeClient(create=PLAN)
        actions = PROPOSAL_JSON["actions"]
        with _patch(fake):
            resp = self._post(client, bot_user, {"actions": actions, "template_version": 2})
        assert resp.status_code == 201, resp.content
        assert fake.calls == [("create", EXT, None, actions, 2)]
        assert resp.json()["plan_lite"]["actions"][0]["cadence"] == "per_2_weeks"

    def test_without_template_version_none_is_passed(
        self, client: Client, bot_user: BotUser
    ) -> None:
        fake = _FakeClient(create=PLAN)
        actions = PROPOSAL_JSON["actions"]
        with _patch(fake):
            resp = self._post(client, bot_user, {"actions": actions})
        assert resp.status_code == 201, resp.content
        assert fake.calls == [("create", EXT, None, actions, None)]

    @pytest.mark.parametrize(
        "bad", ["2", 0, -1, True, 1.5, {}], ids=["str", "zero", "neg", "bool", "float", "obj"]
    )
    def test_non_integer_template_version_is_400_malformed_before_the_catalog(
        self, client: Client, bot_user: BotUser, bad
    ) -> None:
        fake = _FakeClient(create=PLAN)
        with _patch(fake):
            resp = self._post(
                client, bot_user, {"actions": PROPOSAL_JSON["actions"], "template_version": bad}
            )
        assert resp.status_code == 400, resp.content
        assert resp.json()["error"] == "malformed"
        assert fake.calls == []
