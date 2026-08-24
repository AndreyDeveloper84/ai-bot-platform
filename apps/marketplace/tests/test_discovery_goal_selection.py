"""Selection by a curated GOAL, not by a word that resembles one (DRF-1324).

The live Controlled-Pilot measurement this module exists for, taken on the
contour the evening of 23.08:

    «хочу расслабиться»  → stem «рассла» → ILIKE finds ONE service name:
                           «Массаж ног — глубокое расслабление и лимфодренаж».
                           Its curated goal is `recharge`.
                           All NINE services carrying `relax` are missed.

    «хочу подтянуть фигуру» → «подтян» / «фигуру» occur in no service name at
                              all → NOBODY, while fifteen services carry
                              `body_shape`.

So the single word-match hit had the WRONG goal and the right answers were
invisible. DRF-1308 put the curated `{"key", "label"}` on every mirrored
service precisely so this could be selected structurally, and DRF-1317 moved
the curation off the «Массаж тела» root so the keys mean something.

What is pinned here:

* a query that names an OUTCOME selects on the key, and the word-match
  impostor is absent from the result;
* a query that names a SERVICE keeps DRF-1283's name search untouched — the
  goal path must not swallow «снятие отёков» because `relax`'s label happens
  to contain «снять»;
* the goal vocabulary is READ FROM THE CATALOG, so it can never claim a goal
  the catalog does not carry, and never invents one for a word it does not
  know («похудеть»). That is the AYLA-DEC-0045 / OD-9 line: no inference, no
  synonym table, no model — a curated key, read and filtered on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from django.conf import settings as dj_settings

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.marketplace.discovery import discover_masters, discover_services, parse_query
from apps.tenancy.models import Tenant

# Postgres-only, same reason as test_discovery_by_service and
# test_discovery_partial_coverage: matching is ILIKE with Cyrillic case
# folding, and `goals__contains` is a jsonb `@>` containment test that SQLite
# has no equivalent for. Every «and NOT this one» assertion below would pass
# vacuously on SQLite — for the wrong reason.
pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        "postgresql" not in str(dj_settings.DATABASES["default"]["ENGINE"]),
        reason="jsonb containment and Cyrillic ILIKE folding require Postgres.",
    ),
]

_TS = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)

# The labels are the mirror's own, character for character — they are what
# DRF-1308 ships and what the client app shows as a goal chip, which is why
# they are also the recognition vocabulary. A fixture that paraphrased them
# would test a vocabulary nobody has.
_RELAX = [{"key": "relax", "label": "Расслабиться и снять стресс"}]
_RECHARGE = [{"key": "recharge", "label": "Восстановить силы"}]
_BODY_SHAPE = [{"key": "body_shape", "label": "Подтянуть фигуру"}]


@pytest.fixture
def penza() -> Tenant:
    return Tenant.objects.create(slug="salon-penza", name="Salon Penza", city="Пенза")


def _master(tenant: Tenant, name: str) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=_TS,
        name=name,
        specialization="",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
    )


def _service(tenant: Tenant, name: str, *, slug: str, goals: list | None = None) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        slug=slug,
        name=name,
        is_active=True,
        goals=goals if goals is not None else [],
        # Every row is groundable: the card-stamping gate (DRF-962) refuses a
        # service without one, and the tests below that exercise stamping
        # would then pass for a reason unrelated to goals.
        ayla_service_id=uuid4(),
        external_updated_at=_TS,
    )


@pytest.fixture
def contour(penza: Tenant) -> dict[str, object]:
    """Сазонова's real roster in miniature, with the trap intact.

    Reproduced from the contour rather than invented, because the defect lives
    exactly in the mismatch between the two: «Массаж ног — глубокое
    расслабление и лимфодренаж» is the ONLY row whose NAME answers «хочу
    расслабиться», and it is the one row here whose curated goal is not
    `relax`. A convenient fixture — one where the word and the key agree —
    would pass both before and after this ticket.
    """
    master = _master(penza, "Сазонова Инна")
    rows = {
        "head": _service(penza, "Массаж головы", slug="head", goals=_RELAX),
        "classic": _service(penza, "Классический массаж", slug="classic", goals=_RELAX),
        "legs": _service(
            penza,
            "Массаж ног — глубокое расслабление и лимфодренаж (45 минут)",
            slug="legs",
            goals=_RECHARGE,
        ),
        "lymph": _service(
            penza,
            "Лимфодренажный массаж всего тела (60 минут)",
            slug="lymph",
            goals=_BODY_SHAPE,
        ),
        "kids": _service(penza, "Биоэнергетический массаж детский", slug="kids", goals=[]),
    }
    for service in rows.values():
        MasterService.all_tenants.create(tenant=penza, master=master, service=service)
    return {"master": master, **rows}


def _names(query: str) -> list[str]:
    return sorted(card.name for card in discover_services(query=query, limit=50))


class TestGoalQuerySelectsOnTheKey:
    def test_the_word_match_impostor_is_absent(self, contour: dict) -> None:
        """«хочу расслабиться» → the `relax` carriers, and NOT the name match.

        The whole ticket in one assertion. Before DRF-1324 this query returned
        exactly the one row it must not return.
        """
        found = _names("хочу расслабиться")
        assert found == ["Классический массаж", "Массаж головы"]
        assert "Массаж ног — глубокое расслабление и лимфодренаж (45 минут)" not in found

    def test_a_goal_nothing_is_named_by_still_answers(self, contour: dict) -> None:
        """«хочу подтянуть фигуру» found NOBODY on the contour before this.

        Pinned as a property of the fixture, not as folklore: no service name
        here contains either stem, so the name search provably cannot answer
        this query and the goal key provably can.
        """
        assert not any(
            "подтян" in str(name).casefold() or "фигур" in str(name).casefold()
            for name in CatalogService.all_tenants.values_list("name", flat=True)
        )
        assert _names("хочу подтянуть фигуру") == ["Лимфодренажный массаж всего тела (60 минут)"]

    def test_masters_are_selected_by_the_same_key(self, contour: dict) -> None:
        """The master search and the service search must agree about a goal."""
        cards = discover_masters(specialization="хочу подтянуть фигуру", limit=10)
        assert [c.name for c in cards] == ["Сазонова Инна"]

    def test_a_master_with_no_carrier_is_not_returned(self, penza: Tenant) -> None:
        other = _master(penza, "Архипкин Денис")
        MasterService.all_tenants.create(
            tenant=penza,
            master=other,
            service=_service(penza, "Спортивный массаж", slug="sport", goals=_RECHARGE),
        )
        assert discover_masters(specialization="хочу расслабиться", limit=10) == []

    def test_a_goal_query_narrows_by_city_like_any_other(self, contour: dict) -> None:
        """«в пензе» is split off BEFORE goal recognition, or the leftover city
        token would disqualify every goal and the query would silently fall
        back to the name search."""
        parsed = parse_query("хочу расслабиться в пензе")
        assert parsed.goals == ["relax"]
        assert parsed.cities == ["Пенза"]
        assert parsed.stems == []


class TestAServiceQueryKeepsTheNameSearch:
    """The goal path must never swallow a request that named a service."""

    @pytest.mark.parametrize(
        "raw",
        [
            "лимфодренаж",
            "расслабляющий массаж",
            "снятие отёков",
            "запиши на массаж",
        ],
    )
    def test_not_a_goal_query(self, contour: dict, raw: str) -> None:
        parsed = parse_query(raw)
        assert parsed.goals == []
        assert parsed.stems

    def test_the_live_lymph_query_still_finds_the_master(self, contour: dict) -> None:
        """DRF-1283's OR-ranking is untouched — «запиши на лимфодренаж» found
        the right masters on the live pilot and must keep doing so."""
        cards = discover_masters(specialization="запиши на лимфодренаж", limit=10)
        assert [c.name for c in cards] == ["Сазонова Инна"]

    def test_the_stem_cut_keeps_snyatie_off_relax(self, contour: dict) -> None:
        """«снятие» vs `relax`'s «снять» — the six-character cut is what makes
        this a service query. Asserted as behaviour, not as an implementation
        constant: if the cut ever moves, this is the query that breaks."""
        assert parse_query("снятие отёков").goals == []


class TestTheVocabularyIsTheCatalog:
    def test_a_goal_no_live_service_carries_is_not_recognised(self, contour: dict) -> None:
        """Deactivate every `relax` carrier and «расслабиться» stops being a
        goal query — the vocabulary is read from the mirror, exactly as
        `_known_cities` reads the cities, so it can only ever name a goal some
        live service really has."""
        CatalogService.all_tenants.filter(slug__in=["head", "classic"]).update(is_active=False)
        assert parse_query("хочу расслабиться").goals == []

    def test_an_unknown_outcome_is_not_mapped_to_a_known_one(self, contour: dict) -> None:
        """«похудеть» is not in any label, and nothing here is allowed to
        decide that it means `body_shape`. That decision is the recommendation
        engine this ticket is bounded away from; the honest answer is the name
        search, which finds nothing and says so."""
        parsed = parse_query("хочу похудеть")
        assert parsed.goals == []
        assert discover_masters(specialization="хочу похудеть", limit=10) == []

    def test_a_partly_named_goal_does_not_qualify(self, contour: dict) -> None:
        """«подтянуть кожу» is not «body_shape plus skin_care». One goal must
        account for EVERY token on its own, or the query stays a name search."""
        assert parse_query("подтянуть кожу").goals == []

    def test_a_broken_goals_element_does_not_break_the_read(self, contour: dict) -> None:
        """The mirror sync drops malformed items (DRF-1308); a read on the
        query path must not be the place that discovers bad data."""
        CatalogService.all_tenants.filter(slug="kids").update(goals=["relax", {"key": None}, 7])
        assert parse_query("хочу расслабиться").goals == ["relax"]


class TestCarriersAreNotRanked:
    def test_order_is_the_stable_name_order(self, contour: dict) -> None:
        """Carrying a goal is a yes/no fact. Ordering its carriers against each
        other would be choosing FOR the person — the line between selection and
        recommendation the ticket draws."""
        assert [c.name for c in discover_services(query="хочу расслабиться", limit=50)] == [
            "Классический массаж",
            "Массаж головы",
        ]

    def test_a_single_carrier_still_stamps_the_card(self, settings, contour: dict) -> None:
        settings.BOOKING_VIA_AYLA_REST = True
        """`resolve_service` works for a goal query too: one carrier is
        unambiguous, so the tap can enter booking with the service already
        chosen instead of via the menu."""
        cards = discover_masters(
            specialization="хочу подтянуть фигуру", limit=10, resolve_service=True
        )
        assert [c.service_name for c in cards] == ["Лимфодренажный массаж всего тела (60 минут)"]

    def test_several_carriers_leave_the_card_serviceless(self, settings, contour: dict) -> None:
        """Two `relax` services is ambiguous — auto-picking one would carry a
        service the person never chose into the booking preview (DRF-962).

        Also the duplicate-card guard: the goal branch has no aggregate
        annotation to collapse the MasterService join, so without DISTINCT
        this master — who carries the goal twice — renders twice.
        """
        settings.BOOKING_VIA_AYLA_REST = True
        cards = discover_masters(specialization="хочу расслабиться", limit=10, resolve_service=True)
        assert [(c.name, c.service_id) for c in cards] == [("Сазонова Инна", None)]
