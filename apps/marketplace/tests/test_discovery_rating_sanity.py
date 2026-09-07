"""Rating sanity in discovery (DRF-1535).

Owner decision 06.09.2026 (``docs/OPEN_DECISIONS.md`` §29.4):

    Drop the Bayesian-rating promise. Treat a zero rating as absence of
    data. Keep rating OUT of the ordering for now, and do NOT demote
    masters who have no reviews.

The measurement behind it, taken on the pilot the same day: 31 bookable
masters, 22 with ``rating >= 1``, **9 with ``rating = 0.00``**, and **zero
reviews on any of them**. So the nine zeros are an empty column, not nine
bad masters, and ``review_count`` is 0 everywhere.

``apps/catalog/models.py`` used to promise a discovery Bayesian trust-score
«so a 5.0 from 1 review can't outrank a 4.8 from 200». It was never built.
This module is what stands in its place: the behaviour the owner actually
asked for, pinned so the next author cannot quietly add rating to the sort
«while they are in there» — the live risk while the DRF-1529 epic is open on
these same functions.

Two halves, and both are needed (DRF-1411):

* the NEGATIVE half — rating does not order, and a 0.00 is not demoted;
* the POSITIVE guard on the SAME rows — a master with ``rating >= 1`` still
  shows their rating on the card (DRF-1224,
  ``apps.orchestrator.discovery._render_master_cards``). Without it,
  "rating does not affect the order" would pass just as well in a world
  where discovery had stopped reading the field at all.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from apps.catalog.models import CatalogMaster
from apps.marketplace import discovery as marketplace_discovery
from apps.marketplace.discovery import discover_masters

# The DRF-1224 renderer. Imported here rather than mirrored into a second
# file on purpose: the positive guard has to run against the SAME rows the
# negative assertions order, or it guards a different world than the one
# under test.
from apps.orchestrator.discovery import _render_master_cards
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _ts() -> datetime:
    return datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def penza() -> Tenant:
    return Tenant.objects.create(slug="salon-penza", name="Salon Penza", city="Пенза")


def _master(tenant: Tenant, ext: int, name: str, rating: Decimal | None) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=ext,
        external_updated_at=_ts(),
        name=name,
        specialization="",
        rating=rating,
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
    )


# Names chosen so that alphabetical order and rating order DISAGREE: «Анна»
# sorts first and is the unrated one. If rating were a sort key at any
# strength, «Борис» would climb over her and every assertion below would say
# so.
_UNRATED = "Анна"
_RATED = "Борис"


@pytest.fixture
def pilot_pair(penza) -> Tenant:
    """The pilot's two shapes side by side: an empty rating and a real one."""
    _master(penza, 1, _UNRATED, Decimal("0.00"))
    _master(penza, 2, _RATED, Decimal("4.90"))
    return penza


class TestZeroRatingIsNotDemoted:
    """A 0.00 is missing data, so it must cost the master nothing."""

    def test_zero_rated_master_keeps_their_place(self, pilot_pair) -> None:
        names = [card.name for card in discover_masters(city="Пенза")]

        assert names == [_UNRATED, _RATED]

    def test_order_is_the_same_when_the_ratings_are_swapped(self, penza) -> None:
        """The real proof: move the rating, the order does not move.

        The test above alone is satisfied by an alphabetical sort that merely
        happens to agree today. Swapping which master carries the 4.90 changes
        the only thing rating-sensitive code could react to, so an ordering
        that still comes back identical is not reacting to it.
        """
        _master(penza, 1, _UNRATED, Decimal("4.90"))
        _master(penza, 2, _RATED, Decimal("0.00"))

        names = [card.name for card in discover_masters(city="Пенза")]

        assert names == [_UNRATED, _RATED]

    def test_null_rating_is_not_demoted_either(self, penza) -> None:
        """``NULL`` is the other spelling of «no rating».

        A DESC sort on a nullable column is where NULLs famously land
        somewhere the author did not intend — ``_match_score`` documents that
        very trap for its own annotation. Pin the harmless answer.
        """
        _master(penza, 1, _UNRATED, None)
        _master(penza, 2, _RATED, Decimal("4.90"))

        names = [card.name for card in discover_masters(city="Пенза")]

        assert names == [_UNRATED, _RATED]

    def test_zero_rated_master_is_returned_at_all(self, pilot_pair) -> None:
        """Not demoted AND not filtered out — the other way to lose them.

        Nine of the pilot's 31 bookable masters carry 0.00. «Hide the unrated»
        would satisfy every ordering assertion above while taking a third of
        the directory off the marketplace.
        """
        cards = {card.name: card for card in discover_masters(city="Пенза")}

        assert set(cards) == {_UNRATED, _RATED}
        assert cards[_UNRATED].rating == Decimal("0.00")


class TestRatingIsNotASortKey:
    """Pinned at the queryset, not only at the result.

    The assertions above prove today's two rows come back in a
    rating-independent order. These prove the ordering does not name the
    column at all, which is what stops «add rating as a tie-breaker» from
    landing green in a neighbouring DRF-1529 change: a weak tie-breaker can be
    invisible on two rows and decisive on thirty-one.

    Exact tuple equality rather than a «rating not in ...» check, on purpose.
    The ORDER BY is short, hand-written and load-bearing, so stating what it
    IS costs nothing and says more: any new key, under any name, has to come
    through this assertion.
    """

    def _order_by(self, **kw) -> tuple[str, ...]:
        qs = marketplace_discovery._bookable_qs(**kw)
        # ``Query.order_by`` is typed as ``Sequence[str | Combinable]``; every
        # term this queryset carries is a plain string, and ``str`` on one is
        # the identity — it is here to keep the comparison below honest if a
        # future term arrives as an expression instead of a name.
        return tuple(str(term) for term in qs.query.order_by)

    def test_unfiltered_ordering_is_name_and_id_only(self) -> None:
        assert self._order_by(city="Пенза") == ("name", "id")

    def test_ranked_ordering_is_match_score_then_name(self) -> None:
        """The ranked branch — the one a scoring change would touch."""
        assert self._order_by(city="Пенза", specialization="массаж") == (
            "-match_score",
            "name",
            "id",
        )

    def test_the_only_ranking_annotation_is_match_score(self) -> None:
        """No second score rides along waiting to be ordered by.

        ``match_score`` counts matched query stems (DRF-1283) and nothing
        else. An annotation named for a rating or a trust score would be the
        first half of the change this ticket forbids, so it fails here rather
        than on the day someone adds it to the ORDER BY.
        """
        qs = marketplace_discovery._bookable_qs(city="Пенза", specialization="массаж")

        assert set(qs.query.annotations) == {"match_score"}


class TestRatedMasterStillShowsTheirRating:
    """The positive guard (DRF-1411), on the SAME rows as above.

    DRF-1224 stopped «★ 0.00» reaching a user — the pilot answered «массаж в
    Пензе» with four cards, all of them «★ 0.00», which reads as four bad
    masters. That fix must survive this ticket: «zero is no data» is not
    «ratings are gone». If a later change dropped the star entirely, the
    negative half of this module would stay green and silent; this class is
    what breaks instead.
    """

    def _lines(self) -> dict[str, str]:
        cards = discover_masters(city="Пенза")
        rendered = _render_master_cards(cards).text.splitlines()
        # Line 0 is the header; one line per card after it, in card order.
        return {card.name: line for card, line in zip(cards, rendered[1:], strict=True)}

    def test_real_rating_is_still_rendered(self, pilot_pair) -> None:
        line = self._lines()[_RATED]

        assert "★" in line
        assert "4.90" in line

    def test_zero_rating_is_still_hidden(self, pilot_pair) -> None:
        lines = self._lines()

        # Presence first, on the very same render: the star IS drawn for the
        # master who earned one, so its absence below is a decision about 0.00
        # and not a renderer that quietly stopped drawing stars.
        assert "★" in lines[_RATED]
        assert "★" not in lines[_UNRATED]

    def test_the_unrated_master_is_rendered_too(self, pilot_pair) -> None:
        """Un-starred, not unlisted — and still ahead of the rated one."""
        lines = self._lines()

        assert list(lines) == [_UNRATED, _RATED]
        assert _UNRATED in lines[_UNRATED]
