"""Plan Lite в Mini App — прокси к каталогу под субъектом (DRF-2101, бот).

    GET    /customer/plan-lite  → get_wellness_context (.plan_lite)
    POST   /customer/plan-lite  → create_plan_lite({goal_id, actions})
    DELETE /customer/plan-lite  → close_plan_lite

Первый живой вызывающий ``wellness_context_client`` (до этого — только
запертая проактивность). Субъект — ``external_user_id_for(bot_user)`` из
подписанного initData. Флаг ``PLAN_LITE_ENABLED`` выключен → 404
``plan_lite_disabled`` ДО вызова каталога; коды каталога → свои слаги:
409 ``already_active``, 404 ``not_found`` / ``plan_lite_disabled``, 502
``ayla_unavailable``, 503 ``not_configured``. Тел ответов в логах нет.
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
    PlanLiteAlreadyActiveError,
    PlanLiteDisabledError,
    WellnessContext,
    WellnessContextUnavailableError,
)
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-plan-lite"  # noqa: S105 — test fixture  # pragma: allowlist secret
EXT = "bot:max:21010"
GOAL = "0b6f3c2e-9d1a-4c55-8e2f-2101aaaa0001"
PLAN = PlanLite(
    plan_id="0b6f3c2e-9d1a-4c55-8e2f-2101bbbb0001",
    goal_key="tone_up",
    actions=(PlanLiteAction("log_food", "per_week", 3, 1, "2026-09-14", "2026-09-21"),),
)
PLAN_JSON = {
    "plan_id": PLAN.plan_id,
    "goal_key": "tone_up",
    "actions": [
        {
            "action_type": "log_food",
            "cadence": "per_week",
            "target_count": 3,
            "done_count": 1,
            "bucket": {"start": "2026-09-14", "end": "2026-09-21"},
        }
    ],
}


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
    t = Tenant.objects.create(slug="plan-lite-test", name="Plan Lite", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "plan-lite-test"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="21010", display_name="Анна"
    )


def _url() -> str:
    return reverse("miniapp_api:customer_plan_lite")


class _FakeClient:
    def __init__(self, *, ctx=None, create=None, close=None):
        self.ctx = ctx
        self.create = create
        self.close = close
        self.calls: list[tuple] = []

    def get_wellness_context(self, *, external_user_id):
        self.calls.append(("get", external_user_id))
        if isinstance(self.ctx, Exception):
            raise self.ctx
        return self.ctx

    def create_plan_lite(self, *, external_user_id, goal_id, actions):
        self.calls.append(("create", external_user_id, goal_id, actions))
        if isinstance(self.create, Exception):
            raise self.create
        return self.create

    def close_plan_lite(self, *, external_user_id):
        self.calls.append(("close", external_user_id))
        if isinstance(self.close, Exception):
            raise self.close
        return self.close


def _patch(fake: _FakeClient):
    return patch("apps.miniapp_api.views_plan_lite.WellnessContextHttpClient", return_value=fake)


class TestProxy:
    def test_get_returns_plan_lite_under_the_callers_external_id(
        self, client: Client, bot_user: BotUser
    ) -> None:
        fake = _FakeClient(ctx=WellnessContext(has_plan=False, gated=True, plan_lite=PLAN))
        with _patch(fake):
            resp = client.get(_url(), HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id))
        assert resp.status_code == 200, resp.content
        assert resp.json() == {"plan_lite": PLAN_JSON}
        assert fake.calls == [("get", EXT)]

    def test_get_without_a_plan_is_null(self, client: Client, bot_user: BotUser) -> None:
        fake = _FakeClient(ctx=WellnessContext(has_plan=False, gated=True, plan_lite=None))
        with _patch(fake):
            resp = client.get(_url(), HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id))
        assert resp.status_code == 200
        assert resp.json() == {"plan_lite": None}

    def test_post_proxies_the_body(self, client: Client, bot_user: BotUser) -> None:
        fake = _FakeClient(create=PLAN)
        body = {
            "goal_id": GOAL,
            "actions": [{"action_type": "log_food", "cadence": "per_week", "target_count": 3}],
        }
        with _patch(fake):
            resp = client.post(
                _url(),
                data=json.dumps(body),
                content_type="application/json",
                HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id),
            )
        assert resp.status_code == 201, resp.content
        assert resp.json() == {"plan_lite": PLAN_JSON}
        assert fake.calls == [("create", EXT, GOAL, body["actions"])]

    def test_delete_proxies(self, client: Client, bot_user: BotUser) -> None:
        fake = _FakeClient(close=True)
        with _patch(fake):
            resp = client.delete(_url(), HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id))
        assert resp.status_code == 200
        assert resp.json() == {"closed": True}
        assert fake.calls == [("close", EXT)]

    def test_already_active_is_409(self, client: Client, bot_user: BotUser) -> None:
        fake = _FakeClient(create=PlanLiteAlreadyActiveError("x"))
        with _patch(fake):
            resp = client.post(
                _url(),
                data=json.dumps({"goal_id": GOAL, "actions": []}),
                content_type="application/json",
                HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id),
            )
        assert resp.status_code == 409
        assert resp.json()["error"] == "already_active"

    def test_catalog_disabled_maps_to_plan_lite_disabled(
        self, client: Client, bot_user: BotUser
    ) -> None:
        """Флаг в боте включён, в каталоге нет — тот же слаг, что и у своего флага."""
        fake = _FakeClient(create=PlanLiteDisabledError("x"))
        with _patch(fake):
            resp = client.post(
                _url(),
                data=json.dumps({"goal_id": GOAL, "actions": []}),
                content_type="application/json",
                HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id),
            )
        assert resp.status_code == 404
        assert resp.json()["error"] == "plan_lite_disabled"

    def test_unavailable_is_502(self, client: Client, bot_user: BotUser) -> None:
        fake = _FakeClient(ctx=WellnessContextUnavailableError("down"))
        with _patch(fake):
            resp = client.get(_url(), HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id))
        assert resp.status_code == 502
        assert resp.json()["error"] == "ayla_unavailable"


class TestFlag:
    @pytest.mark.parametrize("method", ["get", "post", "delete"])
    def test_flag_off_is_404_plan_lite_disabled_and_ayla_not_called(
        self, client: Client, bot_user: BotUser, settings, method: str
    ) -> None:
        settings.PLAN_LITE_ENABLED = False
        fake = _FakeClient(
            ctx=WellnessContext(has_plan=False, plan_lite=PLAN), create=PLAN, close=True
        )
        with _patch(fake):
            call = getattr(client, method)
            resp = call(
                _url(),
                data=json.dumps({"goal_id": GOAL, "actions": []}) if method == "post" else None,
                content_type="application/json",
                HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id),
            )
        assert resp.status_code == 404, resp.content
        assert resp.json()["error"] == "plan_lite_disabled"
        assert fake.calls == []
