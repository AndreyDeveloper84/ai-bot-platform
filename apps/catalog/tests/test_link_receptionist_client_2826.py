"""DRF-2826 — the bot's link client can ask the catalog for a RECEPTIONIST.

The catalog side (beautygo_backend DRF-2826 K4) takes ``role`` and
``assigned_by_external_user_id`` on ``POST /internal/tenants/<slug>/salon-admins/``
and refuses a receptionist unless the assigner is an active admin of THAT
salon. Here: the receptionist body carries both fields; the admin body is
byte-for-byte what it was (no new keys on the wire for the old path).
"""

from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock

from apps.catalog.services.http_client import CatalogHttpClient

BASE = "http://catalog.test"
SLUG = "link-2826"
URL = f"{BASE}/api/v1/internal/tenants/{SLUG}/salon-admins/"


def _ok(role: str) -> dict:
    return {
        "data": {
            "tenant_id": "6f1c1a9e-0000-4000-8000-000000002826",
            "slug": SLUG,
            "ayla_user_id": "6f1c1a9e-0000-4000-8000-000000002827",
            "relationship_id": "6f1c1a9e-0000-4000-8000-000000002828",
            "role": role,
            "status": "created",
        }
    }


@pytest.fixture
def client() -> CatalogHttpClient:
    return CatalogHttpClient(
        base_url=BASE, token="general-2826", salon_admin_link_token="link-2826"
    )


def _call(client: CatalogHttpClient, **extra):
    return client.link_salon_admin(
        tenant_slug=SLUG,
        external_user_id="bot:max:2826001",
        actor="bot_admin:2826",
        correlation_id="corr-2826",
        idempotency_key="idem-2826-0001",
        **extra,
    )


def test_b1_receptionist_body_names_the_role_and_the_assigning_admin(
    client: CatalogHttpClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(method="POST", url=URL, json=_ok("receptionist"), status_code=201)

    _call(client, role="receptionist", assigned_by_external_user_id="bot:max:2826000")

    (req,) = httpx_mock.get_requests()
    body = json.loads(req.content)
    assert body["role"] == "receptionist"
    assert body["assigned_by_external_user_id"] == "bot:max:2826000"


def test_b2_admin_body_is_unchanged(client: CatalogHttpClient, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", url=URL, json=_ok("admin"), status_code=201)

    _call(client)

    (req,) = httpx_mock.get_requests()
    assert set(json.loads(req.content)) == {
        "external_user_id",
        "actor",
        "correlation_id",
        "idempotency_key",
    }
