"""Vendor-neutral model tiers (DRF-1443).

Reference incident: 2026-08-31/09-01 pilot. ``LLM_PROVIDER=anthropic``,
transport healthy, key valid — and every turn came back

    POST https://api.anthropic.com/v1/messages -> 404 Not Found
    llm.anthropic.complete_failed model=gpt-4o-mini exc=NotFoundError

because the calling side named an OpenAI model. DRF-1437's swap fires
only on a vendor HOP; with Anthropic primary there is no hop.

### What these tests are for

The resolver's value is not "it produces a working model id" — a
function that returned the vendor default for every input would do that,
and would also make the ``model=`` argument meaningless. Each claim
below therefore has its counterpart asserted beside it:

  1. A tier name resolves to the calling vendor's own id — AND the two
     tiers resolve to DIFFERENT ids on a vendor that has two, so the
     tier is carried rather than collapsed.
  2. A foreign vendor's id is translated — AND its tier survives the
     translation, so a hopped intent call does not land on the reply
     model.
  3. An id the called vendor might own is forwarded UNCHANGED, whether
     or not it exists. This is the load-bearing one: it is what allows
     an integration test to prove a wrong name still fails, which is the
     only evidence that a passing test means "the name was honoured"
     rather than "the name was ignored".
"""

from __future__ import annotations

import pytest

from apps.llm.model_tiers import (
    TIER_FAST,
    TIER_SMART,
    resolve_model,
    vendor_of_model,
)

# The vendors' real defaults, imported rather than retyped so a model
# rename in a provider cannot leave these tests agreeing with a value
# production no longer uses.
from apps.llm.providers.anthropic_provider import (
    _DEFAULT_INTENT_MODEL as ANTHROPIC_FAST,
)
from apps.llm.providers.anthropic_provider import (
    _DEFAULT_REPLY_MODEL as ANTHROPIC_SMART,
)
from apps.llm.providers.openai_provider import (
    _DEFAULT_COMPLETION_MODEL as OPENAI_SMART,
)
from apps.llm.providers.openai_provider import (
    _DEFAULT_FAST_MODEL as OPENAI_FAST,
)


def _anthropic(model: str | None) -> str:
    return resolve_model(model, vendor="anthropic", fast=ANTHROPIC_FAST, smart=ANTHROPIC_SMART)


def _openai(model: str | None) -> str:
    return resolve_model(model, vendor="openai", fast=OPENAI_FAST, smart=OPENAI_SMART)


class TestTierNames:
    def test_tiers_resolve_to_the_called_vendors_own_ids(self):
        assert _anthropic(TIER_FAST) == ANTHROPIC_FAST
        assert _anthropic(TIER_SMART) == ANTHROPIC_SMART
        assert _openai(TIER_FAST) == OPENAI_FAST
        assert _openai(TIER_SMART) == OPENAI_SMART

    def test_the_two_tiers_stay_distinct_where_the_vendor_has_two_models(self):
        """The counterpart to the test above.

        Both tiers landing on one id would satisfy "resolves to a working
        model" while quietly putting every intent classification on the
        reply model — the cost regression this design exists to avoid.
        Asserted on Anthropic, the vendor that actually has two tiers;
        OpenAI's two are equal today and that equality is pinned as a
        known fact in :class:`TestOpenAITiersAreEqualToday`.
        """
        assert _anthropic(TIER_FAST) == "claude-haiku-4-5"
        assert _anthropic(TIER_SMART) == "claude-sonnet-4-6"
        assert _anthropic(TIER_FAST) != _anthropic(TIER_SMART)

    def test_tier_name_is_case_insensitive(self):
        assert _anthropic("FAST") == ANTHROPIC_FAST
        assert _anthropic("Smart") == ANTHROPIC_SMART


class TestOpenAITiersAreEqualToday:
    def test_both_openai_tiers_are_the_model_production_already_used(self):
        """Pinned so the fix cannot smuggle in a price change.

        Every OpenAI completion in this codebase ran on ``gpt-4o-mini``
        before DRF-1443. If either tier moved off it, the ticket would
        have changed what the owner pays on the vendor he is about to
        return to — which was never asked for.
        """
        assert OPENAI_FAST == "gpt-4o-mini"
        assert OPENAI_SMART == "gpt-4o-mini"


class TestCrossVendorTranslation:
    def test_openai_id_reaching_anthropic_becomes_an_anthropic_id(self):
        """The live outage, in one line."""
        resolved = _anthropic("gpt-4o-mini")
        assert resolved.startswith("claude-")
        assert resolved == ANTHROPIC_FAST

    def test_translation_preserves_the_tier(self):
        assert _anthropic("gpt-4o-mini") == ANTHROPIC_FAST
        assert _anthropic("gpt-4o") == ANTHROPIC_SMART
        assert _openai("claude-haiku-4-5") == OPENAI_FAST
        assert _openai("claude-sonnet-4-6") == OPENAI_SMART

    def test_unknown_member_of_a_foreign_family_lands_on_the_reply_tier(self):
        """A model we have never heard of, from the other vendor.

        Resolvable only by family, so the tier is a guess — and the guess
        is deliberately the expensive one. This path runs on a call that
        would otherwise 404; serving a customer a cheaper model than the
        caller wanted is a worse trade than serving a pricier one.
        """
        assert _anthropic("gpt-5-ultra-preview") == ANTHROPIC_SMART

    def test_resolution_is_idempotent(self):
        """The router resolves on a hop and the provider resolves again on
        arrival. Running it twice must equal running it once.
        """
        once = _anthropic("gpt-4o-mini")
        assert once == ANTHROPIC_FAST
        assert _anthropic(once) == once


class TestWrongNamesSurvive:
    """The positive guard. See the module docstring, claim 3."""

    def test_a_nonexistent_model_in_the_called_vendors_own_family_is_forwarded_verbatim(self):
        """The mutation detector for every other test in this tree.

        ``claude-does-not-exist-9`` is a name Anthropic refuses with
        ``404``. If the resolver repaired it, an integration test could
        no longer tell a stack that honours ``model=`` from one that
        discards it — and DRF-1443's own false green (a probe that passed
        no model at all, so the provider supplied its own and answered
        200) would be reproducible at this layer too.
        """
        assert _anthropic("claude-does-not-exist-9") == "claude-does-not-exist-9"
        assert _openai("gpt-4o-does-not-exist") == "gpt-4o-does-not-exist"

    def test_an_id_in_no_known_family_is_forwarded_verbatim(self):
        assert _anthropic("llama-3-70b") == "llama-3-70b"
        assert _openai("mistral-large") == "mistral-large"

    def test_a_pinned_same_vendor_model_is_not_rewritten_to_the_default(self):
        """An operator pinning a specific model keeps it.

        ``gpt-4o`` on OpenAI is not the tier default; a resolver that
        normalised everything would silently downgrade the pin.
        """
        assert _openai("gpt-4o") == "gpt-4o"
        assert _anthropic("claude-haiku-4-5") == "claude-haiku-4-5"


class TestEmptyMeansTheVendorDefault:
    @pytest.mark.parametrize("empty", ["", "   ", None])
    def test_empty_resolves_to_the_reply_tier(self, empty):
        """Preserves the pre-DRF-1443 ``model or default_completion_model``.

        Every skill reads ``provider.default_completion_model`` and gets
        exactly the model it got before this ticket.
        """
        assert _anthropic(empty) == ANTHROPIC_SMART
        assert _openai(empty) == OPENAI_SMART


class TestVendorOfModel:
    def test_families_are_recognised_by_prefix(self):
        assert vendor_of_model("gpt-4o-mini") == "openai"
        assert vendor_of_model("gpt-6-whatever-ships-next") == "openai"
        assert vendor_of_model("claude-sonnet-4-6") == "anthropic"
        assert vendor_of_model("claude-opus-9") == "anthropic"

    def test_an_unrecognised_family_is_reported_as_unknown(self):
        """Positive pair for the emptiness below: the recogniser DOES
        answer for names it knows, so an empty answer is a real
        "not mine" rather than a dead function.
        """
        assert vendor_of_model("gpt-4o") == "openai"
        assert vendor_of_model("llama-3-70b") == ""
