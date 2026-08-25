"""Food/drink hint detector — pure regex, no LLM.

Sprint 9 / P4 (DRF-821). Ported from ``mysite/maxbot/food_drink_hints.py``
(DRF-358 T01, mysite PR #145). Decides whether a short free-text message
looks like a food/drink log attempt — used by the FAQ-clarify skill to
emit the diary-or-typo card BEFORE the AI Concierge sees a "Борщ 300г"
and produces a cold "не могу с заказом" reply.

## Detection rule

A message matches when **all** of:

* ``len(text.strip()) ≤ 30`` chars — longer messages are full questions,
  not food log attempts.
* The message either:
  * contains a stem from :data:`FOOD_HINT_WORDS` / :data:`DRINK_HINT_WORDS`, OR
  * matches the ``<number> <unit>`` pattern (``250г``, ``0,5л``, ``2 шт``).

Type-tolerant: non-``str`` returns ``False``.

## False positives are fine

By design, users tap «Опечатка» on a false-positive card; the cost is
one tap. False **negatives** are the bad case — a missed food mention
gets the cold LLM fallback. The hint list is generous on purpose; rare
collisions ("суп" inside "суперинтересно") get filtered by the length
cap (30 chars) and short-form expectation.

## Why a stem list, not full-form

«котлет» matches "котлета" / "котлеты" / "котлету" / etc. Cheaper than
declension-aware NLP for one-shot user messages where грамматика varies.
"""

from __future__ import annotations

import re
from typing import Any

# ─── word stems (matched at a word start — see _hint_pattern) ────────────

FOOD_HINT_WORDS: frozenset[str] = frozenset(
    {
        # Liquids / firsts
        "борщ",
        "суп",
        "щи",
        "окрошк",
        "бульон",
        "уха",
        # Meat / poultry / fish
        "котлет",
        "пельмен",
        "вареник",
        "стейк",
        "шашлык",
        "курин",
        "рыб",
        "куриц",
        "говядин",
        "свинин",
        "бекон",
        "сосиск",
        "колбас",
        # Grains / sides
        "гречк",
        "рис",
        "макарон",
        "паст",
        "лапш",
        "плов",
        "пюре",
        "каш",
        # Vegetables / salads
        "салат",
        "винегрет",
        "капуст",
        "огурц",
        "помидор",
        "морковк",
        "картош",
        # Bread / baking
        "хлеб",
        "булк",
        "пирож",
        "пирог",
        "блин",
        "оладь",
        "сырник",
        "вафл",
        # Breakfast
        "омлет",
        "яичниц",
        "яйц",
        "творог",
        "сырок",
        "хлопь",
        "мюсл",
        # Sweets
        "конфет",
        "шоколад",
        "пирожн",
        "тортик",
        "торт",
        "мороженое",
        # Misc
        "пицц",
        "сэндвич",
        "бургер",
        "роллы",
        "суши",
    }
)


DRINK_HINT_WORDS: frozenset[str] = frozenset(
    {
        # Items the upstream beverage parser misses (DRF-358 known bugs).
        "квас",
        "лимонад",
        "морс",
        "компот",
        "кисель",
        "кефир",
        "ряженк",
        "ряженка",
        "снежок",
        "тан",
        "смузи",
        "коктейл",
        "молочный",
        "айран",
        # When beverage parser format-misses ("Сок 0,5л" decimal-comma,
        # "вода в бутылке" — Bug 1 from 2026-05-08 dev-bot smoke).
        "сок",
        "вод",
    }
)


# ─── DRF-1404 — a stem is a WORD, not a substring ─────────────────────────
#
# The lists above were matched with a bare ``stem in lower``. Measured on
# 2026-08-25, that offered the food card for «правильное питание» («тан»),
# «нет повода» / «завод рядом» / «провод оборвался» («вод»), «высокий
# рост» / «носок порвался» («сок»), «вещи в шкафу» («щи»), «муха летает»
# («уха») and a dozen more — the stem living INSIDE a longer word.
#
# The module's bias is deliberate and is KEPT: a false positive costs one
# tap on «Опечатка», a false negative costs a cold LLM fallback on a real
# meal. So this fix only ever removes a match that no reading of the
# message could call food, and never narrows a stem without pinning its
# food back in ``tests/test_hints.py::FOOD_PHRASES``.
#
# Two tiers, because a word START is not enough on its own:
#
# 1. Every stem must begin a word. This closes the open class above.
# 2. The stems below ALSO collide at a word start, so they additionally
#    require one of their real endings — the same rule the pain
#    classifier uses for «бол» (DRF-973). They are all short, and short
#    is exactly when a Russian stem stops being distinctive.
#
# «кашель» is the one that made this tier necessary: «кашель третий
# день» was answered with «записать в дневник питания?», so a symptom
# was spent on the wrong skill. That is not a cheap tap.
_STEM_TAILS: dict[str, str] = {
    # каша / каши / кашу / кашей / кашка — NOT кашель, кашне
    "каш": r"(?:[аиу]|ей|к[аиу])?",
    # суп / супа / супчик — NOT суперинтересно
    "суп": r"(?:[ауые]|ом|ов|чик\w*)?",
    # рис / риса / рисовая — NOT риск, рискну, рисую
    "рис": r"(?:[ауе]|ом|ов\w*)?",
    # паста / пасты / пастой — NOT пасть, пастельный, паства
    "паст": r"(?:[аыуе]|ой|ам|ами)?",
    # уха — NOT ухаживаю (the genitive «ухи» is given up on purpose:
    # «уха» is rare enough that widening it back would cost more than
    # it buys)
    "уха": r"",
    # тан (the drink) — NOT танцы, танго, танк
    "тан": r"(?:[ае]|ом)?",
    # плов / плова — NOT пловец
    "плов": r"(?:[ауе]|ом)?",
    # щи — NOT щиколотка, щит
    "щи": r"",
    # вода / воды / водичка — NOT водитель
    "вод": r"(?:[аыуе]|ой|ичк\w*)?",
    # сок / сока / соки — NOT сокращаю, сокол
    "сок": r"(?:[ауеи]|ом|ов|ами|ах)?",
}


def _hint_pattern(stem: str) -> re.Pattern[str]:
    """Word-start matcher for one stem.

    Stems listed in :data:`_STEM_TAILS` are matched as a WHOLE word —
    the stem plus one of its own endings and nothing else.

    Letter boundaries rather than ````: «250г борща» must still match
    across the digit/letter seam.
    """

    head = r"(?<![^\W\d_])" + re.escape(stem)
    tail = _STEM_TAILS.get(stem)
    if tail is None:
        return re.compile(head, re.IGNORECASE)
    return re.compile(head + tail + r"(?![^\W\d_])", re.IGNORECASE)


_FOOD_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    _hint_pattern(stem) for stem in sorted(FOOD_HINT_WORDS)
)
_DRINK_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    _hint_pattern(stem) for stem in sorted(DRINK_HINT_WORDS)
)


# Number + unit pattern. Stem matching for unit names handles "г" / "грамм" /
# "гр" / "стакан" / "стакана" / etc.
_NUM_UNIT_PATTERN = re.compile(
    r"\d+[.,]?\d*\s*(?:г|гр|грамм|мл|л|шт|порц|стакан|чашк|ложк)\b",
    re.IGNORECASE,
)

# Length cap — longer free-text is a question, not a food log.
_MAX_LEN = 30


def looks_like_food_drink(text: Any) -> bool:
    """Return ``True`` iff ``text`` looks like a short food/drink log attempt.

    Type-tolerant: anything that's not a ``str`` returns ``False``. The
    pipeline may hand us None or bytes from upstream; we never raise.
    """
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped or len(stripped) > _MAX_LEN:
        return False

    lower = stripped.lower()
    for pattern in _FOOD_PATTERNS:
        if pattern.search(lower):
            return True
    for pattern in _DRINK_PATTERNS:
        if pattern.search(lower):
            return True
    if _NUM_UNIT_PATTERN.search(lower):
        return True
    return False
