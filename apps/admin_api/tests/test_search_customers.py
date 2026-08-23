"""Customer search — the §13 rule that an unreachable search is not «none».

The load-bearing test here is
:meth:`TestUnreachableIsNeverEmpty.test_upstream_failure_is_never_an_empty_list`.
«Nothing found» tells the receptionist to create a new guest; «could not
look» tells them to wait. Rendering the second as the first produces a
duplicate record for a customer who is already in the salon's book, which
is the one mistake a front desk cannot quietly undo.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import init_data_header
from apps.integrations.ayla.salon_client import (
    SalonForbidden,
    SalonNotConfigured,
    SalonNotFound,
    SalonUnauthorized,
    SalonUnavailable,
    SalonValidationError,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _url() -> str:
    return reverse("admin_api:search_customers")


class _StubSalon:
    def __init__(self, *, exc: Exception | None = None, rows: list | None = None) -> None:
        self.exc = exc
        self.rows = rows if rows is not None else []
        self.calls: list[dict] = []

    def search_customers(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.rows


@pytest.fixture
def stub_salon(monkeypatch):
    def _install(stub: _StubSalon) -> _StubSalon:
        monkeypatch.setattr(
            "apps.integrations.ayla.salon_client.get_salon_client",
            lambda: stub,
        )
        return stub

    return _install


def _get(client: Client, q: str = "Мар", *, uid: str = "5001"):
    return client.get(
        _url(),
        {"q": q},
        HTTP_AUTHORIZATION=init_data_header(uid),
    )


class TestUnreachableIsNeverEmpty:
    @pytest.mark.parametrize(
        "exc",
        [
            SalonUnavailable("network: ReadTimeout"),
            SalonUnauthorized("token_not_valid"),
            SalonNotConfigured("token empty"),
            SalonNotFound("no such tenant"),
        ],
    )
    def test_upstream_failure_is_never_an_empty_list(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon, exc
    ) -> None:
        stub_salon(_StubSalon(exc=exc))

        resp = _get(client)

        assert resp.status_code == 503
        body = resp.json()
        # The distinction the whole endpoint exists to preserve.
        assert "results" not in body

    def test_a_real_empty_result_is_an_empty_list_and_a_200(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        """The other half: Ayla answered, and the answer was «nobody»."""
        stub_salon(_StubSalon(rows=[]))

        resp = _get(client)

        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_an_upstream_401_does_not_leak_its_text(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        stub_salon(_StubSalon(exc=SalonUnauthorized("token_not_valid: JWT")))

        resp = _get(client)

        assert "token" not in resp.json()["detail"]


class TestAttributionAndScope:
    def test_the_actor_is_the_calling_admin(
        self, client: Client, tenant: Tenant, owner_bot_user, admin_bot_user, stub_salon
    ) -> None:
        stub = stub_salon(_StubSalon())

        _get(client, uid="5002")

        assert stub.calls[0]["actor_external_id"] == (f"bot:max:{admin_bot_user.channel_user_id}")

    def test_the_tenant_is_taken_from_the_session_not_the_query(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        stub = stub_salon(_StubSalon())

        client.get(
            _url(),
            {"q": "Мар", "tenant": "somebody-else", "tenant_slug": "somebody-else"},
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )

        assert stub.calls[0]["tenant_slug"] == tenant.slug

    def test_a_master_only_caller_is_refused(
        self, client: Client, tenant: Tenant, master_only_bot_user, stub_salon
    ) -> None:
        stub = stub_salon(_StubSalon())

        resp = _get(client, uid="5004")

        assert resp.status_code == 403
        assert stub.calls == []


class TestQueryHandling:
    def test_a_short_query_is_a_400_about_the_query(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        """A one-letter query is the caller's problem, not the salon's.

        That it costs no HTTP round-trip is proved where it can be — in
        the client's own tests, against a mock transport. A stub here
        stands in for the client, so it cannot speak to the network.
        """
        stub_salon(_StubSalon(exc=SalonValidationError("too short")))

        resp = _get(client, q="М")

        assert resp.status_code == 400

    def test_an_over_long_query_is_refused(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        stub = stub_salon(_StubSalon())

        resp = _get(client, q="а" * 500)

        assert resp.status_code == 400
        assert stub.calls == []

    def test_the_query_is_passed_through_trimmed(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        stub = stub_salon(_StubSalon())

        _get(client, q="  Мария  ")

        assert stub.calls[0]["query"] == "Мария"


class TestWhatThePickerIsAllowedToSee:
    """Two upstream shapes need handling, both read out of Ayla's code."""

    def test_a_nameless_customer_gets_a_placeholder_and_is_flagged(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        """Upstream returns "" on purpose; a blank picker row is unusable.

        Flagged rather than dressed up, so «Без имени» cannot pass for
        somebody actually called that.
        """
        stub_salon(_StubSalon(rows=[{"id": "c-1", "name": ""}]))

        row = _get(client).json()["results"][0]

        assert row["name"] == "Без имени"
        assert row["named"] is False

    def test_a_channel_handle_is_not_shown_as_a_name(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        """Ayla's `_client_name` falls back to `username`.

        For anyone who arrived through the bot that is `bot:max:83146139`
        — plumbing, not a name, and not something a receptionist should
        be shown or asked to recognise.
        """
        stub_salon(_StubSalon(rows=[{"id": "c-1", "name": "bot:max:83146139"}]))

        row = _get(client).json()["results"][0]

        assert "bot:max" not in row["name"]
        assert row["named"] is False

    def test_a_real_name_survives_untouched(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        stub_salon(_StubSalon(rows=[{"id": "c-1", "name": "Мария Иванова"}]))

        row = _get(client).json()["results"][0]

        assert row["name"] == "Мария Иванова"
        assert row["named"] is True

    def test_no_phone_reaches_the_response_by_any_path(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        """DRF-1039. The lookup returns none today; if it ever does, this
        endpoint still must not forward it unasked."""
        stub_salon(
            _StubSalon(
                rows=[
                    {
                        "id": "c-1",
                        "name": "Мария",
                        "phone": "+79990000000",
                        "phone_masked": "+7 *** **00",
                    }
                ]
            )
        )

        body = json.dumps(_get(client).json(), ensure_ascii=False)

        assert "79990000000" not in body
        assert "phone" not in body


class TestForbiddenIsNotUnavailable:
    def test_upstream_403_stays_a_403(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        """«This person may not» and «we could not ask» have different
        remedies, and only one of them is the operator's problem."""
        stub_salon(_StubSalon(exc=SalonForbidden("not a tenant admin")))

        assert _get(client).status_code == 403
