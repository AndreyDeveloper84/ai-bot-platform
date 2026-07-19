"""Tests for the Ayla personal-context internal client (frozen contract v1.0).

Wire is faked with ``httpx.MockTransport`` injected into
:class:`PersonalContextHttpClient` — no sockets. Auth/URL shape, envelope
unwrapping, forward-compat, and the idempotency-driven retry policy
(GET/PATCH retried; mark-asked/skip single-attempt) are all pinned here.
"""

from __future__ import annotations

import httpx
import pytest

from apps.integrations.ayla.personal_context_client import (
    PersonalContextAuthError,
    PersonalContextClientError,
    PersonalContextHttpClient,
    PersonalContextNotFoundError,
    PersonalContextTransportError,
)

_BASE = "https://ayla.test"
_TOKEN = "TOKEN-SENTINEL"  # noqa: S105  # pragma: allowlist secret
_UID = "11111111-2222-3333-4444-555555555555"


def _client(handler, **kwargs) -> PersonalContextHttpClient:
    return PersonalContextHttpClient(
        base_url=_BASE,
        token=_TOKEN,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def _ctx_payload(**context) -> dict:
    return {
        "data": {
            "ayla_user_id": _UID,
            "context": context,
            "meta": {
                "filled_fields": len([v for v in context.values() if v]),
                "updated_at": "2026-07-18T12:00:00Z",
            },
        }
    }


class TestGetContext:
    def test_happy_path_envelope(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json=_ctx_payload(diet_type="vegan"))

        out = _client(handler).get_context(ayla_user_id=_UID)

        assert seen["url"] == f"{_BASE}/api/v1/internal/users/{_UID}/personal-context/"
        assert seen["auth"] == f"Bearer {_TOKEN}"
        assert out.ayla_user_id == _UID
        assert out.context == {"diet_type": "vegan"}
        assert out.filled_fields == 1
        assert out.updated_at == "2026-07-18T12:00:00Z"

    def test_unknown_keys_preserved(self) -> None:
        """Forward-compat: fields added after v1.0 must ride through."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_ctx_payload(future_field={"x": 1}, diet_type=""))

        out = _client(handler).get_context(ayla_user_id=_UID)

        assert out.context["future_field"] == {"x": 1}
        assert out.context["diet_type"] == ""

    def test_bare_payload_tolerated(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"context": {"busy_days": ["mon"]}})

        out = _client(handler).get_context(ayla_user_id=_UID)

        assert out.context == {"busy_days": ["mon"]}
        assert out.filled_fields is None


class TestPatchContext:
    def test_sends_updates_envelope(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.content.decode()
            seen["method"] = request.method
            return httpx.Response(200, json=_ctx_payload(price_range_max="2000.00"))

        out = _client(handler).patch_context(
            ayla_user_id=_UID,
            updates=[{"field": "price_range_max", "value": "2000.00"}],
        )

        assert seen["method"] == "PATCH"
        assert '"updates"' in seen["body"]
        assert "price_range_max" in seen["body"]
        assert out.context == {"price_range_max": "2000.00"}

    def test_empty_updates_rejected_locally(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("wire must not be hit")

        with pytest.raises(PersonalContextClientError):
            _client(handler).patch_context(ayla_user_id=_UID, updates=[])

    def test_too_many_updates_rejected_locally(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("wire must not be hit")

        updates = [{"field": f"f{i}", "value": i} for i in range(11)]
        with pytest.raises(PersonalContextClientError, match="TOO_MANY_FIELDS"):
            _client(handler).patch_context(ayla_user_id=_UID, updates=updates)


class TestAskEligibility:
    def test_should_ask_true(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "should_ask": True,
                        "field": "preferred_time_slots",
                        "reason_code": None,
                        "explain": "ok",
                        "prompt_hint": "Тебе удобнее утром или вечером?",
                    }
                },
            )

        out = _client(handler).get_ask_eligibility(ayla_user_id=_UID)

        assert out.should_ask is True
        assert out.field == "preferred_time_slots"
        assert out.prompt_hint == "Тебе удобнее утром или вечером?"
        assert out.blocked_by is None

    def test_should_ask_false_blocked(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"data": {"should_ask": False, "blocked_by": "cooldown"}}
            )

        out = _client(handler).get_ask_eligibility(ayla_user_id=_UID)

        assert out.should_ask is False
        assert out.blocked_by == "cooldown"
        assert out.field is None


class TestMarkAskedSkip:
    def test_mark_asked_posts_field(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = request.content.decode()
            return httpx.Response(200, json={"data": {"ok": True}})

        _client(handler).mark_asked(ayla_user_id=_UID, field="diet_type")

        assert seen["url"].endswith(f"/api/v1/internal/users/{_UID}/personal-context/mark-asked/")
        assert '"diet_type"' in seen["body"]

    def test_skip_returns_count(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"ok": True, "skip_count": 2}})

        assert _client(handler).skip(ayla_user_id=_UID, field="diet_type") == 2

    def test_non_idempotent_verbs_never_retried(self) -> None:
        """A 5xx on mark-asked must surface after ONE attempt — a blind
        retry could double-stamp the cooldown."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(500, json={"error": "boom"})

        with pytest.raises(PersonalContextTransportError):
            _client(handler, retries=3).mark_asked(ayla_user_id=_UID, field="diet_type")

        assert calls["n"] == 1


class TestRetryPolicy:
    def test_get_retries_on_5xx(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(500, json={})
            return httpx.Response(200, json=_ctx_payload())

        out = _client(handler, retries=3).get_context(ayla_user_id=_UID)

        assert calls["n"] == 3
        assert out.context == {}

    def test_get_exhausts_retries(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(500, json={})

        with pytest.raises(PersonalContextTransportError):
            _client(handler, retries=2).get_context(ayla_user_id=_UID)

        assert calls["n"] == 2


class TestErrorMapping:
    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_error(self, status: int) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={})

        with pytest.raises(PersonalContextAuthError):
            _client(handler).get_context(ayla_user_id=_UID)

    def test_not_found(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404, json={"error": {"code": "USER_NOT_FOUND", "message": "User not found."}}
            )

        with pytest.raises(PersonalContextNotFoundError):
            _client(handler).get_context(ayla_user_id=_UID)

    def test_validation_error_carries_code(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400, json={"error": {"code": "VALIDATION_ERROR", "message": "bad field"}}
            )

        with pytest.raises(PersonalContextClientError, match="VALIDATION_ERROR"):
            _client(handler).patch_context(
                ayla_user_id=_UID, updates=[{"field": "nope", "value": 1}]
            )

    def test_missing_token_fails_closed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("wire must not be hit")

        client = PersonalContextHttpClient(
            base_url=_BASE,
            token="",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        with pytest.raises(PersonalContextTransportError, match="TOKEN"):
            client.get_context(ayla_user_id=_UID)


class TestPersonalDataExportDelete:
    """C5 legs (PILOT_CONTRACTS_2026-08-15 §6)."""

    def test_export_get(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            return httpx.Response(
                200, json={"data": {"profile": {"display_name": "М"}, "context": {}}}
            )

        out = _client(handler).get_personal_data_export(ayla_user_id=_UID)

        assert seen["method"] == "GET"
        assert seen["url"] == f"{_BASE}/api/v1/internal/users/{_UID}/personal-data/export/"
        assert out == {"profile": {"display_name": "М"}, "context": {}}

    def test_delete_204_no_content(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            return httpx.Response(204)

        _client(handler).delete_personal_data(ayla_user_id=_UID)

        assert seen["method"] == "DELETE"
        assert seen["url"] == f"{_BASE}/api/v1/internal/users/{_UID}/personal-data/"

    def test_delete_retried_on_5xx(self) -> None:
        """Server-side idempotent delete (C5) → transport retry is safe."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(500, json={})
            return httpx.Response(204)

        _client(handler, retries=2).delete_personal_data(ayla_user_id=_UID)

        assert calls["n"] == 2

    def test_delete_404_raises_not_found(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": {"code": "USER_NOT_FOUND"}})

        with pytest.raises(PersonalContextNotFoundError):
            _client(handler).delete_personal_data(ayla_user_id=_UID)
