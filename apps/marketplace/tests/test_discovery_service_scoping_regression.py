"""Regression: a master who does not perform the queried service stays out (DRF-967).

The Controlled-Pilot failure this pins down:

    bot: один и тот же список из 4 мастеров на ЛЮБОЙ запрос —
         «классический массаж», «RF-лифтинг», без услуги вообще

The mirror held a cartesian product (4 masters × all 58 services), so filtering
by service could not narrow anything: every master matched every query, cards
came back without a resolvable service, and booking never started.

That specific incident was **data**, not code — Ayla's own snapshot published
the full grid, so the local mirror faithfully reflected it. These tests pin the
other half of the contract: given correct edges, the discovery filter really
does discriminate.

Scope, honestly: ``test_discovery_by_service`` already covers the core of this
— ``test_broad_query_matches_every_massage`` holds three masters and demands a
two-master subset, so a filter that degenerated to "everyone" would fail there
too. What lives here is the named DoD regression for DRF-967 plus the cases
that suite does not make: the count assertion against the tenant's full master
roster, an unlinked master standing beside a linked peer in the same tenant,
and scoping proven together with the DRF-962 service stamp.

``apps/marketplace/discovery.py`` itself is deliberately untouched here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from django.conf import settings

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.marketplace.discovery import discover_masters
from apps.tenancy.models import Tenant

# Postgres-only for the same reason as ``test_discovery_by_service`` (see that
# module): matching is ILIKE, and SQLite folds ASCII only, so a lowercase
# «классический» never matches a stored «Классический». Here it matters twice
# over — on SQLite the discriminating assertions would go green vacuously,
# because nothing would match at all.
pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        "postgresql" not in str(settings.DATABASES["default"]["ENGINE"]),
        reason="Cyrillic ILIKE folding requires Postgres; on SQLite these "
        "assertions would pass vacuously.",
    ),
]


def _ts() -> datetime:
    return datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def penza() -> Tenant:
    return Tenant.objects.create(slug="salon-penza", name="Salon Penza", city="Пенза")


def _master(tenant: Tenant, name: str) -> CatalogMaster:
    # specialization="" is the production shape — no sync path populates it, so
    # a test that set it would prove the fallback works, not the relation.
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        name=name,
        specialization="",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
    )


def _service(tenant: Tenant, name: str, *, slug: str, ayla_service_id=None) -> CatalogService:
    # ``ayla_service_id`` stays None by default: that is the shape of a legacy
    # unlinked row, and scoping must hold for it too. Only the stamping test
    # needs a grounded service — a card carries a service_id only when the
    # match resolves to an Ayla-keyed (bookable) row.
    return CatalogService.all_tenants.create(
        tenant=tenant,
        slug=slug,
        name=name,
        is_active=True,
        ayla_service_id=ayla_service_id,
        external_updated_at=_ts(),
    )


def _link(tenant: Tenant, master: CatalogMaster, service: CatalogService) -> MasterService:
    return MasterService.all_tenants.create(tenant=tenant, master=master, service=service)


class TestQueriedServiceScopesTheResult:
    def test_result_is_a_strict_subset_when_several_masters_share_a_service(
        self, penza: Tenant
    ) -> None:
        """DoD wording: «возвращает подмножество мастеров, а не всех».

        Two of four masters perform the queried service. A filter that has
        stopped discriminating returns all four and still looks plausible —
        the ``< 4`` assertion is what makes that visible.
        """
        massage = _service(penza, "Классический массаж", slug="classic-massage")
        manicure = _service(penza, "Маникюр", slug="manicure")
        rf = _service(penza, "RF-лифтинг — Лицо/шея/декольте", slug="rf-lifting")

        first = _master(penza, "Массажист первый")
        second = _master(penza, "Массажист второй")
        third = _master(penza, "Ногтевой мастер")
        fourth = _master(penza, "Косметолог")
        _link(penza, first, massage)
        _link(penza, second, massage)
        _link(penza, third, manicure)
        _link(penza, fourth, rf)

        cards = discover_masters(city="Пенза", specialization="классический массаж")

        names = sorted(c.name for c in cards)
        assert names == ["Массажист второй", "Массажист первый"]
        assert len(cards) < CatalogMaster.all_tenants.filter(tenant=penza).count()

    def test_a_master_missing_the_edge_is_excluded_even_when_a_peer_has_it(
        self, penza: Tenant
    ) -> None:
        """The edge — not the tenant's catalog — decides.

        Both masters live in the tenant that offers the service; only one has
        the ``MasterService`` row. This is the exact shape the pilot's
        cartesian mirror destroyed.
        """
        massage = _service(penza, "Классический массаж", slug="classic-massage")
        performs = _master(penza, "Выполняет массаж")
        does_not = _master(penza, "Не выполняет массаж")
        _link(penza, performs, massage)
        _link(penza, does_not, _service(penza, "Маникюр", slug="manicure"))

        cards = discover_masters(city="Пенза", specialization="классический массаж")

        assert [c.name for c in cards] == ["Выполняет массаж"]
        assert does_not.name not in {c.name for c in cards}

    def test_the_matched_service_is_stamped_on_the_narrowed_card(
        self, penza: Tenant, settings
    ) -> None:
        """Scoping and the DRF-962 handoff have to hold at the same time.

        Narrowing to one master is only half the fix — the card must also carry
        the service, or the tap lands in booking without a ``service_id``.
        Stamping is gated on the Ayla REST path (the only one that can ground a
        service id), which is what the pilot runs, so the flag is set here.
        """
        settings.BOOKING_VIA_AYLA_REST = True
        massage = _service(
            penza, "Классический массаж", slug="classic-massage", ayla_service_id=uuid4()
        )
        masseur = _master(penza, "Массажист")
        _link(penza, masseur, massage)
        _link(penza, _master(penza, "Ногтевой мастер"), _service(penza, "Маникюр", slug="manicure"))

        cards = discover_masters(
            city="Пенза", specialization="классический массаж", resolve_service=True
        )

        assert len(cards) == 1
        assert cards[0].service_id == massage.id
        assert cards[0].service_name == "Классический массаж"
