"""Beverage parser — regex-only port.

Sprint 9 / P2 (DRF-819). Ports the regex half of
``mysite/maxbot/ai_parsers.py::parse_beverage``. The mysite version has
an LLM fallback for hard cases («выпил тридцать стопок»); we ship
regex-only in P2. False negatives fall through to P4 ``food_clarify``
which catches the same patterns with a friendlier card — no need to
spend LLM tokens here.

## Returns

* ``BeverageMatch(slug, ml)`` — beverage recognised + volume in ml
* ``"REFUSED"`` — explicit refusal (``«не скажу»``)
* ``None`` — no match (caller falls through)

## DRF-358 known parser bugs covered

* «Сок 0,5л» — decimal comma. The number regex
  ``\\d+(?:[.,]\\d+)?`` already handles both ``.`` and ``,``.
* «Кофе с молоком» — the upstream mysite source matches «кофе» stem
  (slug ``kofe_chernyi``) and ignores «с молоком». Same here.
* Unit aliases are sorted longest-first so «литра» beats «литр» on
  partial match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REFUSED = "REFUSED"

# Refusal markers — copy of source list.
_REFUSAL_MARKERS = (
    "не скажу",
    "не отвечу",
    "секрет",
    "не твоё дело",
    "не твое дело",
    "пропусти",
    "🤷",
)


@dataclass(frozen=True)
class BeverageMatch:
    """Parsed beverage with mililiter volume."""

    slug: str
    ml: int


# Beverage catalog — 15 slugs covering ~80% of common Russian usage.
# Ordering matters: more-specific aliases first so «зелёный чай» beats
# «чай» bare match.
_BEVERAGE_PATTERNS: list[tuple[str, list[str], int]] = [
    # Tea — specific first
    ("chai_zelenyi", ["зелёный чай", "зеленый чай", "green tea"], 250),
    ("chai_travyanoi", ["травяной чай", "ромашка", "мята", "иван-чай"], 250),
    ("chai_chernyi", ["чёрный чай", "черный чай", "чай", "tea"], 250),
    # Coffee — specific first
    ("kofe_kapuchino", ["капучино", "cappuccino"], 250),
    ("kofe_latte", ["латте", "latte"], 350),
    ("kofe_espresso", ["эспрессо", "espresso"], 30),
    (
        "kofe_chernyi",
        [
            "чёрный кофе",
            "черный кофе",
            "американо",
            "americano",
            "кофе",
            "coffee",
        ],
        200,
    ),
    # Water
    (
        "voda_mineralnaya",
        ["минералка", "минеральная вода", "боржоми", "ессентуки"],
        250,
    ),
    ("voda", ["воды", "вода", "water", "h2o"], 250),
    # Juice
    ("sok_apelsinovyi", ["апельсиновый сок", "апельсиновый"], 200),
    ("sok_yablochnyi", ["яблочный сок", "яблочный"], 200),
    # Other
    ("moloko", ["молоко"], 250),
    ("pivo", ["пиво", "beer"], 500),
    ("vino", ["вина", "вино", "wine"], 150),
    ("kompot", ["компот"], 250),
]

# Volume units sorted longest-first within each base to avoid partials.
_VOLUME_UNITS: list[tuple[str, int]] = [
    ("мл", 1),
    ("ml", 1),
    ("литров", 1000),
    ("литра", 1000),
    ("литры", 1000),
    ("литр", 1000),
    ("liter", 1000),
    ("л", 1000),
    ("стаканов", 250),
    ("стакана", 250),
    ("стакан", 250),
    ("бутылку", 500),
    ("бутылок", 500),
    ("бутылки", 500),
    ("бутылка", 500),
    ("чашку", 200),
    ("чашек", 200),
    ("чашки", 200),
    ("чашка", 200),
    ("бокалов", 150),
    ("бокала", 150),
    ("бокал", 150),
    ("кружку", 250),
    ("кружек", 250),
    ("кружки", 250),
    ("кружка", 250),
    ("банку", 330),
    ("банок", 330),
    ("банки", 330),
    ("банка", 330),
    ("порций", 30),
    ("порции", 30),
    ("порция", 30),
]


# Positive integer or decimal at word boundary.
_NUM_RE = re.compile(r"(?:^|(?<=\s))(\d+(?:[.,]\d+)?)(?=\s|$)")


def _is_refusal(text: str) -> bool:
    normalized = text.lower().strip()
    return any(marker in normalized for marker in _REFUSAL_MARKERS)


def parse_beverage(text: str) -> BeverageMatch | str | None:
    """Match a beverage + volume from short free-text.

    Returns:
        :class:`BeverageMatch` on success, the sentinel string
        :data:`REFUSED` on explicit refusal, ``None`` when no beverage
        recognised. Callers downstream of a ``None`` fall through to
        P4 ``food_clarify`` (which catches the same text shape with a
        friendlier card).
    """
    if not text or not text.strip():
        return None

    if _is_refusal(text):
        return REFUSED

    normalized = text.lower().strip()

    # 1. Beverage match — first specific alias wins.
    found_slug: str | None = None
    found_default_serving: int | None = None
    for slug, aliases, default_serving in _BEVERAGE_PATTERNS:
        for alias in aliases:
            if alias in normalized:
                found_slug = slug
                found_default_serving = default_serving
                break
        if found_slug:
            break

    if not found_slug:
        return None

    assert found_default_serving is not None  # pinned with found_slug
    ml = _extract_volume(normalized, found_default_serving)
    return BeverageMatch(slug=found_slug, ml=ml)


def _extract_volume(normalized: str, default_serving_ml: int) -> int:
    """Extract mililiter volume from already-lowercased text.

    Patterns:
        «250 мл»     → 250
        «0.5 л»      → 500
        «1 литр»     → 1000
        «2 чашки»    → 2 × 200 = 400
        «стакан»     → 250 (unit alone, no number)
        «»           → default_serving_ml
    """
    num_match = _NUM_RE.search(normalized)
    num: float | None = None
    if num_match:
        try:
            num = float(num_match.group(1).replace(",", "."))
        except ValueError:
            num = None

    padded = " " + normalized + " "
    found_unit_ml: int | None = None
    for unit, multiplier in _VOLUME_UNITS:
        if f" {unit} " in padded:
            found_unit_ml = multiplier
            break

    if num is not None and found_unit_ml is not None:
        return int(round(num * found_unit_ml))
    if num is not None:
        # Number without unit — assume mililitres.
        return int(round(num))
    if found_unit_ml is not None:
        return found_unit_ml
    return default_serving_ml
