"""Salon-surface client — the wire shape, pinned.

The auth shape here was decided twice: first as ``X-Service-Token`` (wrong —
that mechanism is keyed to the nutrition secret and scoped to nutrition by its
own docstring), then as ``Authorization: Bearer`` with
``IsBotServiceWithVerifiedClient``, which resolves the actor onto
``request.user`` so Ayla's existing ownership filters keep working.

Getting it wrong cost a round-trip between windows, so it is a test rather
than a comment.
"""

from __future__ import annotations

import json

import httpx
import pytest

from apps.integrations.ayla.salon_client import (
    AylaSalonClient,
    SalonForbidden,
    SalonNotConfigured,
    SalonNotFound,
    SalonSlotTaken,
    SalonUnavailable,
    SalonValidationError,
)

TOKEN = "internal-token-under-test"  # pragma: allowlist secret
ACTOR = "bot:max:83146139"


def _client(handler) -> AylaSalonClient:
    return AylaSalonClient(
        base_url="https://ayla.example",
        service_token=TOKEN,
        transport=httpx.MockTransport(handler),
    )


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(201, json={"id": "appt-1"})


def _create(client: AylaSalonClient):
    return client.create_appointment(
        actor_external_id=ACTOR,
        idempotency_key="key-1",
        specialist_id="m-1",
        service_id="s-1",
        start_datetime="2026-08-21T15:00:00+03:00",
        client_name="Мария",
        client_phone="+79990000000",
    )


class TestWireShape:
    def test_authenticates_with_a_bearer_not_a_service_token(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = dict(request.headers)
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _ok(request)

        _create(_client(handler))

        assert seen["headers"]["authorization"] == f"Bearer {TOKEN}"
        # The nutrition mechanism must not be used here — see module docstring.
        assert "x-service-token" not in seen["headers"]

    def test_names_the_actor_and_carries_the_idempotency_key(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.headers))
            return _ok(request)

        _create(_client(handler))

        assert seen["x-external-user-id"] == ACTOR
        assert seen["x-idempotency-key"] == "key-1"

    def test_posts_to_the_salon_surface(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _ok(request)

        _create(_client(handler))
        assert seen["url"] == "https://ayla.example/api/v1/tenants/me/appointments/"

    def test_new_guest_body_matches_the_canonical_serializer(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _ok(request)

        _create(_client(handler))
        body = seen["body"]
        assert body["client_name"] == "Мария"
        assert body["client_phone"] == "+79990000000"
        assert "client_id" not in body


class TestStatusMapping:
    @pytest.mark.parametrize(
        "status,exc",
        [
            (400, SalonValidationError),
            (403, SalonForbidden),
            (404, SalonNotFound),
            (409, SalonSlotTaken),
            (500, SalonUnavailable),
            (503, SalonUnavailable),
        ],
    )
    def test_status_becomes_its_own_exception(self, status: int, exc: type) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"error": {"message": "nope"}})

        with pytest.raises(exc):
            _create(_client(handler))

    def test_timeout_is_unavailable_not_a_failure(self) -> None:
        """A write that never answered may still have landed."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow", request=request)

        with pytest.raises(SalonUnavailable):
            _create(_client(handler))


class TestRefusals:
    def test_refuses_a_write_without_an_idempotency_key(self) -> None:
        """Upstream would invent one per request, so a retry books twice."""
        client = _client(_ok)
        with pytest.raises(SalonValidationError, match="idempotency"):
            client.create_appointment(
                actor_external_id=ACTOR,
                idempotency_key="",
                specialist_id="m-1",
                service_id="s-1",
                start_datetime="2026-08-21T15:00:00+03:00",
                client_id="c-1",
            )

    def test_refuses_both_identification_paths_at_once(self) -> None:
        client = _client(_ok)
        with pytest.raises(SalonValidationError, match="exactly one"):
            client.create_appointment(
                actor_external_id=ACTOR,
                idempotency_key="k",
                specialist_id="m-1",
                service_id="s-1",
                start_datetime="2026-08-21T15:00:00+03:00",
                client_id="c-1",
                client_name="Мария",
            )

    def test_refuses_neither_identification_path(self) -> None:
        client = _client(_ok)
        with pytest.raises(SalonValidationError, match="exactly one"):
            client.create_appointment(
                actor_external_id=ACTOR,
                idempotency_key="k",
                specialist_id="m-1",
                service_id="s-1",
                start_datetime="2026-08-21T15:00:00+03:00",
            )

    def test_empty_token_fails_closed(self) -> None:
        with pytest.raises(SalonNotConfigured):
            AylaSalonClient(base_url="https://ayla.example", service_token="")

    def test_empty_base_url_fails_closed(self) -> None:
        with pytest.raises(SalonNotConfigured):
            AylaSalonClient(base_url="", service_token=TOKEN)
