"""DRF-1355 — «покажи салоны» and «покажи услуги» must land in different places.

## The turn this suite exists for

Live pilot, 24.08 07:51. The owner wrote «покажи мне салоны» and got::

    Вот услуги этого салона:
    • Массаж спины — от 1500 ₽ · 45 мин

then the same opening again about a second, different salon. He had named no
salon at all, and nothing in either message said which one it was about.

Measured on this branch before the fix, the two other suspects were clean:

* ``apps.orchestrator.fast_path.decide("покажи мне салоны")`` returns
  ``no_service_named`` — the deterministic branch does not claim the turn, so
  it reaches the model exactly as the owner's ruling of 24.08 requires;
* ``execute_catalog_tool("show_salons", {})`` renders the real salons and
  ``execute_catalog_tool("show_services", {})`` refuses to dump the catalog —
  DRF-1304's tools work.

What was broken is the step between: the model chose ``show_services`` and
filled its ``salon`` argument itself, and the platform executed that argument
without asking whether the person had said it.

## What this suite pins

**The answer to a salon question does not depend on which catalog tool the
model picks.** Each row below states a turn, the answer KIND it must get, and
the tool calls a model plausibly emits for it — including the wrong one. Every
listed call must produce the stated kind.

Adding a phrasing is adding a row. A phrasing that lands in the wrong kind
fails here rather than on the pilot, which is what «новая формулировка,
сваливающаяся не туда, роняет CI» means. The complementary guard one layer
up — that the fast path hands each tool's turns over — stays in
``test_fast_path_claim.py``; this one starts where that one ends.

The kind is read STRUCTURALLY, off the chips, not off the header text: a salon
card's chip opens that salon's services (``cb:catalog:services:``) and a
service card's chip opens its masters (``cb:catalog:masters:``). A header can
be reworded; what the person can tap cannot be reworded without changing where
the answer goes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from apps.orchestrator.discovery import (
    CALLBACK_CATALOG_MASTERS_PREFIX,
    CALLBACK_CATALOG_SERVICES_PREFIX,
    execute_catalog_tool,
    salon_named_in,
)

pytestmark = pytest.mark.django_db(transaction=True)


# --------------------------------------------------------------------------- #
# The pilot mirror, in the shape the 24.08 trace came out of                   #
# --------------------------------------------------------------------------- #


def _ts() -> datetime:
    return datetime(2026, 8, 24, 7, 51, tzinfo=timezone.utc)


def _salon(slug: str, name: str, *, city: str = "Пенза", address: str = ""):
    from apps.catalog.models import CatalogMaster
    from apps.tenancy.models import Tenant

    tenant = Tenant.objects.create(slug=slug, name=name, city=city)
    CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        name=f"Мастер {name}",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        raw={"address": address} if address else {},
    )
    return tenant


def _service(tenant, name: str, *, price: str | None = None, duration: int | None = None):
    """A service its salon's master really performs — so it always earns a chip."""
    from apps.catalog.models import CatalogMaster, CatalogService, MasterService

    service = CatalogService.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        slug=f"{tenant.slug}-{name[:30]}".lower().replace(" ", "-"),
        name=name,
        is_active=True,
        price_from=Decimal(price) if price is not None else None,
        duration_min=duration,
    )
    master = CatalogMaster.all_tenants.filter(tenant=tenant).first()
    MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
    return service


@pytest.fixture
def pilot_mirror():
    """Five salons in one city — the shape the live answer came out of.

    «Люмина» and «Afrodita» are the two the 24.08 trace showed services for.
    «Центр коррекции фигуры «Afrodita»» and «Центр красоты «Эстетика»» share
    the word «центр» on purpose: it is the case that proves a word carried by
    two salons cannot identify either of them.
    """
    lumina = _salon("lumina", "Люмина", address="Пенза, ул. Московская, 74")
    _service(lumina, "Массаж спины", price="1500", duration=45)
    _service(lumina, "Тайский массаж", price="2500", duration=60)

    afrodita = _salon("afrodita", "Центр коррекции фигуры «Afrodita»", address="Пенза, Кирова, 1")
    _service(afrodita, "Комплекс «все тело»", price="30900", duration=90)
    _service(afrodita, "Комплекс «подмышки + бикини»", price="11900", duration=45)

    estetika = _salon("estetika", "Центр красоты «Эстетика»", address="Пенза, Суворова, 3")
    _service(estetika, "Чистка лица", price="2200", duration=60)

    body = _salon("bodyformula", "BodyFormula", address="Пенза, Леонова, 15а")
    _service(body, "Стрижка", price="900", duration=40)

    spa = _salon("spatrium", "SPAtrium", address="Пенза, Кулакова, 9")
    _service(spa, "Обёртывание", price="3100", duration=60)

    return {"lumina": lumina, "afrodita": afrodita, "estetika": estetika}


# --------------------------------------------------------------------------- #
# The separation table                                                         #
# --------------------------------------------------------------------------- #

SALONS = "salons"
SERVICES = "services"


@dataclass(frozen=True)
class SeparationCase:
    """One turn, the answer kind it must get, and the calls a model may make.

    ``tool_calls`` deliberately includes the WRONG pick where a wrong pick is
    plausible. That is the whole point: for a salon question the answer must
    be the salons whether the model reaches for ``show_salons`` or for
    ``show_services`` with a salon it made up.
    """

    turn: str
    expects: str
    tool_calls: tuple[tuple[str, dict], ...]
    why: str = ""


CATALOG_SEPARATION: tuple[SeparationCase, ...] = (
    # ── The live defect, verbatim ────────────────────────────────────────
    SeparationCase(
        turn="покажи мне салоны",
        expects=SALONS,
        tool_calls=(
            ("show_salons", {}),
            # What the model actually did on 24.08 07:51 — twice, with two
            # different salons the owner had never mentioned.
            ("show_services", {"salon": "Люмина"}),
            ("show_services", {"salon": "Центр коррекции фигуры «Afrodita»"}),
        ),
        why="DRF-1355. The plainest wording of the plainest marketplace intent.",
    ),
    SeparationCase(
        turn="покажи салоны",
        expects=SALONS,
        tool_calls=(("show_salons", {}), ("show_services", {"salon": "Люмина"})),
    ),
    SeparationCase(
        turn="какие салоны у вас есть",
        expects=SALONS,
        tool_calls=(("show_salons", {}), ("show_services", {"salon": "BodyFormula"})),
        why="DRF-1304's own acceptance turn — met silence on 23.08.",
    ),
    SeparationCase(
        turn="где вы находитесь",
        expects=SALONS,
        tool_calls=(("show_salons", {}), ("show_services", {"salon": "SPAtrium"})),
        why="A question about places, answered with addresses, never with prices.",
    ),
    SeparationCase(
        turn="куда можно прийти",
        expects=SALONS,
        tool_calls=(("show_salons", {}), ("show_services", {"salon": "Люмина"})),
    ),
    SeparationCase(
        turn="какие салоны есть в пензе",
        expects=SALONS,
        tool_calls=(
            ("show_salons", {"city": "Пенза"}),
            ("show_services", {"salon": "Люмина", "city": "Пенза"}),
        ),
        why=(
            "The city rides into the salon list, not into a service search: "
            "«услуги в Пензе» with no salon and no query is every service in "
            "the city — the catalog dump BOT-003 §9 forbids."
        ),
    ),
    SeparationCase(
        turn="в каком салоне делают массаж",
        expects=SERVICES,
        tool_calls=(("show_services", {"query": "массаж"}),),
        why=(
            "Names a service and asks WHERE — the query came from the person, "
            "so it is answered; «салон» in the wording changes nothing, which "
            "is why this file holds no rule about that word."
        ),
    ),
    # ── The service side, which must NOT be dragged along ────────────────
    SeparationCase(
        turn="какие услуги в Люмине",
        expects=SERVICES,
        tool_calls=(("show_services", {"salon": "Люмина"}),),
        why="DRF-1355's control turn: the salon IS named, so it is answered.",
    ),
    SeparationCase(
        turn="что делают в салоне Люмина",
        expects=SERVICES,
        tool_calls=(("show_services", {"salon": "Люмина"}),),
    ),
    SeparationCase(
        turn="сколько стоит массаж",
        expects=SERVICES,
        tool_calls=(("show_services", {"query": "массаж"}),),
    ),
    SeparationCase(
        turn="что у вас есть по лицу",
        expects=SERVICES,
        tool_calls=(("show_services", {"query": "лица"}),),
    ),
)


# --------------------------------------------------------------------------- #
# Reading the answer's kind off the chips                                      #
# --------------------------------------------------------------------------- #


def _chips(reply) -> list[dict[str, str]]:
    if reply.action_data is None:
        return []
    return reply.action_data["attachments"][0]["payload"]["buttons"]


def _kind_of(reply) -> str:
    """SALONS / SERVICES, decided by where the answer's chips lead."""
    callbacks = [chip["callback"] for chip in _chips(reply)]
    assert callbacks, f"a catalog answer with no chips cannot be classified:\n{reply.text}"
    if all(cb.startswith(CALLBACK_CATALOG_SERVICES_PREFIX) for cb in callbacks):
        return SALONS
    if all(cb.startswith(CALLBACK_CATALOG_MASTERS_PREFIX) for cb in callbacks):
        return SERVICES
    raise AssertionError(f"mixed chip kinds in one answer: {callbacks}")


# --------------------------------------------------------------------------- #
# The guards                                                                   #
# --------------------------------------------------------------------------- #


class TestCatalogSeparation:
    @pytest.mark.parametrize(
        "case", CATALOG_SEPARATION, ids=[c.turn.replace(" ", "_") for c in CATALOG_SEPARATION]
    )
    def test_every_plausible_tool_pick_lands_in_the_same_place(self, case, pilot_mirror):
        """The model's choice between the two catalog tools is not load-bearing.

        AYLA-DEC-0045 in one assertion: for a turn that asks about salons, the
        answer is the salons — including when the model reached for
        ``show_services`` and named a salon out of its own head.
        """
        for tool, args in case.tool_calls:
            reply = execute_catalog_tool(tool, args, said=case.turn)
            assert reply is not None, f"{tool}{args} produced no reply"
            assert _kind_of(reply) == case.expects, (
                f"«{case.turn}» + {tool}{args} answered with {_kind_of(reply)}, "
                f"expected {case.expects}:\n{reply.text}"
            )

    def test_the_live_defect_verbatim(self, pilot_mirror):
        """24.08 07:51 — the exact call that produced the exact message.

        «Массаж спины» may still appear: a salon card samples what is done
        there. What must be gone is the ANSWER being that salon's price list —
        no «от 1500 ₽», and every chip opening a salon rather than a master.
        """
        reply = execute_catalog_tool("show_services", {"salon": "Люмина"}, said="покажи мне салоны")

        assert "1500" not in reply.text  # no prices: this is not a service answer
        assert "этого салона" not in reply.text
        # All five salons are offered, not one chosen for the person.
        for name in ("Люмина", "Центр коррекции фигуры «Afrodita»", "BodyFormula", "SPAtrium"):
            assert name in reply.text
        assert _kind_of(reply) == SALONS

    def test_no_answer_speaks_of_a_salon_it_does_not_name(self, pilot_mirror):
        """The negative acceptance of DRF-1355, on every catalog surface.

        «Вот услуги этого салона» is the sentence the trace opened with twice,
        about two different salons. A demonstrative is the one thing a catalog
        answer may never use in place of the name it already holds.
        """
        from apps.orchestrator.discovery import execute_catalog_callback

        rendered = [
            execute_catalog_tool("show_salons", {}, said="покажи мне салоны"),
            execute_catalog_tool("show_salons", {"city": "Пенза"}, said="салоны в пензе"),
            execute_catalog_tool(
                "show_services", {"salon": "Люмина"}, said="какие услуги в Люмине"
            ),
            execute_catalog_tool("show_services", {"query": "массаж"}, said="сколько стоит массаж"),
            execute_catalog_callback(
                f"{CALLBACK_CATALOG_SERVICES_PREFIX}{pilot_mirror['lumina'].id}"
            ),
        ]

        for reply in rendered:
            assert reply is not None
            assert "этого салона" not in reply.text, reply.text

    def test_named_salon_answer_says_which_salon(self, pilot_mirror):
        reply = execute_catalog_tool(
            "show_services", {"salon": "Люмина"}, said="какие услуги в Люмине"
        )

        assert "Люмина" in reply.text
        assert "Массаж спины" in reply.text

    def test_chip_tap_still_answers_by_id_and_names_the_salon(self, pilot_mirror):
        """A tap carries the salon's id, so grounding never applies to it.

        The person picked this salon themselves, one message ago; requiring
        them to have TYPED its name would break the chain DRF-1304 built.
        """
        from apps.orchestrator.discovery import execute_catalog_callback

        reply = execute_catalog_callback(
            f"{CALLBACK_CATALOG_SERVICES_PREFIX}{pilot_mirror['lumina'].id}"
        )

        assert "Люмина" in reply.text
        assert "Массаж спины" in reply.text


class TestSalonGrounding:
    """Who is allowed to decide WHICH salon — the unit under the table above."""

    def test_a_salon_the_person_named_is_grounded(self, pilot_mirror):
        assert salon_named_in("какие услуги в Люмине", "Люмина")
        assert salon_named_in("что делают в BodyFormula", "BodyFormula")

    def test_case_endings_do_not_break_grounding(self, pilot_mirror):
        # The width of a Russian case ending, the same bound city recognition
        # uses for «пензе» ↔ «Пенза».
        assert salon_named_in("была в Люмине вчера", "Люмина")
        assert salon_named_in("расскажи про Люмину", "Люмина")

    def test_a_salon_named_by_the_bot_a_turn_earlier_is_grounded(self, pilot_mirror):
        # ``said`` is both roles: the salon list the bot just rendered is where
        # a follow-up gets the name from.
        said = "покажи салоны\nВот салоны, которые к нам подключены:\n• Люмина — Пенза\nа что там"
        assert salon_named_in(said, "Люмина")

    def test_a_salon_nobody_named_is_not_grounded(self, pilot_mirror):
        assert not salon_named_in("покажи мне салоны", "Люмина")
        assert not salon_named_in("покажи мне салоны", "Центр коррекции фигуры «Afrodita»")
        assert not salon_named_in("привет", "BodyFormula")

    def test_the_category_word_never_answers_with_a_salons_price_list(self, pilot_mirror):
        """«салон» as an argument may not become one salon's services.

        Asserted on the ANSWER rather than on the grounding verdict, because
        the two branches reach it differently and only the answer matters: the
        word lands on no salon here, so the call goes through and is told «нет
        такого салона, могу показать какие есть». What it must never be is the
        24.08 shape — a price list for a salon the person did not choose. The
        narrow case this does not close (a tenant whose own name contains
        «Салон») is stated in the module block above ``salon_named_in``.
        """
        reply = execute_catalog_tool("show_services", {"salon": "салон"}, said="покажи мне салоны")

        assert reply is not None
        assert "Массаж спины" not in reply.text
        assert "1500" not in reply.text
        assert "этого салона" not in reply.text

    def test_a_salon_the_person_named_that_we_do_not_have_goes_through(self, pilot_mirror):
        """DRF-1283's honest «нет такого салона» must survive the check.

        The argument lands on no salon we have, so nothing in the catalog
        could have suggested that string — the person is its only possible
        source. Naming it back is a better answer than the salon list, and it
        only exists if the call is allowed through.
        """
        assert salon_named_in("а есть Афродита?", "Афродита")
        # Still refused when the person did not say it either.
        assert not salon_named_in("покажи мне салоны", "Афродита")

    def test_a_word_two_salons_share_identifies_neither(self, pilot_mirror):
        """«центр» belongs to «Центр коррекции фигуры «Afrodita»» AND to
        «Центр красоты «Эстетика»», so it cannot single out either."""
        assert not salon_named_in("сходить в центр", "Центр коррекции фигуры «Afrodita»")
        assert not salon_named_in("центр", "Центр красоты «Эстетика»")
        # The salon is still reachable by the word that IS its own.
        assert salon_named_in("что есть в Эстетике", "Центр красоты «Эстетика»")

    def test_nothing_said_grounds_nothing(self, pilot_mirror):
        assert not salon_named_in("", "Люмина")
        assert not salon_named_in("покажи салоны", "")

    def test_grounding_degrades_to_ungrounded_when_the_catalog_read_fails(
        self, pilot_mirror, monkeypatch
    ):
        """A mirror hiccup must show the salon list, never answer for a salon."""
        from apps.marketplace import discovery as marketplace_discovery

        def _boom():
            raise RuntimeError("catalog unavailable")

        monkeypatch.setattr(marketplace_discovery, "bookable_salon_names", _boom)

        assert not salon_named_in("какие услуги в Люмине", "Люмина")


class TestCriterialessCallIsUnchanged:
    """A bare ``show_services({})`` is a different state and keeps its answer.

    It asserts nothing about a salon, so there is nothing for the platform to
    check and nothing it can conclude — «услуги какого салона или какого вида»
    is still the honest question (BOT-003 §9). Only a call that DOES claim a
    salon, and claims one nobody named, is answered with the salon list.
    """

    def test_no_criteria_still_asks(self, pilot_mirror):
        from apps.orchestrator.discovery import NO_SERVICE_CRITERIA_QUESTION

        reply = execute_catalog_tool("show_services", {}, said="покажи мне салоны")

        assert reply.text == NO_SERVICE_CRITERIA_QUESTION
