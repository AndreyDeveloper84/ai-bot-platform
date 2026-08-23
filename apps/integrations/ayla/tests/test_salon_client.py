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
    SalonUnauthorized,
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
        tenant_slug="formula-tela",
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
        # Without this, IsTenantAdmin refuses with a 403 that reads like a
        # rights problem and is really a missing header.
        assert seen["x-tenant"] == "formula-tela"

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
            (401, SalonUnauthorized),
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
                tenant_slug="formula-tela",
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
                tenant_slug="formula-tela",
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
                tenant_slug="formula-tela",
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


class TestUpstreamContractAsMeasured:
    """Today's reality on the receiving side, pinned so its repair is visible.

    Measured on live Ayla 2026-08-21: the salon endpoints answer 401
    ``token_not_valid`` to a service Bearer, because their JWT authenticator
    runs before ``permission_classes`` and rejects a non-JWT credential.
    DRF-1231 fixes that.

    The point of writing today's broken behaviour down is that when it stops
    being true, this test goes red and tells us — which is cheaper than
    learning it from a salon administrator, and cheaper than never noticing
    that the fix landed.
    """

    def test_a_service_bearer_is_refused_at_authentication_today(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            # Verbatim shape of the live response.
            return httpx.Response(
                401,
                json={
                    "error": {
                        "code": "token_not_valid",
                        "message": "Given token not valid for any token type",
                    }
                },
            )

        with pytest.raises(SalonUnauthorized) as caught:
            _create(_client(handler))

        assert "not valid" in str(caught.value)

    def test_unauthorized_is_not_confused_with_forbidden(self) -> None:
        """401 and 403 mean different things and have different remedies.

        403 is «this person may not» — the administrator's rights are wrong.
        401 is «we may not» — our credential is wrong, and no action by the
        administrator can help. Collapsing them would send an operator
        problem to the wrong person.
        """
        assert not issubclass(SalonUnauthorized, SalonForbidden)
        assert not issubclass(SalonForbidden, SalonUnauthorized)


class TestTenantHeader:
    def test_refuses_without_a_tenant(self) -> None:
        """A 403 that is really a missing header is worse than a local refusal."""
        client = _client(_ok)
        with pytest.raises(SalonValidationError, match="tenant_slug"):
            client.create_appointment(
                actor_external_id=ACTOR,
                idempotency_key="k",
                tenant_slug="",
                specialist_id="m-1",
                service_id="s-1",
                start_datetime="2026-08-21T15:00:00+03:00",
                client_id="c-1",
            )


class TestCustomerSearch:
    """§13 — the read half of the booking flow.

    Contract read out of Ayla's ``SalonCustomerLookupView`` on 2026-08-21
    rather than guessed; the shape assertions below are what «read it»
    means in practice.
    """

    def test_asks_the_canonical_lookup_with_the_query(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["headers"] = dict(request.headers)
            return httpx.Response(200, json={"data": {"results": []}})

        _client(handler).search_customers(
            actor_external_id=ACTOR,
            tenant_slug="formula-tela",
            query="Мар",
        )

        assert seen["url"].startswith("https://ayla.example/api/v1/tenants/me/customers/")
        assert "q=" in seen["url"]
        assert seen["headers"]["x-tenant"] == "formula-tela"
        assert seen["headers"]["authorization"] == f"Bearer {TOKEN}"

    def test_a_read_carries_no_idempotency_key(self) -> None:
        """There is nothing to de-duplicate, and pretending otherwise
        would tell the next reader that there is."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.headers))
            return httpx.Response(200, json={"data": {"results": []}})

        _client(handler).search_customers(
            actor_external_id=ACTOR,
            tenant_slug="formula-tela",
            query="Мар",
        )

        assert "x-idempotency-key" not in seen

    def test_unwraps_the_data_envelope(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"data": {"results": [{"id": "c-1", "name": "Мария"}]}},
            )

        rows = _client(handler).search_customers(
            actor_external_id=ACTOR,
            tenant_slug="formula-tela",
            query="Мар",
        )

        assert rows == [{"id": "c-1", "name": "Мария"}]

    def test_a_short_query_costs_no_round_trip(self) -> None:
        """One keystroke must not become an HTTP request and a 400."""
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, json={"data": {"results": []}})

        with pytest.raises(SalonValidationError, match="at least"):
            _client(handler).search_customers(
                actor_external_id=ACTOR,
                tenant_slug="formula-tela",
                query="М",
            )

        assert called is False

    def test_refuses_without_a_tenant(self) -> None:
        client = _client(_ok)
        with pytest.raises(SalonValidationError, match="tenant_slug"):
            client.search_customers(
                actor_external_id=ACTOR,
                tenant_slug="",
                query="Мар",
            )

    def test_an_unrecognised_success_shape_is_not_an_empty_result(self) -> None:
        """The §13 rule, enforced at the lowest level that can see it.

        Returning ``[]`` for a payload we failed to understand would hand
        the front desk «no such customer» on the strength of a parsing
        failure.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"customers": []}})

        with pytest.raises(SalonUnavailable):
            _client(handler).search_customers(
                actor_external_id=ACTOR,
                tenant_slug="formula-tela",
                query="Мар",
            )

    @pytest.mark.parametrize(
        "status,exc",
        [
            (401, SalonUnauthorized),
            (403, SalonForbidden),
            (500, SalonUnavailable),
        ],
    )
    def test_reads_map_status_like_writes_do(self, status: int, exc: type) -> None:
        """One mapping for both verbs, so a 403 cannot come to mean two
        different things depending on which method you called."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"error": {"message": "nope"}})

        with pytest.raises(exc):
            _client(handler).search_customers(
                actor_external_id=ACTOR,
                tenant_slug="formula-tela",
                query="Мар",
            )


class TestOneStatusThreeMeanings:
    """409 on this surface means three different things.

    SLOT_NOT_AVAILABLE sends the receptionist to pick another time.
    STALE_VERSION sends them to refresh — the booking they are looking at
    is not the booking that exists. Collapsing the second into the first
    tells them to re-pick a time for a booking somebody else already
    moved, which is precisely what expected_version exists to prevent.
    """

    def _409(self, code: str):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"error": {"code": code, "message": "conflict"}})

        return handler

    def test_stale_version_is_its_own_exception(self) -> None:
        from apps.integrations.ayla.salon_client import SalonStaleVersion

        with pytest.raises(SalonStaleVersion):
            _client(self._409("STALE_VERSION")).cancel_appointment(
                actor_external_id=ACTOR,
                tenant_slug="formula-tela",
                appointment_id="a-1",
            )

    def test_a_taken_slot_stays_a_taken_slot(self) -> None:
        with pytest.raises(SalonSlotTaken):
            _client(self._409("SLOT_NOT_AVAILABLE")).cancel_appointment(
                actor_external_id=ACTOR,
                tenant_slug="formula-tela",
                appointment_id="a-1",
            )

    def test_an_unknown_409_falls_back_to_the_safe_instruction(self) -> None:
        """«Look again» is safe for an unrecognised conflict; «somebody
        moved it» would be a claim we cannot support."""
        with pytest.raises(SalonSlotTaken):
            _client(self._409("SOMETHING_NEW")).cancel_appointment(
                actor_external_id=ACTOR,
                tenant_slug="formula-tela",
                appointment_id="a-1",
            )

    def test_422_is_the_bookings_own_state_not_a_race(self) -> None:
        from apps.integrations.ayla.salon_client import SalonNotAllowed

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                422,
                json={"error": {"code": "CANCELLATION_NOT_ALLOWED", "message": "done"}},
            )

        with pytest.raises(SalonNotAllowed):
            _client(handler).cancel_appointment(
                actor_external_id=ACTOR,
                tenant_slug="formula-tela",
                appointment_id="a-1",
            )


class TestCancel:
    def test_posts_to_the_cancel_endpoint_of_that_booking(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"data": {"id": "a-1"}})

        _client(handler).cancel_appointment(
            actor_external_id=ACTOR,
            tenant_slug="formula-tela",
            appointment_id="a-1",
            reason="мастер заболел",
            reason_code="master_unavailable",
        )

        assert seen["url"] == ("https://ayla.example/api/v1/tenants/me/appointments/a-1/cancel/")
        assert seen["body"] == {
            "reason": "мастер заболел",
            "reason_code": "master_unavailable",
        }

    def test_an_omitted_reason_code_is_not_invented(self) -> None:
        """Upstream defaults it to «other». Sending a guess would put a
        claim in the permanent record that nobody actually made."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"data": {}})

        _client(handler).cancel_appointment(
            actor_external_id=ACTOR,
            tenant_slug="formula-tela",
            appointment_id="a-1",
        )

        assert "reason_code" not in seen["body"]

    def test_a_reason_code_outside_the_salon_allowlist_is_refused(self) -> None:
        """`user_*` codes are the customer's business and
        `payment_hold_expired` is the payment system's fact. Letting the
        salon assert either would let one party author another's
        attribution — refused here rather than learned from a 400.
        """
        client = _client(_ok)
        with pytest.raises(SalonValidationError, match="reason_code"):
            client.cancel_appointment(
                actor_external_id=ACTOR,
                tenant_slug="formula-tela",
                appointment_id="a-1",
                reason_code="user_changed_mind",
            )

    def test_refuses_without_a_tenant(self) -> None:
        client = _client(_ok)
        with pytest.raises(SalonValidationError, match="tenant_slug"):
            client.cancel_appointment(
                actor_external_id=ACTOR,
                tenant_slug="",
                appointment_id="a-1",
            )


class TestReschedule:
    """Complete and tested; no caller yet, and that is deliberate.

    The bot has no canonical read that carries a booking's `version`.
    Measured on the pilot 2026-08-21: 2 of 23 mirrored bookings have one,
    and the single future confirmed booking has none. See report §31.
    """

    def test_sends_the_new_start_and_the_expected_version(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"data": {"id": "a-1", "version": 4}})

        _client(handler).reschedule_appointment(
            actor_external_id=ACTOR,
            tenant_slug="formula-tela",
            appointment_id="a-1",
            new_start_datetime="2026-08-22T15:00:00+03:00",
            expected_version=3,
        )

        assert seen["url"].endswith("/appointments/a-1/reschedule/")
        assert seen["body"] == {
            "new_start_datetime": "2026-08-22T15:00:00+03:00",
            "expected_version": 3,
        }

    @pytest.mark.parametrize("version", [None, 0, -1, "3"])
    def test_a_missing_or_bogus_version_never_reaches_the_network(self, version) -> None:
        """A move without a trustworthy version would be answered — and
        answered wrongly, against whatever revision happens to exist."""
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, json={"data": {}})

        with pytest.raises(SalonValidationError, match="expected_version"):
            _client(handler).reschedule_appointment(
                actor_external_id=ACTOR,
                tenant_slug="formula-tela",
                appointment_id="a-1",
                new_start_datetime="2026-08-22T15:00:00+03:00",
                expected_version=version,  # type: ignore[arg-type]
            )

        assert called is False


class TestComplete:
    def test_posts_to_the_complete_endpoint_with_the_version(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"data": {"id": "a-1"}})

        _client(handler).complete_appointment(
            actor_external_id=ACTOR,
            tenant_slug="formula-tela",
            appointment_id="a-1",
            expected_version=4,
        )

        assert seen["url"].endswith("/appointments/a-1/complete/")
        assert seen["body"] == {"expected_version": 4}

    @pytest.mark.parametrize("version", [None, 0, -1, "4", True])
    def test_a_version_that_is_not_a_positive_int_never_travels(self, version) -> None:
        """Includes ``True``: `isinstance(True, int)` is True in Python,
        and a boolean reaching a concurrency guard would send `1`."""
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, json={"data": {}})

        with pytest.raises(SalonValidationError, match="expected_version"):
            _client(handler).complete_appointment(
                actor_external_id=ACTOR,
                tenant_slug="formula-tela",
                appointment_id="a-1",
                expected_version=version,  # type: ignore[arg-type]
            )

        assert called is False

    def test_a_stale_version_is_its_own_exception(self) -> None:
        from apps.integrations.ayla.salon_client import SalonStaleVersion

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                409, json={"error": {"code": "STALE_VERSION", "message": "moved"}}
            )

        with pytest.raises(SalonStaleVersion):
            _client(handler).complete_appointment(
                actor_external_id=ACTOR,
                tenant_slug="formula-tela",
                appointment_id="a-1",
                expected_version=4,
            )

    def test_an_already_closed_visit_is_not_allowed(self) -> None:
        from apps.integrations.ayla.salon_client import SalonNotAllowed

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                422, json={"error": {"code": "INVALID_STATUS", "message": "done"}}
            )

        with pytest.raises(SalonNotAllowed):
            _client(handler).complete_appointment(
                actor_external_id=ACTOR,
                tenant_slug="formula-tela",
                appointment_id="a-1",
                expected_version=4,
            )
