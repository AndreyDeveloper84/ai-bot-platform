"""Russian rouble formatting helper (DRF-842 / Phase 1 / B6).

Single-currency salon — ₽ only — so a tiny helper is enough; no
locale machinery. The ``calc_price`` tool emits prices like
``"1 500 ₽"`` (NBSP-separated thousands, ₽ symbol after the number).

A repo-wide grep for ``format_rub`` / ``format_price`` / ``format_money``
returned no existing helper at the time of writing (see B6 scope
discipline note). This module is the home for any future currency
formatting work that stays single-currency.
"""

from __future__ import annotations

from decimal import Decimal

# Non-breaking space between the number and the ₽ glyph — keeps the
# unit attached on line-wrap. Russian typographic norm.
_NBSP = " "  # U+00A0 non-breaking space


def format_rub(value: Decimal) -> str:
    """Render a Decimal as a Russian-formatted rouble string.

    Examples:
        >>> from decimal import Decimal
        >>> format_rub(Decimal("1500"))
        '1 500 ₽'
        >>> format_rub(Decimal("1500.00"))
        '1 500 ₽'
        >>> format_rub(Decimal("1500.50"))
        '1 500,50 ₽'
        >>> format_rub(Decimal("0"))
        '0 ₽'

    Rules:
      * Integer-valued amounts render without a fractional part
        (``1500`` not ``1500.00``).
      * Non-integer amounts use a comma as the decimal separator,
        two fractional digits, no trailing zeros stripped (Russian
        currency convention).
      * Thousands separator is a non-breaking space (NBSP / U+00A0).
      * Symbol is the cyrillic ₽ (U+20BD), placed after the number
        with a NBSP between.
    """
    # Quantise to 2 decimal places once so the integer-vs-fraction
    # check is unambiguous (``Decimal("1500")`` vs ``Decimal("1500.00")``
    # would otherwise stringify differently).
    quantised = value.quantize(Decimal("0.01"))
    integer_part, _, fractional_part = str(quantised).partition(".")

    sign = ""
    if integer_part.startswith("-"):
        sign = "-"
        integer_part = integer_part[1:]

    # Insert NBSP every three digits from the right.
    grouped = _group_thousands(integer_part)

    if fractional_part == "00":
        body = grouped
    else:
        body = f"{grouped},{fractional_part}"

    return f"{sign}{body}{_NBSP}₽"


def _group_thousands(digits: str) -> str:
    """Insert NBSP every three digits, right-to-left."""
    if not digits:
        return "0"
    # Walk from the right, splitting into chunks of 3.
    chunks: list[str] = []
    for i in range(len(digits), 0, -3):
        start = max(0, i - 3)
        chunks.append(digits[start:i])
    return _NBSP.join(reversed(chunks))
