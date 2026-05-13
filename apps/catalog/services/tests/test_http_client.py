"""CatalogHttpClient tests (DRF-573 / Sprint 7 / C2).

Network is mocked via ``pytest-httpx`` so CI never hits real mysite.
We exercise the wire-shape contract: cursor pagination + auth header
+ retry / error-mapping behaviour + DTO parsing.
"""

from __future__ import annotations

from datetime import datetime, timezone
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

_BASE = "https://mysite.test"
_TOKEN = "ci-token-abc"  # noqa: S105


def _client(**kwargs: Any) -> CatalogHttpClient:
    return CatalogHttpClient(
        base_url=_BASE,
        service_token=_TOKEN,
        retries=3,
        timeout=5,
        **kwargs,
    )


def _service_row(external_id: int) -> dict[str, Any]:
    return {
        "id": external_id,
        "updated_at": "2026-05-12T10:00:00Z",
        "slug": f"svc-{external_id}",
        "name": f"Service {external_id}",
        "price_from": "1500.00",
        "duration_min": 60,
        "goals": ["relax"],
        "requires_health_check": False,
        "is_active": True,
    }


def _faq_row(external_id: int) -> dict[str, Any]:
    return {
        "id": external_id,
        "updated_at": "2026-05-12T10:00:00Z",
        "question": f"Q{external_id}",
        "answer": f"A{external_id}",
    }


class TestAuthHeader:
    def test_attaches_service_token(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url=f"{_BASE}/api/v1/catalog/services/",
            json={"results": [], "next": None},
        )
        with _client() as c:
            c.fetch_services()
        request = httpx_mock.get_request()
        assert request is not None
        assert request.headers["X-Service-Token"] == _TOKEN


class TestSinceCursor:
    def test_since_passed_as_iso_query_param(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url=f"{_BASE}/api/v1/catalog/services/?since=2026-05-13T00%3A00%3A00%2B00%3A00",
            json={"results": [], "next": None},
        )
        with _client() as c:
            c.fetch_services(since=datetime(2026, 5, 13, tzinfo=timezone.utc))


class TestPagination:
    def test_follows_next_until_exhausted(self, httpx_mock: HTTPXMock) -> None:
        # Page 1 → next page 2 → end.
        next_url = f"{_BASE}/api/v1/catalog/services/?page=2"
        httpx_mock.add_response(
            url=f"{_BASE}/api/v1/catalog/services/",
            json={
                "results": [_service_row(1), _service_row(2)],
                "next": next_url,
            },
        )
        httpx_mock.add_response(
            url=next_url,
            json={"results": [_service_row(3)], "next": None},
        )
        with _client() as c:
            out = c.fetch_services()
        assert [row.external_id for row in out] == [1, 2, 3]


class TestDTOParsing:
    def test_service_dto_fields(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url=f"{_BASE}/api/v1/catalog/services/",
            json={"results": [_service_row(42)], "next": None},
        )
        with _client() as c:
            [svc] = c.fetch_services()
        assert svc.external_id == 42
        assert svc.slug == "svc-42"
        assert svc.name == "Service 42"
        assert svc.price_from == Decimal("1500.00")
        assert svc.duration_min == 60
        assert svc.goals == ["relax"]
        # external_updated_at parsed as aware datetime.
        assert svc.external_updated_at.tzinfo is not None
        # raw retains the original payload for forensics.
        assert svc.raw["slug"] == "svc-42"

    def test_faq_dto(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url=f"{_BASE}/api/v1/catalog/faqs/",
            json={"results": [_faq_row(7)], "next": None},
        )
        with _client() as c:
            [faq] = c.fetch_faqs()
        assert faq.external_id == 7
        assert faq.question == "Q7"
        assert faq.answer == "A7"


class TestAuthErrors:
    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_errors_raise_immediately(self, httpx_mock: HTTPXMock, status: int) -> None:
        # Only ONE response queued — we expect the client NOT to retry.
        httpx_mock.add_response(
            url=f"{_BASE}/api/v1/catalog/services/",
            status_code=status,
            json={"detail": "forbidden"},
        )
        with _client() as c, pytest.raises(CatalogAuthError):
            c.fetch_services()


class TestClientErrors:
    def test_404_raises_client_error(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url=f"{_BASE}/api/v1/catalog/services/",
            status_code=404,
            json={"detail": "not found"},
        )
        with _client() as c, pytest.raises(CatalogClientError):
            c.fetch_services()


class TestRetry5xx:
    def test_two_5xx_then_success(self, httpx_mock: HTTPXMock) -> None:
        for _ in range(2):
            httpx_mock.add_response(
                url=f"{_BASE}/api/v1/catalog/services/",
                status_code=503,
                text="bad gateway",
            )
        httpx_mock.add_response(
            url=f"{_BASE}/api/v1/catalog/services/",
            json={"results": [_service_row(1)], "next": None},
        )
        with _client() as c:
            out = c.fetch_services()
        assert len(out) == 1

    def test_5xx_exhausts_retries_raises_transport_error(
        self, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Drop sleep so the test doesn't take seconds.
        monkeypatch.setattr("time.sleep", lambda _s: None)
        for _ in range(3):
            httpx_mock.add_response(
                url=f"{_BASE}/api/v1/catalog/services/",
                status_code=500,
                text="server burning",
            )
        with _client() as c, pytest.raises(CatalogTransportError):
            c.fetch_services()
