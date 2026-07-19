"""Tests for /customer/recommendations Ayla proxy.

Covers:
- View: happy pass-through, Ayla 5xx graceful, invalid body, Ayla 4xx
  forwarding, config error.
- Client: HTTP layer error mapping (timeout / 5xx / 4xx / malformed JSON).
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
from apps.integrations.ayla import recommendations_client as rc
from apps.integrations.ayla.recommendations_client import (
    RecommendationsBadRequest,
    RecommendationsConfigError,
    RecommendationsUnavailable,
    fetch_recommendations,
    reset_recommendations_circuit,
)
from apps.tenancy.models import Tenant


BOT_TOKEN = "test-bot-token-recommendations"  # noqa: S105 — test fixture  # pragma: allowlist secret


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
def _bot_token(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.AYLA_BASE_URL = "https://ayla.test"
    settings.AYLA_INTERNAL_API_TOKEN = "test-service-token"  # noqa: S105  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _reset_rec_circuit():
    """Isolate the module-level recommendations breaker between cases (#1048)."""
    reset_recommendations_circuit()
    yield
    reset_recommendations_circuit()


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(
        slug="rec-test",
        name="Recommendations Test",
        timezone="Europe/Moscow",
    )
    settings.MAX_BOT_TENANT_SLUG = "rec-test"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="88888",
        display_name="Ольга",
    )


# ─── TestViewIntegration ────────────────────────────────────────────────


class TestRecommendationsView:
    def _url(self) -> str:
        return reverse("miniapp_api:customer_recommendations")

    def test_happy_path_pass_through(self, client: Client, bot_user: BotUser):
        """Body forwarded, Ayla body returned verbatim, external_user_id set."""
        ayla_response = {
            "recommendations": [
                {"service_id": "svc-1", "score": 0.92},
                {"service_id": "svc-2", "score": 0.81},
            ],
            "trace_id": "ayla-trace-abc",
        }
        captured: dict = {}

        def _fake_fetch(*, external_user_id: str, payload: dict) -> dict:
            captured["external_user_id"] = external_user_id
            captured["payload"] = payload
            return ayla_response

        with patch(
            "apps.integrations.ayla.recommendations_client.fetch_recommendations",
            side_effect=_fake_fetch,
        ):
            resp = client.post(
                self._url(),
                data=json.dumps(
                    {"lat": 55.75, "lon": 37.61, "goal": "relax", "tenant_history": []}
                ),
                content_type="application/json",
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )

        assert resp.status_code == 200
        assert resp.json() == ayla_response
        assert captured["external_user_id"] == f"bot:max:{bot_user.channel_user_id}"
        assert captured["payload"] == {
            "lat": 55.75,
            "lon": 37.61,
            "goal": "relax",
            "tenant_history": [],
        }

    def test_ayla_5xx_graceful(self, client: Client, bot_user: BotUser):
        """Ayla outage → 502 ayla_unavailable (frontend retries)."""
        with patch(
            "apps.integrations.ayla.recommendations_client.fetch_recommendations",
            side_effect=RecommendationsUnavailable("server: HTTP 503"),
        ):
            resp = client.post(
                self._url(),
                data=json.dumps({"goal": "relax"}),
                content_type="application/json",
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )

        assert resp.status_code == 502
        assert resp.json()["error"] == "ayla_unavailable"

    def test_invalid_input_non_object_body(self, client: Client, bot_user: BotUser):
        """Body is a JSON array — view rejects with 400 before any Ayla call."""
        with patch(
            "apps.integrations.ayla.recommendations_client.fetch_recommendations"
        ) as mock_fetch:
            resp = client.post(
                self._url(),
                data=json.dumps([1, 2, 3]),
                content_type="application/json",
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )

        assert resp.status_code == 400
        assert resp.json()["error"] == "malformed"
        mock_fetch.assert_not_called()

    def test_ayla_4xx_forwarded(self, client: Client, bot_user: BotUser):
        """Ayla 4xx → 400 with ayla_error body forwarded for frontend display."""
        with patch(
            "apps.integrations.ayla.recommendations_client.fetch_recommendations",
            side_effect=RecommendationsBadRequest(422, {"detail": "lat/lon out of range"}),
        ):
            resp = client.post(
                self._url(),
                data=json.dumps({"lat": 999, "lon": 999}),
                content_type="application/json",
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )

        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "ayla_bad_request"
        assert data["ayla_error"] == {"detail": "lat/lon out of range"}

    def test_config_error_returns_503(self, client: Client, bot_user: BotUser):
        """Missing AYLA_INTERNAL_API_TOKEN → 503 not_configured."""
        with patch(
            "apps.integrations.ayla.recommendations_client.fetch_recommendations",
            side_effect=RecommendationsConfigError("AYLA_INTERNAL_API_TOKEN missing"),
        ):
            resp = client.post(
                self._url(),
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )

        assert resp.status_code == 503
        assert resp.json()["error"] == "not_configured"

    def test_empty_body_defaults_to_empty_dict(self, client: Client, bot_user: BotUser):
        """No body → empty {} forwarded to Ayla (caller had no scoring hints)."""
        captured: dict = {}

        def _fake_fetch(*, external_user_id: str, payload: dict) -> dict:
            captured["payload"] = payload
            return {"recommendations": []}

        with patch(
            "apps.integrations.ayla.recommendations_client.fetch_recommendations",
            side_effect=_fake_fetch,
        ):
            resp = client.post(
                self._url(),
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )

        assert resp.status_code == 200
        assert captured["payload"] == {}


# ─── TestClient (HTTP layer) ────────────────────────────────────────────


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

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, url: str, *, headers: dict, json: dict):
        self.last_call = {"url": url, "headers": headers, "json": json}
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response


class TestFetchRecommendations:
    def test_happy_path_returns_body(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        fake = _FakeHttpxClient(
            response=_FakeResponse(status_code=200, payload={"recommendations": []})
        )
        with patch(
            "apps.integrations.ayla.recommendations_client.httpx.Client",
            return_value=fake,
        ):
            out = fetch_recommendations(
                external_user_id="bot:max:1",
                payload={"goal": "relax"},
            )
        assert out == {"recommendations": []}
        assert fake.last_call["url"] == (
            "https://ayla.test/api/v1/internal/me/catalog/recommendations/"
        )
        assert fake.last_call["headers"]["Authorization"] == "Bearer tok"
        assert fake.last_call["headers"]["X-External-User-ID"] == "bot:max:1"
        assert fake.last_call["json"] == {"goal": "relax"}

    def test_config_error_when_token_missing(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = ""
        with pytest.raises(RecommendationsConfigError):
            fetch_recommendations(external_user_id="bot:max:1", payload={})

    def test_5xx_raises_unavailable(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        fake = _FakeHttpxClient(response=_FakeResponse(status_code=503, payload={"detail": "down"}))
        with patch(
            "apps.integrations.ayla.recommendations_client.httpx.Client",
            return_value=fake,
        ):
            with pytest.raises(RecommendationsUnavailable, match="server"):
                fetch_recommendations(external_user_id="bot:max:1", payload={})

    def test_timeout_raises_unavailable(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        fake = _FakeHttpxClient(raise_exc=httpx.ConnectTimeout("slow"))
        with patch(
            "apps.integrations.ayla.recommendations_client.httpx.Client",
            return_value=fake,
        ):
            with pytest.raises(RecommendationsUnavailable, match="network"):
                fetch_recommendations(external_user_id="bot:max:1", payload={})

    def test_4xx_raises_bad_request_with_body(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        fake = _FakeHttpxClient(
            response=_FakeResponse(status_code=422, payload={"detail": "lat/lon out of range"})
        )
        with patch(
            "apps.integrations.ayla.recommendations_client.httpx.Client",
            return_value=fake,
        ):
            with pytest.raises(RecommendationsBadRequest) as exc_info:
                fetch_recommendations(external_user_id="bot:max:1", payload={})
        assert exc_info.value.status_code == 422
        assert exc_info.value.body == {"detail": "lat/lon out of range"}

    def test_malformed_json_raises_unavailable(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        fake = _FakeHttpxClient(
            response=_FakeResponse(status_code=200, payload=ValueError("bad json"))
        )
        with patch(
            "apps.integrations.ayla.recommendations_client.httpx.Client",
            return_value=fake,
        ):
            with pytest.raises(RecommendationsUnavailable, match="malformed_json"):
                fetch_recommendations(external_user_id="bot:max:1", payload={})

    def test_non_dict_top_level_raises(self, settings):
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        fake = _FakeHttpxClient(response=_FakeResponse(status_code=200, payload=["a", "list"]))
        with patch(
            "apps.integrations.ayla.recommendations_client.httpx.Client",
            return_value=fake,
        ):
            with pytest.raises(RecommendationsUnavailable, match="not an object"):
                fetch_recommendations(external_user_id="bot:max:1", payload={})

    def test_breaker_opens_after_threshold_5xx(self, settings, monkeypatch):
        """5 consecutive 5xx → breaker opens; next call short-circuits (#1048)."""
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        # Silence the Telegram alert path — the breaker is the unit under test.
        monkeypatch.setattr(rc, "_fire_breaker_alert", lambda transition, failures: None)
        fake = _FakeHttpxClient(response=_FakeResponse(status_code=503, payload={"detail": "down"}))
        with patch(
            "apps.integrations.ayla.recommendations_client.httpx.Client",
            return_value=fake,
        ):
            for _ in range(rc.CIRCUIT_FAILURE_THRESHOLD):
                with pytest.raises(RecommendationsUnavailable, match="server"):
                    fetch_recommendations(external_user_id="bot:max:1", payload={})
            # Threshold reached — the next call short-circuits BEFORE the
            # transport, so the message is ``circuit_open`` not ``server``.
            with pytest.raises(RecommendationsUnavailable, match="circuit_open"):
                fetch_recommendations(external_user_id="bot:max:1", payload={})

    def test_4xx_does_not_trip_breaker(self, settings):
        """A run of 4xx (we sent garbage, Ayla is healthy) must NOT open the breaker."""
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret
        bad = _FakeHttpxClient(response=_FakeResponse(status_code=422, payload={"detail": "x"}))
        with patch(
            "apps.integrations.ayla.recommendations_client.httpx.Client",
            return_value=bad,
        ):
            for _ in range(rc.CIRCUIT_FAILURE_THRESHOLD + 1):
                with pytest.raises(RecommendationsBadRequest):
                    fetch_recommendations(external_user_id="bot:max:1", payload={})
        # Breaker is still closed: a subsequent healthy call goes through.
        ok = _FakeHttpxClient(
            response=_FakeResponse(status_code=200, payload={"recommendations": []})
        )
        with patch(
            "apps.integrations.ayla.recommendations_client.httpx.Client",
            return_value=ok,
        ):
            out = fetch_recommendations(external_user_id="bot:max:1", payload={})
        assert out == {"recommendations": []}
