"""Правило «просят человека?» — одно на оба пути и держит свои числа (DRF-2545).

Корпус, роли его частей и таблица «до/после» — в ``handoff_request_corpus.py``.
Узлы:

* каждая размеченная фраза отвечает как её метка — КРОМЕ названных пределов,
  и список пределов совпадает с фактом в обе стороны: починили предел —
  вычеркните его, появился новый — впишите с причиной;
* числа по частям корпуса — те, что в таблице модуля корпуса;
* глобальный путь и навык салона отвечают одинаково на каждой фразе: до
  DRF-2545 это были два матчера с разными ответами.

Положительная сторона стоит первой: правило, которое не срабатывает никогда,
на корпусе провалит первым же утверждением, а не пройдёт на «ложных ноль».
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.orchestrator.handoff import matches_human_handoff_request
from apps.skills.human_handoff.skill import HumanHandoffSkill, is_handoff_request
from apps.skills.human_handoff.tests.handoff_request_corpus import (
    BUILD,
    FRESH,
    KNOWN_LIMITS,
    TUNED,
)

ALL = BUILD + TUNED + FRESH


def test_real_requests_fire():
    requests = [t for label, t in ALL if label == "R"]
    fired = [t for t in requests if is_handoff_request(t)]
    assert len(requests) >= 40
    assert len(fired) >= 40, fired


def test_every_phrase_answers_as_labelled_except_the_named_limits():
    disagree = {t for label, t in ALL if is_handoff_request(t) != (label == "R")}
    assert disagree == set(KNOWN_LIMITS), {
        "не названо в KNOWN_LIMITS": sorted(disagree - set(KNOWN_LIMITS)),
        "в KNOWN_LIMITS, но уже отвечает верно": sorted(set(KNOWN_LIMITS) - disagree),
    }


@pytest.mark.parametrize(
    ("name", "corpus", "false_fires", "non_requests", "caught", "requests"),
    [
        ("BUILD", BUILD, 0, 24, 20, 23),
        ("TUNED", TUNED, 0, 15, 13, 13),
        ("FRESH", FRESH, 3, 12, 11, 12),
    ],
)
def test_numbers_per_corpus_match_the_table(
    name, corpus, false_fires, non_requests, caught, requests
):
    n = [t for label, t in corpus if label == "N"]
    r = [t for label, t in corpus if label == "R"]
    assert (len(n), len(r)) == (non_requests, requests), name
    assert sum(map(is_handoff_request, r)) == caught, name
    assert sum(map(is_handoff_request, n)) == false_fires, name


def test_both_paths_give_one_answer():
    skill = HumanHandoffSkill()
    for _label, text in ALL:
        own = is_handoff_request(text)
        assert matches_human_handoff_request(text) is own, text
        assert skill.matches(SimpleNamespace(message_text=text)) is own, text
