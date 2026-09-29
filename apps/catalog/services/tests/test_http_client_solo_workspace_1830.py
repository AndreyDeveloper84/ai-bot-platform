"""``CatalogHttpClient.provision_solo_workspace`` — каталожный workspace соло (DRF-1830).

Сеть подменена ``pytest-httpx``. Проверяется проводная форма ручки DRF-1828
(beautygo_backend #432): путь, ПРОВИЖИНИНГ-токен (не общий Bearer), тело,
разбор 201/200/409/403/4xx/5xx и пустой токен на нашей стороне — каждый исход
своим именем, потому что чинятся они в разных местах.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from apps.catalog.services.http_client import (
    CatalogClientError,
    CatalogHttpClient,
    CatalogProvisioningRefused,
    CatalogProvisioningTokenMissing,
    CatalogSoloProvisioningRefused,
    CatalogTransportError,
)

_BASE = "https://ayla.test"
_TOKEN = "internal-token-abc"  # noqa: S105
_PROVISIONING = "provisioning-token-xyz"  # noqa: S105
_URL = f"{_BASE}/api/v1/internal/tenants/solo-workspaces/"
_TID = "b0a1c2d3-0000-4000-8000-000000001830"
_SID = "c0a1c2d3-0000-4000-8000-000000001830"
_UID = "d0a1c2d3-0000-4000-8000-000000001830"

_ARGS: dict[str, Any] = {
    "tenant_id": uuid.UUID(_TID),
    "slug": "solo-max-1830abcd",
    "name": "Студия Ольга",
    "city": "Пенза",
    "external_user_id": "bot:max:1830001",
    "display_name": "Ольга Петрова",
}


def _client(**kwargs: Any) -> CatalogHttpClient:
    kwargs.setdefault("provisioning_token", _PROVISIONING)
    return CatalogHttpClient(base_url=_BASE, token=_TOKEN, retries=3, timeout=5, **kwargs)


def _row(**over: Any) -> dict[str, Any]:
    base = {
        "tenant_id": _TID,
        "slug": "solo-max-1830abcd",
        "specialist_id": _SID,
        "user_id": _UID,
        "status": "draft",
    }
    base.update(over)
    return {"data": base}


class TestTheWireShape:
    def test_posts_the_workspace_with_the_provisioning_token(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(method="POST", url=_URL, status_code=201, json=_row())

        with _client() as c:
            dto = c.provision_solo_workspace(**_ARGS)

        request = httpx_mock.get_request()
        assert request is not None
        assert request.method == "POST"
        assert request.headers["Authorization"] == f"Bearer {_PROVISIONING}"
        assert request.headers["Authorization"] != f"Bearer {_TOKEN}"
        assert json.loads(request.content) == {
            "tenant_id": _TID,
            "slug": "solo-max-1830abcd",
            "name": "Студия Ольга",
            "city": "Пенза",
            "external_user_id": "bot:max:1830001",
            "display_name": "Ольга Петрова",
        }
        assert dto.tenant_id == uuid.UUID(_TID)
        assert dto.specialist_id == uuid.UUID(_SID)
        assert dto.user_id == uuid.UUID(_UID)
        assert dto.status == "draft"
        assert dto.created is True

    def test_200_is_the_same_workspace_not_created(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(method="POST", url=_URL, status_code=200, json=_row())
        with _client() as c:
            dto = c.provision_solo_workspace(**_ARGS)
        assert dto.created is False
        assert dto.specialist_id == uuid.UUID(_SID)


class TestEveryOutcomeHasItsOwnName:
    def test_empty_token_on_our_side_sends_nothing(self, httpx_mock: HTTPXMock) -> None:
        with _client(provisioning_token="") as c, pytest.raises(CatalogProvisioningTokenMissing):
            c.provision_solo_workspace(**_ARGS)
        assert httpx_mock.get_requests() == []

    def test_403_is_refused_not_auth_error(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(method="POST", url=_URL, status_code=403, json={"error": {}})
        with _client() as c, pytest.raises(CatalogProvisioningRefused):
            c.provision_solo_workspace(**_ARGS)

    def test_409_carries_the_catalog_reason(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="POST",
            url=_URL,
            status_code=409,
            json={
                "error": {"code": "SOLO_PROVISIONING_REFUSED", "details": {"reason": "slug_taken"}}
            },
        )
        with _client() as c, pytest.raises(CatalogSoloProvisioningRefused) as exc:
            c.provision_solo_workspace(**_ARGS)
        assert exc.value.reason == "slug_taken"

    def test_other_4xx_is_a_client_error(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(method="POST", url=_URL, status_code=400, json={"error": {}})
        with _client() as c, pytest.raises(CatalogClientError):
            c.provision_solo_workspace(**_ARGS)

    def test_5xx_is_transport(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(method="POST", url=_URL, status_code=502)
        with _client() as c, pytest.raises(CatalogTransportError):
            c.provision_solo_workspace(**_ARGS)

    def test_a_body_without_ids_is_transport_not_a_half_dto(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="POST", url=_URL, status_code=201, json={"data": {"tenant_id": _TID}}
        )
        with _client() as c, pytest.raises(CatalogTransportError):
            c.provision_solo_workspace(**_ARGS)
