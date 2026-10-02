"""Числа словами → цифры (решение владельца по K15, 22.09.2026).

Зачем. OpenAI ``gpt-transcribe`` пишет числа как придётся: «Съел 250 грамм
гречки» и тут же «Триста грамм борща» (замер 21.09, §2.5.1 отчёта этапа 0).
``parse_food_text`` понимает только цифры, время записи «на три часа дня»
тоже приходит словами. Без нормализации дневник теряет граммы в половине
случаев.

Что делаем. Количественные числительные (0…999 999, с тысячами) и
простые дроби («ноль целых три десятых», «полтора») заменяются цифрами
прямо в тексте, регистр и остальная пунктуация не трогаются.

Два числа подряд — не одно число (DRF-2710). Цепочка числительных
складывалась целиком, без проверки, что это ОДНО число: «в десять тридцать»
становилось «в 40», «в два тридцать» — «в 32», и запись уходила на время,
которого человек не называл. Теперь цепочка режется на числа по грамматике
(сотни → десятки 20–90 → единицы 1–9; 10–19 и «ноль» стоят сами по себе):

* «час (0–23) + минуты (10–59, либо «ноль ноль», «ноль пять»)» сразу после
  «в» или «на» — это время: «в десять тридцать» → «в 10:30», «на пятнадцать
  ноль ноль» → «на 15:00»;
* всё прочее остаётся несколькими числами: «десять тридцать» без предлога →
  «10 30». Временем это не объявляется намеренно: ложное время дороже
  неузнанного.

День месяца, названный порядковым («шестнадцатого сентября», «двадцать пятое
октября»), становится тем, что человек набрал бы: «16 сентября», «25
октября» — только когда сразу за ним месяц в родительном падеже. Так дату
видят одинаково модель, ``parse_explicit_date`` и строка «Я услышала: …».

Чего не делаем — намеренно:

* порядковые без месяца («третьего числа», «первый раз», «двадцать пятое») —
  не тронуты целиком; числительное перед порядковым словом — его часть, и
  отдельно не заменяется («двадцать пятое» не становится «20 пятое»);
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

#: Слова, которые в составном числе стоят только сами по себе: 10–19 и ноль.
_TEENS: frozenset[str] = frozenset(w for w, v in _UNITS.items() if v >= 10)
_ZEROS: frozenset[str] = frozenset(w for w, v in _UNITS.items() if v == 0)

#: После этих предлогов «час + минуты» читается как время (DRF-2710).
_CLOCK_PREPOSITIONS: frozenset[str] = frozenset({"в", "на"})

#: День месяца порядковым: именительный среднего («пятое») и родительный
#: («пятого») — так называют дату. Мужской род («первый раз») сюда не входит.
_ORDINAL_FORMS: dict[int, tuple[str, ...]] = {
    1: ("первое", "первого"),
    2: ("второе", "второго"),
    3: ("третье", "третьего"),
    4: ("четвёртое", "четвёртого", "четвертое", "четвертого"),
    5: ("пятое", "пятого"),
    6: ("шестое", "шестого"),
    7: ("седьмое", "седьмого"),
    8: ("восьмое", "восьмого"),
    9: ("девятое", "девятого"),
    10: ("десятое", "десятого"),
    11: ("одиннадцатое", "одиннадцатого"),
    12: ("двенадцатое", "двенадцатого"),
    13: ("тринадцатое", "тринадцатого"),
    14: ("четырнадцатое", "четырнадцатого"),
    15: ("пятнадцатое", "пятнадцатого"),
    16: ("шестнадцатое", "шестнадцатого"),
    17: ("семнадцатое", "семнадцатого"),
    18: ("восемнадцатое", "восемнадцатого"),
    19: ("девятнадцатое", "девятнадцатого"),
    20: ("двадцатое", "двадцатого"),
    30: ("тридцатое", "тридцатого"),
}
_ORDINAL_DAYS: dict[str, int] = {w: day for day, words in _ORDINAL_FORMS.items() for w in words}
_MONTHS_GENITIVE: frozenset[str] = frozenset(
    {
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    }
)
#: Десятки, с которых начинается составной день: «двадцать пятое», «тридцать первое».
_DAY_TENS: dict[str, int] = {"двадцать": 20, "тридцать": 30}
_LONE_ONE = {"один", "одна", "одно", "одну"}

_NUMERAL_WORDS = (
    set(_UNITS) | set(_TENS) | set(_HUNDREDS) | _THOUSAND | _WHOLE | set(_FRACTIONS) | set(_HALF)
)

# Слово — буквы кириллицы с дефисом внутри; между словами сохраняем всё как есть.
_TOKEN = re.compile(r"[а-яё]+(?:-[а-яё]+)?", re.IGNORECASE)


def _split_simple_numbers(words: list[str]) -> list[int] | None:
    """Числа до 999, на которые распадается цепочка слов без «тысяч».

    Одно число — это сотни, затем десятки (20–90), затем единицы (1–9), каждый
    разряд не больше раза и только в этом порядке; 10–19 занимают и десятки, и
    единицы; «ноль» — отдельное число. Слово, которое в текущее число не
    встаёт, начинает следующее: «десять тридцать» — это 10 и 30, а не 40.

    ``None`` — в цепочке есть слово, которое не числительное этих трёх разрядов.
    """
    numbers: list[int] = []
    value = 0
    rank = 4  # разряд последнего принятого слова: 3 сотни, 2 десятки, 1 единицы
    open_number = False

    def close() -> None:
        nonlocal value, rank, open_number
        if open_number:
            numbers.append(value)
        value, rank, open_number = 0, 4, False

    for w in words:
        if w in _ZEROS:
            close()
            numbers.append(0)
            continue
        if w in _HUNDREDS:
            word_rank, word_value, fills_units = 3, _HUNDREDS[w], False
        elif w in _TENS:
            word_rank, word_value, fills_units = 2, _TENS[w], False
        elif w in _TEENS:
            # «одиннадцать» встаёт на место десятков и занимает единицы тоже.
            word_rank, word_value, fills_units = 2, _UNITS[w], True
        elif w in _UNITS:
            word_rank, word_value, fills_units = 1, _UNITS[w], False
        else:
            return None
        if word_rank >= rank:
            close()
        value += word_value
        rank = 1 if fills_units else word_rank
        open_number = True
    close()
    return numbers


def _group_value(words: list[str]) -> int | None:
    """Целое из последовательности слов до 999 999. ``None`` — не ОДНО число.

    DRF-2710: слова обязаны складываться в одно число по грамматике. Раньше
    складывалось всё подряд, и «десять тридцать» давало 40.
    """
    thousands = [i for i, w in enumerate(words) if w in _THOUSAND]
    if len(thousands) > 1:
        return None
    if thousands:
        idx = thousands[0]
        before = _split_simple_numbers(words[:idx])
        after = _split_simple_numbers(words[idx + 1 :])
        if before is None or after is None or len(before) > 1 or len(after) > 1:
            return None
        return (before[0] if before else 1) * 1000 + (after[0] if after else 0)
    numbers = _split_simple_numbers(words)
    if numbers is None or len(numbers) != 1:
        return None
    return numbers[0]


def _clock_time(numbers: list[int]) -> str | None:
    """«10:30» для «час + минуты», как их называют вслух, иначе ``None``.

    Минуты — двузначное число («тридцать», «сорок пять») либо «ноль ноль» /
    «ноль пять». «Десять пять» вслух временем не называют.
    """
    if len(numbers) == 2 and 10 <= numbers[1] <= 59:
        hour, minute = numbers
    elif len(numbers) == 3 and numbers[1] == 0 and 0 <= numbers[2] <= 9:
        hour, minute = numbers[0], numbers[2]
    else:
        return None
    if not 0 <= hour <= 23:
        return None
    return f"{hour}:{minute:02d}"


def _format(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.replace(".", ",")


def _convert_run(words: list[str], *, after_clock_preposition: bool = False) -> str | None:
    """Строка цифр для непрерывного числительного или ``None``, если это не число.

    ``after_clock_preposition`` — цепочка стоит сразу после «в» / «на»: только
    тогда «час + минуты» читается как время.
    """
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
    if value is not None:
        return str(value)

    # Не одно число (DRF-2710). С «тысячами» неоднозначно — не трогаем, как и
    # раньше («тысяча тысяча»). Иначе это несколько чисел подряд.
    if any(w in _THOUSAND for w in lower):
        return None
    numbers = _split_simple_numbers(lower)
    if not numbers:
        return None
    if after_clock_preposition:
        clock = _clock_time(numbers)
        if clock is not None:
            return clock
    return " ".join(str(n) for n in numbers)


def _spoken_dates(text: str) -> str:
    """«шестнадцатого сентября» → «16 сентября»; остальной текст как есть.

    Только «день 1–31 порядковым + месяц в родительном». Порядковое без
    месяца датой не объявляется и не трогается.
    """
    tokens = list(_TOKEN.finditer(text))
    out: list[str] = []
    pos = 0
    i = 0
    while i < len(tokens):
        first = tokens[i]
        word = first.group(0).lower()
        day: int | None = None
        last = i
        if word in _ORDINAL_DAYS:
            day = _ORDINAL_DAYS[word]
        elif word in _DAY_TENS and i + 1 < len(tokens):
            unit = _ORDINAL_DAYS.get(tokens[i + 1].group(0).lower())
            only_space = not text[first.end() : tokens[i + 1].start()].strip()
            if unit is not None and 1 <= unit <= 9 and only_space:
                day = _DAY_TENS[word] + unit
                last = i + 1
        month = tokens[last + 1] if last + 1 < len(tokens) else None
        # Числительное вплотную перед днём — это другое, большее порядковое:
        # «тридцать второго октября», «сто двадцать пятого» днём не являются.
        before = tokens[i - 1] if i > 0 else None
        part_of_a_larger_ordinal = (
            before is not None
            and before.group(0).lower() in _NUMERAL_WORDS
            and not text[before.end() : first.start()].strip()
        )
        if (
            day is not None
            and not part_of_a_larger_ordinal
            and 1 <= day <= 31
            and month is not None
            and month.group(0).lower() in _MONTHS_GENITIVE
            and not text[tokens[last].end() : month.start()].strip()
        ):
            out.append(text[pos : first.start()])
            out.append(str(day))
            pos = tokens[last].end()
            i = last + 1
            continue
        i += 1
    out.append(text[pos:])
    return "".join(out)


def normalize_numbers(text: str) -> str:
    """Заменить числительные словами на цифры, остальной текст оставить как есть."""
    if not text:
        return text
    text = _spoken_dates(text)
    out: list[str] = []
    pos = 0
    run: list[re.Match[str]] = []
    tokens = list(_TOKEN.finditer(text))
    index_of = {m.start(): i for i, m in enumerate(tokens)}

    def neighbour(m: re.Match[str], step: int) -> str | None:
        """Соседнее слово, если между ним и ``m`` только пробелы."""
        j = index_of[m.start()] + step
        if not 0 <= j < len(tokens):
            return None
        other = tokens[j]
        gap = text[other.end() : m.start()] if step < 0 else text[m.end() : other.start()]
        return None if gap.strip() else other.group(0).lower()

    def flush() -> None:
        nonlocal pos
        if not run:
            return
        start, end = run[0].start(), run[-1].end()
        if neighbour(run[-1], +1) in _ORDINAL_DAYS:
            # «двадцать пятое», «сто двадцать пятое место»: числительное —
            # часть порядкового, порознь они не число.
            converted = None
        else:
            converted = _convert_run(
                [m.group(0) for m in run],
                after_clock_preposition=neighbour(run[0], -1) in _CLOCK_PREPOSITIONS,
            )
        if converted is None:
            out.append(text[pos:end])
        else:
            out.append(text[pos:start])
            out.append(converted)
        pos = end
        run.clear()

    for m in tokens:
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
