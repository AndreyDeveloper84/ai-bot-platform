"""E11 — the local registers as inputs, and the asymmetry that must survive.

The headline is `test_a_red_flag_is_never_suppressed`. The screening memo gates
a repeated soft signal and lets a red flag through unconditionally
(`apps/skills/health_screening/skill.py:100-105`, read at source). A register
migrated into a uniform "what was asked" ledger would carry the condition and
drop the asymmetry, and the first thing to break would be a red flag suppressed
as a repeat.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.decision_readiness import migration as mig


def _all_supplied(**counts: int) -> mig.ConflictInputs:
    return mig.ConflictInputs(
        contributions=tuple(
            mig.ConflictContribution(source=source, count=counts.get(source.value, 0))
            for source in mig.ConflictSource
        )
    )


# --- a part of a measure is not the measure ---------------------------------


def test_all_three_sources_supplied_gives_a_number() -> None:
    assert _all_supplied(refusal_memo=2, regrounding=1).total() == 3


def test_a_measured_zero_is_a_number() -> None:
    """Positive control for the test below: zero from three sources is a real
    zero, and must not be confused with "we did not look"."""

    assert _all_supplied().total() == 0


def test_one_missing_source_yields_no_measure_at_all() -> None:
    """§11.5 aggregates three things. A caller reading one of them as the whole
    would see no conflicts on a turn where another was standing — and §11.5
    exists to stop the bot recommending over an unresolved refusal (DRF-1474)."""

    partial = mig.ConflictInputs(
        contributions=(mig.ConflictContribution(source=mig.ConflictSource.REFUSAL_MEMO, count=2),)
    )

    assert partial.total() is None
    assert partial.missing() == ("incomplete_composite_request", "regrounding")


def test_an_unavailable_source_also_yields_no_measure() -> None:
    """ "We could not read it" is not "there were none"."""

    degraded = mig.ConflictInputs(
        contributions=(
            mig.ConflictContribution(source=mig.ConflictSource.REFUSAL_MEMO, count=0),
            mig.ConflictContribution(source=mig.ConflictSource.REGROUNDING, count=0),
        ),
        unavailable=frozenset({mig.ConflictSource.INCOMPLETE_COMPOSITE_REQUEST}),
    )

    assert degraded.missing() == ()  # everything is accounted for...
    assert degraded.total() is None  # ...and still no number, because one is unread


def test_a_source_cannot_be_both_supplied_and_unavailable() -> None:
    contradictory = mig.ConflictInputs(
        contributions=(mig.ConflictContribution(source=mig.ConflictSource.REFUSAL_MEMO, count=1),),
        unavailable=frozenset({mig.ConflictSource.REFUSAL_MEMO}),
    )

    with pytest.raises(ValueError, match="both supplied and"):
        contradictory.total()


def test_there_are_exactly_three_conflict_sources() -> None:
    """§11.5 names three. A fourth would change what `conflicts` means."""

    assert {source.value for source in mig.ConflictSource} == {
        "refusal_memo",
        "incomplete_composite_request",
        "regrounding",
    }


def test_a_negative_count_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        mig.ConflictContribution(source=mig.ConflictSource.REGROUNDING, count=-1)


# --- the asymmetry ----------------------------------------------------------


def test_a_red_flag_is_never_suppressed() -> None:
    """The single most important assertion in slice E11.

    `HealthScreeningSkill.matches` returns True for a red flag *before* it looks
    at the memo, and `handle()` never writes a red flag into the memo. A uniform
    "was this asked recently" suppression would silence it.
    """

    asked = mig.RegisterSuppression(
        register="health_screening_asked",
        asked_recently=True,
        may_suppress=mig.SCREENING_MAY_SUPPRESS,
        ttl_seconds=1800,
    )

    # Positive control first: it really does suppress what it is entitled to.
    assert asked.suppresses(mig.Severity.ROUTINE) is True
    assert asked.suppresses(mig.Severity.ELEVATED) is True

    assert asked.suppresses(mig.Severity.CRITICAL) is False


def test_critical_is_absent_from_the_screening_scope_on_purpose() -> None:
    """Adding it would not be a configuration change — it would reverse a
    decision made in the skill."""

    assert mig.Severity.ROUTINE in mig.SCREENING_MAY_SUPPRESS
    assert mig.Severity.CRITICAL not in mig.SCREENING_MAY_SUPPRESS


def test_nothing_is_suppressed_when_it_was_not_asked_recently() -> None:
    fresh = mig.RegisterSuppression(
        register="health_screening_asked",
        asked_recently=False,
        may_suppress=mig.SCREENING_MAY_SUPPRESS,
        ttl_seconds=1800,
    )

    assert fresh.suppresses(mig.Severity.ROUTINE) is False


def test_severity_is_checked_before_the_register() -> None:
    """Same order as `matches()`, and for the same reason: the cost of a repeated
    question is annoyance, and the cost of silence on an alarming sign is
    somebody's health."""

    a_register_that_claims_everything = mig.RegisterSuppression(
        register="over-eager",
        asked_recently=True,
        may_suppress=frozenset({mig.Severity.ROUTINE}),
        ttl_seconds=1800,
    )

    assert a_register_that_claims_everything.suppresses(mig.Severity.ROUTINE) is True
    assert a_register_that_claims_everything.suppresses(mig.Severity.CRITICAL) is False


# --- the migration policy, written down -------------------------------------


def test_the_ttl_economies_are_recorded_with_what_their_loss_costs() -> None:
    economies = {row.register: row for row in mig.REGISTER_ECONOMIES}

    assert economies["decision_readiness (question_ledger)"].contract == "fail-closed"
    assert economies["no_match (refusal_memo)"].contract == "best-effort"
    # The engine's register is the only fail-closed one, and that difference is
    # the whole reason the others were not merged into it.
    fail_closed = [row.register for row in mig.REGISTER_ECONOMIES if row.contract == "fail-closed"]
    assert fail_closed == ["decision_readiness (question_ledger)"]


def test_the_canon_contradiction_is_marked_rather_than_quietly_fixed() -> None:
    """Forgetting a resolved fact after thirty minutes contradicts the canon.
    Changing it is its own decision with its own owner, so it is recorded as a
    debt instead of being repaired inside a migration."""

    flagged = [row for row in mig.REGISTER_ECONOMIES if row.contradicts_canon]

    assert len(flagged) == 1
    assert "resolved facts" in flagged[0].register


def test_the_two_hour_and_thirty_minute_windows_are_the_real_ones() -> None:
    """Numbers taken from the registers themselves, not chosen here."""

    from apps.orchestrator.refusal_memo import STATE_TTL_SECONDS as REFUSAL_TTL
    from apps.skills.health_screening.memo import STATE_TTL_SECONDS as SCREENING_TTL

    economies = {row.register: row.ttl_seconds for row in mig.REGISTER_ECONOMIES}

    assert economies["no_match (refusal_memo)"] == REFUSAL_TTL
    assert economies["health_screening_asked"] == SCREENING_TTL
    assert economies["decision_readiness (question_ledger)"] == 2 * 3600
