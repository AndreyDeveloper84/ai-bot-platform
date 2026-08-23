"""Salon/service discovery tests (DRF-1304).

The concierge could show masters but had no reader for the two questions the
live owner asked on 23.08: «какие салоны у нас есть?» and «что у вас есть по
лицу». ``discover_salons`` / ``discover_services`` answer both from the same
mirror and the same bookable predicate as master discovery.

Cyrillic ILIKE matching is Postgres-only (SQLite folds ASCII only), so the
query-matching tests sit in a gated class — same posture as
``test_discovery_by_service.py``: vacuous green on SQLite is worse than an
honest skip.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from django.conf import settings

from apps.catalog.models import CatalogMaster, CatalogService
from apps.marketplace.discovery import discover_salons, discover_services
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _ts() -> datetime:
    return datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def penza() -> Tenant:
    return Tenant.objects.create(slug="salon-penza", name="BodyFormula", city="Пенза")


@pytest.fixture
def moscow() -> Tenant:
    return Tenant.objects.create(slug="salon-msk", name="Медиклиник", city="Москва")


def _master(tenant: Tenant, name: str, **kw) -> CatalogMaster:
    defaults = {
        "is_active": True,
        "invite_status": CatalogMaster.InviteStatus.ACCEPTED,
    }
    defaults.update(kw)
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        name=name,
        **defaults,
    )


def _service(
    tenant: Tenant, name: str, *, price: str | None = None, duration: int | None = None, **kw
) -> CatalogService:
    defaults = {"is_active": True}
    defaults.update(kw)
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        slug=name[:40].lower().replace(" ", "-"),
        name=name,
        price_from=Decimal(price) if price is not None else None,
        duration_min=duration,
        **defaults,
    )


class TestDiscoverSalons:
    def test_returns_salons_across_tenants(self, penza, moscow) -> None:
        _master(penza, "Анна", raw={"address": "Пенза, ул. Леонова, 15а"})
        _master(moscow, "Борис", raw={"address": "Москва, Тверская 1"})
        _service(penza, "Массаж спины", price="1700", duration=45)
        _service(penza, "Массаж лица", price="1500", duration=30)
        _service(penza, "Прессотерапия")
        _service(penza, "УЗ чистка")

        cards = discover_salons()

        by_name = {c.name: c for c in cards}
        assert set(by_name) == {"BodyFormula", "Медиклиник"}
        body = by_name["BodyFormula"]
        assert body.city == "Пенза"
        assert body.address == "Пенза, ул. Леонова, 15а"
        assert body.master_count == 1
        assert body.service_count == 4
        assert len(body.sample_services) == 3  # sample is capped, count is full

    def test_tenant_without_bookable_masters_is_not_a_salon(self, penza, moscow) -> None:
        _master(penza, "Анна")
        _master(moscow, "Pending", invite_status=CatalogMaster.InviteStatus.PENDING)
        _master(moscow, "Inactive", is_active=False)

        assert {c.name for c in discover_salons()} == {"BodyFormula"}

    def test_inactive_tenant_is_not_a_salon(self, penza, moscow) -> None:
        _master(penza, "Анна")
        _master(moscow, "Борис")
        moscow.is_active = False
        moscow.save()

        assert {c.name for c in discover_salons()} == {"BodyFormula"}

    def test_city_filter(self, penza, moscow) -> None:
        _master(penza, "Анна")
        _master(moscow, "Борис")

        assert {c.name for c in discover_salons(city="Пенза")} == {"BodyFormula"}
        assert discover_salons(city="Сочи") == []

    def test_missing_address_is_empty_string_not_a_crash(self, penza) -> None:
        # The pilot salon's masters carry no address at all (live mirror,
        # 23.08) — and raw may even be a non-dict on a hand-written row.
        _master(penza, "Безадресная")
        _master(penza, "Странная", raw=["not", "a", "dict"])

        (card,) = discover_salons()

        assert card.address == ""

    def test_first_non_empty_address_wins(self, penza) -> None:
        _master(penza, "Безадресная", raw={})
        _master(penza, "Садресом", raw={"address": "  Пенза, Московская 74  "})

        (card,) = discover_salons()

        assert card.address == "Пенза, Московская 74"

    def test_salon_without_services_says_so_via_empty_sample(self, penza) -> None:
        _master(penza, "Анна")

        (card,) = discover_salons()

        assert card.service_count == 0
        assert card.sample_services == ()

    def test_inactive_services_not_counted(self, penza) -> None:
        _master(penza, "Анна")
        _service(penza, "Active")
        _service(penza, "Retired", is_active=False)

        (card,) = discover_salons()

        assert card.service_count == 1
        assert card.sample_services == ("Active",)


class TestDiscoverServicesStructural:
    """Filters that do not depend on Cyrillic ILIKE folding run everywhere."""

    def test_only_services_of_bookable_salons(self, penza, moscow) -> None:
        _master(penza, "Анна")
        _service(penza, "Массаж", price="1700", duration=45)
        _service(moscow, "Невидимая")  # moscow has no bookable master

        cards = discover_services()

        assert {c.name for c in cards} == {"Массаж"}
        (card,) = cards
        assert card.salon_name == "BodyFormula"
        assert card.city == "Пенза"
        assert card.price_from == Decimal("1700")
        assert card.duration_min == 45

    def test_inactive_service_excluded(self, penza) -> None:
        _master(penza, "Анна")
        _service(penza, "Active")
        _service(penza, "Retired", is_active=False)

        assert {c.name for c in discover_services()} == {"Active"}

    def test_inactive_tenant_excluded(self, penza) -> None:
        _master(penza, "Анна")
        _service(penza, "Массаж")
        penza.is_active = False
        penza.save()

        assert discover_services() == []

    def test_missing_price_and_duration_stay_none(self, penza) -> None:
        _master(penza, "Анна")
        _service(penza, "Безцена")

        (card,) = discover_services()

        assert card.price_from is None
        assert card.duration_min is None

    def test_untokenizable_query_fails_closed(self, penza) -> None:
        _master(penza, "Анна")
        _service(penza, "Массаж")

        # «я» conveys a request we cannot serve; the whole catalog is not
        # the answer (same posture as _bookable_qs).
        assert discover_services(query="я") == []

    def test_limit_clamped(self, penza) -> None:
        _master(penza, "Анна")
        for i in range(5):
            _service(penza, f"Svc{i}")

        assert len(discover_services(limit=2)) == 2
        assert len(discover_services(limit=0)) == 1  # clamped up to >=1


@pytest.mark.skipif(
    "postgresql" not in str(settings.DATABASES["default"]["ENGINE"]),
    reason="Cyrillic ILIKE folding requires Postgres; on SQLite the negative "
    "assertions would pass vacuously.",
)
class TestDiscoverServicesMatching:
    """Query/salon matching — Postgres-gated like test_discovery_by_service."""

    def test_query_matches_service_name(self, penza, moscow) -> None:
        _master(penza, "Анна")
        _master(moscow, "Борис")
        _service(penza, "Массаж спины")
        _service(moscow, "Стрижка")

        assert {c.name for c in discover_services(query="массаж")} == {"Массаж спины"}

    def test_query_stem_reaches_profession_form(self, penza) -> None:
        # «массажист» is LONGER than the stored «Массаж…» — the 6-char stem
        # cut is what makes the match hold in both directions.
        _master(penza, "Анна")
        _service(penza, "Массаж лица")

        assert {c.name for c in discover_services(query="массажисты")} == {"Массаж лица"}

    def test_city_token_routes_to_city_filter(self, penza, moscow) -> None:
        _master(penza, "Анна")
        _master(moscow, "Борис")
        _service(penza, "Массаж спины")
        _service(moscow, "Массаж ног")

        cards = discover_services(query="массаж в пензе")

        assert {c.name for c in cards} == {"Массаж спины"}

    def test_salon_name_filter(self, penza, moscow) -> None:
        _master(penza, "Анна")
        _master(moscow, "Борис")
        _service(penza, "Массаж спины")
        _service(moscow, "Массаж ног")

        cards = discover_services(salon="медиклиник")

        assert {c.name for c in cards} == {"Массаж ног"}

    def test_city_filter(self, penza, moscow) -> None:
        _master(penza, "Анна")
        _master(moscow, "Борис")
        _service(penza, "Массаж спины")
        _service(moscow, "Массаж ног")

        assert {c.name for c in discover_services(city="Москва")} == {"Массаж ног"}

    def test_best_match_ranks_first(self, penza) -> None:
        _master(penza, "Анна")
        _service(penza, "Спортивный массаж")
        _service(penza, "Массаж лица")

        cards = discover_services(query="спортивный массаж")

        assert cards[0].name == "Спортивный массаж"
