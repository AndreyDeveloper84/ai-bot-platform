"""Catalog masters upserter tests — Ayla specialists → CatalogMaster (S3B).

Pins the mapping (id←Ayla id, user_id→ayla_user_id, display_name→name,
bio→bio, experience_years→str, rating decimal, reviews_count→count,
is_active←status==active AND is_available), rerun idempotency, the
upsert-only missing-row policy, and tenant isolation. Platform-owned
fields (invite_status, photo_url) must survive sync untouched.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from apps.catalog.models import CatalogMaster
from apps.catalog.services.http_client import CatalogSpecialistDTO
from apps.catalog.services.upserter import UpsertResult, upsert_specialists
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="cat-mst", name="Cat Masters")


@pytest.fixture
def tenant_b(db) -> Tenant:
    return Tenant.objects.create(slug="cat-mst-b", name="Cat Masters B")


def _dto(
    ayla_master_id: str | None = None,
    *,
    user_id: str | None = None,
    name: str = "Анна Иванова",
    bio: str = "Топ-мастер",
    experience_years: int | None = 5,
    rating: str = "4.90",
    reviews_count: int = 42,
    status: str = "active",
    is_available: bool = True,
    tenant: str | None = None,
) -> CatalogSpecialistDTO:
    return CatalogSpecialistDTO(
        ayla_master_id=ayla_master_id or str(uuid.uuid4()),
        user_id=user_id or str(uuid.uuid4()),
        name=name,
        tenant=tenant,
        external_updated_at=datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc),
        bio=bio,
        experience=str(experience_years) if experience_years is not None else "",
        rating=Decimal(rating) if rating else None,
        review_count=reviews_count,
        is_active=(status == "active" and is_available),
        raw={"id": ayla_master_id, "display_name": name},
    )


class TestCreate:
    def test_creates_row_keyed_by_ayla_uuid(self, tenant: Tenant) -> None:
        mid = str(uuid.uuid4())
        uid = str(uuid.uuid4())
        res = upsert_specialists(tenant, [_dto(mid, user_id=uid)])

        assert isinstance(res, UpsertResult)
        assert (res.created, res.updated, res.errors) == (1, 0, [])
        m = CatalogMaster.all_tenants.get(tenant=tenant, id=mid)
        assert m.name == "Анна Иванова"
        assert str(m.ayla_user_id) == uid
        assert m.bio == "Топ-мастер"
        assert m.experience == "5"
        assert m.rating == Decimal("4.90")
        assert m.review_count == 42
        assert m.is_active is True

    def test_mapping_inactive_when_not_available(self, tenant: Tenant) -> None:
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid, status="active", is_available=False)])
        assert CatalogMaster.all_tenants.get(id=mid).is_active is False

    def test_experience_none_maps_empty_string(self, tenant: Tenant) -> None:
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid, experience_years=None)])
        assert CatalogMaster.all_tenants.get(id=mid).experience == ""

    def test_platform_fields_untouched_on_create(self, tenant: Tenant) -> None:
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid)])
        m = CatalogMaster.all_tenants.get(id=mid)
        # Platform-owned defaults must not be overwritten by sync.
        assert m.invite_status == CatalogMaster.InviteStatus.ACCEPTED
        assert m.photo_url == ""


class TestUpdate:
    def test_second_upsert_updates_same_row(self, tenant: Tenant) -> None:
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid, name="Старое имя", rating="4.10")])
        res = upsert_specialists(tenant, [_dto(mid, name="Новое имя", rating="4.95")])

        assert (res.created, res.updated) == (0, 1)
        assert CatalogMaster.all_tenants.filter(id=mid).count() == 1
        m = CatalogMaster.all_tenants.get(id=mid)
        assert m.name == "Новое имя"
        assert m.rating == Decimal("4.95")

    def test_platform_fields_survive_update(self, tenant: Tenant) -> None:
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid)])
        m = CatalogMaster.all_tenants.get(id=mid)
        m.photo_url = "https://cdn.test/photo.jpg"
        m.save(update_fields=["photo_url"])

        upsert_specialists(tenant, [_dto(mid, name="Обновлён")])

        m.refresh_from_db()
        assert m.photo_url == "https://cdn.test/photo.jpg"
        assert m.name == "Обновлён"


class TestIdempotency:
    def test_rerun_same_data_updates_not_duplicates(self, tenant: Tenant) -> None:
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid)])
        res = upsert_specialists(tenant, [_dto(mid)])
        assert (res.created, res.updated) == (0, 1)
        assert CatalogMaster.all_tenants.filter(id=mid).count() == 1

    def test_missing_from_feed_row_kept(self, tenant: Tenant) -> None:
        """Upsert-only policy (same as salon-services): a master that
        disappears from the feed is NOT deactivated or deleted by sync."""
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid)])
        res = upsert_specialists(tenant, [])  # empty feed
        assert (res.created, res.updated) == (0, 0)
        m = CatalogMaster.all_tenants.get(id=mid)
        assert m.is_active is True


class TestTenantIsolation:
    def test_same_ayla_id_second_tenant_cannot_hijack(
        self, tenant: Tenant, tenant_b: Tenant
    ) -> None:
        """``CatalogMaster.id`` is the canonical Ayla UUID and the GLOBAL
        primary key — a specialist lives in exactly one tenant of the
        mirror (pilot single-tenant). Upserting the same id into a second
        tenant must NOT steal or duplicate the row: it lands as a per-row
        error, the first tenant's row is untouched."""
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid, name="В салоне А")])

        res = upsert_specialists(tenant_b, [_dto(mid, name="В салоне Б")])

        assert (res.created, res.updated) == (0, 0)
        assert len(res.errors) == 1  # PK collision, isolated per-row
        assert CatalogMaster.all_tenants.filter(id=mid).count() == 1
        m = CatalogMaster.all_tenants.get(id=mid)
        assert m.tenant_id == tenant.id
        assert m.name == "В салоне А"

    def test_pk_collision_names_the_cause(self, tenant: Tenant, tenant_b: Tenant) -> None:
        """DRF-1313 — the collision above is not hypothetical any more.

        A tenant-blind pull left five pilot masters under the wrong salon, and
        because ``CatalogMaster.id`` is the global PK those rows now hold the
        ids hostage: the *corrected* sync still cannot create each master under
        its real salon while the wrong salon owns the row. Every beat fails
        here until someone removes it — so the failure has to say so, not
        surface as a bare duplicate-key string.

        This is the seam between the code fix and the data cleanup. The upsert
        deliberately does not re-parent or delete: whose row that is, is an
        owner decision.
        """
        mid = str(uuid.uuid4())
        upsert_specialists(tenant, [_dto(mid, name="В салоне А")])

        res = upsert_specialists(tenant_b, [_dto(mid, name="В салоне Б")])

        assert res.errors[0]["reason"] == "held_by_other_tenant"
        assert res.errors[0]["ayla_master_id"] == mid
        assert CatalogMaster.all_tenants.get(id=mid).tenant_id == tenant.id


class TestCrossTenantGuard:
    """DRF-1313 — the payload's own tenant is re-checked before any write.

    ``fetch_specialists`` sends ``?tenant=`` now, so in principle the feed is
    already scoped. In practice the whole defect was a filter that did not
    apply and said nothing about it, so the mirror verifies rather than trusts
    — the same guard the edge upsert has carried since DRF-945.
    """

    def test_foreign_tenant_payload_is_skipped_not_written(
        self, tenant: Tenant, tenant_b: Tenant
    ) -> None:
        mid = str(uuid.uuid4())
        res = upsert_specialists(
            tenant,
            [_dto(mid, name="Чужой мастер", tenant=str(tenant_b.id))],
        )

        assert (res.created, res.updated, res.skipped) == (0, 0, 1)
        assert res.errors == []
        assert not CatalogMaster.all_tenants.filter(id=mid).exists()

    def test_matching_tenant_payload_is_written(self, tenant: Tenant) -> None:
        mid = str(uuid.uuid4())
        res = upsert_specialists(tenant, [_dto(mid, tenant=str(tenant.id))])

        assert (res.created, res.skipped) == (1, 0)
        assert CatalogMaster.all_tenants.get(id=mid).tenant_id == tenant.id

    def test_absent_tenant_payload_is_written(self, tenant: Tenant) -> None:
        """No ``tenant`` on the row means *unverifiable*, not *foreign*.

        An Ayla deployed before the field exists must keep mirroring. Treating
        a missing value as a mismatch would turn a deploy-order skew into an
        outage — and the ordering (Ayla first, bot second) exists precisely so
        this window is survivable.
        """
        mid = str(uuid.uuid4())
        res = upsert_specialists(tenant, [_dto(mid, tenant=None)])

        assert (res.created, res.skipped) == (1, 0)
        assert CatalogMaster.all_tenants.get(id=mid).tenant_id == tenant.id

    def test_foreign_row_does_not_abort_the_batch(self, tenant: Tenant, tenant_b: Tenant) -> None:
        """One bad row must not cost the salon its other masters."""
        good = _dto(str(uuid.uuid4()), tenant=str(tenant.id))
        foreign = _dto(str(uuid.uuid4()), tenant=str(tenant_b.id))

        res = upsert_specialists(tenant, [foreign, good])

        assert (res.created, res.skipped) == (1, 1)
        assert CatalogMaster.all_tenants.get(id=good.ayla_master_id)
        assert not CatalogMaster.all_tenants.filter(id=foreign.ayla_master_id).exists()


class TestErrorIsolation:
    def test_bad_row_does_not_abort_batch(self, tenant: Tenant) -> None:
        good = _dto(str(uuid.uuid4()))
        bad = CatalogSpecialistDTO(
            ayla_master_id=str(uuid.uuid4()),
            user_id=None,
            name="Bad",
            external_updated_at=None,  # type: ignore[arg-type]  # violates NOT NULL
        )
        res = upsert_specialists(tenant, [good, bad])
        assert res.created == 1
        assert len(res.errors) == 1
        assert res.errors[0]["ayla_master_id"] == bad.ayla_master_id
