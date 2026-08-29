"""Tests for the Ayla wellness-context internal client (DRF-1344).

Wire is faked with ``httpx.MockTransport`` — no sockets. Pinned here:
auth/URL shape, envelope unwrapping, the gated document, forward-compat
on unknown keys, and the failure surface. The privacy property — DTOs
carry codes, never observation values — is pinned by construction tests
that feed the wire numeric values and read the DTO back.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from apps.integrations.ayla.wellness_context_client import (
    OutcomeState,
    WellnessContextAuthError,
    WellnessContextClientError,
    WellnessContextConfigError,
    WellnessContextHttpClient,
    WellnessContextUnavailableError,
)

_BASE = "https://ayla.test"
_TOKEN = "TOKEN-SENTINEL"  # noqa: S105  # pragma: allowlist secret
_EXT = "bot:max:wp-1"


def _client(handler, **kwargs) -> WellnessContextHttpClient:
    return WellnessContextHttpClient(
        base_url=_BASE,
        token=_TOKEN,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def _ok(payload: dict):
    return lambda _request: httpx.Response(200, json=payload)


def _outcome(**overrides) -> dict:
    base = {
        "target": "weight",
        "link_status": "linked",
        "horizon_status": "active",
        "progress_state": "no_observations",
    }
    base.update(overrides)
    return base


class TestGetWellnessContext:
    def test_happy_path_envelope_and_headers(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["ext"] = request.headers.get("x-external-user-id")
            return httpx.Response(
                200,
                json={
                    "data": {
                        "plan": {"code": "plan-1"},
                        "outcomes": [_outcome()],
                        "gated": None,
                    }
                },
            )

        out = _client(handler).get_wellness_context(external_user_id=_EXT)

        assert seen["url"] == f"{_BASE}/api/v1/internal/me/wellness-context/"
        assert seen["auth"] == f"Bearer {_TOKEN}"
        assert seen["ext"] == _EXT
        assert out.has_plan is True
        assert out.gated is False
        assert out.outcomes == (
            OutcomeState(
                target="weight",
                link_status="linked",
                horizon_status="active",
                progress_state="no_observations",
            ),
        )

    def test_gated_document(self) -> None:
        """Gates closed upstream: plan and rows are not disclosed at all."""
        out = _client(
            _ok({"data": {"plan": None, "outcomes": [], "gated": {"reason": "x"}}})
        ).get_wellness_context(external_user_id=_EXT)
        assert out.gated is True
        assert out.has_plan is False
        assert out.outcomes == ()

    def test_unknown_keys_ride_along_but_values_do_not_enter_dtos(self) -> None:
        """Forward-compat + the privacy property in one case.

        The wire may add keys additively — including numeric observation
        values next to the codes. The DTO has no fields for them, so the
        parsed context holds codes only; the values die at the parser.
        """
        payload = {
            "data": {
                "plan": {"code": "plan-1", "target_value": 71.8},
                "outcomes": [
                    _outcome(last_value=71.8, baseline_value=70.35, observations=2),
                ],
                "gated": None,
                "future_block": {"anything": True},
            }
        }
        out = _client(_ok(payload)).get_wellness_context(external_user_id=_EXT)

        outcome = out.outcomes[0]
        assert set(vars(outcome)) == {
            "target",
            "link_status",
            "horizon_status",
            "progress_state",
        }
        serialised = repr(out)
        assert "71.8" not in serialised
        assert "70.35" not in serialised
        # Positive guard on the same data: the codes ARE there.
        assert outcome.target == "weight"
        assert outcome.progress_state == "no_observations"

    def test_malformed_rows_are_skipped_not_fatal(self) -> None:
        # Разнородный по замыслу: смысл теста в том, что рядом с
        # корректной строкой лежит НЕ-словарь, поэтому литерал сам по
        # себе не сужается до одного типа значения.
        payload: dict[str, Any] = {
            "data": {"plan": {}, "outcomes": ["junk", _outcome()], "gated": None}
        }
        out = _client(_ok(payload)).get_wellness_context(external_user_id=_EXT)
        assert len(out.outcomes) == 1

    def test_missing_envelope_is_an_empty_document(self) -> None:
        out = _client(_ok({"unexpected": True})).get_wellness_context(external_user_id=_EXT)
        assert out.has_plan is False
        assert out.outcomes == ()
        assert out.gated is False


class TestFailures:
    def test_missing_token_is_a_config_error(self) -> None:
        client = WellnessContextHttpClient(base_url=_BASE, token="")
        with pytest.raises(WellnessContextConfigError):
            client.get_wellness_context(external_user_id=_EXT)

    def test_auth_error(self) -> None:
        client = _client(lambda _r: httpx.Response(403, json={"detail": "nope"}))
        with pytest.raises(WellnessContextAuthError):
            client.get_wellness_context(external_user_id=_EXT)

    def test_other_4xx_is_a_client_error(self) -> None:
        client = _client(lambda _r: httpx.Response(422, json={"error": {}}))
        with pytest.raises(WellnessContextClientError):
            client.get_wellness_context(external_user_id=_EXT)

    def test_5xx_is_unavailable(self) -> None:
        client = _client(lambda _r: httpx.Response(502, text="bad gateway"))
        with pytest.raises(WellnessContextUnavailableError):
            client.get_wellness_context(external_user_id=_EXT)

    def test_network_failure_is_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        with pytest.raises(WellnessContextUnavailableError):
            _client(handler).get_wellness_context(external_user_id=_EXT)

    def test_malformed_json_is_unavailable(self) -> None:
        client = _client(lambda _r: httpx.Response(200, content=b"not json"))
        with pytest.raises(WellnessContextUnavailableError):
            client.get_wellness_context(external_user_id=_EXT)
