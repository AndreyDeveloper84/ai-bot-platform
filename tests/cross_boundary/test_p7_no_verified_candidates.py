"""P7 — нет VERIFIED-кандидата → честный пустой ответ, через настоящую границу.

Идёт через **код клиента бота** (``fetch_recommendations``), а не через
``httpx`` напрямую: заголовки, версия пути и разбор ответа — те же, что на
пилоте. Каталог — отдельный процесс на стенде.

Два «пусто», и они разные — так их различает сам каталог
(``recommendation/_pipeline.py::_decision_codes``):

* **P7-a, пустой каталог** — свидетельств нет вовсе: слой 2 отдаёт
  ``QUALITY_NO_EVIDENCE``;
* **P7-b, мастера есть, VERIFIED нет** — «каталог видно, рекомендовать
  нечего»: слой 2 отдаёт ``ELIG_EXCLUDED_NOT_RECOMMENDABLE``. Это состояние
  **пилота** (34 мастера, verified 0) и то, что матрица зовёт
  ``BLOCKED(CATALOG_NOT_RECOMMENDABLE)``. Требует посева
  (``seed_golden --scenario p7`` в каталоге).

Слой 1 в обоих случаях — ``items: [], reason_codes: []``: решения не
принимали, у нового клиента истории нет. Пустые коды здесь — правда, а не
пропуск, и golden это утверждает, а не терпит.

Коды названы по факту ответа стенда 11.09.2026, не по чтению кода.
"""

from __future__ import annotations

import os

import pytest

from apps.integrations.ayla.recommendations_client import fetch_recommendations

from .conftest import GOLDEN_EXTERNAL_USER_ID, Catalog

pytestmark = pytest.mark.cross_boundary

#: Какой посев стоит на стенде. ``empty`` — ничего не сеяли (P7-a);
#: ``p7`` — мастера без VERIFIED-маппинга (P7-b). Стенд обязан сказать это
#: сам: golden, гадающий о состоянии каталога, проверял бы не то.
SCENARIO_ENV = "CROSS_BOUNDARY_SCENARIO"

NO_EVIDENCE = "QUALITY_NO_EVIDENCE"
NOT_RECOMMENDABLE = "ELIG_EXCLUDED_NOT_RECOMMENDABLE"


def _scenario() -> str:
    value = os.environ.get(SCENARIO_ENV, "empty")
    assert value in ("empty", "p7"), f"{SCENARIO_ENV}={value!r}: ожидалось empty | p7"
    return value


def _ask(external_user_id: str = GOLDEN_EXTERNAL_USER_ID) -> dict:
    body = fetch_recommendations(external_user_id=external_user_id, payload={})
    data = body.get("data") or body
    assert set(data) >= {"layer_1_your_places", "layer_2_ayla_picks", "layer_3_explore"}, data
    return data


def test_no_verified_candidates_arrives_as_absence_with_a_name(
    bot_points_at_catalog: Catalog,
) -> None:
    """Сердце P7: ни одного кандидата — и **названа причина**, а не просто
    пустой список."""
    scenario = _scenario()
    data = _ask()
    layer_1, layer_2, layer_3 = (
        data["layer_1_your_places"],
        data["layer_2_ayla_picks"],
        data["layer_3_explore"],
    )

    print(
        f"\n[CROSS-BOUNDARY P7] scenario={scenario} sha={bot_points_at_catalog.sha} "
        f"l1={layer_1['reason_codes']} l2={layer_2['reason_codes']} "
        f"l3_categories={len(layer_3.get('categories') or [])}"
    )

    # Ни одного кандидата ни на одной полке — предмет P7.
    assert layer_1["items"] == [], layer_1
    assert layer_2["items"] == [], layer_2

    # Слой 1: пустые коды — ПРАВДА, не пропуск. Истории у нового клиента
    # нет, решения не принимали, сказать нечего. Появится код — значит
    # кто-то начал принимать решение там, где не должен.
    assert layer_1["reason_codes"] == [], "слой 1 назвал причину там, где решения не было: " + str(
        layer_1["reason_codes"]
    )

    # Слой 2: отсутствие С ИМЕНЕМ — и имя зависит от того, пуст ли каталог.
    expected = NO_EVIDENCE if scenario == "empty" else NOT_RECOMMENDABLE
    assert expected in layer_2["reason_codes"], (
        f"сценарий {scenario}: ожидался код {expected}, каталог назвал {layer_2['reason_codes']}"
    )
    # Положительная стража к самому различению: коды двух сценариев не
    # должны появляться вместе — иначе golden не различал бы состояния.
    other = NOT_RECOMMENDABLE if scenario == "empty" else NO_EVIDENCE
    assert other not in layer_2["reason_codes"], (
        f"сценарий {scenario}: код другого сценария {other} тоже пришёл — стенд посеян не так"
    )


def test_the_answer_is_stable_across_two_calls(bot_points_at_catalog: Catalog) -> None:
    """Детерминизм — свойство стенда, и оно проверяется, а не подразумевается.
    Тот же клиент, тот же каталог, два вызова подряд — одно и то же."""
    first = _ask()
    second = _ask()
    assert (
        first["layer_2_ayla_picks"]["reason_codes"] == second["layer_2_ayla_picks"]["reason_codes"]
    )
    assert first["layer_1_your_places"] == second["layer_1_your_places"]


def test_a_second_client_sees_the_same_absence(bot_points_at_catalog: Catalog) -> None:
    """Отсутствие не зависит от того, кто спрашивает: второй proxy-клиент
    получает тот же код. Без этого зелёный P7 мог бы держаться на
    особенности одного пользователя."""
    data = _ask(external_user_id="bot:golden-p7-second")
    layer_2 = data["layer_2_ayla_picks"]
    assert layer_2["items"] == []
    assert layer_2["reason_codes"], "второй клиент получил пустоту без имени"
