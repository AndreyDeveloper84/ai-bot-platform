"""Service-relation discovery tests (DRF-945).

The live Controlled-Pilot failure this covers:

    user: «ищу массаж, что можешь предложить»
    bot:  уточняет тип и город
    user: «Город Пенза, хочу спортивный»
    bot:  «По вашему запросу мастеров пока не нашлось…»

``discover_masters`` filtered ``CatalogMaster.specialization``, which no sync
path populates, so every service-specific query matched "" and returned zero.
Matching now goes through ``MasterService`` — the real master↔service relation
— with ``specialization`` kept only as an OR fallback.

Every master here is created with ``specialization=""`` unless a test is
explicitly about the fallback: that is the production shape, and a test that
quietly sets it would prove nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from django.conf import settings

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.marketplace.discovery import discover_masters
from apps.tenancy.models import Tenant

# Postgres-only, deliberately, for the WHOLE module.
#
# Matching is ``icontains`` → ILIKE and case folding is the DB's job. Postgres
# (CI + production) folds Unicode; SQLite folds ASCII only, so a lowercase
# «спортивный» never matches a stored «Спортивный» there.
#
# The positive tests would fail on SQLite — but the negative ones ("returns
# nothing") would PASS for the wrong reason, since nothing can match at all.
# Vacuous green is worse than an honest skip, so the module skips wholesale
# rather than splitting into two classes of trustworthiness.
#
# Run locally against Postgres by exporting POSTGRES_HOST / POSTGRES_PORT /
# POSTGRES_USER / POSTGRES_PASSWORD before `uv run pytest apps/marketplace`.
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


def _master(tenant: Tenant, name: str, *, specialization: str = "", **kw) -> CatalogMaster:
    defaults = {
        "is_active": True,
        "invite_status": CatalogMaster.InviteStatus.ACCEPTED,
    }
    defaults.update(kw)
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        name=name,
        specialization=specialization,
        **defaults,
    )


def _service(
    tenant: Tenant,
    name: str,
    *,
    slug: str = "svc",
    is_active: bool = True,
    ayla_service_id=None,
) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        slug=slug,
        name=name,
        is_active=is_active,
        ayla_service_id=ayla_service_id,
        external_updated_at=_ts(),
    )


def _link(tenant: Tenant, master: CatalogMaster, service: CatalogService) -> MasterService:
    return MasterService.all_tenants.create(tenant=tenant, master=master, service=service)


class TestServiceRelationMatch:
    def test_finds_master_by_linked_service(self, penza: Tenant) -> None:
        """The exact live scenario, at the retrieval layer."""
        master = _master(penza, "Массажист", specialization="")
        _link(penza, master, _service(penza, "Спортивный массаж", slug="sport"))

        cards = discover_masters(city="Пенза", specialization="спортивный массаж")

        assert {c.name for c in cards} == {"Массажист"}

    def test_finds_master_by_partial_query(self, penza: Tenant) -> None:
        """The live tool passed only «спортивный» — the adjective, no noun."""
        master = _master(penza, "Массажист", specialization="")
        _link(penza, master, _service(penza, "Спортивный массаж", slug="sport"))

        cards = discover_masters(city="Пенза", specialization="спортивный")

        assert {c.name for c in cards} == {"Массажист"}

    def test_word_order_does_not_matter(self, penza: Tenant) -> None:
        master = _master(penza, "Массажист", specialization="")
        _link(penza, master, _service(penza, "Спортивный массаж", slug="sport"))

        cards = discover_masters(city="Пенза", specialization="массаж спортивный")

        assert {c.name for c in cards} == {"Массажист"}

    def test_broad_query_matches_every_massage(self, penza: Tenant) -> None:
        sport = _master(penza, "Спортивный мастер", specialization="")
        classic = _master(penza, "Классический мастер", specialization="")
        nails = _master(penza, "Ногтевой мастер", specialization="")
        _link(penza, sport, _service(penza, "Спортивный массаж", slug="sport"))
        _link(penza, classic, _service(penza, "Классический массаж", slug="classic"))
        _link(penza, nails, _service(penza, "Маникюр", slug="manicure"))

        cards = discover_masters(city="Пенза", specialization="массаж")

        assert {c.name for c in cards} == {"Спортивный мастер", "Классический мастер"}

    def test_tokens_must_match_the_same_service(self, penza: Tenant) -> None:
        """AND binds within one joined row, not across a master's whole list.

        A master doing «Спортивный маникюр» and «Тайский массаж» must NOT
        answer «спортивный массаж» — neither service is that.
        """
        master = _master(penza, "Универсал", specialization="")
        _link(penza, master, _service(penza, "Спортивный маникюр", slug="sport-nails"))
        _link(penza, master, _service(penza, "Тайский массаж", slug="thai"))

        cards = discover_masters(city="Пенза", specialization="спортивный массаж")

        assert cards == []

    def test_case_insensitive(self, penza: Tenant) -> None:
        master = _master(penza, "Массажист", specialization="")
        _link(penza, master, _service(penza, "Спортивный Массаж", slug="sport"))

        assert len(discover_masters(specialization="СПОРТИВНЫЙ массаж")) == 1


class TestResolvedServiceContext:
    """DRF-962 — cards carry the matched service so the booking handoff can.

    Without this the discovery→booking tap dispatched a serviceless
    ``pick_master`` and the booking skill's stale-context guard dead-ended
    every card tap with «Контекст записи устарел».

    A stamped service is a promise the button must keep, so resolution also
    requires deliverability: ``BOOKING_VIA_AYLA_REST`` ON and a non-NULL
    ``ayla_service_id`` (the one proven native id family). Every positive
    test here sets both; the negative deliverability tests pin the gate.
    """

    @pytest.fixture(autouse=True)
    def _ayla_flag_on(self, settings) -> None:
        settings.BOOKING_VIA_AYLA_REST = True

    def test_unambiguous_match_stamps_service_on_card(self, penza: Tenant) -> None:
        master = _master(penza, "Массажист", specialization="")
        svc = _service(penza, "Спортивный массаж", slug="sport", ayla_service_id=uuid4())
        _link(penza, master, svc)

        cards = discover_masters(
            city="Пенза", specialization="спортивный массаж", resolve_service=True
        )

        assert len(cards) == 1
        assert cards[0].service_id == svc.id
        assert cards[0].service_name == "Спортивный массаж"

    def test_ambiguous_match_carries_no_service(self, penza: Tenant) -> None:
        """«массаж» matches two of the master's services — auto-picking one
        would book a service the user never chose. The card must stay
        serviceless; the handoff answers it by asking for the service."""
        master = _master(penza, "Массажист", specialization="")
        _link(
            penza,
            master,
            _service(penza, "Спортивный массаж", slug="sport", ayla_service_id=uuid4()),
        )
        _link(
            penza,
            master,
            _service(penza, "Классический массаж", slug="classic", ayla_service_id=uuid4()),
        )

        cards = discover_masters(city="Пенза", specialization="массаж", resolve_service=True)

        assert len(cards) == 1
        assert cards[0].service_id is None
        assert cards[0].service_name == ""

    def test_each_master_resolves_its_own_service(self, penza: Tenant) -> None:
        sport = _master(penza, "Спортивный мастер", specialization="")
        classic = _master(penza, "Классический мастер", specialization="")
        sport_svc = _service(penza, "Спортивный массаж", slug="sport", ayla_service_id=uuid4())
        classic_svc = _service(
            penza, "Классический массаж", slug="classic", ayla_service_id=uuid4()
        )
        _link(penza, sport, sport_svc)
        _link(penza, classic, classic_svc)

        cards = discover_masters(city="Пенза", specialization="массаж", resolve_service=True)

        by_name = {c.name: c for c in cards}
        assert by_name["Спортивный мастер"].service_id == sport_svc.id
        assert by_name["Классический мастер"].service_id == classic_svc.id

    def test_mixed_services_resolve_only_the_matching_one(self, penza: Tenant) -> None:
        """Join-reuse pin: a master with one matching and one NON-matching
        service must resolve to the matching one. Correctness requires the
        resolver's values_list to read from the same joined MasterService row
        its conditions bound to — if a Django upgrade or refactor breaks join
        reuse, the non-matching service leaks in, the pair set doubles, and
        every card silently goes serviceless with a green suite."""
        master = _master(penza, "Универсал", specialization="")
        massage = _service(penza, "Спортивный массаж", slug="sport", ayla_service_id=uuid4())
        _link(penza, master, massage)
        _link(penza, master, _service(penza, "Маникюр", slug="nails", ayla_service_id=uuid4()))

        cards = discover_masters(city="Пенза", specialization="массаж", resolve_service=True)

        assert len(cards) == 1
        assert cards[0].service_id == massage.id
        assert cards[0].service_name == "Спортивный массаж"

    def test_default_call_does_not_resolve(self, penza: Tenant) -> None:
        """The HTTP directory and other list readers keep the old shape (and
        skip the extra query) unless they opt in."""
        master = _master(penza, "Массажист", specialization="")
        _link(
            penza,
            master,
            _service(penza, "Спортивный массаж", slug="sport", ayla_service_id=uuid4()),
        )

        cards = discover_masters(city="Пенза", specialization="спортивный массаж")

        assert cards[0].service_id is None

    def test_specialization_fallback_match_carries_no_service(self, penza: Tenant) -> None:
        """A master surfaced via the legacy free-text specialization OR-branch
        has no matching service row — the card must not invent one."""
        master = _master(penza, "Ветеран", specialization="спортивный массаж")
        assert master.services_offered.count() == 0

        cards = discover_masters(
            city="Пенза", specialization="спортивный массаж", resolve_service=True
        )

        assert len(cards) == 1
        assert cards[0].service_id is None

    def test_inactive_service_is_not_resolved(self, penza: Tenant) -> None:
        master = _master(penza, "Массажист", specialization="спортивный массаж")
        _link(
            penza,
            master,
            _service(
                penza,
                "Спортивный массаж",
                slug="sport",
                is_active=False,
                ayla_service_id=uuid4(),
            ),
        )

        cards = discover_masters(
            city="Пенза", specialization="спортивный массаж", resolve_service=True
        )

        # Master still surfaces via the specialization fallback, but the
        # inactive service must not ride the callback into booking.
        assert len(cards) == 1
        assert cards[0].service_id is None

    def test_null_ayla_service_id_is_not_stamped(self, penza: Tenant) -> None:
        """Deliverability: a service the handoff cannot ground (NULL
        ayla_service_id — e.g. a legacy mysite row) must not be advertised on
        the card; the promise would dead-end in the ask-the-service loop."""
        master = _master(penza, "Массажист", specialization="")
        _link(penza, master, _service(penza, "Спортивный массаж", slug="sport"))

        cards = discover_masters(
            city="Пенза", specialization="спортивный массаж", resolve_service=True
        )

        assert len(cards) == 1
        assert cards[0].service_id is None

    def test_legacy_flag_never_stamps_a_service(self, penza: Tenant, settings) -> None:
        """Under BOOKING_VIA_AYLA_REST=False the mirror's external_id is the
        mysite pk — an unverified id family for the YClients booking contract
        — so no service is ever advertised on that path."""
        settings.BOOKING_VIA_AYLA_REST = False
        master = _master(penza, "Массажист", specialization="")
        _link(
            penza,
            master,
            _service(penza, "Спортивный массаж", slug="sport", ayla_service_id=uuid4()),
        )

        cards = discover_masters(
            city="Пенза", specialization="спортивный массаж", resolve_service=True
        )

        assert len(cards) == 1
        assert cards[0].service_id is None


class TestNoFalsePositives:
    def test_wrong_city_returns_nothing(self, penza: Tenant, moscow: Tenant) -> None:
        master = _master(moscow, "Массажист", specialization="")
        _link(moscow, master, _service(moscow, "Спортивный массаж", slug="sport"))

        assert discover_masters(city="Пенза", specialization="спортивный массаж") == []

    def test_inactive_service_returns_nothing(self, penza: Tenant) -> None:
        master = _master(penza, "Массажист", specialization="")
        _link(penza, master, _service(penza, "Спортивный массаж", slug="sport", is_active=False))

        assert discover_masters(city="Пенза", specialization="спортивный массаж") == []

    def test_inactive_master_returns_nothing(self, penza: Tenant) -> None:
        master = _master(penza, "Массажист", specialization="", is_active=False)
        _link(penza, master, _service(penza, "Спортивный массаж", slug="sport"))

        assert discover_masters(city="Пенза", specialization="спортивный массаж") == []

    def test_unaccepted_master_returns_nothing(self, penza: Tenant) -> None:
        master = _master(
            penza,
            "Массажист",
            specialization="",
            invite_status=CatalogMaster.InviteStatus.PENDING,
        )
        _link(penza, master, _service(penza, "Спортивный массаж", slug="sport"))

        assert discover_masters(city="Пенза", specialization="спортивный массаж") == []

    def test_unrelated_service_does_not_match(self, penza: Tenant) -> None:
        master = _master(penza, "Ногтевой мастер", specialization="")
        _link(penza, master, _service(penza, "Маникюр", slug="manicure"))

        assert discover_masters(city="Пенза", specialization="спортивный массаж") == []

    def test_master_without_any_service_does_not_match(self, penza: Tenant) -> None:
        _master(penza, "Пустой мастер", specialization="")

        assert discover_masters(city="Пенза", specialization="массаж") == []

    def test_foreign_tenant_edge_cannot_surface_a_master(
        self, penza: Tenant, moscow: Tenant
    ) -> None:
        """A cross-tenant edge must not let a Penza service pull in a Moscow
        master. Sync cannot create such a row, but discovery is the one reader
        that sees every tenant at once and must not rely on that."""
        moscow_master = _master(moscow, "Московский мастер", specialization="")
        penza_service = _service(penza, "Спортивный массаж", slug="sport")
        # Deliberately malformed edge: master and service in different tenants.
        MasterService.all_tenants.create(tenant=moscow, master=moscow_master, service=penza_service)

        cards = discover_masters(specialization="спортивный массаж")

        assert cards == []


class TestNoDuplicateCards:
    def test_master_with_two_matching_services_renders_once(self, penza: Tenant) -> None:
        master = _master(penza, "Массажист", specialization="")
        _link(penza, master, _service(penza, "Спортивный массаж", slug="sport"))
        _link(penza, master, _service(penza, "Классический массаж", slug="classic"))

        cards = discover_masters(city="Пенза", specialization="массаж")

        assert [c.name for c in cards] == ["Массажист"]

    def test_match_on_both_service_and_specialization_renders_once(self, penza: Tenant) -> None:
        """The OR branch must not double-count a master satisfying both sides."""
        master = _master(penza, "Массажист", specialization="спортивный массаж")
        _link(penza, master, _service(penza, "Спортивный массаж", slug="sport"))

        cards = discover_masters(city="Пенза", specialization="спортивный массаж")

        assert [c.name for c in cards] == ["Массажист"]


class TestSpecializationFallback:
    def test_specialization_only_match_still_works(self, penza: Tenant) -> None:
        """Accepted current behaviour — must not regress."""
        _master(penza, "Анна", specialization="маникюр педикюр")

        assert {c.name for c in discover_masters(specialization="педикюр")} == {"Анна"}

    def test_specialization_tokens_also_order_independent(self, penza: Tenant) -> None:
        _master(penza, "Анна", specialization="маникюр педикюр")

        assert {c.name for c in discover_masters(specialization="педикюр маникюр")} == {"Анна"}


class TestQueryDegenerateInput:
    def test_quotes_are_stripped(self, penza: Tenant) -> None:
        master = _master(penza, "Массажист", specialization="")
        _link(penza, master, _service(penza, "Спортивный массаж", slug="sport"))

        assert len(discover_masters(specialization="«спортивный массаж»")) == 1


class TestPagination:
    def test_page_helper_shares_the_service_match(self, penza: Tenant) -> None:
        from apps.marketplace.discovery import discover_masters_page

        master = _master(penza, "Массажист", specialization="")
        _link(penza, master, _service(penza, "Спортивный массаж", slug="sport"))

        cards, meta = discover_masters_page(city="Пенза", specialization="спортивный массаж")

        assert [c.name for c in cards] == ["Массажист"]
        assert meta.total_count == 1

    def test_page_count_not_inflated_by_join(self, penza: Tenant) -> None:
        """``Paginator`` COUNTs the queryset — the join must not inflate it."""
        from apps.marketplace.discovery import discover_masters_page

        master = _master(penza, "Массажист", specialization="")
        _link(penza, master, _service(penza, "Спортивный массаж", slug="sport"))
        _link(penza, master, _service(penza, "Классический массаж", slug="classic"))

        _cards, meta = discover_masters_page(city="Пенза", specialization="массаж")

        assert meta.total_count == 1


class TestPunctuationAndFillerDoNotBreakMatching:
    """Regression guards for the ways a model can phrase the same request.

    Each of these previously produced the exact «мастеров пока не нашлось»
    line the DRF-945 ticket is about, despite the salon offering the service.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "спортивный массаж",
            "спортивный массаж.",
            "спортивный, массаж",
            "«спортивный массаж»",
            "спортивный массаж!",
            "  спортивный   массаж  ",
            "Спортивный массаж?",
        ],
        ids=[
            "plain",
            "trailing-period",
            "internal-comma",
            "guillemets",
            "exclamation",
            "extra-whitespace",
            "capitalized-question",
        ],
    )
    def test_punctuation_variants_all_match(self, penza: Tenant, query: str) -> None:
        master = _master(penza, "Массажист", specialization="")
        _link(penza, master, _service(penza, "Спортивный массаж", slug="sport"))

        assert {c.name for c in discover_masters(specialization=query)} == {"Массажист"}

    def test_two_services_separated_by_comma(self, penza: Tenant) -> None:
        """«маникюр, педикюр» must not be read as a service literally named
        «маникюр,».

        Scope note: this is about the COMMA, not about multi-service requests.
        Two *separate* services «Маникюр» + «Педикюр» on one master still
        return nothing — tokens are AND-ed within a single service row by
        design (see ``test_tokens_must_match_the_same_service``).
        """
        master = _master(penza, "Мастер", specialization="")
        _link(penza, master, _service(penza, "Маникюр педикюр", slug="mani-pedi"))

        assert {c.name for c in discover_masters(specialization="маникюр, педикюр")} == {"Мастер"}

    def test_polite_preamble_does_not_crowd_out_the_request(self, penza: Tenant) -> None:
        """Filler removal, not the tail slice, is what saves this one.

        The query tokenizes to exactly two words, so the ``_MAX_TOKENS`` slice
        is a no-op here — see ``test_tail_slice_keeps_the_trailing_tokens`` for
        the case that actually exercises it.
        """
        master = _master(penza, "Массажист", specialization="")
        _link(penza, master, _service(penza, "Спортивный массаж", slug="sport"))

        cards = discover_masters(
            specialization="здравствуйте я бы хотела записаться на спортивный массаж"
        )

        assert {c.name for c in cards} == {"Массажист"}


class TestDegenerateQueryFailsClosed:
    """A query we cannot tokenize must return nothing, never everything.

    Falling through would drop the filter and hand back the whole nationwide
    directory under «Вот мастера, которые могут подойти» — confidently wrong.
    This path is also reachable unauthenticated via the public directory
    endpoint.
    """

    @pytest.mark.parametrize(
        "query",
        ["я", "😀", "!!!", "-", "?"],
        ids=["single-letter", "emoji", "punctuation-only", "dash", "question-mark"],
    )
    def test_untokenizable_query_returns_nothing(self, penza: Tenant, query: str) -> None:
        master = _master(penza, "Массажист", specialization="")
        _link(penza, master, _service(penza, "Спортивный массаж", slug="sport"))
        _master(penza, "Другой мастер", specialization="")

        assert discover_masters(specialization=query) == []

    def test_blank_query_still_means_no_filter(self, penza: Tenant) -> None:
        """Whitespace-only is falsy-ish input, not a failed tokenization — the
        caller supplied no filter at all, so everything bookable is correct."""
        _master(penza, "Массажист", specialization="")

        assert len(discover_masters(specialization="   ")) == 1

    def test_degenerate_query_does_not_leak_the_directory_page(self, penza: Tenant) -> None:
        from apps.marketplace.discovery import discover_masters_page

        _master(penza, "Массажист", specialization="")

        cards, meta = discover_masters_page(specialization="я")

        assert cards == []
        assert meta.total_count == 0

    @pytest.mark.parametrize(
        "query",
        [
            "хочу спортивный массаж",
            "ищу спортивный массаж",
            "мне нужен спортивный массаж",
            "здравствуйте я бы хотела записаться на спортивный массаж",
            "нужен мастер на спортивный массаж, пожалуйста",
        ],
        ids=["хочу", "ищу", "мне-нужен", "polite-sentence", "master-plus-please"],
    )
    def test_filler_words_do_not_defeat_the_request(self, penza: Tenant, query: str) -> None:
        """Every token is AND-ed against ONE service name, so a stray «хочу»
        would otherwise reduce a valid request to zero results."""
        master = _master(penza, "Массажист", specialization="")
        _link(penza, master, _service(penza, "Спортивный массаж", slug="sport"))

        assert {c.name for c in discover_masters(specialization=query)} == {"Массажист"}

    def test_all_filler_query_does_not_match_everything(self, penza: Tenant) -> None:
        """A request made entirely of filler must not widen into the directory."""
        master = _master(penza, "Массажист", specialization="")
        _link(penza, master, _service(penza, "Спортивный массаж", slug="sport"))

        assert discover_masters(specialization="хочу записаться пожалуйста") == []


class TestGreetingIsStrippedAsAPhrase:
    """«день» is both half of «добрый день» and half of «День красоты».

    A word-level filler list cannot express that difference: listing «день»
    degrades the real salon package to «красоты», and not listing it lets the
    bare greeting match the package. Stripping the greeting as a PHRASE does
    both correctly.
    """

    def test_day_package_is_not_diluted(self, penza: Tenant) -> None:
        package_master = _master(penza, "Пакетный мастер", specialization="")
        _link(penza, package_master, _service(penza, "День красоты", slug="beauty-day"))
        other = _master(penza, "Другой мастер", specialization="")
        _link(penza, other, _service(penza, "Массаж для красоты тела", slug="body"))

        cards = discover_masters(specialization="день красоты")

        assert {c.name for c in cards} == {"Пакетный мастер"}

    @pytest.mark.parametrize(
        "greeting",
        [
            "добрый день",
            "день добрый",
            "Добрый день!",
            "доброе утро",
            "добрый вечер",
            "доброго дня",
            "доброго вечера",
            "доброго времени суток",
        ],
        ids=[
            "добрый-день",
            "день-добрый",
            "capitalized",
            "утро",
            "вечер",
            "genitive-дня",
            "genitive-вечера",
            "времени-суток",
        ],
    )
    def test_bare_greeting_matches_nothing(self, penza: Tenant, greeting: str) -> None:
        master = _master(penza, "Пакетный мастер", specialization="")
        _link(penza, master, _service(penza, "День красоты", slug="beauty-day"))

        assert discover_masters(specialization=greeting) == []

    @pytest.mark.parametrize(
        "greeting",
        [
            "добрый день",
            "доброго дня",
            "доброго времени суток",
            "добрый вечер",
            "доброе утро",
        ],
        ids=["nominative", "genitive", "времени-суток", "вечер", "утро"],
    )
    def test_greeting_before_a_real_request_is_ignored(self, penza: Tenant, greeting: str) -> None:
        """Genitive forms are as common in written Russian as nominative ones,
        and left in the AND chain they produce the very fallback this PR
        exists to remove."""
        master = _master(penza, "Массажист", specialization="")
        _link(penza, master, _service(penza, "Спортивный массаж", slug="sport"))

        cards = discover_masters(specialization=f"{greeting}, хочу спортивный массаж")

        assert {c.name for c in cards} == {"Массажист"}

    def test_greeting_words_are_not_eaten_out_of_other_words(self, penza: Tenant) -> None:
        """The \b anchors must not strip «день» out of «деньги» or «добрый»
        out of «недобрый»."""
        master = _master(penza, "Мастер", specialization="")
        _link(penza, master, _service(penza, "Деньги на уход", slug="money"))

        assert {c.name for c in discover_masters(specialization="деньги уход")} == {"Мастер"}


class TestTokenCapKeepsTheTail:
    """The ``_MAX_TOKENS`` slice itself — the filler tests never reach it.

    Seven non-filler words against a service whose name contains only the last
    five. Keeping the FIRST five would AND «один»/«два» against that name and
    return nothing, so this discriminates between the two slice directions
    rather than passing either way.
    """

    def test_last_tokens_survive_the_cap(self, penza: Tenant) -> None:
        master = _master(penza, "Массажист", specialization="")
        _link(penza, master, _service(penza, "Три четыре пять шесть массаж", slug="long"))

        cards = discover_masters(specialization="один два три четыре пять шесть массаж")

        assert {c.name for c in cards} == {"Массажист"}

    def test_leading_tokens_are_the_ones_dropped(self, penza: Tenant) -> None:
        """The mirror image: a service named after the LEADING words is NOT found.

        The name must be matched by the first five tokens and not by the last
        five, otherwise the assertion holds under either slice direction and
        proves nothing. «Один два три четыре пять» is exactly `first5`, so this
        fails the moment the code goes back to ``tokens[:_MAX_TOKENS]``.
        """
        master = _master(penza, "Массажист", specialization="")
        _link(penza, master, _service(penza, "Один два три четыре пять", slug="leading"))

        assert discover_masters(specialization="один два три четыре пять шесть массаж") == []
