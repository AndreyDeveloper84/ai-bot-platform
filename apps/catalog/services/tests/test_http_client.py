"""CatalogHttpClient tests — Ayla internal catalog (S3B / #1044).

Network is mocked via ``pytest-httpx`` so CI never hits real Ayla. We
exercise the wire-shape contract: Bearer auth + tenant filter + DRF
PageNumberPagination + retry / error-mapping + DTO parsing against
``docs/CATALOG_INTERNAL_API_CONTRACT.md``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
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


class TestFetchSpecialists:
    def test_parses_specialist_rows(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/v1/internal/specialists/"
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "next": None,
                    "previous": None,
                    "results": [
                        {
                            "id": "9d3f0000-0000-4000-8000-0000000000aa",
                            "user_id": "9d3f0000-0000-4000-8000-0000000000bb",
                            "display_name": "Анна Иванова",
                            "bio": "Топ-мастер",
                            "experience_years": 5,
                            "status": "active",
                            "rating": "4.90",
                            "reviews_count": 42,
                            "is_available": True,
                            "tenant": _TID,
                            "address": "Пенза, Московская 1",
                        }
                    ],
                },
            )

        from apps.catalog.services.http_client import CatalogHttpClient

        client = CatalogHttpClient(
            base_url="https://ayla.test",
            token="t",  # noqa: S106
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        rows = client.fetch_specialists(tenant_id=_TID)

        assert len(rows) == 1
        dto = rows[0]
        assert dto.ayla_master_id == "9d3f0000-0000-4000-8000-0000000000aa"
        assert dto.user_id == "9d3f0000-0000-4000-8000-0000000000bb"
        assert dto.name == "Анна Иванова"
        assert dto.experience == "5"
        assert str(dto.rating) == "4.90"
        assert dto.review_count == 42
        assert dto.is_active is True
        assert dto.tenant == _TID
        assert dto.raw["address"] == "Пенза, Московская 1"

    def test_sends_the_tenant_filter(self) -> None:
        """DRF-1313: the pull must be scoped, and the wire is the only place
        that can be checked. Without ``?tenant=`` Ayla answers with every
        active master on the platform and the first tenant to sync claims all
        of them.
        """
        seen: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.params.get("tenant"))
            return httpx.Response(
                200,
                json={"count": 0, "next": None, "previous": None, "results": []},
            )

        from apps.catalog.services.http_client import CatalogHttpClient

        client = CatalogHttpClient(
            base_url=_BASE,
            token="t",  # noqa: S106
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        client.fetch_specialists(tenant_id=_TID)

        assert seen == [_TID]

    def test_tenant_absent_from_payload_parses_as_none(self) -> None:
        """An Ayla that predates the ``tenant`` field must not crash the parse.

        The cross-tenant guard downstream reads this as "unverifiable" rather
        than "mismatch", which is what keeps the two deploys orderable.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "next": None,
                    "previous": None,
                    "results": [
                        {
                            "id": "9d3f0000-0000-4000-8000-0000000000dd",
                            "display_name": "Old Ayla",
                            "status": "active",
                            "is_available": True,
                        }
                    ],
                },
            )

        from apps.catalog.services.http_client import CatalogHttpClient

        client = CatalogHttpClient(
            base_url=_BASE,
            token="t",  # noqa: S106
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        assert client.fetch_specialists(tenant_id=_TID)[0].tenant is None

    def test_missing_updated_at_uses_now(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "next": None,
                    "previous": None,
                    "results": [
                        {
                            "id": "9d3f0000-0000-4000-8000-0000000000cc",
                            "display_name": "No Timestamp",
                            "experience_years": None,
                            "rating": None,
                            "reviews_count": 0,
                            "status": "active",
                            "is_available": True,
                        }
                    ],
                },
            )

        from apps.catalog.services.http_client import CatalogHttpClient

        client = CatalogHttpClient(
            base_url="https://ayla.test",
            token="t",  # noqa: S106
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        rows = client.fetch_specialists(tenant_id=_TID)
        assert rows[0].external_updated_at is not None
        assert rows[0].experience == ""
        assert rows[0].rating is None


_SPEC_SVC_URL = f"{_BASE}/api/v1/internal/catalog/specialist-services/"


def _edge_row(**overrides: Any) -> dict[str, Any]:
    """A specialist-services row per CATALOG_INTERNAL_API_CONTRACT.md §2."""
    row = {
        "id": "a4e00000-0000-4000-8000-000000000010",
        "salon_service": "6f1c0000-0000-4000-8000-000000000011",
        "specialist": "77aa0000-0000-4000-8000-000000000012",
        "user_id": "33cc0000-0000-4000-8000-000000000013",
        "tenant": _TID,
        "template": "9d3f0000-0000-4000-8000-000000000002",
        "name": "Спортивный массаж",
        "category_slug": "massage",
        "duration_minutes": 45,
        "resolved_duration": 45,
        "requires_health_check": False,
        "resolved_requires_health_check": True,
        "price": "1500.00",
        "buffer_after_minutes": 0,
        "is_active": True,
        "yclients_staff_id": "9001",
        "reviews_count": 17,
        "rating": "4.7",
        "created_at": "2026-07-09T18:31:00Z",
        "updated_at": "2026-07-09T18:31:00Z",
    }
    row.update(overrides)
    return row


class TestFetchSpecialistServices:
    """The bookable master↔service edge feed (DRF-945)."""

    def test_edge_dto_fields(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url=f"{_SPEC_SVC_URL}?tenant={_TID}&page_size=100",
            json={"count": 1, "next": None, "previous": None, "results": [_edge_row()]},
        )

        snap = _client().fetch_specialist_services(tenant_id=_TID)

        assert snap.complete is True
        assert len(snap.edges) == 1
        dto = snap.edges[0]
        assert dto.ayla_specialist_service_id == "a4e00000-0000-4000-8000-000000000010"
        assert dto.salon_service == "6f1c0000-0000-4000-8000-000000000011"
        assert dto.specialist == "77aa0000-0000-4000-8000-000000000012"
        assert dto.user_id == "33cc0000-0000-4000-8000-000000000013"
        assert dto.tenant == _TID
        assert dto.name == "Спортивный массаж"
        assert dto.category_slug == "massage"
        assert dto.is_active is True
        # Booking-gate fields stay in raw — no fail-open mirror column.
        assert dto.raw["resolved_requires_health_check"] is True

    def test_tenant_filter_is_sent(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url=f"{_SPEC_SVC_URL}?tenant={_TID}&page_size=100",
            json={"count": 0, "next": None, "previous": None, "results": []},
        )

        _client().fetch_specialist_services(tenant_id=_TID)

        request = httpx_mock.get_request()
        assert request is not None
        assert request.url.params["tenant"] == _TID
        assert request.headers["Authorization"] == f"Bearer {_TOKEN}"
        # page_size is a correctness requirement, not a tweak: reconciliation
        # deletes rows absent from this snapshot, and upstream orders by a
        # non-unique created_at, so fewer page seams means fewer chances for a
        # row to fall out of the snapshot and read as "deleted upstream".
        assert request.url.params["page_size"] == "100"

    def test_inactive_edge_preserved(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url=f"{_SPEC_SVC_URL}?tenant={_TID}&page_size=100",
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [_edge_row(is_active=False)],
            },
        )

        snap = _client().fetch_specialist_services(tenant_id=_TID)

        assert snap.edges[0].is_active is False

    def test_missing_updated_at_falls_back_to_now(self, httpx_mock: HTTPXMock) -> None:
        row = _edge_row()
        del row["updated_at"]
        httpx_mock.add_response(
            url=f"{_SPEC_SVC_URL}?tenant={_TID}&page_size=100",
            json={"count": 1, "next": None, "previous": None, "results": [row]},
        )

        snap = _client().fetch_specialist_services(tenant_id=_TID)

        assert snap.edges[0].external_updated_at is not None

    def test_optional_text_fields_tolerate_null(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url=f"{_SPEC_SVC_URL}?tenant={_TID}&page_size=100",
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [_edge_row(name=None, category_slug=None, user_id=None)],
            },
        )

        dto = _client().fetch_specialist_services(tenant_id=_TID).edges[0]

        assert dto.name == ""
        assert dto.category_slug == ""
        assert dto.user_id is None

    def test_missing_join_key_raises(self, httpx_mock: HTTPXMock) -> None:
        """An edge without its join keys is unmirrorable — fail loudly, not silently."""
        row = _edge_row()
        del row["specialist"]
        httpx_mock.add_response(
            url=f"{_SPEC_SVC_URL}?tenant={_TID}&page_size=100",
            json={"count": 1, "next": None, "previous": None, "results": [row]},
        )

        with pytest.raises(KeyError):
            _client().fetch_specialist_services(tenant_id=_TID)

    def test_auth_failure_maps_to_auth_error(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=f"{_SPEC_SVC_URL}?tenant={_TID}&page_size=100", status_code=403)

        with pytest.raises(CatalogAuthError):
            _client().fetch_specialist_services(tenant_id=_TID)

    def test_snapshot_flagged_incomplete_when_count_disagrees(self, httpx_mock: HTTPXMock) -> None:
        """count=2 but one row delivered ⇒ the page window shifted mid-walk.

        The caller DELETEs rows on absence, so it must learn that this
        snapshot cannot prove absence.
        """
        httpx_mock.add_response(
            url=f"{_SPEC_SVC_URL}?tenant={_TID}&page_size=100",
            json={"count": 2, "next": None, "previous": None, "results": [_edge_row()]},
        )

        snap = _client().fetch_specialist_services(tenant_id=_TID)

        assert snap.complete is False
        assert len(snap.edges) == 1

    def test_snapshot_complete_across_pages(self, httpx_mock: HTTPXMock) -> None:
        page2 = f"{_SPEC_SVC_URL}?tenant={_TID}&page_size=100&page=2"
        httpx_mock.add_response(
            url=f"{_SPEC_SVC_URL}?tenant={_TID}&page_size=100",
            json={
                "count": 2,
                "next": page2,
                "previous": None,
                "results": [_edge_row()],
            },
        )
        httpx_mock.add_response(
            url=page2,
            json={
                "count": 2,
                "next": None,
                "previous": None,
                "results": [_edge_row(id="a4e00000-0000-4000-8000-000000000099")],
            },
        )

        snap = _client().fetch_specialist_services(tenant_id=_TID)

        assert snap.complete is True
        assert len(snap.edges) == 2
