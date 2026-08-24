"""Composite-request coverage at the catalog layer (DRF-1312).

The live Controlled-Pilot turn this covers:

    владелец: «давай будет несколько: массаж классика, и маникюр»
    бот:      «Вот мастера, которые могут подойти:» + пять карточек

Ни один салон контура не оказывает маникюр (94 услуги, из них ногтевых — 0).
Половина запроса исчезла молча, и человек ушёл с уверенностью, что нашёл
мастера на обе услуги.

Retrieval was not the bug — «массаж» matched exactly what it should. What was
missing is a per-service verdict: the OR-ranked query (DRF-1283) scores a part
nobody offers at zero and drops it out of sight, which is the right thing for
RANKING and the wrong thing for TELLING THE TRUTH.

This module pins the two functions that supply that verdict:

* :func:`split_requested_services` — which parts of an enumeration name a
  service at all (and, just as load-bearing, which do not);
* :func:`service_coverage` — which of those the CATALOG can serve.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from django.conf import settings

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.marketplace.discovery import (
    service_coverage,
    split_requested_services,
)
from apps.tenancy.models import Tenant

# Postgres-only for the whole module, same reason as test_discovery_by_service:
# matching is ILIKE and Cyrillic case folding is the DB's job. Here it matters
# doubly — every negative assertion is «this service is MISSING», which SQLite
# would satisfy for the wrong reason (nothing matches at all).
pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        "postgresql" not in str(settings.DATABASES["default"]["ENGINE"]),
        reason="Cyrillic ILIKE folding requires Postgres; every «missing» "
        "assertion here would pass vacuously on SQLite.",
    ),
]


def _ts() -> datetime:
    return datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def penza() -> Tenant:
    return Tenant.objects.create(slug="salon-penza", name="Salon Penza", city="Пенза")


@pytest.fixture
def moscow() -> Tenant:
    return Tenant.objects.create(slug="salon-msk", name="Salon Moscow", city="Москва")


def _master(tenant: Tenant, name: str, *, specialization: str = "") -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        name=name,
        specialization=specialization,
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
    )


def _offers(tenant: Tenant, master_name: str, service_name: str, *, slug: str) -> None:
    master = _master(tenant, master_name)
    service = CatalogService.all_tenants.create(
        tenant=tenant,
        slug=slug,
        name=service_name,
        is_active=True,
        external_updated_at=_ts(),
    )
    MasterService.all_tenants.create(tenant=tenant, master=master, service=service)


@pytest.fixture
def contour(penza: Tenant) -> None:
    """The pilot contour in miniature: massage exists, nails do not."""
    _offers(penza, "Архипкин Денис", "Классический массаж", slug="klass-massage")
    _offers(penza, "Сазонова Инна", "Спортивный массаж", slug="sport-massage")


class TestSplitRequestedServices:
    """Which parts of a sentence name a service — and which must NOT be quoted."""

    def test_the_live_turn_splits_into_its_two_services(self, contour: None) -> None:
        """The 23.08 turn, verbatim.

        «давай будет несколько» is the lead-in clause. It is pure glue, and if
        it survived as a part the bot would tell the owner that «давай будет
        несколько» is a service nobody offers — a confident lie, and strictly
        worse than the silence being fixed.
        """
        assert split_requested_services("давай будет несколько: массаж классика, и маникюр") == [
            "массаж классика",
            "маникюр",
        ]

    def test_plain_conjunction(self, contour: None) -> None:
        assert split_requested_services("массаж и маникюр") == ["массаж", "маникюр"]

    @pytest.mark.parametrize(
        "raw",
        [
            "стрижка, брови",
            "стрижка; брови",
            "стрижка / брови",
            "стрижка + брови",
            "стрижка плюс брови",
            "стрижка, а также брови",
        ],
    )
    def test_every_enumeration_separator(self, contour: None, raw: str) -> None:
        assert split_requested_services(raw) == ["стрижка", "брови"]

    def test_keeps_the_users_own_spelling(self, contour: None) -> None:
        """Parts are quoted back at the user, so case must survive the split."""
        assert split_requested_services("Массаж и Маникюр") == ["Массаж", "Маникюр"]

    def test_single_service_is_not_an_enumeration(self, contour: None) -> None:
        """Nothing can be half-answered, so there is nothing to report."""
        assert split_requested_services("спортивный массаж") == []

    def test_hyphen_is_not_a_separator(self, contour: None) -> None:
        """«гель-лак» is one service, not two."""
        assert split_requested_services("гель-лак") == []

    def test_a_named_city_is_not_a_second_service(self, contour: None) -> None:
        """«массаж, пенза» is ONE service in a place — not a composite request.

        Without city recognition this would count as two parts, «пенза» would
        be checked as a service, found nowhere, and announced as missing.
        """
        assert split_requested_services("массаж, пенза") == ["массаж"]

    def test_all_filler_part_is_dropped(self, contour: None) -> None:
        assert split_requested_services("хочу записаться, и ещё маникюр") == ["маникюр"]

    def test_empty_input(self, contour: None) -> None:
        assert split_requested_services("") == []
        assert split_requested_services("   ,   ") == []

    def test_capped_at_five_parts(self, contour: None) -> None:
        raw = "массаж, маникюр, стрижка, брови, ресницы, педикюр, чистка"
        assert len(split_requested_services(raw)) == 5


class TestServiceCoverage:
    """The verdict itself — supplied by the catalog, never by a model."""

    def test_the_live_measurement(self, contour: None) -> None:
        """Massage is offered, nails are offered by nobody in the contour."""
        available, missing = service_coverage(["массаж классика", "маникюр"])

        assert available == ["массаж классика"]
        assert missing == ["маникюр"]

    def test_both_available(self, contour: None) -> None:
        available, missing = service_coverage(["классический массаж", "спортивный массаж"])

        assert available == ["классический массаж", "спортивный массаж"]
        assert missing == []

    def test_both_missing(self, contour: None) -> None:
        available, missing = service_coverage(["маникюр", "педикюр"])

        assert available == []
        assert missing == ["маникюр", "педикюр"]

    def test_free_text_specialization_counts_as_offered(self, penza: Tenant) -> None:
        """Same OR predicate as discovery: legacy ``specialization`` still matches.

        Coverage MUST agree with the search that produced the cards. A master
        surfaced through the free-text field for «брови» and then told «бровей
        у нас нет» in the same message would be the DRF-1312 bug inverted.
        """
        _master(penza, "Бровист", specialization="Брови и ресницы")

        available, missing = service_coverage(["брови"])

        assert available == ["брови"]
        assert missing == []

    def test_scoped_to_the_city_the_search_was_scoped_to(
        self, contour: None, moscow: Tenant
    ) -> None:
        """A city-filtered answer needs a city-filtered verdict.

        Nails exist in Moscow, the search was Penza. Reporting «маникюр есть»
        would promise a master the Penza card list does not contain; reporting
        it unqualified in the reply is why ``render_missing_services`` says
        «в городе Пенза».
        """
        _offers(moscow, "Ногтевой мастер", "Маникюр классический", slug="msk-mani")

        available, missing = service_coverage(["массаж", "маникюр"], city="Пенза")
        assert missing == ["маникюр"]

        available, missing = service_coverage(["маникюр"], city="Москва")
        assert available == ["маникюр"]
        assert missing == []

    def test_a_name_with_no_service_token_makes_no_claim(self, contour: None) -> None:
        """Neither confirmed nor denied — there is nothing to rule on.

        A city, bare filler or punctuation must not land in ``missing``: that
        list is quoted verbatim at the user as «такой услуги у нас нет».
        """
        available, missing = service_coverage(["пенза", "хочу", "...", "", "массаж"])

        assert available == ["массаж"]
        assert missing == []

    def test_case_insensitive_deduplication(self, contour: None) -> None:
        available, missing = service_coverage(["Маникюр", "маникюр", "МАНИКЮР"])

        assert missing == ["Маникюр"]

    def test_capped_at_five_names(self, contour: None) -> None:
        names = ["маникюр", "педикюр", "брови", "ресницы", "шугаринг", "татуаж"]
        _available, missing = service_coverage(names)

        assert len(missing) == 5

    def test_empty_input_costs_nothing(self, contour: None) -> None:
        assert service_coverage([]) == ([], [])
