"""format_rub helper tests (DRF-842 / Phase 1 / B6)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.promotions.formatting import format_rub


NBSP = " "


class TestFormatRub:
    def test_integer_value(self) -> None:
        assert format_rub(Decimal("1500")) == f"1{NBSP}500{NBSP}₽"

    def test_integer_value_with_explicit_two_decimals(self) -> None:
        assert format_rub(Decimal("1500.00")) == f"1{NBSP}500{NBSP}₽"

    def test_fractional_value(self) -> None:
        assert format_rub(Decimal("1500.50")) == f"1{NBSP}500,50{NBSP}₽"

    def test_zero(self) -> None:
        assert format_rub(Decimal("0")) == f"0{NBSP}₽"

    def test_small_value_no_grouping(self) -> None:
        assert format_rub(Decimal("99")) == f"99{NBSP}₽"

    def test_large_value_multiple_groups(self) -> None:
        assert format_rub(Decimal("1234567")) == f"1{NBSP}234{NBSP}567{NBSP}₽"

    @pytest.mark.parametrize(
        "value,expected_body",
        [
            (Decimal("1000"), f"1{NBSP}000"),
            (Decimal("10000"), f"10{NBSP}000"),
            (Decimal("100000"), f"100{NBSP}000"),
            (Decimal("1000000"), f"1{NBSP}000{NBSP}000"),
        ],
    )
    def test_grouping_boundaries(self, value: Decimal, expected_body: str) -> None:
        assert format_rub(value) == f"{expected_body}{NBSP}₽"

    def test_negative_value_keeps_sign(self) -> None:
        # Not a likely production input (we never show negative prices),
        # but the formatter shouldn't choke on Decimal arithmetic edge
        # cases (e.g. a price after a hypothetical credit applied).
        assert format_rub(Decimal("-100")) == f"-100{NBSP}₽"
