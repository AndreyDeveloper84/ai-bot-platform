"""Числа словами → цифры (решение владельца по K15, 22.09.2026).

Зачем. OpenAI ``gpt-transcribe`` пишет числа как придётся: «Съел 250 грамм
гречки» и тут же «Триста грамм борща» (замер 21.09, §2.5.1 отчёта этапа 0).
``parse_food_text`` понимает только цифры, время записи «на три часа дня»
тоже приходит словами. Без нормализации дневник теряет граммы в половине
случаев.

Что делаем. Количественные числительные (0…999 999, с тысячами) и
простые дроби («ноль целых три десятых», «полтора») заменяются цифрами
прямо в тексте, регистр и остальная пунктуация не трогаются.

Чего не делаем — намеренно:

* порядковые («третьего», «первый») — их нет в словаре, они не тронуты;
* одиночное «один / одна / одно» без продолжения («один раз», «одна
  подруга») — счёт, не количество, оставляем словом; «два литра» → «2 литра»;
* «пол» в составе слов («полкило», «полчаса») — это другое слово;
* десятичная запись — через запятую («0,3»), как принято в русском и как
  читает ``_GRAMS_IN_TEXT`` (``[.,]``).
"""

from __future__ import annotations

import re
from decimal import Decimal

_UNITS: dict[str, int] = {
    "ноль": 0,
    "нуль": 0,
    "один": 1,
    "одна": 1,
    "одно": 1,
    "одну": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
}
_TENS: dict[str, int] = {
    "двадцать": 20,
    "тридцать": 30,
    "сорок": 40,
    "пятьдесят": 50,
    "шестьдесят": 60,
    "семьдесят": 70,
    "восемьдесят": 80,
    "девяносто": 90,
}
_HUNDREDS: dict[str, int] = {
    "сто": 100,
    "двести": 200,
    "триста": 300,
    "четыреста": 400,
    "пятьсот": 500,
    "шестьсот": 600,
    "семьсот": 700,
    "восемьсот": 800,
    "девятьсот": 900,
}
_THOUSAND = {"тысяча", "тысячи", "тысяч", "тысячу"}
_WHOLE = {"целых", "целая", "целую", "целой"}
_FRACTIONS: dict[str, int] = {
    "десятая": 10,
    "десятых": 10,
    "десятой": 10,
    "десятую": 10,
    "сотая": 100,
    "сотых": 100,
    "сотой": 100,
    "сотую": 100,
    "тысячная": 1000,
    "тысячных": 1000,
}
_HALF = {"полтора": Decimal("1.5"), "полторы": Decimal("1.5")}
_LONE_ONE = {"один", "одна", "одно", "одну"}

_NUMERAL_WORDS = (
    set(_UNITS) | set(_TENS) | set(_HUNDREDS) | _THOUSAND | _WHOLE | set(_FRACTIONS) | set(_HALF)
)

# Слово — буквы кириллицы с дефисом внутри; между словами сохраняем всё как есть.
_TOKEN = re.compile(r"[а-яё]+(?:-[а-яё]+)?", re.IGNORECASE)


def _group_value(words: list[str]) -> int | None:
    """Целое из последовательности слов до 999 999. ``None`` — не число."""
    total = 0
    current = 0
    seen_thousand = False
    for w in words:
        if w in _HUNDREDS:
            current += _HUNDREDS[w]
        elif w in _TENS:
            current += _TENS[w]
        elif w in _UNITS:
            current += _UNITS[w]
        elif w in _THOUSAND:
            if seen_thousand:
                return None
            seen_thousand = True
            total += (current or 1) * 1000
            current = 0
        else:
            return None
    return total + current


def _format(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.replace(".", ",")


def _convert_run(words: list[str]) -> str | None:
    """Строка цифр для непрерывного числительного или ``None``, если это не число."""
    lower = [w.lower() for w in words]
    if len(lower) == 1 and lower[0] in _HALF:
        return _format(_HALF[lower[0]])
    if len(lower) == 1 and lower[0] in _LONE_ONE:
        return None
    if any(w in _HALF for w in lower):
        return None

    # Дробь: <целое> целых <числитель> десятых/сотых
    if any(w in _WHOLE for w in lower):
        idx = next(i for i, w in enumerate(lower) if w in _WHOLE)
        whole_words, frac_words = lower[:idx], lower[idx + 1 :]
        if not whole_words or not frac_words or frac_words[-1] not in _FRACTIONS:
            return None
        whole = _group_value(whole_words)
        numerator = _group_value(frac_words[:-1])
        if whole is None or numerator is None:
            return None
        denominator = _FRACTIONS[frac_words[-1]]
        if numerator >= denominator:
            return None
        return _format(Decimal(whole) + Decimal(numerator) / Decimal(denominator))

    if any(w in _FRACTIONS for w in lower):
        return None
    value = _group_value(lower)
    return None if value is None else str(value)


def normalize_numbers(text: str) -> str:
    """Заменить числительные словами на цифры, остальной текст оставить как есть."""
    if not text:
        return text
    out: list[str] = []
    pos = 0
    run: list[re.Match[str]] = []

    def flush() -> None:
        nonlocal pos
        if not run:
            return
        converted = _convert_run([m.group(0) for m in run])
        start, end = run[0].start(), run[-1].end()
        if converted is None:
            out.append(text[pos:end])
        else:
            out.append(text[pos:start])
            out.append(converted)
        pos = end
        run.clear()

    for m in _TOKEN.finditer(text):
        word = m.group(0).lower()
        if word in _NUMERAL_WORDS:
            # Между словами одного числа допускаем только пробелы.
            if run and text[run[-1].end() : m.start()].strip():
                flush()
            run.append(m)
        else:
            flush()
    flush()
    out.append(text[pos:])
    return "".join(out)
