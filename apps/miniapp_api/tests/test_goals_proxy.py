"""Tests for /customer/decision-context + /customer/goals/select Ayla proxy (DRF-1190).

Covers:
- Views: happy pass-through (GET document, POST select → updated document),
  malformed body, Ayla 4xx forwarding, config error, Ayla outage.
- Client: URL/headers seam, HTTP error mapping (timeout / 5xx / 4xx /
  malformed JSON), circuit breaker policy.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from unittest.mock import patch
from urllib.parse import urlencode

import httpx
import pytest
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.integrations.ayla import goals_client as gc
from apps.integrations.ayla.goals_client import (
    GoalsBadRequest,
    GoalsConfigError,
    GoalsUnavailable,
    fetch_decision_context,
    post_goal_select,
    reset_goals_circuit,
)
from apps.tenancy.models import Tenant


BOT_TOKEN = "test-bot-token-goals"  # noqa: S105 — test fixture  # pragma: allowlist secret

_DOC = {
    "version": 1,
    "known": {"goal": None},
    "missing": [{"kind": "goal", "prompt": "Что хочешь изменить?"}],
    "suggestions": [{"key": "relax", "label": "Расслабиться и восстановиться"}],
    "intents": [
        {"id": "choose_suggested", "label": "Выбрать из предложенного"},
        {"id": "formulate_own", "label": "Сформулирую своими словами"},
        {"id": "need_guidance", "label": "Не понимаю, чего хочу"},
    ],
}


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Ольга"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.AYLA_BASE_URL = "https://ayla.test"
    settings.AYLA_INTERNAL_API_TOKEN = "test-service-token"  # noqa: S105  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _reset_circuit():
    reset_goals_circuit()
    yield
    reset_goals_circuit()


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(
        slug="goals-test",
        name="Goals Test",
        timezone="Europe/Moscow",
    )
    settings.MAX_BOT_TENANT_SLUG = "goals-test"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="99999",
        display_name="Ольга",
    )


# ─── Views ──────────────────────────────────────────────────────────────


class TestDecisionContextView:
    def _url(self) -> str:
        return reverse("miniapp_api:customer_decision_context")

    def test_happy_path_pass_through(self, client: Client, bot_user: BotUser):
        captured: dict = {}

        def _fake(*, external_user_id: str) -> dict:
            captured["external_user_id"] = external_user_id
            return _DOC

        with patch(
            "apps.integrations.ayla.goals_client.fetch_decision_context",
            side_effect=_fake,
        ):
            resp = client.get(
                self._url(),
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )

        assert resp.status_code == 200
        assert resp.json() == _DOC
        assert captured["external_user_id"] == f"bot:max:{bot_user.channel_user_id}"

    def test_ayla_unavailable_returns_502(self, client: Client, bot_user: BotUser):
        with patch(
            "apps.integrations.ayla.goals_client.fetch_decision_context",
            side_effect=GoalsUnavailable("server: HTTP 503"),
        ):
            resp = client.get(
                self._url(),
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
        assert resp.status_code == 502
        assert resp.json()["error"] == "ayla_unavailable"

    def test_config_error_returns_503(self, client: Client, bot_user: BotUser):
        with patch(
            "apps.integrations.ayla.goals_client.fetch_decision_context",
            side_effect=GoalsConfigError("missing token"),
        ):
            resp = client.get(
                self._url(),
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
        assert resp.status_code == 503
        assert resp.json()["error"] == "not_configured"


class TestGoalSelectView:
    def _url(self) -> str:
        return reverse("miniapp_api:customer_goal_select")

    def test_happy_path_returns_updated_document(self, client: Client, bot_user: BotUser):
        updated = {**_DOC, "known": {"goal": {"goal_key": "relax"}}}
        captured: dict = {}

        def _fake(*, external_user_id: str, payload: dict) -> dict:
            captured["external_user_id"] = external_user_id
            captured["payload"] = payload
            return updated

        with patch(
            "apps.integrations.ayla.goals_client.post_goal_select",
            side_effect=_fake,
        ):
            resp = client.post(
                self._url(),
                data=json.dumps({"goal_key": "relax", "source_channel": "miniapp"}),
                content_type="application/json",
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )

        assert resp.status_code == 200
        assert resp.json() == updated
        assert captured["external_user_id"] == f"bot:max:{bot_user.channel_user_id}"
        assert captured["payload"] == {"goal_key": "relax", "source_channel": "miniapp"}

    def test_non_object_body_rejected(self, client: Client, bot_user: BotUser):
        with patch("apps.integrations.ayla.goals_client.post_goal_select") as mock_post:
            resp = client.post(
                self._url(),
                data=json.dumps([1, 2]),
                content_type="application/json",
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
        assert resp.status_code == 400
        assert resp.json()["error"] == "malformed"
        mock_post.assert_not_called()

    def test_ayla_4xx_forwarded(self, client: Client, bot_user: BotUser):
        with patch(
            "apps.integrations.ayla.goals_client.post_goal_select",
            side_effect=GoalsBadRequest(400, {"detail": "Provide exactly one of: ..."}),
        ):
            resp = client.post(
                self._url(),
                data=json.dumps({"goal_key": "a", "goal_text": "b", "source_channel": "miniapp"}),
                content_type="application/json",
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "ayla_bad_request"
        assert "ayla_error" in data

    def test_ayla_unavailable_returns_502(self, client: Client, bot_user: BotUser):
        with patch(
            "apps.integrations.ayla.goals_client.post_goal_select",
            side_effect=GoalsUnavailable("network: ConnectTimeout"),
        ):
            resp = client.post(
                self._url(),
                data=json.dumps({"goal_key": "relax", "source_channel": "miniapp"}),
                content_type="application/json",
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
        assert resp.status_code == 502
        assert resp.json()["error"] == "ayla_unavailable"


# ─── Client (HTTP layer) ────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, *, status_code: int, payload: object):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload)

    def json(self):
        if isinstance(self._payload, ValueError):
            raise self._payload
        return self._payload


class _FakeHttpxClient:
    def __init__(self, *, response=None, raise_exc=None):
        self._response = response
        self._raise_exc = raise_exc
        self.last_call: dict = {}
        # DRF-1435: клиент стал переиспользуемым пулом на весь процесс, а не
        # одноразовым контекстным менеджером — двойник обязан уметь то же.
        self.is_closed = False

    def close(self):
        self.is_closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def request(self, method: str, url: str, *, headers: dict, json: dict | None, timeout=None):
        self.last_call = {
            "method": method,
            "url": url,
            "headers": headers,
            "json": json,
            "timeout": timeout,
        }
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response


@pytest.fixture(autouse=True)
def _reset_goals_pool():
    """DRF-1435: пул живёт весь процесс, поэтому между тестами его надо
    сбрасывать — иначе первый закешированный двойник обслужит и остальные."""
    gc.close_goals_client()
    yield
    gc.close_goals_client()


class TestGoalsClient:
    def test_decision_context_url_and_headers(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        fake = _FakeHttpxClient(response=_FakeResponse(status_code=200, payload=_DOC))
        with patch(
            "apps.integrations.ayla.goals_client.httpx.Client",
            return_value=fake,
        ):
            out = fetch_decision_context(external_user_id="bot:max:1")
        assert out == _DOC
        assert fake.last_call["method"] == "GET"
        assert fake.last_call["url"] == ("https://ayla.test/api/v1/internal/me/decision-context/")
        assert fake.last_call["headers"]["Authorization"] == "Bearer tok"
        assert fake.last_call["headers"]["X-External-User-ID"] == "bot:max:1"

    def test_goal_select_posts_payload(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        fake = _FakeHttpxClient(response=_FakeResponse(status_code=200, payload=_DOC))
        with patch(
            "apps.integrations.ayla.goals_client.httpx.Client",
            return_value=fake,
        ):
            post_goal_select(
                external_user_id="bot:max:1",
                payload={"goal_key": "relax", "source_channel": "miniapp"},
            )
        assert fake.last_call["method"] == "POST"
        assert fake.last_call["url"] == ("https://ayla.test/api/v1/internal/me/goals/select/")
        assert fake.last_call["json"] == {"goal_key": "relax", "source_channel": "miniapp"}

    def test_config_error_when_token_missing(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = ""
        with pytest.raises(GoalsConfigError):
            fetch_decision_context(external_user_id="bot:max:1")

    def test_5xx_raises_unavailable(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        fake = _FakeHttpxClient(response=_FakeResponse(status_code=503, payload={"detail": "down"}))
        with patch(
            "apps.integrations.ayla.goals_client.httpx.Client",
            return_value=fake,
        ):
            with pytest.raises(GoalsUnavailable, match="server"):
                fetch_decision_context(external_user_id="bot:max:1")

    def test_timeout_raises_unavailable(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        fake = _FakeHttpxClient(raise_exc=httpx.ConnectTimeout("slow"))
        with patch(
            "apps.integrations.ayla.goals_client.httpx.Client",
            return_value=fake,
        ):
            with pytest.raises(GoalsUnavailable, match="network"):
                fetch_decision_context(external_user_id="bot:max:1")

    def test_4xx_raises_bad_request_with_body(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        fake = _FakeHttpxClient(
            response=_FakeResponse(status_code=400, payload={"detail": "exactly one"})
        )
        with patch(
            "apps.integrations.ayla.goals_client.httpx.Client",
            return_value=fake,
        ):
            with pytest.raises(GoalsBadRequest) as exc_info:
                post_goal_select(external_user_id="bot:max:1", payload={})
        assert exc_info.value.status_code == 400
        assert exc_info.value.body == {"detail": "exactly one"}

    def test_malformed_json_raises_unavailable(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        fake = _FakeHttpxClient(
            response=_FakeResponse(status_code=200, payload=ValueError("bad json"))
        )
        with patch(
            "apps.integrations.ayla.goals_client.httpx.Client",
            return_value=fake,
        ):
            with pytest.raises(GoalsUnavailable, match="malformed_json"):
                fetch_decision_context(external_user_id="bot:max:1")

    def test_breaker_opens_after_threshold_5xx(self, settings, monkeypatch):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        monkeypatch.setattr(gc, "_fire_breaker_alert", lambda transition, failures: None)
        fake = _FakeHttpxClient(response=_FakeResponse(status_code=503, payload={"detail": "down"}))
        with patch(
            "apps.integrations.ayla.goals_client.httpx.Client",
            return_value=fake,
        ):
            for _ in range(gc.CIRCUIT_FAILURE_THRESHOLD):
                with pytest.raises(GoalsUnavailable, match="server"):
                    fetch_decision_context(external_user_id="bot:max:1")
            with pytest.raises(GoalsUnavailable, match="circuit_open"):
                fetch_decision_context(external_user_id="bot:max:1")

    def test_4xx_does_not_trip_breaker(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        bad = _FakeHttpxClient(response=_FakeResponse(status_code=400, payload={"detail": "x"}))
        with patch(
            "apps.integrations.ayla.goals_client.httpx.Client",
            return_value=bad,
        ):
            for _ in range(gc.CIRCUIT_FAILURE_THRESHOLD + 1):
                with pytest.raises(GoalsBadRequest):
                    post_goal_select(external_user_id="bot:max:1", payload={})
        # DRF-1435: пул переживает выход из `patch`, поэтому подменить
        # двойника посреди теста без сброса пула нельзя — иначе второй
        # `patch` не имеет никакого эффекта, а тест молча проверяет первого
        # двойника дважды. Проверяемое утверждение («4xx не размыкает
        # предохранитель») от этого не меняется.
        gc.close_goals_client()
        ok = _FakeHttpxClient(response=_FakeResponse(status_code=200, payload=_DOC))
        with patch(
            "apps.integrations.ayla.goals_client.httpx.Client",
            return_value=ok,
        ):
            out = fetch_decision_context(external_user_id="bot:max:1")
        assert out == _DOC
