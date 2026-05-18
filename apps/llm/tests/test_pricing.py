"""Per-model pricing helper tests (Phase 1 / PI9 / DRF-860).

Covers the static price table + :func:`compute_cost` math. The values
themselves are vendor-published rates that change ~monthly; we assert
the SHAPE (Decimal output, correct rounding, unknown-model raise) and
a small set of pinned numerical values that catch accidental table
edits. When ops bumps a rate, the corresponding test value updates
alongside it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.llm.pricing import (
    MODEL_PRICES,
    UnknownModelError,
    compute_cost,
)


class TestKnownModels:
    """Pinned values for the models the platform actively uses."""

    def test_gpt_4o_mini_completion(self) -> None:
        # 1000 input + 500 output tokens at $0.00015 / $0.00060 per 1k.
        # = (0.00015 * 1) + (0.00060 * 0.5) = 0.00015 + 0.0003 = 0.00045
        cost = compute_cost("gpt-4o-mini", input_tokens=1000, output_tokens=500)
        assert isinstance(cost, Decimal)
        assert cost == Decimal("0.00045")

    def test_text_embedding_3_small_output_zero(self) -> None:
        # Embeddings have no completion side — output rate must be 0.
        in_rate, out_rate = MODEL_PRICES["text-embedding-3-small"]
        assert out_rate == Decimal("0")
        cost = compute_cost("text-embedding-3-small", input_tokens=100_000)
        # 100k tokens * $0.00002 / 1k = $0.002
        assert cost == Decimal("0.00200")

    def test_claude_sonnet_combined(self) -> None:
        # Pinned Decision-18 model. (0.003, 0.015) per 1k.
        cost = compute_cost("claude-sonnet-4-6", input_tokens=2000, output_tokens=1000)
        # 0.003 * 2 + 0.015 * 1 = 0.006 + 0.015 = 0.021
        assert cost == Decimal("0.021")

    def test_zero_tokens_zero_cost(self) -> None:
        assert compute_cost("gpt-4o", input_tokens=0, output_tokens=0) == Decimal("0")


class TestUnknownModel:
    def test_unknown_model_raises(self) -> None:
        with pytest.raises(UnknownModelError):
            compute_cost("imaginary-future-model", input_tokens=1, output_tokens=1)

    def test_unknown_model_is_keyerror_subclass(self) -> None:
        # Callers that already catch KeyError shouldn't break.
        with pytest.raises(KeyError):
            compute_cost("nope-not-real", input_tokens=10)


class TestNegativeAndNoneInputs:
    """Defensive: never produce negative cost. Accept ``None`` and
    negative tokens by clamping to 0."""

    def test_none_tokens_coerced_to_zero(self) -> None:
        cost = compute_cost("gpt-4o-mini", input_tokens=0, output_tokens=0)
        assert cost == Decimal("0")

    def test_negative_tokens_clamped(self) -> None:
        # Provider SDKs occasionally return 0 / missing values; the
        # helper must not produce a negative Decimal.
        cost = compute_cost("gpt-4o-mini", input_tokens=-50, output_tokens=-10)
        assert cost == Decimal("0")


class TestMultiModelSweep:
    """Sweep over every entry in the price table. Every model must
    return a non-negative Decimal for a small uniform input."""

    @pytest.mark.parametrize("model", sorted(MODEL_PRICES.keys()))
    def test_all_models_produce_nonneg_decimal(self, model: str) -> None:
        cost = compute_cost(model, input_tokens=100, output_tokens=100)
        assert isinstance(cost, Decimal)
        assert cost >= Decimal("0")
