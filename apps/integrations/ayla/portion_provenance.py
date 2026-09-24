"""Происхождение порции — единственный вход перевода провода (DRF-2371).

Близнец `apps/miniapp/src/lib/portion-provenance.ts`: там его читает
Mini App, здесь — карточки в чате. Словарь каталога (DRF-2402) живёт
ровно в двух местах, по одному на язык, и больше нигде.

Зачем признак. Числа о еде считаются от веса порции, а вес приходит из
разных источников: его называет распознаватель фото, называет сам
человек, подставляет справочник типовой величиной — или не называет
никто, и каталог считает по своей базовой константе. Все четыре случая
дают число одного вида. Показать подставленное как названное — та же
ложь, что «0 ккал» о блюде, которого никто не считал.

Правила, от которых зависит правдивость:

1. **Поля нет — это «не названо».** Старый ответ провенанса не несёт, и
   выдавать его за названный нельзя.
2. **Незнакомая строка — тоже «не названо».** Когда в словаре появится
   четвёртое значение, старый код покажет его осторожно, а не как
   подтверждённое.
3. **«Не названо» ≠ «чисел нет».** Числа могут быть посчитаны по
   константе каталога — именно поэтому их нельзя подавать как названные.
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class PortionProvenance(str, Enum):
    """Внутреннее значение. ``ABSENT`` — поля в ответе не было вовсе."""

    NAMED = "named"
    TYPICAL = "typical"
    UNNAMED = "unnamed"
    ABSENT = "absent"


#: Провод → внутреннее. Единственное место со строками каталога.
#: ``provider`` значит «вес назвал наблюдавший», включая самого человека
#: при ручном вводе, — «измерено провайдером» было бы неверным чтением.
_WIRE_TO_PROVENANCE = {
    "provider": PortionProvenance.NAMED,
    "typical": PortionProvenance.TYPICAL,
    "unknown": PortionProvenance.UNNAMED,
}


def portion_provenance_of(wire: Any) -> PortionProvenance:
    """Прочитать признак из тела ответа каталога.

    ``None``/отсутствие → ``ABSENT``; незнакомое значение → ``UNNAMED``.
    """
    if wire is None:
        return PortionProvenance.ABSENT
    if not isinstance(wire, str):
        return PortionProvenance.UNNAMED
    return _WIRE_TO_PROVENANCE.get(wire, PortionProvenance.UNNAMED)


def portion_numbers_are_named(provenance: PortionProvenance) -> bool:
    """Можно ли назвать число человеку как полученное от кого-то.

    Только ``NAMED``. ``ABSENT`` — переходный случай: до половины B
    (DRF-2444) числа в ответе существуют только при названном весе,
    поэтому старый ответ читается как прежде. Ветка станет мёртвой, когда
    поле появится во всех ответах, — снимать вместе со значением.
    """
    return provenance in (PortionProvenance.NAMED, PortionProvenance.ABSENT)


def portion_needs_confirmation(provenance: PortionProvenance) -> bool:
    """Нужен ли рядом ход «назови вес».

    Пока владелец не дал слов, которыми оговаривается неподтверждённое
    число (OWNER_QUESTIONS §6-омикрон), такое число не показывается
    вовсе. **Это ожидание текста, а не решение по существу**: с ответом
    владельца показ включается одной строкой.
    """
    return provenance in (PortionProvenance.TYPICAL, PortionProvenance.UNNAMED)
