"""DRF-358 T01: food/drink hints detector.

Когда `parse_beverage` (Phase 3.1 Part 2D.1) вернул None но текст похож на еду
или напиток — этот helper возвращает True. Используется в `on_free_text` для
рендера clarification card вместо передачи текста в AI Concierge (где он
получает корпоративный «не могу с заказом» ответ).

Pure regex — no LLM. Detection rules:
1. Text length ≤30 chars (длинные тексты — это full-fledged questions, не
   short food log attempts)
2. Match содержит хотя бы одно слово из FOOD_HINT_WORDS / DRINK_HINT_WORDS
3. Type-tolerant — non-string input → False

False-positives acceptable (юзер кликнет «Опечатка», silent ack).
False-negatives — backlog для EPIC-Q Q-4 specialized intent classifier.
"""
from __future__ import annotations

import re

# Распространённые блюда + базовые продукты (case-insensitive matching).
# Use stem-форму — «котлет» матчит «котлета», «котлеты», «котлету».
FOOD_HINT_WORDS: frozenset[str] = frozenset({
    # Жидкие/первые
    "борщ", "суп", "щи", "окрошк", "бульон", "уха",
    # Мясное / птица / рыба
    "котлет", "пельмен", "вареник", "стейк", "шашлык", "курин", "рыб",
    "куриц", "говядин", "свинин", "бекон", "сосиск", "колбас",
    # Крупы / гарниры
    "гречк", "рис", "макарон", "паст", "лапш", "плов", "пюре", "каш",
    # Овощи / салаты
    "салат", "винегрет", "капуст", "огурц", "помидор", "морковк", "картош",
    # Хлеб / выпечка
    "хлеб", "булк", "пирож", "пирог", "блин", "оладь", "сырник", "вафл",
    # Завтрак-еда
    "омлет", "яичниц", "яйц", "творог", "сырок", "хлопь", "мюсл",
    # Сладкое
    "конфет", "шоколад", "пирожн", "тортик", "торт", "мороженое",
    # Прочее
    "пицц", "сэндвич", "бургер", "роллы", "суши",
})

DRINK_HINT_WORDS: frozenset[str] = frozenset({
    # parse_beverage ловит mainstream — тут только misses
    "квас", "лимонад", "морс", "компот", "кисель",
    "кефир", "ряженк", "ряженка", "снежок", "тан",
    "смузи", "коктейл", "молочный", "айран",
    # Если parse_beverage упустил формат:
    "сок", "вод",  # «Сок 0,5л» / «вода в бутылке» — Bug 1 из 2026-05-08
})

_NUM_UNIT_PATTERN = re.compile(
    r"\d+[.,]?\d*\s*(?:г|гр|грамм|мл|л|шт|порц|стакан|чашк|ложк)\b",
    re.IGNORECASE,
)

_MAX_LEN = 30


def looks_like_food_drink(text) -> bool:
    """Return True iff text похож на short food/drink log attempt.

    Rule:
        len(text.strip()) ≤30 chars AND (
            contains FOOD_HINT_WORDS / DRINK_HINT_WORDS stem
            OR matches `<num> <unit>` pattern (250г / 0,5л / etc.)
        )

    Type-tolerant: non-string returns False.
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
