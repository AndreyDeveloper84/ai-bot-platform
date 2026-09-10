"""§13.2 — one question, one id, whatever channel it arrives through.

The failure being guarded against was named to this lane twice before a line of
code existed: if a tap produced a different identity from typing, a person would
answer with a button and the text branch would ask the same thing again. That is
DRF-1542 rebuilt out of channels instead of turns.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.decision_readiness import questions as q


def _entry(**kwargs: object) -> q.QuestionCatalogEntry:
    defaults: dict[str, object] = {
        "kind": q.QuestionKind.DISCRIMINATION,
        "target_slots": ("body_area",),
        "mode": q.MODE_CONFIRM_ONE,
        "semantics_version": 1,
        "discriminator_key": "price_band",
        "answer_domain": ("low", "high"),
    }
    defaults.update(kwargs)
    return q.QuestionCatalogEntry(**defaults)  # type: ignore[arg-type]


# --- what the id is made of --------------------------------------------------


def test_the_id_is_sixteen_hex_characters() -> None:
    qid = _entry().question_id

    assert len(qid) == 16
    assert all(char in "0123456789abcdef" for char in qid)


def test_the_same_question_asked_by_tap_or_by_text_has_one_id() -> None:
    """No surface and no answer channel enters the derivation. Deliberately."""

    import inspect

    params = set(inspect.signature(q.question_id).parameters)

    assert params == {"kind", "target_slots", "discriminator_key", "semantics_version"}
    assert "surface" not in params
    assert "mode" not in params


def test_wording_does_not_change_the_id() -> None:
    """A model rephrasing the same question must not produce a new question."""

    plain = _entry(options=(q.SemanticOption("o1", q.OptionRole.CHOICE, "pick_low", "Подешевле"),))
    reworded = _entry(
        options=(q.SemanticOption("o1", q.OptionRole.CHOICE, "pick_low", "Что-то бюджетное"),)
    )

    assert plain.question_id == reworded.question_id


def test_option_order_does_not_change_the_id() -> None:
    a = q.SemanticOption("o1", q.OptionRole.CHOICE, "low")
    b = q.SemanticOption("o2", q.OptionRole.CHOICE, "high")

    assert _entry(options=(a, b)).question_id == _entry(options=(b, a)).question_id


def test_slot_order_does_not_change_the_id_but_the_slot_set_does() -> None:
    """§13.2: a narrowed question is a different question."""

    two_slots_one_way = _entry(target_slots=("body_area", "onset_context"))
    two_slots_other_way = _entry(target_slots=("onset_context", "body_area"))
    one_slot = _entry(target_slots=("body_area",))

    assert two_slots_one_way.question_id == two_slots_other_way.question_id
    assert one_slot.question_id != two_slots_one_way.question_id


def test_semantics_version_changes_the_id() -> None:
    """The only controlled way to reset the ledger — and it takes a catalog edit,
    not a deploy (§13.2)."""

    assert _entry(semantics_version=1).question_id != _entry(semantics_version=2).question_id


def test_policy_version_is_not_part_of_the_id() -> None:
    """Moving a threshold does not buy the right to ask again (§18.1).

    `policy_version` is not a parameter of the derivation at all, so there is no
    code path by which it could leak in.
    """

    import inspect

    assert "policy_version" not in inspect.signature(q.question_id).parameters


def test_kind_and_discriminator_change_the_id() -> None:
    assert _entry(kind=q.QuestionKind.BROADENING).question_id != _entry().question_id
    assert _entry(discriminator_key="duration").question_id != _entry().question_id


def test_required_context_must_not_carry_a_discriminator() -> None:
    with pytest.raises(ValueError, match="discriminates nothing"):
        _entry(kind=q.QuestionKind.REQUIRED_CONTEXT, discriminator_key="price_band")


# --- the catalog -------------------------------------------------------------


def test_a_question_outside_the_catalog_is_not_askable() -> None:
    """Z3 (§7): the engine blocks rather than asking something nobody approved."""

    catalog = q.QuestionCatalog(entries=(_entry(),))

    assert catalog.contains(_entry().question_id)
    assert not catalog.contains("0000000000000000")
    assert catalog.get("0000000000000000") is None


def test_the_default_catalog_is_empty() -> None:
    assert q.EMPTY_CATALOG.entries == ()


def test_only_the_three_existing_modes_are_accepted() -> None:
    with pytest.raises(ValueError, match="no fourth"):
        _entry(mode="pick_a_number")

    assert q.CLARIFICATION_MODES == {"confirm_one", "choose_many", "free"}


def test_a_question_with_no_target_slot_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be answered"):
        _entry(target_slots=())


# --- parity with the code that already has these values ----------------------


def test_the_three_modes_match_apps_orchestrator_discovery() -> None:
    """§1.1 — the engine keeps literals so it does not import the LLM contour
    (§17.2), and this is the guard that catches the two drifting apart.

    Imported inside the test on purpose: at module import time the engine must
    stay clear of `discovery`, and a test is not the engine.
    """

    from apps.orchestrator import discovery

    assert q.MODE_CONFIRM_ONE == discovery.CLARIFICATION_MODE_CONFIRM_ONE
    assert q.MODE_CHOOSE_MANY == discovery.CLARIFICATION_MODE_CHOOSE_MANY
    assert q.MODE_FREE == discovery.CLARIFICATION_MODE_FREE


def test_option_roles_match_canon_13_1() -> None:
    """Canon §13.1 lists seven. An eighth would be a semantic option the surface
    contract has never heard of."""

    assert {role.value for role in q.OptionRole} == {
        "choice",
        "delegate",
        "escape",
        "confirm",
        "action",
        "reaction",
        "constraint_resolution",
    }
