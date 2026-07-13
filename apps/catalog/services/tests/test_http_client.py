"""CatalogHttpClient tests — Ayla internal catalog (S3B / #1044).

Network is mocked via ``pytest-httpx`` so CI never hits real Ayla. We
exercise the wire-shape contract: Bearer auth + tenant filter + DRF
PageNumberPagination + retry / error-mapping + DTO parsing against
``docs/CATALOG_INTERNAL_API_CONTRACT.md``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from apps.catalog.services.http_client import (
    CatalogAuthError,
    CatalogClientError,
    CatalogHttpClient,
    CatalogTransportError,
)

_BASE = "https://ayla.test"
_TOKEN = "internal-token-abc"  # noqa: S105
_TID = "b0a1c2d3-0000-4000-8000-000000000001"
_SALON_URL = f"{_BASE}/api/v1/internal/catalog/salon-services/"


def _client(**kwargs: Any) -> CatalogHttpClient:
    return CatalogHttpClient(base_url=_BASE, token=_TOKEN, retries=3, timeout=5, **kwargs)


def _salon_service_row(sid: str) -> dict[str, Any]:
    return {
        "id": sid,
        "tenant": _TID,
        "template": "9d3f0000-0000-4000-8000-000000000002",
        "category": "11220000-0000-4000-8000-000000000003",
        "name": f"Service {sid}",
        "duration_minutes": 45,
        "base_price": "1500.00",
        "requires_health_check": True,
        "is_active": True,
        "source": "manual",
        "created_at": "2026-07-09T18:31:00Z",
        "updated_at": "2026-07-09T18:31:00Z",
    }


class TestAuthHeader:
    def test_attaches_bearer_token(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url=f"{_SALON_URL}?tenant={_TID}",
            json={"count": 0, "next": None, "previous": None, "results": []},
        )
        with _client() as c:
            c.fetch_salon_services(tenant_id=_TID)
        request = httpx_mock.get_request()
        assert request is not None
        assert request.headers["Authorization"] == f"Bearer {_TOKEN}"


class TestTenantFilter:
    def test_tenant_passed_as_query_param(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url=f"{_SALON_URL}?tenant={_TID}",
            json={"count": 0, "next": None, "previous": None, "results": []},
        )
        with _client() as c:
            c.fetch_salon_services(tenant_id=_TID)
        request = httpx_mock.get_request()
        assert request is not None
        assert request.url.params["tenant"] == _TID


class TestPagination:
    def test_follows_next_until_exhausted(self, httpx_mock: HTTPXMock) -> None:
        next_url = f"{_SALON_URL}?tenant={_TID}&page=2"
        a = "aaaa0000-0000-4000-8000-000000000001"
        b = "bbbb0000-0000-4000-8000-000000000002"
        d = "dddd0000-0000-4000-8000-000000000003"
        httpx_mock.add_response(
            url=f"{_SALON_URL}?tenant={_TID}",
            json={
                "count": 3,
                "next": next_url,
                "previous": None,
                "results": [_salon_service_row(a), _salon_service_row(b)],
            },
        )
        httpx_mock.add_response(
            url=next_url,
            json={"count": 3, "next": None, "previous": None, "results": [_salon_service_row(d)]},
        )
        with _client() as c:
            out = c.fetch_salon_services(tenant_id=_TID)
        assert [row.ayla_service_id for row in out] == [a, b, d]


class TestDTOParsing:
    def test_salon_service_dto_fields(self, httpx_mock: HTTPXMock) -> None:
        sid = "6f1c2e9a-0000-4000-8000-000000000042"
        httpx_mock.add_response(
            url=f"{_SALON_URL}?tenant={_TID}",
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [_salon_service_row(sid)],
            },
        )
        with _client() as c:
            [svc] = c.fetch_salon_services(tenant_id=_TID)
        assert svc.ayla_service_id == sid
        assert svc.name == f"Service {sid}"
        assert svc.price_from == Decimal("1500.00")
        assert svc.duration_min == 45
        assert svc.requires_health_check is True
        assert svc.template == "9d3f0000-0000-4000-8000-000000000002"
        assert svc.category == "11220000-0000-4000-8000-000000000003"
        assert svc.external_updated_at.tzinfo is not None
        # raw retains the original payload for forensics + template/category.
        assert svc.raw["source"] == "manual"

    def test_null_scalar_fields_tolerated(self, httpx_mock: HTTPXMock) -> None:
        sid = "6f1c2e9a-0000-4000-8000-000000000099"
        row = _salon_service_row(sid)
        row["base_price"] = None
        row["duration_minutes"] = None
        row["template"] = None
        httpx_mock.add_response(
            url=f"{_SALON_URL}?tenant={_TID}",
            json={"count": 1, "next": None, "previous": None, "results": [row]},
        )
        with _client() as c:
            [svc] = c.fetch_salon_services(tenant_id=_TID)
        assert svc.price_from is None
        assert svc.duration_min is None
        assert svc.template is None


class TestAuthErrors:
    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_errors_raise_immediately(self, httpx_mock: HTTPXMock, status: int) -> None:
        # Only ONE response queued — we expect the client NOT to retry.
        httpx_mock.add_response(
            url=f"{_SALON_URL}?tenant={_TID}", status_code=status, json={"detail": "forbidden"}
        )
        with _client() as c, pytest.raises(CatalogAuthError):
            c.fetch_salon_services(tenant_id=_TID)


class TestClientErrors:
    def test_404_raises_client_error(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url=f"{_SALON_URL}?tenant={_TID}", status_code=404, json={"detail": "not found"}
        )
        with _client() as c, pytest.raises(CatalogClientError):
            c.fetch_salon_services(tenant_id=_TID)


class TestRetry5xx:
    def test_two_5xx_then_success(
        self, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("time.sleep", lambda _s: None)
        sid = "6f1c2e9a-0000-4000-8000-000000000001"
        for _ in range(2):
            httpx_mock.add_response(
                url=f"{_SALON_URL}?tenant={_TID}", status_code=503, text="unavailable"
            )
        httpx_mock.add_response(
            url=f"{_SALON_URL}?tenant={_TID}",
            json={"count": 1, "next": None, "previous": None, "results": [_salon_service_row(sid)]},
        )
        with _client() as c:
            out = c.fetch_salon_services(tenant_id=_TID)
        assert len(out) == 1

    def test_5xx_exhausts_retries_raises_transport_error(
        self, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("time.sleep", lambda _s: None)
        for _ in range(3):
            httpx_mock.add_response(
                url=f"{_SALON_URL}?tenant={_TID}", status_code=500, text="burning"
            )
        with _client() as c, pytest.raises(CatalogTransportError):
            c.fetch_salon_services(tenant_id=_TID)


class TestConfigGap:
    def test_missing_token_raises_transport_error(self) -> None:
        with CatalogHttpClient(base_url=_BASE, token="") as c, pytest.raises(CatalogTransportError):
            c.fetch_salon_services(tenant_id=_TID)

    def test_malformed_base_url_raises_transport_error(self) -> None:
        with (
            CatalogHttpClient(base_url="not-a-url", token=_TOKEN) as c,
            pytest.raises(CatalogTransportError),
        ):
            c.fetch_salon_services(tenant_id=_TID)
