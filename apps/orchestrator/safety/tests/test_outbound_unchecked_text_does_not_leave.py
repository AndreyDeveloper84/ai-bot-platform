"""When the outbound check cannot run, the unchecked draft does not go out.

Owner §111: «Safety uncertain → fail closed затронутой capability», not the
whole product. This module is that rule applied to exactly one capability —
the draft the check could not look at.

### Why these tests assert absence, not interception

"The crash was caught" is easy to prove and proves the wrong thing: a caught
crash that then returns the original text is precisely the behaviour this
change removes. So every test below asserts what did **not** come out — the
drafted sentence is not in the verdict, on any field — rather than that an
exception was handled.

The difference matters because the two are indistinguishable in a log. An
operator reading ``safety.outbound.check_failed`` cannot tell whether the
sentence went out; only the returned verdict can say.

### Why `check_failed` is its own category

"Replaced because it matched a banned shape" and "replaced because we never
got to look" are different states with opposite fixes: the first says the
model wrote something it should not, the second says our own check is broken.
Outward they are the same replacement line. Inward they must stay countable
apart, or a month from now nobody can tell a content problem from an outage.
"""

from __future__ import annotations

import re

import pytest

from apps.orchestrator.safety.outbound import (
    CHECK_FAILED_CATEGORY,
    REPLACEMENT_TEXT,
    evaluate_outbound,
)

#: A draft that is perfectly innocuous. Using a clean sentence is deliberate:
#: if the guard only held for drafts that would have been blocked anyway, it
#: would prove nothing about the case that matters — an unchecked draft whose
#: content nobody knows.
_DRAFT = "Завтра у вас три записи, первая в десять утра."

#: A draft that WOULD have been caught. Included so the test set covers both
#: sides of "we do not know": the guard must behave identically whether the
#: unchecked text was harmless or not, because not knowing is the point.
_DANGEROUS_DRAFT = "У вас аллергия, примите ибупрофен, телефон 8 999 123 45 67."


@pytest.fixture(params=["clean", "dangerous"])
def draft(request) -> str:
    return _DRAFT if request.param == "clean" else _DANGEROUS_DRAFT


@pytest.fixture
def broken_check(monkeypatch):
    """Break the check the way a bad regex or a runtime fault would."""
    import apps.orchestrator.safety.outbound as mod

    def boom(*_args, **_kwargs):
        raise RuntimeError("regex engine on fire")

    monkeypatch.setattr(mod.re, "search", boom)


class TestUncheckedTextDoesNotLeave:
    def test_the_draft_is_not_returned(self, draft, broken_check):
        verdict = evaluate_outbound(draft)

        assert verdict.text == REPLACEMENT_TEXT
        assert verdict.text != draft

    def test_the_draft_appears_nowhere_in_the_verdict(self, draft, broken_check):
        """Not "the text field is replaced" — the sentence is gone entirely.

        A verdict that swapped ``text`` while carrying the original in some
        other field would pass a narrower assertion and still hand the draft
        to whoever renders the categories.
        """
        verdict = evaluate_outbound(draft)

        blob = " ".join(str(v) for v in vars(verdict).values())

        # Positive control on the absence below, and it is not ceremony: if
        # ``vars()`` ever stopped seeing this dataclass's fields, ``blob``
        # would be empty and every "not in blob" below would pass while
        # proving nothing. These two lines say the haystack is real and was
        # built from THIS verdict, so a miss underneath means the draft is
        # genuinely gone rather than never looked for.
        assert blob.strip(), "the verdict rendered to nothing — nothing was searched"
        assert REPLACEMENT_TEXT in blob, "blob does not carry this verdict's own text"

        assert draft not in blob
        # A distinctive fragment too, in case something truncates rather than
        # copies whole.
        assert "ибупрофен" not in blob
        assert "три записи" not in blob

    def test_it_is_refused_not_allowed(self, draft, broken_check):
        verdict = evaluate_outbound(draft)

        assert verdict.allowed is False
        assert verdict.blocked is True

    def test_the_reason_says_we_could_not_look(self, draft, broken_check):
        """The state has a name, and the name is not a content label."""
        verdict = evaluate_outbound(draft)

        assert verdict.categories == (CHECK_FAILED_CATEGORY,)
        assert CHECK_FAILED_CATEGORY not in {"medical", "promise", "contact"}

    def test_nothing_is_raised_into_the_turn(self, draft, broken_check):
        """The original fear is still answered: the turn does not blow up."""
        verdict = evaluate_outbound(draft)  # must not raise

        assert verdict is not None


class TestTheTwoRefusalsStayApart:
    """A matched draft and an unlooked-at draft must not become one number."""

    def test_a_content_match_is_not_labelled_check_failed(self):
        verdict = evaluate_outbound(_DANGEROUS_DRAFT)

        assert verdict.blocked
        assert verdict.text == REPLACEMENT_TEXT
        assert CHECK_FAILED_CATEGORY not in verdict.categories
        assert verdict.categories, "a content match must name what it matched"

    def test_a_broken_check_is_not_labelled_with_content(self, broken_check):
        verdict = evaluate_outbound(_DANGEROUS_DRAFT)

        assert verdict.categories == (CHECK_FAILED_CATEGORY,)

    def test_the_same_line_reaches_the_person_either_way(self, broken_check):
        """Outward one coarse answer, inward two reasons.

        The person must not be able to tell our outage from our judgement —
        that is a detail about our internals, and the honest reply is the
        same in both cases.
        """
        matched = evaluate_outbound(_DANGEROUS_DRAFT)
        # Re-run with the check broken via the fixture already applied.
        unlooked = evaluate_outbound(_DRAFT)

        assert matched.text == unlooked.text == REPLACEMENT_TEXT


class TestTheGuardStillHasItsSubject:
    """A guard whose subject disappeared reports success forever."""

    def test_the_replacement_line_still_exists_and_is_not_empty(self):
        assert REPLACEMENT_TEXT
        assert REPLACEMENT_TEXT.strip()

    def test_the_check_still_has_categories_to_check(self):
        """Zero categories would make every draft "clean" and every test here
        green for the wrong reason."""
        import apps.orchestrator.safety.outbound as mod

        assert mod._CATEGORIES, "the outbound check has no categories left"
        assert all(patterns for _label, patterns in mod._CATEGORIES)

    def test_a_clean_draft_still_goes_out_untouched(self):
        """The other half of the bargain.

        A change that refused everything would pass every assertion above and
        be worse than the defect it fixes: a filter that mangles ordinary
        replies gets switched off within a week, and then there is no filter.
        """
        verdict = evaluate_outbound(_DRAFT)

        assert verdict.allowed is True
        assert verdict.text == _DRAFT
        assert verdict.categories == ()

    def test_the_patterns_still_compile(self):
        """The crash path is reachable only through a real fault, so the
        normal path must not be quietly broken to reach it."""
        import apps.orchestrator.safety.outbound as mod

        for _label, patterns in mod._CATEGORIES:
            for pattern in patterns:
                re.compile(pattern)
