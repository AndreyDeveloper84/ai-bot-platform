"""A short token is a stem only if the catalog uses it as a word (DRF-1352).

The live Controlled-Pilot measurement this module exists for, taken on the
contour on 24.08:

    «маникюр»                       → stems ['маникю']            → 0 cards,
                                      an honest «we don't do that»
    «найди мне мастера по маникюру» → stems ['найди', 'по', 'маникю']
                                      → ALL SEVEN bookable masters
    «по»                            → stems ['по']                → all seven

Seven was every bookable master in the contour, and no salon in it offers a
single nail service. The preposition «по» cleared ``_MIN_TOKEN_LEN`` and went
into the OR-chain as ``name ILIKE '%по%'``, which matches «подмышек»,
«поверхности», «поясницы», «после», «похудения» — a large arbitrary slice of
any Russian catalog. So the POLITE phrasing was answered confidently and
wrongly while the blunt one was answered correctly, which is the one shape of
defect a person cannot see and cannot work around.

What is pinned here:

* the two phrasings of the same request get the SAME answer, and it is the
  honest refusal — the polite one is not punished for being polite;
* the rule is about the CLASS, not about «по»: «за», «из», «до», «от» are
  disposed of by the same mechanism, none of them named anywhere in the code;
* a short word the catalog really uses — «LPG», «спа» — stays searchable, and
  it did not have to be foreseen either;
* a four-character PREFIX («воск» → «Восковая депиляция») keeps working, which
  is why the line is four and not ``_STEM_LEN``;
* attestation cannot be turned into a substring licence: «Уход за кожей»
  makes «за» a word of the catalog, and «за» then matches THAT name and not
  «задней поверхности»;
* the master's free-text ``specialization`` («Мастер по массажу») does not
  attest anything — it is the field most likely to contain exactly the
  function words this excludes;
* the ordinary requests measured alongside — «где делают лимфодренаж»,
  «массаж по телу», «покажи массажистов в пензе» — do not move.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from django.conf import settings as dj_settings

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.marketplace.discovery import (
    discover_masters,
    parse_query,
    parse_stems,
    query_stems,
    service_coverage,
    split_requested_services,
)
from apps.tenancy.models import Tenant

# Postgres-only, same reason as test_discovery_by_service and
# test_discovery_goal_selection: the matching is Cyrillic ILIKE plus, since
# this ticket, a word-boundary regex. SQLite would answer both differently and
# every «and NOT this one» assertion below would pass for the wrong reason.
pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        "postgresql" not in str(dj_settings.DATABASES["default"]["ENGINE"]),
        reason="Cyrillic ILIKE folding and word-boundary regex require Postgres.",
    ),
]

_TS = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)

_RELAX = [{"key": "relax", "label": "Расслабиться и снять стресс"}]
_RECHARGE = [{"key": "recharge", "label": "Восстановить силы"}]
_BODY_SHAPE = [{"key": "body_shape", "label": "Подтянуть фигуру"}]

# The contour in miniature, and the trap is the point: EVERY master offers at
# least one service whose name carries the letters «по» inside a word
# («поверхности», «поясницы», «после», «похудения», «подмышек»), and NOBODY
# offers a nail service. A fixture without that property would answer «по»
# with an empty list before the fix as well as after it, and prove nothing.
_ROSTER: dict[str, tuple[str, list[tuple[str, list | None]]]] = {
    "Сазонова Инна": (
        "Мастер по массажу",
        [
            ("Классический массаж", _RELAX),
            ("Классический массаж задней поверхности тела", _RELAX),
            ("Лимфодренажный массаж всего тела (60 минут)", _RECHARGE),
        ],
    ),
    "Архипкин Денис": (
        "Мастер по массажу",
        [
            ("Массаж спины и поясницы", _RECHARGE),
            ("Спортивный массаж", _RECHARGE),
            ("Лимфодренажный массаж — снятие отёков (экспресс 30 минут)", _RECHARGE),
        ],
    ),
    "Татьяна Паламарчук": (
        "Мастер по массажу",
        [
            ("Массаж ног — глубокое расслабление и лимфодренаж (45 минут)", _RECHARGE),
            ("Восстановление после нагрузок", _RECHARGE),
        ],
    ),
    "Мария Петрова": (
        "Массаж и обёртывания",
        [
            ("Обёртывание для похудения", _BODY_SHAPE),
            ("LPG-массаж", _BODY_SHAPE),
            ("СПА-уход для тела", _RELAX),
        ],
    ),
    "Анна Иванова": (
        "Аппаратные методики",
        [("Вакуумно-роликовый массаж поясницы", _BODY_SHAPE)],
    ),
    "Ольга Смирнова": (
        "Мастер по депиляции",
        [("Восковая депиляция подмышек", None), ("Шугаринг ног", None)],
    ),
    "Ирина Волкова": (
        "Косметолог",
        [("Уход за кожей после чистки", None), ("Чистка лица", None)],
    ),
}

# Every master who performs something called «массаж» — the answer the
# ordinary massage requests must keep giving, before and after.
_MASSAGE = {
    "Сазонова Инна",
    "Архипкин Денис",
    "Татьяна Паламарчук",
    "Мария Петрова",
    "Анна Иванова",
}


@pytest.fixture
def contour() -> Tenant:
    penza = Tenant.objects.create(slug="salon-penza", name="Салон Пенза", city="Пенза")
    for master_name, (specialization, services) in _ROSTER.items():
        master = CatalogMaster.all_tenants.create(
            tenant=penza,
            external_updated_at=_TS,
            name=master_name,
            specialization=specialization,
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        for service_name, goals in services:
            service = CatalogService.all_tenants.create(
                tenant=penza,
                slug=f"s-{uuid4().hex[:12]}",
                name=service_name,
                is_active=True,
                goals=goals or [],
                # Groundable, or the card-stamping gate (DRF-962) would refuse
                # the row for a reason unrelated to this ticket.
                ayla_service_id=uuid4(),
                external_updated_at=_TS,
            )
            MasterService.all_tenants.create(tenant=penza, master=master, service=service)
    return penza


def _names(query: str) -> set[str]:
    return {card.name for card in discover_masters(specialization=query, limit=50)}


# ─── the defect itself ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "phrase",
    [
        "маникюр",
        "мастер по маникюру",
        "найди мне мастера по маникюру",
        "ищу мастера по педикюру",
        "есть кто-нибудь по эпиляции",
    ],
)
def test_polite_phrasing_gets_the_same_honest_refusal(contour: Tenant, phrase: str) -> None:
    """Naming a service nobody offers answers nobody, however it is phrased.

    Before DRF-1352 only the first of these five answered honestly; the other
    four returned every bookable master in the contour.
    """
    assert _names(phrase) == set()


def test_a_bare_preposition_is_not_a_service_query(contour: Tenant) -> None:
    """«по» yields nothing to match on, so discovery fails closed.

    Not «matches nothing» — «asked for nothing». The parse is empty, which is
    the state ``_bookable_qs`` refuses rather than answering with the
    unfiltered directory.
    """
    parsed = parse_query("по")
    assert parsed.stems == []
    assert parsed.is_empty
    assert _names("по") == set()


@pytest.mark.parametrize(
    ("phrase", "dropped"),
    [
        ("найди мне мастера по маникюру", "по"),
        ("до скольки работаете", "до"),
        ("мастер из пензы", "из"),
        ("где делают лимфодренаж", "где"),
    ],
)
def test_the_rule_is_the_class_and_not_the_word(contour: Tenant, phrase: str, dropped: str) -> None:
    """Every function word goes the same way, and none is named in the code.

    ``grep -c 'по\\b'`` over ``discovery.py`` is the real assertion here: the
    fix adds no word to any list, so «за», «из», «до», «от», «об», «ко» need
    no follow-up ticket.
    """
    assert dropped not in parse_query(phrase).stems


# ─── the other side: what a short token must still be able to do ─────────


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # Two- and three-character words the catalog really uses. Neither was
        # foreseen by a list; both are attested by a service name.
        ("LPG", {"Мария Петрова"}),
        ("спа", {"Мария Петрова"}),
        # Four characters: a PREFIX of a longer stored word, matched as a
        # substring exactly as before. This is why the line is four and not
        # ``_STEM_LEN`` — word-judging these would lose both.
        ("воск", {"Ольга Смирнова"}),
        ("уход", {"Ирина Волкова", "Мария Петрова"}),
    ],
)
def test_short_words_the_catalog_uses_stay_searchable(
    contour: Tenant, query: str, expected: set[str]
) -> None:
    assert _names(query) == expected


def test_an_attested_short_word_matches_as_a_word_not_as_a_substring(
    contour: Tenant,
) -> None:
    """«Уход за кожей» attests «за» — and «за» must not become ``%за%``.

    This is the half that keeps attestation from being one service name away
    from re-opening the defect. «за» is a word of this catalog, so it survives
    the parse; it then matches «Уход ЗА кожей» and NOT «задней поверхности
    тела», which the substring form matched before.
    """
    assert "за" in parse_query("уход за кожей").stems
    assert _names("за") == {"Ирина Волкова"}


def test_free_text_specialization_attests_nothing(contour: Tenant) -> None:
    """Every massage master here is «Мастер по массажу» — and «по» is still out.

    The vocabulary is read from curated service NAMES only. Attesting from
    ``specialization`` would attest precisely the function words this
    excludes, and that field is matched by the same stems.
    """
    assert parse_query("мастер по маникюру").stems == ["маникю"]
    assert _names("мастер по маникюру") == set()


# ─── and the requests that must not move ─────────────────────────────────


def test_ordinary_requests_are_unchanged(contour: Tenant) -> None:
    """The rows a fix that merely swept prepositions away would have broken."""
    # A preposition BETWEEN two service words: the words survive, and the
    # preposition no longer drags in the depilation and skincare masters.
    assert _names("массаж по телу") == _MASSAGE
    # DRF-1283's city recognition, untouched.
    assert _names("покажи массажистов в пензе") == _MASSAGE
    # DRF-1324's goal selection, untouched.
    assert _names("хочу расслабиться") == {"Сазонова Инна", "Мария Петрова"}
    # A service that exists still finds exactly the people who perform it.
    assert _names("где делают лимфодренаж") == {
        "Сазонова Инна",
        "Архипкин Денис",
        "Татьяна Паламарчук",
    }
    assert _names("хочу записаться на массаж") == _MASSAGE
    assert _names("депиляция подмышек") == {"Ольга Смирнова"}


def test_a_city_only_request_is_answered_rather_than_refused(contour: Tenant) -> None:
    """«мастер из пензы» answered NOBODY before, because «из» matched nothing.

    An unattested short token used to sit in the OR-chain and, being the only
    stem, reduce a perfectly answerable request to zero. Dropping it leaves
    stems empty WITH a city — the state ``_bookable_qs`` documents as «named a
    place, not a service» and must not fail closed on.
    """
    parsed = parse_query("мастер из пензы")
    assert parsed.stems == []
    assert parsed.cities == ["Пенза"]
    assert _names("мастер из пензы") == set(_ROSTER)


# ─── the same rule everywhere the stems are re-read ──────────────────────


def test_the_booking_callback_applies_the_same_rule(contour: Tenant) -> None:
    """Stems carried on a button mean what they meant when the card was drawn.

    ``query_stems`` is the pure half and still carries «по» — as documented,
    it also still carries city tokens. ``parse_stems``, which runs at the tap
    inside a database context, is where both are dropped.
    """
    stems = query_stems("найди мне мастера по маникюру")
    assert "по" in stems
    assert parse_stems(stems).stems == ["найди", "маникю"]


def test_a_part_that_is_only_a_preposition_makes_no_claim(contour: Tenant) -> None:
    """DRF-1312 must not announce «по» as a service the contour does not offer.

    A part that reduces to an unattested short token names nothing, so it is
    neither a part of an enumeration nor a name to confirm or deny — the same
    treatment a lead-in clause and a city name already get.
    """
    # ONE part, not two — exactly the treatment «массаж, пенза» already gets,
    # so a caller keying off ``len(parts) >= 2`` does not read this as a
    # composite request and does not report «по» as a service we lack.
    assert split_requested_services("массаж, по") == ["массаж"]
    assert service_coverage(["по"]) == ([], [])
    assert service_coverage(["массаж", "по"]) == (["массаж"], [])
