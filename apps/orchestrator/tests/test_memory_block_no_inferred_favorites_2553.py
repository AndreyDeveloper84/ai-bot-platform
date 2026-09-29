"""Выведенные каталогом «любимые мастера» в подсказку не идут (DRF-2553, замер DRF-2551).

Каталог выводит ``favorite_masters`` из завершённых визитов во всех салонах;
решение 24.08 (OD_MEMORY §3) — любимые мастера не переходят между салонами,
переходит только сказанное самим человеком. Пользы в подсказке не было: голые
UUID, а ``start_booking`` ждёт имя. Контракт не меняется — каталог поле
по-прежнему присылает, поэтому узлы кормят блок НЕПУСТЫМ полем: ноль,
доказанный пустым входом, не доказывал бы ничего.

Два узла парой: ключа нет — и соседи из того же ответа на месте. Без второго
«убрали одно» было бы неотличимо от «уронили всё».
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from ayla_ai_core import build_memory_block

from apps.orchestrator import memory_block
from apps.orchestrator.memory_block import build_concierge_memory_block

MASTER_ID = str(uuid4())

#: Ответ каталога той формы, что отдаёт ``internal_personal_context_api``:
#: значения и полная карта происхождения рядом.
CONTEXT = {
    "favorite_masters": [MASTER_ID],
    "diet_type": "vegan",
    "preferred_time_slots": ["evening"],
    "busy_days": ["tue"],
    "workplace_district": "Центр",
}
DATA_SOURCES = {
    "favorite_masters": "inferred",
    "diet_type": "explicit",
    "preferred_time_slots": "explicit",
    "busy_days": "inferred",
    "workplace_district": "explicit",
}


@pytest.fixture
def block(monkeypatch) -> str:
    monkeypatch.setattr("apps.consent.memory.can_store_green_memory", lambda bot_user: False)
    declared = SimpleNamespace(context=dict(CONTEXT), raw={"data_sources": dict(DATA_SOURCES)})
    monkeypatch.setattr(
        memory_block,
        "get_declared_prefs",
        lambda bot_user: SimpleNamespace(status=memory_block.GateStatus.OK, context=declared),
    )
    return build_concierge_memory_block(object())


def test_the_catalog_sent_favorites_and_the_block_has_none(block):
    # Вход непуст — каталог прислал поле.
    assert CONTEXT["favorite_masters"], "узел кормит блок пустым полем — ноль ничего не докажет"
    # Тот же ai-core на том же входе поле печатает: пропажа — наша, не его.
    assert MASTER_ID in build_memory_block({"favorite_masters": [MASTER_ID]})

    assert block, "блок памяти не собрался — узел ниже прошёл бы на пустом"
    assert MASTER_ID not in block
    assert "Любимые мастера" not in block


def test_every_other_key_from_the_same_answer_still_reaches_the_prompt(block):
    """Блок — ровно то, что ai-core собрал бы из ответа без одного ключа.

    Сравнение байтовое и по тем же уверенностям и происхождению, что ставит
    ``memory_block``: соседи не просто «где-то есть», а не изменились вовсе.
    """
    rest = {k: v for k, v in CONTEXT.items() if k != "favorite_masters"}
    rest["preferred_time_slots"] = [
        memory_block._SLOT_DISPLAY.get(s, s) for s in rest["preferred_time_slots"]
    ]
    expected = build_memory_block(
        rest,
        confidences=dict.fromkeys(rest, memory_block._DECLARED_CONFIDENCE),
        sources={
            k: (
                memory_block.SOURCE_STATED
                if DATA_SOURCES[k] == memory_block.BACKEND_STATED_SOURCE
                else memory_block.SOURCE_INFERRED
            )
            for k in rest
        },
    )
    assert expected, "эталон пуст — сравнение ничего бы не проверило"
    assert block == expected
