"""``CatalogHttpClient.ensure_tenant`` — салон по slug в каталоге (DRF-1525).

Сеть подменена ``pytest-httpx``. Проверяется проводная форма: путь,
ПРОВИЖИНИНГ-токен (не общий Bearer), тело, разбор 201/200/409/403/5xx и
пустой токен на нашей стороне — каждый исход своим именем, потому что
чинятся они в разных местах.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from apps.catalog.services.http_client import (
    CatalogClientError,
    CatalogHttpClient,
    CatalogProvisioningRefused,
    CatalogProvisioningTokenMissing,
    CatalogSlugTaken,
    CatalogTransportError,
)

_BASE = "https://ayla.test"
_TOKEN = "internal-token-abc"  # noqa: S105
_PROVISIONING = "provisioning-token-xyz"  # noqa: S105
_URL = f"{_BASE}/api/v1/internal/tenants/"
_TID = "b0a1c2d3-0000-4000-8000-000000000001"


def _client(**kwargs: Any) -> CatalogHttpClient:
    kwargs.setdefault("provisioning_token", _PROVISIONING)
    return CatalogHttpClient(base_url=_BASE, token=_TOKEN, retries=3, timeout=5, **kwargs)


def _row(**over: Any) -> dict[str, Any]:
    base = {
        "id": _TID,
        "slug": "mednyy-kovsh",
        "name": "Медный ковш",
        "city": "Пенза",
        "is_active": True,
    }
    base.update(over)
    return {"data": base}


class TestTheWireShape:
    def test_posts_with_the_provisioning_token_not_the_general_one(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Тот же клиент, другой секрет: право заводить — не право читать."""
        httpx_mock.add_response(method="POST", url=_URL, status_code=201, json=_row())

        with _client() as c:
            c.ensure_tenant(slug="mednyy-kovsh", name="Медный ковш", city="Пенза")

        request = httpx_mock.get_request()
        assert request is not None
        assert request.method == "POST"
        assert request.headers["Authorization"] == f"Bearer {_PROVISIONING}"
        assert request.headers["Authorization"] != f"Bearer {_TOKEN}"
        assert json.loads(request.content) == {
            "slug": "mednyy-kovsh",
            "name": "Медный ковш",
            "city": "Пенза",
        }

    def test_201_is_created_and_200_is_found(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(method="POST", url=_URL, status_code=201, json=_row())
        httpx_mock.add_response(method="POST", url=_URL, status_code=200, json=_row())

        with _client() as c:
            first = c.ensure_tenant(slug="mednyy-kovsh", name="Медный ковш")
            second = c.ensure_tenant(slug="mednyy-kovsh", name="Медный ковш")

        assert first.created is True
        assert second.created is False
        assert str(first.id) == str(second.id) == _TID

    def test_null_city_stays_none(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(method="POST", url=_URL, status_code=200, json=_row(city=None))
        with _client() as c:
            dto = c.ensure_tenant(slug="mednyy-kovsh", name="Медный ковш")
        assert dto.city is None


class TestEachRefusalHasItsOwnName:
    def test_empty_token_on_our_side_never_sends(self, httpx_mock: HTTPXMock) -> None:
        """Пусто у нас → отдельное имя, и запрос НЕ уходит."""
        with _client(provisioning_token="") as c, pytest.raises(CatalogProvisioningTokenMissing):
            c.ensure_tenant(slug="mednyy-kovsh", name="Медный ковш")
        assert httpx_mock.get_request() is None

    def test_403_is_refused_not_auth_error(self, httpx_mock: HTTPXMock) -> None:
        """403 от каталога — «контур не донастроен», не сбой зеркала."""
        from apps.catalog.services.http_client import CatalogAuthError

        httpx_mock.add_response(method="POST", url=_URL, status_code=403, json={"detail": "no"})
        with _client() as c, pytest.raises(CatalogProvisioningRefused) as info:
            c.ensure_tenant(slug="mednyy-kovsh", name="Медный ковш")
        # Присутствие впереди отсутствия: это именно отказ провижининга —
        # и НЕ тот класс, который синхронизация читает как сбой зеркала.
        assert isinstance(info.value, CatalogProvisioningRefused)
        assert not isinstance(info.value, CatalogAuthError)

    def test_409_carries_the_existing_name(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="POST",
            url=_URL,
            status_code=409,
            json={
                "error": {
                    "code": "TENANT_SLUG_TAKEN",
                    "message": "занят",
                    "details": {
                        "slug": "mednyy-kovsh",
                        "existing_name": "Медный Ковшъ",
                        "requested_name": "Медный ковш",
                    },
                }
            },
        )
        with _client() as c, pytest.raises(CatalogSlugTaken) as info:
            c.ensure_tenant(slug="mednyy-kovsh", name="Медный ковш")
        assert info.value.existing_name == "Медный Ковшъ"
        assert info.value.requested_name == "Медный ковш"

    def test_other_4xx_is_client_error(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(method="POST", url=_URL, status_code=400, json={"error": {}})
        with _client() as c, pytest.raises(CatalogClientError):
            c.ensure_tenant(slug="Bad Slug", name="x")

    def test_5xx_and_transport_are_transport_errors_without_retry(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Действие не повторяется вслепую: один запрос на один вызов."""
        httpx_mock.add_response(method="POST", url=_URL, status_code=503)
        with _client() as c, pytest.raises(CatalogTransportError):
            c.ensure_tenant(slug="mednyy-kovsh", name="Медный ковш")
        assert len(httpx_mock.get_requests()) == 1

        httpx_mock.add_exception(httpx.ConnectError("down"), method="POST", url=_URL)
        with _client() as c, pytest.raises(CatalogTransportError):
            c.ensure_tenant(slug="mednyy-kovsh", name="Медный ковш")
        assert len(httpx_mock.get_requests()) == 2

    def test_a_2xx_without_a_uuid_is_not_trusted(self, httpx_mock: HTTPXMock) -> None:
        """Ответ без UUID — не «салон без UUID», а неразборчивый ответ."""
        httpx_mock.add_response(
            method="POST", url=_URL, status_code=200, json={"data": {"slug": "x"}}
        )
        with _client() as c, pytest.raises(CatalogTransportError):
            c.ensure_tenant(slug="mednyy-kovsh", name="Медный ковш")
