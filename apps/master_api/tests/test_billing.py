"""C2/C3 proxy tests — master_api billing status + payout preview.

Covers the service layer (:mod:`apps.master_api.services.billing`) and
the two views. The Ayla wire is stubbed at the client level; the
specialist-id mapping seam is exercised both in its current (None →
503 fail-closed) and resolved (patched) states.
"""

from __future__ import annotations

import uuid

import pytest
from django.test import Client as DjangoClient

from apps.catalog.models import CatalogMaster
from apps.integrations.ayla.billing_client import (
    BillingNotFoundError,
    BillingTransportError,
)
from apps.master_api.services import billing as billing_svc
from apps.master_api.services.billing import (
    ProxyStatus,
    billing_status_for_master,
    payout_preview_for_master,
)
from apps.master_api.tests.conftest import init_data_header, make_master


pytestmark = pytest.mark.django_db

_SID = "7c2d8e1f-0a5c-4c3a-9e1b-4d52f8eb3a17"


@pytest.fixture
def master(tenant, bot_user) -> CatalogMaster:
    return make_master(
        tenant,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        linked_bot_user=bot_user,
    )


class _StubClient:
    """AylaBillingClient stand-in capturing calls and returning a payload."""

    def __init__(self, payload=None, exc: Exception | None = None) -> None:
        self.payload = payload or {}
        self.exc = exc
        self.calls: list[tuple[str, ...]] = []
        self.closed = False

    def get_billing_status(self, *, specialist_id: str):
        self.calls.append(("get_billing_status", specialist_id))
        if self.exc:
            raise self.exc
        return self.payload

    def get_payout_preview(self, *, specialist_id: str):
        self.calls.append(("get_payout_preview", specialist_id))
        if self.exc:
            raise self.exc
        return self.payload

    def card_setup(self, *, specialist_id: str, tariff: str, return_url: str):
        self.calls.append(("card_setup", specialist_id, tariff, return_url))
        if self.exc:
            raise self.exc
        return self.payload

    def pay_debt(self, *, specialist_id: str, return_url: str = ""):
        self.calls.append(("pay_debt", specialist_id, return_url))
        if self.exc:
            raise self.exc
        return self.payload

    def close(self) -> None:
        self.closed = True


class TestMappingGap:
    """Master without an Ayla link (ayla_user_id NULL) → fail-closed."""

    def test_status_mapping_unavailable(self, master) -> None:
        result = billing_status_for_master(master)
        assert result.status is ProxyStatus.MAPPING_UNAVAILABLE
        assert result.payload == {}

    def test_payout_mapping_unavailable(self, master) -> None:
        result = payout_preview_for_master(master)
        assert result.status is ProxyStatus.MAPPING_UNAVAILABLE

    def test_resolver_returns_none(self, master) -> None:
        assert billing_svc.specialist_id_for_master(master) is None


class TestLinkedMaster:
    """AMD-005: the mirror's ayla_user_id IS the billing key — no seam patch."""

    @pytest.fixture
    def linked_master(self, master) -> CatalogMaster:
        master.ayla_user_id = uuid.UUID(_SID)
        master.save(update_fields=["ayla_user_id"])
        return master

    def test_resolver_returns_ayla_user_id(self, linked_master) -> None:
        assert billing_svc.specialist_id_for_master(linked_master) == _SID

    def test_status_ok_verbatim(self, linked_master) -> None:
        payload = {"specialist_id": _SID, "subscription": {"status": "active"}}
        client = _StubClient(payload=payload)
        result = billing_status_for_master(linked_master, client=client)  # type: ignore[arg-type]
        assert result.status is ProxyStatus.OK
        assert result.payload is payload
        assert client.calls == [("get_billing_status", _SID)]

    def test_payout_ok_verbatim(self, linked_master) -> None:
        payload = {"pending_amount": "5730.00", "currency": "RUB", "items": []}
        client = _StubClient(payload=payload)
        result = payout_preview_for_master(linked_master, client=client)  # type: ignore[arg-type]
        assert result.status is ProxyStatus.OK
        assert result.payload is payload
        assert client.calls == [("get_payout_preview", _SID)]

    def test_upstream_404(self, linked_master) -> None:
        client = _StubClient(exc=BillingNotFoundError("nope"))
        result = billing_status_for_master(linked_master, client=client)  # type: ignore[arg-type]
        assert result.status is ProxyStatus.NOT_FOUND

    def test_upstream_transport_error(self, linked_master) -> None:
        client = _StubClient(exc=BillingTransportError("http_500"))
        result = payout_preview_for_master(linked_master, client=client)  # type: ignore[arg-type]
        assert result.status is ProxyStatus.UPSTREAM_ERROR

    def test_status_view_ok(self, client: DjangoClient, linked_master, monkeypatch) -> None:
        payload = {"specialist_id": _SID, "subscription": {"status": "trial"}}
        monkeypatch.setattr(
            "apps.master_api.views.billing_status_for_master",
            lambda master: billing_svc.BillingProxyResult(status=ProxyStatus.OK, payload=payload),
        )
        resp = client.get(
            "/api/v1/master/billing/status",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200
        assert resp.json() == {"data": payload}


class TestViews:
    def test_auth_required(self, client) -> None:
        # The init-data guard rejects a headerless request with 400.
        assert client.get("/api/v1/master/billing/status").status_code in (400, 401, 403)
        assert client.get("/api/v1/master/payout-preview").status_code in (400, 401, 403)

    def test_status_view_mapping_gap_503(self, client, master) -> None:
        resp = client.get(
            "/api/v1/master/billing/status",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 503
        assert resp.json()["error"] == "specialist_mapping_unavailable"

    def test_status_view_ok(self, client, master, monkeypatch) -> None:
        monkeypatch.setattr(billing_svc, "specialist_id_for_master", lambda master: _SID)
        payload = {"specialist_id": _SID, "subscription": {"status": "trial"}}
        monkeypatch.setattr(
            "apps.master_api.views.billing_status_for_master",
            lambda master: billing_svc.BillingProxyResult(status=ProxyStatus.OK, payload=payload),
        )
        resp = client.get(
            "/api/v1/master/billing/status",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200
        assert resp.json() == {"data": payload}

    def test_payout_view_upstream_error_502(self, client, master, monkeypatch) -> None:
        monkeypatch.setattr(
            "apps.master_api.views.payout_preview_for_master",
            lambda master: billing_svc.BillingProxyResult(status=ProxyStatus.UPSTREAM_ERROR),
        )
        resp = client.get(
            "/api/v1/master/payout-preview",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 502
        assert resp.json()["error"] == "billing_upstream_unavailable"

    def test_payout_view_not_found_404(self, client, master, monkeypatch) -> None:
        monkeypatch.setattr(
            "apps.master_api.views.payout_preview_for_master",
            lambda master: billing_svc.BillingProxyResult(status=ProxyStatus.NOT_FOUND),
        )
        resp = client.get(
            "/api/v1/master/payout-preview",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "specialist_not_found"


class TestCardSetup:
    """D7 card binding proxy — the money path (no binding → no charges
    → dunning → past_due → C1 blocks new bookings)."""

    @pytest.fixture
    def linked_master(self, master) -> CatalogMaster:
        master.ayla_user_id = uuid.UUID(_SID)
        master.save(update_fields=["ayla_user_id"])
        return master

    def test_service_ok_verbatim(self, linked_master) -> None:
        payload = {"confirmation_url": "https://pay.test/bind/1"}
        client = _StubClient(payload=payload)
        result = billing_svc.card_setup_for_master(
            linked_master,
            tariff="solo",
            return_url="https://x.test/",
            client=client,  # type: ignore[arg-type]
        )
        assert result.status is ProxyStatus.OK
        assert result.payload is payload

    def test_service_mapping_unavailable(self, master) -> None:
        result = billing_svc.card_setup_for_master(
            master, tariff="solo", return_url="https://x.test/"
        )
        assert result.status is ProxyStatus.MAPPING_UNAVAILABLE

    def test_service_upstream_404(self, linked_master) -> None:
        client = _StubClient(exc=BillingNotFoundError("nope"))
        result = billing_svc.card_setup_for_master(
            linked_master,
            tariff="solo",
            return_url="https://x.test/",
            client=client,  # type: ignore[arg-type]
        )
        assert result.status is ProxyStatus.NOT_FOUND

    def test_service_upstream_400_client_error(self, linked_master) -> None:
        from apps.integrations.ayla.billing_client import BillingClientError

        client = _StubClient(exc=BillingClientError("VALIDATION_ERROR"))
        result = billing_svc.card_setup_for_master(
            linked_master,
            tariff="salon",
            return_url="https://x.test/",
            client=client,  # type: ignore[arg-type]
        )
        assert result.status is ProxyStatus.CLIENT_ERROR

    def test_service_upstream_5xx(self, linked_master) -> None:
        client = _StubClient(exc=BillingTransportError("http_503"))
        result = billing_svc.card_setup_for_master(
            linked_master,
            tariff="solo",
            return_url="https://x.test/",
            client=client,  # type: ignore[arg-type]
        )
        assert result.status is ProxyStatus.UPSTREAM_ERROR

    def _post(self, client, body: dict, user_id: str = "12345"):
        return client.post(
            "/api/v1/master/billing/card-setup",
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header(user_id),
        )

    def test_view_200_with_url(self, client: DjangoClient, linked_master, monkeypatch) -> None:
        payload = {"confirmation_url": "https://pay.test/bind/1"}
        monkeypatch.setattr(
            "apps.master_api.views.card_setup_for_master",
            lambda master, *, tariff, return_url: billing_svc.BillingProxyResult(
                status=ProxyStatus.OK, payload=payload
            ),
        )
        resp = self._post(client, {"tariff": "solo", "return_url": "https://x.test/"})
        assert resp.status_code == 200
        assert resp.json() == {"data": payload}

    def test_view_foreign_specialist_403(self, client: DjangoClient, linked_master) -> None:
        resp = self._post(
            client,
            {
                "tariff": "solo",
                "return_url": "https://x.test/",
                "specialist_id": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"

    def test_view_invalid_body_400(self, client: DjangoClient, linked_master) -> None:
        resp = self._post(client, {"tariff": "gold"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_view_upstream_400_maps_400(
        self, client: DjangoClient, linked_master, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "apps.master_api.views.card_setup_for_master",
            lambda master, *, tariff, return_url: billing_svc.BillingProxyResult(
                status=ProxyStatus.CLIENT_ERROR
            ),
        )
        resp = self._post(client, {"tariff": "salon", "return_url": "https://x.test/"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_view_upstream_5xx_maps_502(
        self, client: DjangoClient, linked_master, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "apps.master_api.views.card_setup_for_master",
            lambda master, *, tariff, return_url: billing_svc.BillingProxyResult(
                status=ProxyStatus.UPSTREAM_ERROR
            ),
        )
        resp = self._post(client, {"tariff": "solo", "return_url": "https://x.test/"})
        assert resp.status_code == 502
        assert resp.json()["error"] == "billing_upstream_unavailable"

    def test_view_unlinked_master_503(self, client: DjangoClient, master) -> None:
        resp = self._post(client, {"tariff": "solo", "return_url": "https://x.test/"})
        assert resp.status_code == 503
        assert resp.json()["error"] == "specialist_mapping_unavailable"


class TestPayDebt:
    """One-shot debt collection proxy (past_due CTA — dunning escape)."""

    @pytest.fixture
    def linked_master(self, master) -> CatalogMaster:
        master.ayla_user_id = uuid.UUID(_SID)
        master.save(update_fields=["ayla_user_id"])
        return master

    def test_service_ok_verbatim(self, linked_master) -> None:
        payload = {
            "payment_id": "p-1",
            "invoice_id": "i-1",
            "confirmation_url": "https://pay.test/debt/1",
            "amount": "960.00",
            "status": "pending",
            "subscription_status": "past_due",
        }
        client = _StubClient(payload=payload)
        result = billing_svc.pay_debt_for_master(
            linked_master,
            return_url="https://x.test/",
            client=client,  # type: ignore[arg-type]
        )
        assert result.status is ProxyStatus.OK
        assert result.payload is payload

    def test_service_no_debt_conflict(self, linked_master) -> None:
        from apps.integrations.ayla.billing_client import BillingConflictError

        client = _StubClient(exc=BillingConflictError("409", code="NO_DEBT"))
        result = billing_svc.pay_debt_for_master(
            linked_master,
            return_url="https://x.test/",
            client=client,  # type: ignore[arg-type]
        )
        assert result.status is ProxyStatus.CONFLICT

    def test_service_mapping_unavailable(self, master) -> None:
        result = billing_svc.pay_debt_for_master(master, return_url="https://x.test/")
        assert result.status is ProxyStatus.MAPPING_UNAVAILABLE

    def test_service_upstream_404(self, linked_master) -> None:
        client = _StubClient(exc=BillingNotFoundError("nope"))
        result = billing_svc.pay_debt_for_master(
            linked_master,
            return_url="https://x.test/",
            client=client,  # type: ignore[arg-type]
        )
        assert result.status is ProxyStatus.NOT_FOUND

    def test_service_upstream_5xx(self, linked_master) -> None:
        client = _StubClient(exc=BillingTransportError("http_503"))
        result = billing_svc.pay_debt_for_master(
            linked_master,
            return_url="https://x.test/",
            client=client,  # type: ignore[arg-type]
        )
        assert result.status is ProxyStatus.UPSTREAM_ERROR

    def _post(self, client, body: dict):
        return client.post(
            "/api/v1/master/billing/pay-debt",
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )

    def test_view_200_verbatim(self, client: DjangoClient, linked_master, monkeypatch) -> None:
        payload = {"payment_id": "p-1", "confirmation_url": "https://pay.test/debt/1"}
        monkeypatch.setattr(
            "apps.master_api.views.pay_debt_for_master",
            lambda master, *, return_url: billing_svc.BillingProxyResult(
                status=ProxyStatus.OK, payload=payload
            ),
        )
        resp = self._post(client, {"return_url": "https://x.test/"})
        assert resp.status_code == 200
        assert resp.json() == {"data": payload}

    def test_view_no_debt_409(self, client: DjangoClient, linked_master, monkeypatch) -> None:
        monkeypatch.setattr(
            "apps.master_api.views.pay_debt_for_master",
            lambda master, *, return_url: billing_svc.BillingProxyResult(
                status=ProxyStatus.CONFLICT
            ),
        )
        resp = self._post(client, {"return_url": "https://x.test/"})
        assert resp.status_code == 409
        assert resp.json()["error"] == "no_debt"

    def test_view_foreign_specialist_403(self, client: DjangoClient, linked_master) -> None:
        resp = self._post(
            client,
            {"return_url": "https://x.test/", "specialist_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"

    def test_view_upstream_5xx_maps_502(
        self, client: DjangoClient, linked_master, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "apps.master_api.views.pay_debt_for_master",
            lambda master, *, return_url: billing_svc.BillingProxyResult(
                status=ProxyStatus.UPSTREAM_ERROR
            ),
        )
        resp = self._post(client, {"return_url": "https://x.test/"})
        assert resp.status_code == 502
        assert resp.json()["error"] == "billing_upstream_unavailable"
