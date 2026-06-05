"""Discovery service tests (#1018).

Acceptance: discovery returns masters from MULTIPLE tenants, public fields
only; the cross-tenant carve-out is the only path that sees across tenants
(contrast against the tenant-scoped `.objects` manager).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from apps.catalog.models import CatalogMaster
from apps.marketplace.discovery import discover_masters
from apps.marketplace.dto import MasterCard
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


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
def penza() -> Tenant:
    return Tenant.objects.create(slug="salon-penza", name="Salon Penza", city="Пенза")


@pytest.fixture
def moscow() -> Tenant:
    return Tenant.objects.create(slug="salon-msk", name="Salon Moscow", city="Москва")


class TestDiscoverMasters:
    def test_returns_masters_across_tenants(self, penza, moscow) -> None:
        _master(penza, 1, "Анна", specialization="маникюр", rating=Decimal("4.8"))
        _master(moscow, 1, "Борис", specialization="стрижка", rating=Decimal("4.5"))

        cards = discover_masters()

        assert {c.name for c in cards} == {"Анна", "Борис"}
        assert {c.tenant_id for c in cards} == {penza.id, moscow.id}
        assert {c.city for c in cards} == {"Пенза", "Москва"}
        assert all(isinstance(c, MasterCard) for c in cards)

    def test_excludes_non_bookable(self, penza) -> None:
        _master(penza, 1, "Active")
        _master(penza, 2, "Inactive", is_active=False)
        _master(penza, 3, "Pending", invite_status=CatalogMaster.InviteStatus.PENDING)

        names = {c.name for c in discover_masters()}

        assert names == {"Active"}

    def test_city_filter(self, penza, moscow) -> None:
        _master(penza, 1, "Анна")
        _master(moscow, 1, "Борис")

        # Exact match on the owning tenant's city.
        cards = discover_masters(city="Пенза")

        assert {c.name for c in cards} == {"Анна"}

    def test_city_filter_case_insensitive(self) -> None:
        # `iexact` case-folding (ASCII here — SQLite, the test backend, only
        # folds ASCII; Postgres folds Unicode in production).
        t = Tenant.objects.create(slug="salon-penza-en", name="Penza EN", city="Penza")
        _master(t, 1, "Anna")

        assert {c.name for c in discover_masters(city="penza")} == {"Anna"}

    def test_specialization_filter(self, penza) -> None:
        _master(penza, 1, "Анна", specialization="маникюр педикюр")
        _master(penza, 2, "Борис", specialization="стрижка")

        cards = discover_masters(specialization="педикюр")

        assert {c.name for c in cards} == {"Анна"}

    def test_limit_is_clamped_and_applied(self, penza) -> None:
        for i in range(5):
            _master(penza, i, f"M{i}")

        assert len(discover_masters(limit=2)) == 2
        assert len(discover_masters(limit=0)) == 1  # clamped up to >=1

    def test_cross_tenant_carveout_is_the_only_path(self, penza, moscow) -> None:
        """`.objects` (tenant-scoped) sees one tenant; discovery sees both."""
        _master(penza, 1, "Анна")
        _master(moscow, 1, "Борис")

        with tenant_scope(penza):
            scoped = {m.name for m in CatalogMaster.objects.all()}
        assert scoped == {"Анна"}  # tenant-scoped manager hides Moscow

        assert {c.name for c in discover_masters()} == {"Анна", "Борис"}
