"""DRF-2669: a quote prices only an edge of THIS salon.

The model may name any ``master_id``. The edge read goes to the catalog's
mirror door, where ``tenant`` is an optional filter by a recorded decision
(``beautygo_backend/docs/CATALOG_INTERNAL_API_CONTRACT.md`` §2a — «Verify, don't trust: the
consumer should re-check it»), and the read sends none. Today no foreign
price reaches the reply by construction — the catalog refuses a cross-salon
edge (``services/tests/test_specialist_service_same_tenant.py``) and the
service id is this salon's — but both links live elsewhere. The adapter now
re-checks every row against the bound salon.

Real ``AylaYClientsAdapter`` bound to the salon, over a catalog stand-in whose
rows have the contract's shape (§2: ``tenant`` uuid|null, ``price`` str).
The three prices are deliberately different, so no case can pass by a
coincidence of numbers: the salon's base 1500, its master's edge 2200, the
foreign edge 9900.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from django.test import override_settings

from apps.catalog.models import CatalogMaster, CatalogService
from apps.promotions.formatting import format_rub
from apps.skills.booking.provider import AylaYClientsAdapter
from apps.skills.booking.tools import calc_price
from apps.tenancy.models import Tenant
from tests.support.catalog_mirror import sync_shaped

pytestmark = pytest.mark.django_db

BASE = Decimal("1500.00")
OURS = "2200.00"
FOREIGN = "9900.00"
FOREIGN_NAME = "Чужая-Мастерица"


class _Catalog:
    """``get_specialist_service_edges`` answering per specialist, recorded."""

    def __init__(self, rows_by_specialist: dict[str, list[dict[str, Any]]]) -> None:
        self.rows = rows_by_specialist
        self.calls: list[str] = []

    def get_specialist_service_edges(
        self, *, specialist_id: str, service_id: str
    ) -> list[dict[str, Any]]:
        self.calls.append(specialist_id)
        return self.rows.get(specialist_id, [])


def _edge(*, tenant: str | None, price: str) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "tenant": tenant,
        "price": price,
        "duration_minutes": 60,
        "is_active": True,
    }


def _master(tenant: Tenant, name: str, **extra: Any) -> CatalogMaster:
    return sync_shaped(
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=CatalogMaster.all_tenants.count() + 1,
            external_updated_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
            name=name,
            **extra,
        )
    )


@pytest.fixture
def salon(db) -> Tenant:
    return Tenant.objects.create(slug="quote-2669", name="Свой салон")


@pytest.fixture
def other_salon(db) -> Tenant:
    return Tenant.objects.create(slug="quote-2669-other", name="Чужой салон")


@pytest.fixture
def service(salon: Tenant) -> tuple[CatalogService, str]:
    sid = uuid.uuid4()
    row = CatalogService.all_tenants.create(
        tenant=salon,
        external_id=2669,
        external_updated_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
        slug="massage-2669",
        name="Массаж",
        price_from=BASE,
        duration_min=60,
        ayla_service_id=sid,
    )
    return row, str(sid)


def _adapter(salon: Tenant, catalog: _Catalog) -> AylaYClientsAdapter:
    return AylaYClientsAdapter(client=catalog, external_user_id="bot:max:1", tenant=salon)  # type: ignore[arg-type]


def _quote(salon: Tenant, catalog: _Catalog, service, master_id: str):
    row, sid = service
    with override_settings(BOOKING_VIA_AYLA_REST=True):
        return calc_price(
            tenant=salon,
            client=_adapter(salon, catalog),
            arguments={"service_id": sid, "master_id": master_id},
            allowed_service_ids={sid},
            service_lookup={sid: row.name},
        )


def _price(result) -> Decimal | None:
    return result.price.original_price if result.price is not None else None


class TestOnlyThisSalonsEdge:
    def test_our_master_is_priced_at_the_edge(self, salon, other_salon, service) -> None:
        ours = _master(salon, "Своя")
        catalog = _Catalog({str(ours.pk): [_edge(tenant=str(salon.id), price=OURS)]})

        assert _price(_quote(salon, catalog, service, str(ours.pk))) == Decimal(OURS)

    def test_a_foreign_master_gets_the_base_price_and_leaks_nothing(
        self, salon, other_salon, service
    ) -> None:
        stranger = _master(other_salon, FOREIGN_NAME)
        catalog = _Catalog({str(stranger.pk): [_edge(tenant=str(other_salon.id), price=FOREIGN)]})

        result = _quote(salon, catalog, service, str(stranger.pk))

        assert _price(result) == BASE
        assert format_rub(Decimal(FOREIGN)) not in result.text
        assert FOREIGN_NAME not in result.text

    def test_an_invented_id_gets_the_very_same_answer(self, salon, other_salon, service) -> None:
        """Pair: the foreign master's reply is indistinguishable from an
        unknown id's — no existence leak."""
        stranger = _master(other_salon, FOREIGN_NAME)
        foreign = _quote(
            salon,
            _Catalog({str(stranger.pk): [_edge(tenant=str(other_salon.id), price=FOREIGN)]}),
            service,
            str(stranger.pk),
        )
        invented = _quote(salon, _Catalog({}), service, str(uuid.uuid4()))

        assert (foreign.text, _price(foreign)) == (invented.text, _price(invented))


class TestVerifyDontTrust:
    def test_a_row_of_another_salon_is_dropped_even_for_our_master(
        self, salon, other_salon, service
    ) -> None:
        """What a ``bulk_create``/``update()`` past the catalog's own guard
        could leave behind."""
        ours = _master(salon, "Своя")
        catalog = _Catalog({str(ours.pk): [_edge(tenant=str(other_salon.id), price=FOREIGN)]})

        assert _price(_quote(salon, catalog, service, str(ours.pk))) == BASE
        assert catalog.calls == [str(ours.pk)]  # presence: it was asked, then refused

    @pytest.mark.parametrize(("placed_here", "expected"), [(True, OURS), (False, str(BASE))])
    def test_an_unstamped_row_is_trusted_only_for_a_master_placed_here(
        self, salon, other_salon, service, placed_here, expected
    ) -> None:
        """«null = unverifiable, not foreign»: kept for our master; for a
        master the mirror places elsewhere nothing vouches for the row."""
        master = _master(salon if placed_here else other_salon, "Кто-то")
        catalog = _Catalog({str(master.pk): [_edge(tenant=None, price=OURS)]})

        assert _price(_quote(salon, catalog, service, str(master.pk))) == Decimal(expected)


class TestOurMastersTheMirrorDoesNotSell:
    """The question is WHICH salon, not whether bookable (review of DRF-2669):
    a master of this salon keeps the edge price whatever the mirror says."""

    def test_not_mirrored_yet_but_stamped_ours(self, salon, service) -> None:
        catalog_id = str(uuid.uuid4())  # no CatalogMaster row at all
        catalog = _Catalog({catalog_id: [_edge(tenant=str(salon.id), price=OURS)]})

        assert _price(_quote(salon, catalog, service, catalog_id)) == Decimal(OURS)

    def test_mirrored_but_not_on_sale(self, salon, service) -> None:
        hidden = _master(salon, "Не в продаже", is_active=False)
        catalog = _Catalog({str(hidden.pk): [_edge(tenant=None, price=OURS)]})

        assert _price(_quote(salon, catalog, service, str(hidden.pk))) == Decimal(OURS)

    def test_a_solo_master_named_by_catalog_id(self, salon, service) -> None:
        """Mirror key (uuid4) ≠ catalog id — the membership test reads both."""
        catalog_id = uuid.uuid4()
        CatalogMaster.all_tenants.create(
            tenant=salon,
            external_id=9001,
            external_updated_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
            name="Соло",
            catalog_specialist_id=catalog_id,
        )
        catalog = _Catalog({str(catalog_id): [_edge(tenant=None, price=OURS)]})

        assert _price(_quote(salon, catalog, service, str(catalog_id))) == Decimal(OURS)


class TestTheBookingPreviewQuote:
    """``get_specialist_service_quote`` reads the same rows (DRF-1708)."""

    def test_our_edge_and_a_foreign_one(self, salon, other_salon, service) -> None:
        _row, sid = service
        ours = _master(salon, "Своя")
        stranger = _master(other_salon, FOREIGN_NAME)
        catalog = _Catalog(
            {
                str(ours.pk): [_edge(tenant=str(salon.id), price=OURS)],
                str(stranger.pk): [_edge(tenant=str(other_salon.id), price=FOREIGN)],
            }
        )
        adapter = _adapter(salon, catalog)

        pair = (
            adapter.get_specialist_service_quote(staff_id=str(ours.pk), service_id=sid),
            adapter.get_specialist_service_quote(staff_id=str(stranger.pk), service_id=sid),
        )
        assert pair == ((Decimal(OURS), 60), (None, None))
