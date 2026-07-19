"""Profile client unit tests (#446 / #978).

The #446 ``user.profile.updated`` consumer fetches the PII subset
(``display_name`` + ``avatar_url`` only) from Ayla. These tests pin the
post-migration contract (#978):

* URL is built through :class:`AylaUrlBuilder` →
  ``{base}/api/v1/internal/users/{user_id}/`` (was ``/api/v1/users/{id}``).
* Auth is ``Authorization: Bearer {AYLA_INTERNAL_API_TOKEN}`` (was the
  deprecated ``AYLA_SERVICE_TOKEN``).
* Error mapping (config / 404 / 5xx / malformed) and the circuit breaker.

No network — ``httpx.Client`` is patched with a fake that records the last
call so the URL + headers can be asserted.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch
from uuid import UUID

import httpx
import pytest

from apps.integrations.ayla import profile_client as pc
from apps.integrations.ayla.profile_client import ProfileFetchError, fetch_profile_fields


_USER_ID = UUID("11111111-2222-3333-4444-555555555555")


@pytest.fixture(autouse=True)
def _config(settings):
    settings.AYLA_BASE_URL = "https://ayla.test"
    settings.AYLA_INTERNAL_API_TOKEN = "tok"  # noqa: S105  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _reset_circuit():
    """Isolate the module-level profile breaker between cases."""
    pc._circuit.record_success()
    yield
    pc._circuit.record_success()


class _FakeResponse:
    def __init__(self, *, status_code: int, payload: object):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
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

    def get(self, url: str, *, headers: dict):
        self.last_call = {"url": url, "headers": headers}
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response


def _patched(fake: _FakeHttpxClient):
    return patch("apps.integrations.ayla.profile_client.httpx.Client", return_value=fake)


class TestFetchProfileFields:
    def test_happy_path_returns_fields(self):
        fake = _FakeHttpxClient(
            response=_FakeResponse(
                status_code=200,
                payload={"display_name": "Ольга", "avatar_url": "https://cdn/a.jpg"},
            )
        )
        with _patched(fake):
            fields = fetch_profile_fields(_USER_ID)
        assert fields.display_name == "Ольга"
        assert fields.avatar_url == "https://cdn/a.jpg"

    def test_url_and_bearer_token(self):
        """Migrated path (#978): /api/v1/internal/users/{uuid}/ + Bearer internal token."""
        fake = _FakeHttpxClient(
            response=_FakeResponse(
                status_code=200,
                payload={"display_name": "n", "avatar_url": "u"},
            )
        )
        with _patched(fake):
            fetch_profile_fields(_USER_ID)
        assert fake.last_call["url"] == f"https://ayla.test/api/v1/internal/users/{_USER_ID}/"
        assert fake.last_call["headers"]["Authorization"] == "Bearer tok"

    def test_extra_fields_dropped(self):
        """PII §7: extra fields (phone/email) never propagate past the closed shape."""
        fake = _FakeHttpxClient(
            response=_FakeResponse(
                status_code=200,
                payload={
                    "display_name": "n",
                    "avatar_url": "u",
                    "phone": "+79990000000",
                    "email": "leak@example.com",
                },
            )
        )
        with _patched(fake):
            fields = fetch_profile_fields(_USER_ID)
        assert not hasattr(fields, "phone")
        assert not hasattr(fields, "email")

    def test_config_error_when_token_missing(self, settings):
        settings.AYLA_INTERNAL_API_TOKEN = ""
        with pytest.raises(ProfileFetchError, match="AYLA_INTERNAL_API_TOKEN"):
            fetch_profile_fields(_USER_ID)

    def test_404_raises_not_found(self):
        fake = _FakeHttpxClient(response=_FakeResponse(status_code=404, payload={}))
        with _patched(fake):
            with pytest.raises(ProfileFetchError, match="not_found"):
                fetch_profile_fields(_USER_ID)

    def test_401_raises_auth(self):
        fake = _FakeHttpxClient(response=_FakeResponse(status_code=401, payload={}))
        with _patched(fake):
            with pytest.raises(ProfileFetchError, match="auth"):
                fetch_profile_fields(_USER_ID)

    def test_5xx_raises_server(self):
        fake = _FakeHttpxClient(response=_FakeResponse(status_code=503, payload={}))
        with _patched(fake):
            with pytest.raises(ProfileFetchError, match="server"):
                fetch_profile_fields(_USER_ID)

    def test_missing_keys_raises_malformed(self):
        """Key absent (not empty string) → refuse to overwrite the local mirror."""
        fake = _FakeHttpxClient(
            response=_FakeResponse(status_code=200, payload={"display_name": "n"})
        )
        with _patched(fake):
            with pytest.raises(ProfileFetchError, match="missing keys"):
                fetch_profile_fields(_USER_ID)

    def test_malformed_json_raises(self):
        fake = _FakeHttpxClient(
            response=_FakeResponse(status_code=200, payload=ValueError("bad json"))
        )
        with _patched(fake):
            with pytest.raises(ProfileFetchError, match="malformed_json"):
                fetch_profile_fields(_USER_ID)

    def test_network_error_raises(self):
        fake = _FakeHttpxClient(raise_exc=httpx.ConnectTimeout("slow"))
        with _patched(fake):
            with pytest.raises(ProfileFetchError, match="network"):
                fetch_profile_fields(_USER_ID)

    def test_circuit_opens_after_threshold(self, monkeypatch: pytest.MonkeyPatch):
        """5 consecutive 5xx → breaker opens; next call short-circuits."""
        # Silence the Telegram alert path — the breaker is the unit under test.
        monkeypatch.setattr(pc, "_fire_breaker_alert", lambda transition, failures: None)
        fake = _FakeHttpxClient(response=_FakeResponse(status_code=503, payload={}))
        with _patched(fake):
            for _ in range(pc.CIRCUIT_FAILURE_THRESHOLD):
                with pytest.raises(ProfileFetchError, match="server"):
                    fetch_profile_fields(_USER_ID)
            with pytest.raises(ProfileFetchError, match="circuit open"):
                fetch_profile_fields(_USER_ID)


def test_fake_response_json_roundtrips():
    """Guard the test double itself — json() returns the payload verbatim."""
    payload = {"display_name": "x", "avatar_url": "y"}
    assert _FakeResponse(status_code=200, payload=payload).json() == json.loads(json.dumps(payload))
