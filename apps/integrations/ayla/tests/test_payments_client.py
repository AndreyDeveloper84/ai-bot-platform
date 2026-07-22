"""Tests for the Ayla C7 client-payments client (contract §7.5).

Auth pins (IsBotServiceWithVerifiedClient, verified against Ayla's live
views): EVERY method sends ``X-External-User-ID`` alongside the Bearer;
C7.1 create additionally carries body ``client_id`` (the C7.6
cross-check) + ``return_url``; C7.2 setup carries ``consent_version`` +
``return_url``. Amounts never leave the bot (C7.1/C7.6).
"""

from __future__ import annotations

import json

import httpx
import pytest

from apps.integrations.ayla.payments_client import (
    AylaClientPaymentsClient,
    ClientPaymentsAuthError,
    ClientPaymentsConflictError,
    ClientPaymentsNotFoundError,
    ClientPaymentsTransportError,
)

_BASE = "https://ayla.test"
_TOKEN = "TOKEN-SENTINEL"  # noqa: S105  # pragma: allowlist secret
_UID = "11111111-2222-3333-4444-555555555555"
_APPT = "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"
_CARD = "5c8e2d1f-3b4a-4c6d-9e8f-1a2b3c4d5e6f"
_EXT = "bot:max:12345"
_RETURN = "https://miniapp.test/return"
_CONSENT = "offer-client-cards-1.0"


def _client(handler, **kwargs) -> AylaClientPaymentsClient:
    return AylaClientPaymentsClient(
        base_url=_BASE,
        token=_TOKEN,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


class TestCreatePayment:
    def test_happy_path_verbatim_auth_and_body(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["ext"] = request.headers.get("x-external-user-id")
            seen["body"] = json.loads(request.content.decode())
            return httpx.Response(
                200,
                json={
                    "data": {
                        "payment_id": "p-1",
                        "confirmation_url": "https://pay.test/c/1",
                        "amount": "2000.00",
                        "currency": "RUB",
                        "capture_state": "authorized",
                    }
                },
            )

        out = _client(handler).create_payment(
            appointment_id=_APPT,
            external_user_id=_EXT,
            client_id=_UID,
            return_url=_RETURN,
        )

        assert seen["url"] == f"{_BASE}/api/v1/internal/appointments/{_APPT}/payment/"
        assert seen["auth"] == f"Bearer {_TOKEN}"
        # IsBotServiceWithVerifiedClient — the resolved-actor header.
        assert seen["ext"] == _EXT
        # C7.6 cross-check + YooKassa redirect; amount NEVER leaves the bot.
        assert seen["body"] == {"client_id": _UID, "return_url": _RETURN}
        assert "amount" not in seen["body"]
        assert out == {
            "payment_id": "p-1",
            "confirmation_url": "https://pay.test/c/1",
            "amount": "2000.00",
            "currency": "RUB",
            "capture_state": "authorized",
        }

    def test_409_carries_code(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                409, json={"error": {"code": "SUBSCRIPTION_PAST_DUE", "message": "x"}}
            )

        with pytest.raises(ClientPaymentsConflictError) as exc_info:
            _client(handler).create_payment(
                appointment_id=_APPT,
                external_user_id=_EXT,
                client_id=_UID,
                return_url=_RETURN,
            )

        assert exc_info.value.code == "SUBSCRIPTION_PAST_DUE"


class TestCards:
    def test_setup(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["ext"] = request.headers.get("x-external-user-id")
            seen["body"] = json.loads(request.content.decode())
            return httpx.Response(200, json={"data": {"confirmation_url": "https://pay.test/bind"}})

        out = _client(handler).cards_setup(
            ayla_user_id=_UID,
            external_user_id=_EXT,
            consent_version=_CONSENT,
            return_url=_RETURN,
        )

        assert seen["method"] == "POST"
        assert seen["url"].endswith(f"/api/v1/internal/users/{_UID}/cards/setup/")
        assert seen["ext"] == _EXT
        # Consent boundary (C7.2) + YooKassa redirect; no client_id in the
        # cards family — ownership is the path scope check.
        assert seen["body"] == {"consent_version": _CONSENT, "return_url": _RETURN}
        assert out == {"confirmation_url": "https://pay.test/bind"}

    def test_list(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["ext"] = request.headers.get("x-external-user-id")
            return httpx.Response(
                200,
                json={"data": [{"id": _CARD, "last4": "4242", "brand": "visa"}]},
            )

        out = _client(handler).list_cards(ayla_user_id=_UID, external_user_id=_EXT)

        assert seen["ext"] == _EXT
        assert out == [{"id": _CARD, "last4": "4242", "brand": "visa"}]

    def test_delete_204(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["ext"] = request.headers.get("x-external-user-id")
            return httpx.Response(204)

        _client(handler).delete_card(ayla_user_id=_UID, card_id=_CARD, external_user_id=_EXT)

        assert seen["method"] == "DELETE"
        assert seen["url"].endswith(f"/api/v1/internal/users/{_UID}/cards/{_CARD}/")
        assert seen["ext"] == _EXT

    def test_delete_retried_on_5xx(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(500, json={})
            return httpx.Response(204)

        _client(handler, retries=2).delete_card(
            ayla_user_id=_UID, card_id=_CARD, external_user_id=_EXT
        )
        assert calls["n"] == 2


class TestErrors:
    @pytest.mark.parametrize("status", [401, 403])
    def test_auth(self, status: int) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={})

        with pytest.raises(ClientPaymentsAuthError):
            _client(handler).list_cards(ayla_user_id=_UID, external_user_id=_EXT)

    def test_404(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})

        with pytest.raises(ClientPaymentsNotFoundError):
            _client(handler).cards_setup(
                ayla_user_id=_UID,
                external_user_id=_EXT,
                consent_version=_CONSENT,
                return_url=_RETURN,
            )

    def test_5xx_exhausts(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(500, json={})

        with pytest.raises(ClientPaymentsTransportError):
            _client(handler, retries=2).list_cards(ayla_user_id=_UID, external_user_id=_EXT)
        assert calls["n"] == 2
