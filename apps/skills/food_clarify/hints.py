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

# ─── word stems (case-insensitive substring match) ────────────────────────

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
    for stem in FOOD_HINT_WORDS:
        if stem in lower:
            return True
    for stem in DRINK_HINT_WORDS:
        if stem in lower:
            return True
    if _NUM_UNIT_PATTERN.search(lower):
        return True
    return False
