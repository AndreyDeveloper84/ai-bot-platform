"""The pre-LLM branch may not outgrow the concierge's tools (DRF-1328).

## Why this file exists

Twice in two days the deterministic branch in front of the concierge
answered a turn that was not its own, and both times the mechanism was the
same: its entry condition said «a service word is in the text», and every
capability it must NOT swallow had to be subtracted from that by hand.

    23.08  «давай будет несколько: массаж классика, и маникюр»
           → five massage masters, silence about the nails   (DRF-1312)
    24.08  «Найди мне САЛОНЫ массажа»
           → master cards, twice, while ``show_salons``
             — built 23.08, described as «НЕ отдельных мастеров» —
             sat unused because the model never got the turn  (DRF-1328)

DRF-1328 inverted the default (:mod:`apps.orchestrator.fast_path`). This file
is the condition of acceptance the owner attached to that inversion: **a test
that fails when a new concierge tool appears and the branch does not know
about it.** Without it the inverted default grows the same pile from the
other side — a parser whose vocabulary quietly widens until it is claiming
turns a newer tool was built for.

## How it fails on the day a tool lands

:data:`apps.orchestrator.concierge.CONCIERGE_TOOL_SPECS` is the roster the
model is armed with. Every name in it must have an entry in
:data:`apps.orchestrator.fast_path.FAST_PATH_TOOL_CLAIMS` saying whether the
fast path claims that tool's turns and why — with SAMPLE TURNS that are then
routed for real. So:

* add a tool, forget the table  → :class:`TestRosterIsCovered` fails;
* claim to hand a tool's turns over while the parser still eats them
  → :class:`TestSampleTurnsRouteAsDeclared` fails;
* delete or rename a tool and leave a stale entry behind
  → :class:`TestRosterIsCovered` fails the other way.

Modelled on the two guards already in the contour: ``TestEveryLiveHandlerIsGated``
(DRF-1300 — tracks the FACT of a call, not a filename) and
``apps/skills/welcome/tests/test_miniapp_routes.py`` (DRF-1326 — reads the
table out of the source instead of restating it here, so it cannot drift).

## Why the roster is also read out of the source

Importing ``CONCIERGE_TOOL_SPECS`` proves what the constant holds, not what
the concierge is armed with. A future edit that passes an extra tool inline
at the call site — exactly the shape this constant was extracted FROM —
would leave the constant, and this file, serenely green while a new
capability shipped unguarded. :class:`TestRosterIsTheRealOne` reads
``concierge.py`` and pins that ``tool_definitions`` is handed the constant
itself.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from apps.orchestrator import fast_path
from apps.orchestrator.concierge import CONCIERGE_TOOL_SPECS
from apps.orchestrator.fast_path import (
    FAST_PATH_CLAIM_BY_TOOL,
    FAST_PATH_TOOL_CLAIMS,
    claims_direct_show_masters,
    decide,
)

#: City recognition and the composite split each read the live catalog. Both
#: degrade to «nothing recognised» rather than failing, so an unmarked test
#: here would pass for the wrong reason.
pytestmark = pytest.mark.django_db

_CONCIERGE_PY = Path(fast_path.__file__).resolve().parent / "concierge.py"


@pytest.fixture
def penza() -> None:
    """One bookable master in Пенза — enough for «пенза» to be a known city.

    The claim parser never looks at masters; it asks
    ``apps.marketplace.discovery.strip_known_cities`` whether a leftover word
    names a place we can actually book in, and that set is derived from live
    masters. Without a row, «в пензе» reads as an unaccounted word and every
    city case below would pass for the wrong reason.
    """
    from datetime import datetime, timezone

    from apps.catalog.models import CatalogMaster
    from apps.tenancy.models import Tenant

    tenant = Tenant.objects.create(slug="salon-penza-1328", name="SPAtrium", city="Пенза")
    CatalogMaster.all_tenants.create(
        tenant=tenant,
        name="Архипкин Денис",
        specialization="массаж",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        external_updated_at=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
    )


def _roster() -> frozenset[str]:
    """Tool names the concierge arms the model with."""
    names = frozenset(str(spec["name"]) for spec in CONCIERGE_TOOL_SPECS)
    if not names:
        raise AssertionError(
            "CONCIERGE_TOOL_SPECS is empty — the roster reader and the source "
            "have drifted apart. Fix the reader; do not delete the test."
        )
    return names


# ---------------------------------------------------------------------------
# The guard itself
# ---------------------------------------------------------------------------


class TestRosterIsCovered:
    """Every concierge tool has an answer about who owns its turns."""

    def test_the_roster_is_not_vacuous(self) -> None:
        """Guard the guard: an empty roster would make everything below pass."""
        assert len(_roster()) >= 5, (
            f"expected the concierge's real tool roster, got {sorted(_roster())}"
        )

    def test_every_tool_has_a_claim_entry(self) -> None:
        missing = sorted(_roster() - set(FAST_PATH_CLAIM_BY_TOOL))
        assert missing == [], (
            f"concierge tool(s) with no entry in FAST_PATH_TOOL_CLAIMS: {missing}. "
            "A new tool is a new way for the pre-LLM branch to answer the wrong "
            "question — say in apps/orchestrator/fast_path.py whether the branch "
            "claims its turns, and add sample turns that pin the routing "
            "(DRF-1328)."
        )

    def test_no_claim_entry_outlives_its_tool(self) -> None:
        stale = sorted(set(FAST_PATH_CLAIM_BY_TOOL) - _roster())
        assert stale == [], (
            f"FAST_PATH_TOOL_CLAIMS entries for tool(s) the concierge no longer "
            f"has: {stale}. A stale entry is a rule nobody can trip, and it "
            "hides the tool that replaced it."
        )

    def test_exactly_one_tool_is_claimed(self) -> None:
        """The branch is a show-masters short-circuit, not a router.

        If a second tool is ever claimed here, the fast path has become a
        second dispatcher running ahead of the model — the thing DRF-1328
        exists to stop. That may one day be the right call, but it is a
        decision, and it should have to be made deliberately.
        """
        claimed = sorted(c.tool for c in FAST_PATH_TOOL_CLAIMS if c.claimed)
        assert claimed == ["show_masters"], claimed

    def test_every_entry_says_why_and_gives_turns(self) -> None:
        """A table of booleans decays; a table of reasons is reviewable."""
        for claim in FAST_PATH_TOOL_CLAIMS:
            assert len(claim.why.strip()) >= 40, claim.tool
            assert claim.sample_turns, claim.tool


class TestRosterIsTheRealOne:
    """The constant this file reads is the one the concierge actually passes."""

    def test_tool_definitions_is_the_constant(self) -> None:
        src = _CONCIERGE_PY.read_text(encoding="utf-8")
        assert re.search(r"tool_definitions\s*=\s*CONCIERGE_TOOL_SPECS\b", src), (
            f"{_CONCIERGE_PY} no longer hands `tool_definitions` the "
            "CONCIERGE_TOOL_SPECS constant. Whatever it hands over instead is "
            "the real roster, and this file is no longer guarding it — point "
            "the concierge back at the constant, or teach this reader the new "
            "shape. Do not delete the test (DRF-1328)."
        )


class TestSampleTurnsRouteAsDeclared:
    """Each entry's sample turns are routed for real, not merely declared.

    This is what makes the table load-bearing. An entry that says «handed to
    the concierge» while the parser still claims those words is a lie the
    booleans alone cannot expose.
    """

    @pytest.mark.parametrize(
        ("turn", "expected"),
        [
            pytest.param(turn, claim.claimed, id=f"{claim.tool}-{index}")
            for claim in FAST_PATH_TOOL_CLAIMS
            for index, turn in enumerate(claim.sample_turns)
        ],
    )
    def test_sample_turn(self, penza: None, turn: str, expected: bool) -> None:
        got = claims_direct_show_masters(turn)
        assert got is expected, (
            f"«{turn}» is a sample turn for a tool declared "
            f"{'CLAIMED' if expected else 'HANDED OVER'} in FAST_PATH_TOOL_CLAIMS, "
            f"but the parser says claimed={got} ({decide(turn)}). Either the "
            "vocabulary in apps/orchestrator/fast_path.py is wrong, or the "
            "table is."
        )


# ---------------------------------------------------------------------------
# The three live turns the owner asked to be proven
# ---------------------------------------------------------------------------


class TestTheThreeLiveTurns:
    """DRF-1328's acceptance, as the owner wrote it."""

    def test_salons_go_to_the_model(self, penza: None) -> None:
        """The turn of 24.08, 04:46 — twice answered with master cards."""
        assert claims_direct_show_masters("Найди мне САЛОНЫ массажа") is False

    def test_a_plain_service_question_still_answers_here(self, penza: None) -> None:
        assert claims_direct_show_masters("где делают лимфодренаж") is True

    def test_the_cheap_path_stays_cheap(self, penza: None) -> None:
        """«покажи массажистов в пензе» must not start costing an LLM call."""
        assert claims_direct_show_masters("покажи массажистов в пензе") is True


# ---------------------------------------------------------------------------
# The parse itself
# ---------------------------------------------------------------------------


class TestTheInvertedDefault:
    """An unknown word is a refusal, not noise.

    This is the whole shape. Nothing below names a tool — the point is that
    the parser does not need to know a tool exists in order to leave its
    turns alone.
    """

    @pytest.mark.parametrize(
        "turn",
        [
            "хочу массаж",
            "запиши меня на массаж",
            "нужен хороший мастер по маникюру",
            "где делают лимфодренаж",
            "хочу массаж спины",
            "классический массаж пожалуйста",
            "есть окошко на массаж",
        ],
    )
    def test_a_pure_master_request_is_claimed(self, turn: str) -> None:
        assert decide(turn).claimed is True, decide(turn)

    @pytest.mark.parametrize(
        "turn",
        [
            "найди мне салоны массажа",
            "сколько стоит массаж",
            "болит спина хочу массаж",
            "можно ли массаж при беременности",
            "хочу массаж чтобы похудеть к отпуску",
            "массаж это больно",
        ],
    )
    def test_an_unaccounted_word_hands_the_turn_over(self, turn: str) -> None:
        result = decide(turn)
        assert result.claimed is False
        assert result.residue, "the refusal must name the words it could not place"

    def test_a_turn_naming_no_service_is_not_ours(self) -> None:
        result = decide("какие у вас салоны")
        assert result.claimed is False
        assert result.reason == "no_service_named"

    def test_a_blank_turn_is_not_ours(self) -> None:
        assert decide("   ").reason == "empty"

    def test_the_reason_names_the_tool_when_claimed(self) -> None:
        assert decide("хочу массаж").reason == "show_masters"


class TestCompositeIsNowAClauseNotAnException:
    """DRF-1312 survives the inversion — as a rule, not as a subtraction.

    Its shape leaves no unaccounted word when both halves name services
    («массаж и маникюр»), so the residue rule alone would claim it. It must
    not: this branch forwards the raw turn as ONE OR-ranked substring, so the
    half nobody offers scores zero and vanishes.
    """

    def test_two_services_named_at_once_go_to_the_model(self, penza: None) -> None:
        result = decide("хочу массаж и маникюр")
        assert result.claimed is False
        assert result.reason == "composite_request"

    def test_the_live_turn_of_23_08(self, penza: None) -> None:
        assert (
            claims_direct_show_masters("давай будет несколько: массаж классика, и маникюр") is False
        )

    def test_a_city_is_not_a_second_service(self, penza: None) -> None:
        """«массаж, пенза» is one service in a place — it must stay cheap."""
        assert claims_direct_show_masters("массаж, пенза") is True


class TestVocabularyDoesNotOverlapTheTools:
    """The fast path's glue words must not be another tool's topic.

    The bug being fixed is one module widening a word list without knowing
    who else reads it — ``apps.marketplace.discovery._FILLER_TOKENS`` lists
    «салон», «услуга» and «мастер» as filler, which is correct THERE (it can
    only widen a catalog search) and would be DRF-1328 all over again here.
    """

    @pytest.mark.parametrize(
        "word",
        ["салон", "салоны", "салона", "салоне", "услуга", "услуги", "цена", "стоит", "прайс"],
    )
    def test_another_tools_topic_is_never_glue(self, word: str) -> None:
        assert fast_path._is_request_word(word) is False, (
            f"«{word}» is treated as glue by the claim parser, so a turn "
            "containing it can be claimed. That word belongs to another "
            "concierge tool."
        )
