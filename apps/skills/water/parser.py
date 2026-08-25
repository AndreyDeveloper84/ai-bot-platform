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
    (
        "chai_zelenyi",
        ["зелёный чай", "зеленый чай", "зелёного чая", "зеленого чая", "green tea"],
        250,
    ),
    (
        "chai_travyanoi",
        [
            "травяной чай",
            "травяного чая",
            "ромашка",
            "ромашки",
            "мята",
            "мяты",
            "мятой",
            "иван-чай",
            "иван-чая",
        ],
        250,
    ),
    (
        "chai_chernyi",
        [
            "чёрный чай",
            "черный чай",
            "чёрного чая",
            "черного чая",
            "чай",
            "чая",
            "чаю",
            "чаем",
            "чайку",
            "чаёк",
            "чаек",
            "tea",
        ],
        250,
    ),
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
            "кофейку",
            "coffee",
        ],
        200,
    ),
    # Water
    (
        "voda_mineralnaya",
        [
            "минералка",
            "минералки",
            "минералку",
            "минеральная вода",
            "минеральной воды",
            "боржоми",
            "ессентуки",
        ],
        250,
    ),
    (
        "voda",
        ["воды", "вода", "воду", "водой", "воде", "водичка", "водички", "водичку", "water", "h2o"],
        250,
    ),
    # Juice
    (
        "sok_apelsinovyi",
        ["апельсиновый сок", "апельсинового сока", "апельсиновый", "апельсинового"],
        200,
    ),
    (
        "sok_yablochnyi",
        ["яблочный сок", "яблочного сока", "яблочный", "яблочного"],
        200,
    ),
    # Other
    ("moloko", ["молоко", "молока", "молоком", "молоке", "молочка"], 250),
    ("pivo", ["пиво", "пива", "пивом", "пиве", "пивка", "пивко", "beer"], 500),
    ("vino", ["вино", "вина", "вину", "вином", "вине", "wine"], 150),
    ("kompot", ["компот", "компота", "компотом", "компоте"], 250),
]


# ─── DRF-1404 — an alias is a WORD, not a substring ───────────────────────
#
# The catalog was matched with a bare ``alias in normalized``. Measured
# on 2026-08-25, that logged a drink for «случайно проспала», «чайник
# сломался», «чайная ложка сахара», «чайхана», «случайность», «кофта
# помятая», «измятая юбка» and «молокозавод» — the «чай» / «мята» /
# «молоко» aliases living inside longer words.
#
# This is the DRF-973 defect class, but it is the expensive member of
# the family: :meth:`~apps.skills.water.skill.WaterSkill.handle` calls
# ``add_water`` on a match, so a false positive does not merely answer
# oddly — it WRITES to the user's Ayla beverage diary and stays there.
# Hence the direction of every judgement call below: when a phrase is
# genuinely ambiguous we decline to log it. A missed drink costs one
# re-typed line; a phantom one silently corrupts a health record.
#
# The fix is the rule this bot already uses for pain stems: the set of
# words that merely CONTAIN a drink name is open, the set of drink
# forms is closed and short. So the forms are enumerated in the catalog
# above (that is why «чая», «чаю», «пива», «молока» appear — the
# substring rule matched those only by accident, and «стакан чая» /
# «выпил пива» were in fact NOT parsed at all before this patch), and
# each is matched between letter boundaries.
def _word_pattern(alias: str) -> re.Pattern[str]:
    """Whole-word matcher for one alias.

    Letter boundaries rather than ````: an alias may legitimately sit
    against a digit or punctuation («0.5л воды», «чай, кофе»), but never
    inside a longer word.
    """

    return re.compile(
        r"(?<![^\W\d_])" + re.escape(alias) + r"(?![^\W\d_])",
        re.IGNORECASE,
    )


# ─── DRF-1404 — «вина» is a homograph, and no boundary can split it ───────
#
# «вина» is BOTH the genitive of «вино» («бокал вина») and the
# nominative of «вина», guilt («это моя вина»). Same for «вину»
# («признать вину») and «вине» («по вине водителя»). A word boundary
# does not help: both readings are whole words, spelled identically.
#
# «это моя вина» → 150 ml of wine in the diary was the single worst
# observed case of this ticket, so these three forms are the one place
# where a POSITIVE cue is required rather than a negative one: they
# count as wine only when the message also carries drinking context —
# a volume unit, a number, or a verb of drinking/pouring/ordering.
# «вино» and «вином» are unambiguous and need no cue.
#
# Deliberately biased toward not logging: «вина было много» is missed,
# and that is the correct trade for a write path.
_CONTEXT_REQUIRED_ALIASES: frozenset[str] = frozenset({"вина", "вину", "вине"})

_DRINK_CONTEXT = re.compile(
    r"\d"
    r"|(?<![^\W\d_])(?:"
    r"бокал\w*|стакан\w*|бутыл\w*|кружк\w*|банк[аиуое]\w*|рюмк\w*|фужер\w*"
    r"|глоток|глотк\w*|литр\w*|мл"
    r"|пил|пила|пили|пью|пьём|пьем|выпил\w*|допил\w*|попил\w*|отпил\w*"
    r"|нал(?:ей|ил|ила|ила)\w*|заказал\w*|дегустир\w*|пригуб\w*"
    r")(?![^\W\d_])",
    re.IGNORECASE,
)


# Compiled once at import — the catalog is static.
_COMPILED_BEVERAGES: list[tuple[str, tuple[tuple[str, re.Pattern[str]], ...], int]] = [
    (slug, tuple((alias, _word_pattern(alias)) for alias in aliases), serving)
    for slug, aliases, serving in _BEVERAGE_PATTERNS
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
    has_drink_context: bool | None = None
    for slug, aliases, default_serving in _COMPILED_BEVERAGES:
        for alias, pattern in aliases:
            if not pattern.search(normalized):
                continue
            if alias in _CONTEXT_REQUIRED_ALIASES:
                if has_drink_context is None:
                    has_drink_context = bool(_DRINK_CONTEXT.search(normalized))
                if not has_drink_context:
                    continue
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
