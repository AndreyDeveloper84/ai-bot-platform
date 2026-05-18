"""Per-model LLM pricing helper (Phase 1 / PI9 / DRF-860).

Single source of truth for the USD cost of one ``complete`` /
``embedding`` call. Used by :mod:`apps.llm.cost_tracker` to convert a
``CompletionResult`` (or embedding token count) into a Decimal cost
that the per-tenant daily cost cap is enforced against.

### Manual price table — why

OpenAI + Anthropic publish per-model rates on their pricing pages with
no machine-readable feed. We hardcode the rates and bump them when the
vendor moves the number. The table is small (5-10 rows) and changes
~monthly; a runtime lookup would be more code than it's worth.

### Rate shape

``model_name → (input_per_1k_usd, output_per_1k_usd)`` — both as
:class:`Decimal` so the cost-tracker math stays exact (we accumulate
microcents in Redis to avoid float drift on a high-volume tenant).

For embedding models, ``output_per_1k_usd`` is set to ``Decimal("0")``
— embeddings have no completion side. The tracker still calls
:func:`compute_cost` uniformly with ``output_tokens=0``.

### Unknown models

A model not in the table is a misconfiguration. We raise
:class:`UnknownModelError` rather than silently returning zero — zero
cost would let an unknown model bypass the daily cost cap, which is
exactly the failure mode the cap exists to prevent.

### Rates as of 2026-05 (DRF-860 ship)

Sourced from openai.com/pricing and anthropic.com/pricing on the
sprint kick-off. When the vendor adjusts a number, update the entry
here and add a one-line note in the docstring with the date — keeps
the historic audit trail visible without trawling git blame.
"""

from __future__ import annotations

from decimal import Decimal

__all__ = [
    "MODEL_PRICES",
    "UnknownModelError",
    "compute_cost",
]


class UnknownModelError(KeyError):
    """Raised when :func:`compute_cost` is called with a model that is
    not in :data:`MODEL_PRICES`.

    Subclasses :class:`KeyError` so callers that already catch
    ``KeyError`` keep working, but introduces a specific class so the
    cost-tracker can log a targeted warning.
    """


# Per-1k-token USD rates. (input, output). Source: vendor pricing pages,
# 2026-05.
#
# When a new model lands in the platform, add it here BEFORE shipping
# the integration. Skipping this table is a CI-visible failure:
# :func:`compute_cost` raises on first use.
MODEL_PRICES: dict[str, tuple[Decimal, Decimal]] = {
    # --- OpenAI completion ---
    "gpt-4o-mini": (Decimal("0.00015"), Decimal("0.00060")),
    "gpt-4o": (Decimal("0.00250"), Decimal("0.01000")),
    "gpt-4-turbo": (Decimal("0.01000"), Decimal("0.03000")),
    "gpt-3.5-turbo": (Decimal("0.00050"), Decimal("0.00150")),
    # --- OpenAI embeddings (output side = 0) ---
    "text-embedding-3-small": (Decimal("0.00002"), Decimal("0")),
    "text-embedding-3-large": (Decimal("0.00013"), Decimal("0")),
    # --- Anthropic completion ---
    # Decision 18 pinned models.
    "claude-haiku-4-5": (Decimal("0.00080"), Decimal("0.00400")),
    "claude-sonnet-4-6": (Decimal("0.00300"), Decimal("0.01500")),
    # Legacy fall-back names — kept so the L5 router's older
    # configurations don't break the cost path if they slip through.
    "claude-3-5-haiku": (Decimal("0.00080"), Decimal("0.00400")),
    "claude-3-5-sonnet": (Decimal("0.00300"), Decimal("0.01500")),
    "claude-sonnet-3.5": (Decimal("0.00300"), Decimal("0.01500")),
    "claude-haiku": (Decimal("0.00080"), Decimal("0.00400")),
}


def compute_cost(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> Decimal:
    """Compute the USD cost of one call.

    Args:
      model: model id as reported by the provider (e.g.
             ``"gpt-4o-mini"``). Vendor-resolved aliases (the model
             string the API echoed back) are preferred over the
             developer-supplied alias when both are known.
      input_tokens: prompt / embedded text tokens.
      output_tokens: completion tokens. Pass 0 for embedding calls.

    Returns:
      :class:`Decimal` USD cost. Always non-negative.

    Raises:
      :class:`UnknownModelError` when ``model`` is not in
      :data:`MODEL_PRICES`.
    """
    try:
        in_rate, out_rate = MODEL_PRICES[model]
    except KeyError as exc:
        raise UnknownModelError(
            f"compute_cost: no pricing entry for model {model!r}. "
            "Add it to apps.llm.pricing.MODEL_PRICES."
        ) from exc

    # Token counts can't be negative — defensive coerce. The provider
    # SDKs occasionally return None for missing fields; the caller is
    # already supposed to default to 0 but a second guard here keeps
    # the math sane if a value slips through.
    input_tokens = max(0, int(input_tokens or 0))
    output_tokens = max(0, int(output_tokens or 0))

    return (in_rate * Decimal(input_tokens) + out_rate * Decimal(output_tokens)) / Decimal(1000)
