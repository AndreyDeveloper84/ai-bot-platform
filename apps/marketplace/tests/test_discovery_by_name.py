"""Finding a master the client named BY NAME (DRF-1354).

The live pilot failure this covers, 24.08 07:52–07:53:

    владелец: «запиши к Архипкину Денису на завтра»
    бот:      «В Пензе есть несколько мастеров: — Архипкин Денис — …
               Если хочешь записаться к Архипкину Денису на завтра, дай знать!»
    владелец: «даю знать»
    бот:      то же самое

The person named the master in the first sentence. Nothing in
``apps.marketplace.discovery`` could look a master up by name — every reader
answered «who does X?» — so the only thing the bot could do with a name was
print it back inside a list of names.

Postgres-only, for the same reason as ``test_discovery_by_service``: matching
is ``icontains`` → ILIKE, and Cyrillic case folding is the DB's job. On SQLite
the negative assertions would pass vacuously.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from django.conf import settings

from apps.catalog.models import CatalogMaster
from apps.marketplace.discovery import find_masters_by_name
from apps.tenancy.models import Tenant

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        "postgresql" not in str(settings.DATABASES["default"]["ENGINE"]),
        reason="Cyrillic ILIKE folding requires Postgres; on SQLite the negative "
        "assertions would pass vacuously.",
    ),
]


def _ts() -> datetime:
    return datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def penza() -> Tenant:
    return Tenant.objects.create(slug="salon-penza", name="Salon Penza", city="Пенза")


@pytest.fixture
def moscow() -> Tenant:
    return Tenant.objects.create(slug="salon-msk", name="Salon Moscow", city="Москва")


def _master(tenant: Tenant, name: str) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        name=name,
        specialization="",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
    )


@pytest.fixture
def contour(penza: Tenant) -> Tenant:
    """The three masters the owner was shown on 24.08."""
    _master(penza, "Архипкин Денис")
    _master(penza, "Сазонова Инна")
    _master(penza, "Татьяна Паламарчук")
    return penza


class TestFindsTheNamedMaster:
    def test_full_name_as_written(self, contour) -> None:
        cards = find_masters_by_name("Архипкин Денис")
        assert [c.name for c in cards] == ["Архипкин Денис"]

    def test_word_order_does_not_matter(self, contour) -> None:
        """«Денис Архипкин» is the same person as «Архипкин Денис»."""
        cards = find_masters_by_name("Денис Архипкин")
        assert [c.name for c in cards] == ["Архипкин Денис"]

    def test_inflected_form_still_finds_the_nominative(self, contour) -> None:
        """The turn says «к Архипкину Денису»; the mirror stores the nominative."""
        cards = find_masters_by_name("Архипкину Денису")
        assert [c.name for c in cards] == ["Архипкин Денис"]

    def test_given_name_alone(self, contour) -> None:
        cards = find_masters_by_name("Денис")
        assert [c.name for c in cards] == ["Архипкин Денис"]

    def test_surname_alone(self, contour) -> None:
        cards = find_masters_by_name("Паламарчук")
        assert [c.name for c in cards] == ["Татьяна Паламарчук"]

    def test_booking_verb_around_the_name_is_dropped(self, contour) -> None:
        """A model that passes «запиши к Денису» must still resolve — the
        filler list the service search uses is applied here too."""
        cards = find_masters_by_name("запиши к Денису")
        assert [c.name for c in cards] == ["Архипкин Денис"]


class TestAmbiguity:
    def test_two_of_the_same_name_return_both(self, contour, penza) -> None:
        """«запиши к Денису» with two Денисов is a QUESTION, not a miss — the
        caller asks it with both names in hand (DRF-1354 negative proof)."""
        _master(penza, "Денис Кузнецов")
        cards = find_masters_by_name("Денис")
        assert sorted(c.name for c in cards) == ["Архипкин Денис", "Денис Кузнецов"]

    def test_the_surname_closes_it_in_one_word(self, contour, penza) -> None:
        _master(penza, "Денис Кузнецов")
        cards = find_masters_by_name("Кузнецов")
        assert [c.name for c in cards] == ["Денис Кузнецов"]

    def test_a_shared_prefix_widens_rather_than_misses(self, contour, penza) -> None:
        """Stem matching cannot tell «Сазонов» from «Сазонова» — a masculine
        surname and its feminine form share every letter that survives case
        inflection. It widens, and a widened match becomes the question above.
        The opposite trade — «такого мастера нет» about someone on the list —
        is the failure this whole function exists to remove, so this is the
        direction the ambiguity is allowed to fall in."""
        _master(penza, "Денис Сазонов")
        cards = find_masters_by_name("Сазонов")
        assert sorted(c.name for c in cards) == ["Денис Сазонов", "Сазонова Инна"]


class TestMisses:
    def test_unknown_name_returns_nothing(self, contour) -> None:
        assert find_masters_by_name("Иванов Пётр") == []

    def test_untokenizable_query_never_returns_the_directory(self, contour) -> None:
        """«запиши к» reduces to no name tokens at all. The honest answer is
        nobody; the whole nationwide directory would be a confident wrong one."""
        assert find_masters_by_name("запиши к") == []
        assert find_masters_by_name("") == []

    def test_a_non_bookable_master_is_invisible(self, penza) -> None:
        master = _master(penza, "Архипкин Денис")
        master.is_active = False
        master.save(update_fields=["is_active"])
        assert find_masters_by_name("Архипкин Денис") == []


class TestCity:
    def test_city_narrows_a_namesake_in_another_town(self, contour, moscow) -> None:
        _master(moscow, "Денис Московский")
        assert len(find_masters_by_name("Денис")) == 2
        cards = find_masters_by_name("Денис", city="Пенза")
        assert [c.name for c in cards] == ["Архипкин Денис"]

    def test_a_wrong_city_guess_does_not_hide_the_named_master(self, contour) -> None:
        """``city`` is the MODEL's guess about a field the person never typed.
        It narrows only when it leaves someone — otherwise «Архипкина нет»
        about a master who is right there."""
        cards = find_masters_by_name("Архипкин Денис", city="Москва")
        assert [c.name for c in cards] == ["Архипкин Денис"]
