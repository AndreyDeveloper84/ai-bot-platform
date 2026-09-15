"""The bot's catalog sync reads «не принимаю записи» (DRF-1845, K1b).

The catalog keeps a master whose bookings are paused IN the specialists feed,
with ``is_booking_enabled=false`` — it has to, because this sync upserts rows
and never deactivates the ones that stop arriving. So the pause is read here.

What is locked:

* the wire → DTO mapping: paused → ``is_active=False``; taking bookings →
  ``True``; the key absent (an older catalog) → today's behaviour, ``True``;
* across two syncs the mirror follows: paused → ``CatalogMaster.is_active``
  goes false; pause lifted → back to true on the next sync.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from apps.catalog.models import CatalogMaster
from apps.catalog.services.http_client import CatalogHttpClient
from apps.catalog.services.upserter import upsert_specialists
from apps.tenancy.models import Tenant

MASTER_ID = "9d3f0000-0000-4000-8000-00000000184a"


def _row(tenant_id: str, **overrides) -> dict:
    row = {
        "id": MASTER_ID,
        "user_id": "9d3f0000-0000-4000-8000-00000000184b",
        "display_name": "Анна Иванова",
        "bio": "",
        "experience_years": 5,
        "status": "active",
        "rating": "4.90",
        "reviews_count": 3,
        "is_available": True,
        "is_booking_enabled": True,
        "tenant": tenant_id,
    }
    row.update(overrides)
    return {k: v for k, v in row.items() if v is not _ABSENT}


_ABSENT = object()


def _fetch(tenant_id: str, row: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"count": 1, "next": None, "previous": None, "results": [row]}
        )

    client = CatalogHttpClient(
        base_url="https://ayla.test",
        token="t",  # noqa: S106
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return client.fetch_specialists(tenant_id=tenant_id)


class TestWireMapping:
    TID = str(uuid.uuid4())

    def test_a_paused_master_is_not_active(self) -> None:
        (dto,) = _fetch(self.TID, _row(self.TID, is_booking_enabled=False))
        assert dto.is_active is False

    def test_a_master_taking_bookings_is_active(self) -> None:
        (dto,) = _fetch(self.TID, _row(self.TID, is_booking_enabled=True))
        assert dto.is_active is True

    def test_an_older_catalog_without_the_key_keeps_todays_behaviour(self) -> None:
        (dto,) = _fetch(self.TID, _row(self.TID, is_booking_enabled=_ABSENT))
        assert dto.is_active is True

    def test_the_pause_does_not_override_other_reasons(self) -> None:
        (dto,) = _fetch(self.TID, _row(self.TID, is_available=False, is_booking_enabled=True))
        assert dto.is_active is False


@pytest.mark.django_db
class TestTheMirrorFollowsAcrossSyncs:
    def test_pause_then_resume(self) -> None:
        tenant = Tenant.objects.create(slug="sync-1845", name="Sync 1845")
        tid = str(tenant.id)

        upsert_specialists(tenant, _fetch(tid, _row(tid, is_booking_enabled=True)))
        assert CatalogMaster.all_tenants.get(pk=MASTER_ID).is_active is True

        upsert_specialists(tenant, _fetch(tid, _row(tid, is_booking_enabled=False)))
        assert CatalogMaster.all_tenants.get(pk=MASTER_ID).is_active is False

        # The main window's case: lifting the pause comes back on the next sync.
        upsert_specialists(tenant, _fetch(tid, _row(tid, is_booking_enabled=True)))
        assert CatalogMaster.all_tenants.get(pk=MASTER_ID).is_active is True
