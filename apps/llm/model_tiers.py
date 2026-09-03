"""Vendor-neutral model tiers (DRF-1443).

### The failure this exists to prevent

Model ids are vendor-specific and there is no translation between them.
Until this module, the id was chosen by the CALLER — and every caller
chose an OpenAI id, because OpenAI was the only vendor when they were
written. The moment the pilot switched to ``LLM_PROVIDER=anthropic`` the
live conversation path started sending ``gpt-4o-mini`` to
``api.anthropic.com`` and collecting ``404 not_found_error`` on every
turn. Transport, key, proxy and SDK were all healthy; the NAME was wrong.

DRF-1437 already solved a narrow slice of this — :func:`apps.llm.router
._retarget_model` swaps the id, but only on the quota-fallback HOP
between vendors. With Anthropic as the PRIMARY there is no hop, so the
swap never fired.

### The vocabulary

Two logical tiers, both vendor-neutral:

* :data:`TIER_FAST` — cheap, low-latency, structured-output work.
  Intent classification and the post-reply resolver. High volume.
* :data:`TIER_SMART` — the customer-facing reply tier.

A call site names a TIER. The vendor's own id for that tier is filled in
at the vendor boundary — inside the concrete provider's ``complete()`` —
which is the only place that knows which vendor actually answered, since
the router may have rerouted the skill and the quota fallback may have
hopped.

### Why this is not "the provider always substitutes its default"

That was the cheaper option and it is wrong twice over. It collapses the
two tiers into one, so every intent classification would land on the
reply-tier model (on Anthropic: ``claude-sonnet-4-6`` instead of
``claude-haiku-4-5``) on the highest-volume path in the product. And it
erases the caller's intent entirely, so a genuinely mistyped model id
would be silently rewritten into a working call — meaning a green test
could never distinguish "the name is honoured" from "the name is
ignored". That distinction is the whole point of this ticket.

### Why unknown ids are passed through UNCHANGED

:func:`resolve_model` translates exactly two things:

1. a logical tier name (``"fast"`` / ``"smart"``);
2. an id that demonstrably belongs to a DIFFERENT vendor than the one
   about to be called.

Everything else — including an id that looks like it belongs to the
CURRENT vendor but does not exist — goes to the vendor verbatim and
fails loudly there. This is deliberate and load-bearing: it is what lets
a test assert that a bogus name still produces ``404``. A resolver that
"helpfully" normalised every unrecognised id would make every such test
green on broken code, which is exactly the trap this ticket was opened
to escape.

The vendor of an id is recognised by FAMILY PREFIX, not by an
enumeration. An equivalence table with one row per model per vendor is
the design ``_retarget_model``'s docstring rightly argued against: it
goes stale on every vendor release. A prefix says "this is somebody
else's namespace" without claiming to know every name in it.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


#: Cheap / low-latency tier — intent classification, resolver drafts.
TIER_FAST = "fast"

#: Customer-facing reply tier.
TIER_SMART = "smart"

TIERS: frozenset[str] = frozenset({TIER_FAST, TIER_SMART})


#: Model-id family prefixes → the vendor that owns that namespace.
#:
#: Prefixes, not full ids, so a vendor shipping ``gpt-5-nano`` tomorrow is
#: still recognised as OpenAI's without a code change here. Only used to
#: answer "does this id belong to somebody OTHER than the vendor we are
#: about to call" — never to pick a model.
_VENDOR_PREFIXES: tuple[tuple[str, str], ...] = (
    ("gpt-", "openai"),
    ("chatgpt-", "openai"),
    ("o1-", "openai"),
    ("o3-", "openai"),
    ("o4-", "openai"),
    ("text-embedding-", "openai"),
    ("claude-", "anthropic"),
)


#: Ids whose tier we know explicitly. Anything else in a foreign family
#: resolves to :data:`TIER_SMART` — see :func:`_tier_of_foreign_model`.
_KNOWN_TIERS: dict[str, str] = {
    "gpt-4o-mini": TIER_FAST,
    "gpt-3.5-turbo": TIER_FAST,
    "gpt-4o": TIER_SMART,
    "gpt-4-turbo": TIER_SMART,
    "claude-haiku-4-5": TIER_FAST,
    "claude-sonnet-4-6": TIER_SMART,
}


def vendor_of_model(model: str) -> str:
    """Vendor that owns ``model``'s id namespace, or ``""`` if unknown."""

    lowered = (model or "").strip().lower()
    for prefix, vendor in _VENDOR_PREFIXES:
        if lowered.startswith(prefix):
            return vendor
    return ""


def _tier_of_foreign_model(model: str) -> str:
    """Tier of an id belonging to a vendor we are NOT about to call.

    An unrecognised member of a known family resolves to
    :data:`TIER_SMART`. Correct-but-pricier beats cheap-and-wrong: this
    only runs on a call that would otherwise 404, and downgrading an
    unknown model to the cheap tier could silently swap a reasoning
    model for a mini one on a customer-facing turn.
    """

    return _KNOWN_TIERS.get((model or "").strip().lower(), TIER_SMART)


def resolve_model(
    model: str | None,
    *,
    vendor: str,
    fast: str,
    smart: str,
) -> str:
    """Return the id ``vendor`` should actually be called with.

    Args:
      model: what the caller asked for. May be a logical tier name, a
        vendor id (this vendor's or another's), empty, or ``None``.
      vendor: the vendor whose API is about to be called — ``provider.name``.
      fast: that vendor's own id for :data:`TIER_FAST`.
      smart: that vendor's own id for :data:`TIER_SMART`.

    Translation happens in exactly three cases; every other input is
    returned unchanged so that a wrong name fails at the vendor instead
    of being quietly repaired. See the module docstring.
    """

    asked = (model or "").strip()

    # 1. Nothing asked for — the vendor's reply-tier default. This is the
    #    pre-existing `model or self.default_completion_model` behaviour,
    #    preserved exactly: every skill that reads
    #    `provider.default_completion_model` keeps the model it has today.
    if not asked:
        return smart

    # 2. A logical tier name. The vendor-neutral vocabulary call sites
    #    are expected to use from now on.
    lowered = asked.lower()
    if lowered in TIERS:
        return fast if lowered == TIER_FAST else smart

    # 3. A concrete id owned by a DIFFERENT vendor. This is the live
    #    pilot break: `gpt-4o-mini` arriving at the Anthropic provider,
    #    both from `ayla_ai_core.orchestrator.DEFAULT_MODEL_NAME` (a
    #    separate repo we cannot edit from here) and from any in-repo
    #    call site still naming one.
    owner = vendor_of_model(asked)
    if owner and vendor and owner != vendor:
        tier = _tier_of_foreign_model(asked)
        replacement = fast if tier == TIER_FAST else smart
        logger.warning(
            "llm.model_tiers.cross_vendor_model asked=%s owner=%s vendor=%s tier=%s using=%s",
            asked,
            owner,
            vendor,
            tier,
            replacement,
        )
        return replacement

    # Anything else — this vendor's own id, or an id in no known family.
    # Passed through verbatim ON PURPOSE: a typo must still 404.
    return asked
