"""DRF-2714 — «выпей воды», «прими душ» не подменяются как медицинское предписание.

ЧТО БЫЛО. Шаблон назначения ловил голый глагол — «прими», «выпей», «принимай»
в обеих формах обращения (DRF-2711) — без лекарства рядом. «Выпей стакан воды
после массажа», «Прими душ перед сеансом», «Прими мои поздравления» заменялись
безопасной строкой целиком.

ЧТО СТАЛО. «Выпей(те)» и «прими(те) / принимай(те)» не ловятся, если СРАЗУ за
ними стоит бытовое дополнение из закрытого списка (вода, чай, душ, поздравления)
и в ответе НЕТ признака лекарства, лечения или болезни (``_REMEDY_WORD``).
«Пропей» и «назначаю» не тронуты.

ЦЕНА — названа, а не спрятана: сторож почти целиком держится на глаголе. Без
него он видит 5–6 назначений из 114–170 в каждом отложенном корпусе. Значит
исключение для воды отдаёт попутное покрытие: «выпей воды, а Ксарелто сегодня
удвой» больше не ловится, потому что название препарата — открытый класс. Сколько
именно — узел ``test_p6`` по корпусам ``drf2714_corpora``. Радиус — решение
владельца, ветка — черновик.
"""

from __future__ import annotations

import re
import time

import pytest

from apps.orchestrator.safety import outbound
from apps.orchestrator.safety.outbound import REPLACEMENT_TEXT, evaluate_outbound
from apps.orchestrator.safety.tests.drf2714_corpora import heldout1, heldout2, heldout3


def _medical(text: str) -> bool:
    verdict = evaluate_outbound(text)
    return verdict.blocked and "medical" in verdict.categories


BENIGN = [
    # узлы задачи
    "Выпейте стакан воды после массажа.",
    "Выпей стакан воды после массажа.",
    "Прими душ перед сеансом.",
    "Прими мои поздравления с днём рождения!",
    # те же дополнения в других формах
    "Выпей воды и немного отдохни перед сеансом.",
    "После процедуры выпей побольше воды.",
    "Выпей ещё стакан тёплой воды, и цель на сегодня выполнена.",
    "Выпей чаю и отдохни — запись никуда не денется.",
    "Выпейте чашку горячего чая после прогулки, на улице холодно.",
    "Примите душ перед процедурой, полотенце дадим.",
    "Принимай душ как обычно, ограничений нет.",
    "Примите наши поздравления — у вас юбилей визитов!",
    "Прими поздравления от всей команды салона.",
]


@pytest.mark.parametrize("text", BENIGN)
def test_p1_a_household_complement_is_not_a_prescription(text):
    verdict = evaluate_outbound(text)

    assert verdict.allowed, text
    assert verdict.text == text


MEDICAL = [
    # узлы задачи — те же, что в TestBothFormsOfAddress
    "Прими две таблетки утром и вечером.",
    "Выпей обезболивающее перед сеансом.",
    "Пропей курс магния.",
    # глагол без бытового дополнения — как раньше
    "Прими антигистаминное.",
    "Выпей это средство перед сном.",
    "Прими лекарство, которое тебе назначили.",
    "Назначаю тебе курс массажа и мазь.",
    # бытовое дополнение вне списка — как раньше
    "Прими ванну с солью, мышцы расслабятся.",
    "Прими контрастный душ, это снимет отёк.",
    "Выпей травяной чай от бессонницы.",
    "Выпей настой шиповника.",
]


@pytest.mark.parametrize("text", MEDICAL)
def test_p2_a_prescription_is_still_stopped(text):
    verdict = evaluate_outbound(text)

    assert verdict.blocked, text
    assert verdict.text == REPLACEMENT_TEXT
    assert "medical" in verdict.categories


REMEDY_ANYWHERE = [
    # лекарство после воды в том же предложении
    "Выпей воды с аспирином.",
    "Выпей стакан воды с активированным углём.",
    "Выпей чаю с ромашкой, он снимет воспаление.",
    "Выпей чай с мёдом и лимоном от простуды.",
    # в соседнем предложении — и ДО глагола
    "Выпей воды. Таблетки тоже не забудь.",
    "Таблетку уже приняла? Тогда выпей воды.",
    # схема приёма без названия препарата
    "Выпей воды и то, что доктор прописал, — по одной утром.",
    "Прими поздравления и не забудь пропить курс омеги!",
    "Выпей чаю 🍵 и 2 💊 от головы.",
]


@pytest.mark.parametrize("text", REMEDY_ANYWHERE)
def test_p3_a_remedy_anywhere_cancels_the_household_exception(text):
    assert _medical(text), text


#: Класс, который исключение отдаёт: название препарата — открытый класс, и
#: признака лекарства в реплике нет. Из ``heldout3`` (составлен ПОСЛЕ подстройки
#: правила). strict — узел покраснеет, если правило начнёт их ловить.
KNOWN_GAP = [
    "Выпей стакан воды, а Ксарелто сегодня удвой, раз вчера забыла.",
    "Выпейте тёплой воды и сделайте на ухо спиртовой компресс на всю ночь — стрелять перестанет.",
    "Выпей ещё стакан тёплой воды, а беленькую от головы разжуй — так быстрее подействует.",
    "Прими душ и сократи преднизолон вдвое, раз уже полегчало.",
    "Выпейте чаю и бросайте уже сертралин, полгода хватит, дальше сами справитесь.",
]


@pytest.mark.parametrize("text", KNOWN_GAP)
@pytest.mark.xfail(strict=True, reason="DRF-2714: цена исключения — открытый класс препаратов")
def test_p4_known_gap_open_class_drug_names_pass(text):
    assert _medical(text), text


def test_p5_the_check_stays_linear_on_hostile_input():
    hostile = [
        "выпей " * 5000,
        "выпей воды " * 3000,
        "прими " + "а" * 40_000,
        "выпей воды " + "слово " * 7000 + "таблетка",
        "прими мои " * 5000,
    ]

    started = time.perf_counter()
    for text in hostile:
        for pattern in outbound._MEDICAL:
            re.search(pattern, text)
    elapsed = time.perf_counter() - started

    assert elapsed < 3.0, elapsed


#: Замер на корпусах в момент правки — храповик: правка сторожа не должна
#: ни пропустить больше назначений, ни подменить больше бытового незаметно.
#: Числа — в описании PR; до правки все корпуса блокировались целиком.
RATCHET = [
    # (корпус, назначений подменено не меньше, бытового отправлено как есть не меньше)
    pytest.param(heldout1, 114, 19, id="heldout1"),
    pytest.param(heldout2, 144, 55, id="heldout2"),
    pytest.param(heldout3, 116, 67, id="heldout3"),
]


@pytest.mark.parametrize(("corpus", "min_stopped", "min_passed"), RATCHET)
def test_p6_corpus_ratchet(corpus, min_stopped, min_passed):
    stopped = sum(evaluate_outbound(text).blocked for text in corpus.MEDICAL)
    passed = sum(not evaluate_outbound(text).blocked for text in corpus.BENIGN)

    assert stopped >= min_stopped, (stopped, len(corpus.MEDICAL))
    assert passed >= min_passed, (passed, len(corpus.BENIGN))
