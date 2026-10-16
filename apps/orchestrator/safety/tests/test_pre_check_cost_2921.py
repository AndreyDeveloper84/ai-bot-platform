"""DRF-2921 — время гейта растёт с длиной сообщения, а не с её квадратом.

Шаблон BLOCK «попросили подобрать + назвали лекарство» состоял из двух
просмотров ``(?=.*…)`` без якоря: ``re.search`` пробовал их с каждой позиции
текста, и каждый раз ``.*`` шёл до конца. На 4 000 знаков любого бытового
текста гейт думал секунды; потребитель очереди один на всех
(``apps/workers/consumer.py``), так что длинное сообщение задерживало всех.

Якорь ``\\A`` вердикта не меняет — это проверяется здесь же, на старой и
новой строке шаблона рядом.
"""

from __future__ import annotations

import re
import time

import pytest

from apps.channels.max.voice_turn import strip_for_gate
from apps.orchestrator.safety.pre_check import _DEFAULT_PATTERNS, SafetyVerdict, pre_check
from apps.orchestrator.safety.tests.test_gate_strip_parity import CORPUS, spoken_variants

_ANCHOR = r"(?is)\A"
_DRUG_REQUEST = next(p for p in _DEFAULT_PATTERNS[SafetyVerdict.BLOCK.value] if "ибупрофен" in p)

_MESSAGE = 4000
_HOSTILE = [
    "Запишите меня пожалуйста на маникюр на пятницу после шести вечера если можно. ",
    "а ",
    "подберите мне что-нибудь на пятницу ",  # первый просмотр сходится, второй — никогда
    "таблетки таблетки таблетки ",  # второй сходится, первый — никогда
    "дай" * 3,
    "сколько раз в ",
]


def _fit(unit: str) -> str:
    return (unit * (_MESSAGE // len(unit) + 1))[:_MESSAGE]


def test_the_pattern_is_anchored() -> None:
    assert _DRUG_REQUEST.startswith(_ANCHOR)


@pytest.mark.parametrize("unit", _HOSTILE)
def test_a_message_of_full_length_costs_milliseconds(unit: str) -> None:
    text = _fit(unit)
    pre_check(text)  # сборка шаблонов — за таймером
    best = float("inf")
    for _ in range(3):
        started = time.perf_counter()
        pre_check(text)
        best = min(best, time.perf_counter() - started)
    # До правки — от 1 до 9 с на таких текстах; после — миллисекунды. Потолок
    # с запасом на загруженную машину.
    assert best < 0.5, f"{unit!r}: {best:.2f} с на {_MESSAGE} знаков"


def test_the_anchor_changes_no_verdict() -> None:
    unanchored = re.compile("(?is)" + _DRUG_REQUEST[len(_ANCHOR) :])
    anchored = re.compile(_DRUG_REQUEST)
    texts = [
        "посоветуйте ибупрофен от боли",
        "подберите обезболивающее",
        "сколько таблеток парацетамола принять",
        "какие таблетки лучше",
        "вчера выпила ибупрофен, можно на массаж?",
        "принимаю парацетамол, это помешает?",
        "Ибупрофен вчера пила.\nПосоветуйте мастера",
        "мастера посоветуйте\nа таблетки я пью сама",
        "what to take for pain, any pills?",
        "запишите на маникюр",
        "",
    ]
    for phrase in CORPUS:
        texts.append(phrase)
        for variant in spoken_variants(phrase):
            texts.extend((variant, strip_for_gate(variant)))
    hits = 0
    for text in texts:
        was, now = bool(unanchored.search(text)), bool(anchored.search(text))
        assert was == now, text
        hits += now
    # Сравнение было не на одном значении: шаблон срабатывал и молчал.
    assert 0 < hits < len(texts)
