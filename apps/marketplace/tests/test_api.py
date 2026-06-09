"""Public marketplace directory HTTP tests (#249, #250).

Acceptance: anonymous (no auth header) browsing of bookable masters across
MULTIPLE tenants; public fields ONLY on the wire (JSON-guard); page-based
pagination (default + max clamp, page boundaries); city / specialization
filters; detail 200 + 404 (unknown / non-bookable).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from django.test import Client

from apps.catalog.models import CatalogMaster
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/providers/"


def _detail_url(master_id) -> str:
    return f"/api/v1/providers/{master_id}/"


def _ts() -> datetime:
    return datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _master(tenant: Tenant, ext: int, name: str, **kw) -> CatalogMaster:
    defaults = dict(
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
    )
    defaults.update(kw)
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=ext,
        external_updated_at=_ts(),
        name=name,
        **defaults,
    )


@pytest.fixture
def client() -> Client:
    return Client()


@pytest.fixture
def penza() -> Tenant:
    return Tenant.objects.create(slug="salon-penza", name="Salon Penza", city="Пенза")


@pytest.fixture
def moscow() -> Tenant:
    return Tenant.objects.create(slug="salon-msk", name="Salon Moscow", city="Москва")


# Exactly the public fields #1018 sanctions — the JSON-guard contract.
_PUBLIC_KEYS = {
    "tenant_id",
    "master_id",
    "name",
    "specialization",
    "rating",
    "photo_url",
    "city",
}


class TestProvidersList:
    def test_anonymous_returns_masters_across_tenants(self, client, penza, moscow) -> None:
        _master(penza, 1, "Анна", specialization="маникюр", rating=Decimal("4.8"))
        _master(moscow, 1, "Борис", specialization="стрижка", rating=Decimal("4.5"))

        resp = client.get(LIST_URL)  # no Authorization header — public

        assert resp.status_code == 200
        body = resp.json()
        assert {p["name"] for p in body["providers"]} == {"Анна", "Борис"}
        assert {p["city"] for p in body["providers"]} == {"Пенза", "Москва"}
        assert body["total_count"] == 2

    def test_public_fields_only(self, client, penza) -> None:
        # Commercial / identity fields set on the row must NOT reach the wire.
        _master(
            penza,
            1,
            "Анна",
            specialization="маникюр",
            bio="секретная биография",
            experience=10,
            yclients_staff_id=999,
        )

        provider = client.get(LIST_URL).json()["providers"][0]

        assert set(provider.keys()) == _PUBLIC_KEYS
        assert "bio" not in provider
        assert "experience" not in provider
        assert "yclients_staff_id" not in provider
        # Decimal rating is serialized as a string (JSON-safe).
        assert provider["tenant_id"] == str(penza.id)

    def test_pagination_default_and_page_size_clamp(self, client, penza) -> None:
        for i in range(60):
            _master(penza, i, f"M{i:02d}")

        default = client.get(LIST_URL).json()
        assert default["page_size"] == 20  # discovery default
        assert len(default["providers"]) == 20
        assert default["total_count"] == 60
        assert default["num_pages"] == 3

        clamped = client.get(LIST_URL, {"page_size": 999}).json()
        assert clamped["page_size"] == 50  # clamped to _MAX_PAGE_SIZE
        assert len(clamped["providers"]) == 50

    def test_pagination_page_boundaries(self, client, penza) -> None:
        for i in range(25):
            _master(penza, i, f"M{i:02d}")

        page2 = client.get(LIST_URL, {"page": 2, "page_size": 10}).json()
        assert page2["page"] == 2
        assert [p["name"] for p in page2["providers"]] == [f"M{i:02d}" for i in range(10, 20)]

        # Out-of-range page clamps to the last page (here: 3 → items 20-24).
        beyond = client.get(LIST_URL, {"page": 99, "page_size": 10}).json()
        assert beyond["page"] == 3
        assert len(beyond["providers"]) == 5

    def test_bad_pagination_param_returns_400(self, client, penza) -> None:
        _master(penza, 1, "Анна")

        assert client.get(LIST_URL, {"page": "abc"}).status_code == 400
        assert client.get(LIST_URL, {"page_size": "0"}).status_code == 400
        assert client.get(LIST_URL, {"page": "-1"}).status_code == 400

    def test_city_filter(self, client, penza, moscow) -> None:
        _master(penza, 1, "Анна")
        _master(moscow, 1, "Борис")

        body = client.get(LIST_URL, {"city": "Пенза"}).json()
        assert {p["name"] for p in body["providers"]} == {"Анна"}

    def test_specialization_filter(self, client, penza) -> None:
        _master(penza, 1, "Анна", specialization="маникюр педикюр")
        _master(penza, 2, "Борис", specialization="стрижка")

        body = client.get(LIST_URL, {"specialization": "педикюр"}).json()
        assert {p["name"] for p in body["providers"]} == {"Анна"}

    def test_excludes_non_bookable(self, client, penza) -> None:
        _master(penza, 1, "Active")
        _master(penza, 2, "Inactive", is_active=False)
        _master(penza, 3, "Pending", invite_status=CatalogMaster.InviteStatus.PENDING)

        body = client.get(LIST_URL).json()
        assert {p["name"] for p in body["providers"]} == {"Active"}


class TestProviderDetail:
    def test_detail_ok(self, client, penza) -> None:
        m = _master(penza, 1, "Анна", specialization="маникюр", rating=Decimal("4.8"))

        resp = client.get(_detail_url(m.id))

        assert resp.status_code == 200
        provider = resp.json()["provider"]
        assert set(provider.keys()) == _PUBLIC_KEYS
        assert provider["master_id"] == str(m.id)
        assert provider["name"] == "Анна"

    def test_detail_unknown_uuid_404(self, client) -> None:
        resp = client.get(_detail_url(uuid4()))
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_detail_non_bookable_404(self, client, penza) -> None:
        inactive = _master(penza, 1, "Inactive", is_active=False)
        pending = _master(penza, 2, "Pending", invite_status=CatalogMaster.InviteStatus.PENDING)

        assert client.get(_detail_url(inactive.id)).status_code == 404
        assert client.get(_detail_url(pending.id)).status_code == 404
