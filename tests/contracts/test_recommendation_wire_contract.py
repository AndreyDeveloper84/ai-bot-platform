"""Consumer-driven contract на форму границы рекомендаций (DRF-1626, §9.4).

# Почему consumer-driven, а не «подстроим потребителя»

Владелец запретил учить полку принимать обе формы. Здесь это запрещено
устройством: **форму диктует золотой образец, который лежит на стороне
потребителя** (`apps/miniapp/src/lib/__fixtures__/`), и обе половины
границы обязаны его принять. Не «сервер прислал — клиент подстроился», а
«вот форма, и её принимают оба».

Дефект, ради которого это заводится, был не в форме, а в проводе. Есть
две ручки Ayla:

* `internal/me/catalog/recommendations/` — легаси-полка, `layer_1/2/3`;
* `internal/recommendation/resolve/` — граница §9.4, `ordered[]`.

Транзит ходил на первую, полка написана против второй. Валидатор
отвергал ответ целиком, `picks` оставался пустым — при том, что **55 из
56 живых вызовов ответили `200`**. Ломалось не то, что отвечало.

# Три звена цепочки, и каждое проверяется здесь

```
золотой образец  →  серверный валидатор   (этот файл)
золотой образец  →  ручка транзита        (этот файл)
золотой образец  →  клиентский валидатор  (recommendation-wire-contract.test.ts)
```

Третье звено живёт на TypeScript и в этот файл не помещается. Чтобы
половины не разошлись молча, здесь стоит **сторож на расхождение
констант**: закрытые множества и номер поддерживаемой мажорной версии
читаются из ОБОИХ исходников и сравниваются. Репозиторий один, языка
два, и разделить код нельзя — значит сравнивать объявления.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from apps.integrations.ayla.recommendation_resolver_client import (
    SUPPORTED_SPEC_MAJOR,
    _CANDIDATE_KINDS,
    _DISPLAY_FIELDS,
    decision_contract_violation,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = REPO_ROOT / "apps/miniapp/src/lib/__fixtures__/recommendation-wire-v1.json"
CONSUMER_SOURCE = REPO_ROOT / "apps/miniapp/src/lib/api.ts"


def _golden() -> dict[str, Any]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def test_the_golden_sample_exists_and_carries_candidates() -> None:
    """Положительная стража перед всеми утверждениями ниже.

    Пустой или отсутствующий образец сделал бы «нарушений нет» верным и
    бессмысленным: валидатор, которому нечего проверять, всегда доволен.
    """
    golden = _golden()
    ordered = golden["data"]["ordered"]

    assert ordered, "золотой образец пуст — проверки ниже проверяли бы пустоту"
    assert all(c["candidate"]["id"] for c in ordered)


def test_the_server_validator_accepts_the_consumer_golden() -> None:
    """Первое звено: форма, которую диктует потребитель, проходит сервер.

    Это и есть consumer-driven: не потребитель подстраивается под то, что
    пришло, а обе стороны сверяются с одним артефактом.
    """
    assert decision_contract_violation(_golden()) is None


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        ("drop_envelope", "конверт"),
        ("drop_ordered", "ordered"),
        ("bump_major", "мажорная версия"),
        ("unknown_kind", "kind"),
        ("empty_reason_codes", "reason_codes"),
        ("display_string", "строку для показа"),
        # Найдено сторожем на расхождение половин: до DRF-1626 сервер не
        # проверял `excluded[]` вовсе, и неконформный ответ ловил браузер
        # человека вместо транзита.
        ("excluded_wrong_code", "код исключения"),
    ],
)
def test_each_guarantee_of_the_contract_actually_fires(
    mutation: str, expected_fragment: str
) -> None:
    """Сторож проверен на срабатывание, а не принят на веру.

    Валидатор, который никогда не видели красным, — это `assert True` с
    длинным именем. Каждая мутация ломает РОВНО ОДНУ гарантию §9.4, и
    сообщение обязано назвать сломанную, а не любую.
    """
    golden = _golden()
    data = golden["data"]

    if mutation == "drop_envelope":
        golden = data
    elif mutation == "drop_ordered":
        del data["ordered"]
    elif mutation == "bump_major":
        data["resolver_spec_version"] = f"{SUPPORTED_SPEC_MAJOR + 1}.0.0"
    elif mutation == "unknown_kind":
        data["ordered"][0]["candidate"]["kind"] = "TENANT"
    elif mutation == "empty_reason_codes":
        data["ordered"][0]["reason_codes"] = []
    elif mutation == "display_string":
        data["ordered"][0]["reasoning_text"] = "20 минут от тебя"
    elif mutation == "excluded_wrong_code":
        data["excluded"][0]["reason_code"] = "MATCH_SERVICE_EXACT"

    violation = decision_contract_violation(golden)

    assert violation is not None, f"мутация {mutation} прошла валидатор — гарантия не охраняется"
    assert expected_fragment in violation, (
        f"мутация {mutation} поймана, но названа не своим именем: {violation!r}"
    )


def _ts_string_set(source: str, const_name: str) -> set[str]:
    """Значения строкового массива-константы из исходника TypeScript.

    Читается объявление, а не поведение: разделить код между Python и
    TypeScript нельзя, репозитории одни, языки разные, и единственное,
    что можно сверить механически, — это объявленные множества.
    """
    match = re.search(rf"{const_name}\s*(?::[^=]*?)?=\s*\[(.*?)\]", source, re.DOTALL)
    assert match, f"в {CONSUMER_SOURCE.name} не найдено объявление {const_name}"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_the_two_halves_declare_the_same_closed_sets() -> None:
    """Сторож на молчаливое расхождение половин.

    Множество `CandidateKind` закрыто (§5, K1). Разойдись половины — одна
    начнёт отвергать то, что другая считает законным, и наружу это
    выйдет как `CONTRACT_VIOLATION` без виноватого: обе стороны будут
    уверены, что правы они.
    """
    source = CONSUMER_SOURCE.read_text(encoding="utf-8")

    assert _ts_string_set(source, "CANDIDATE_KINDS") == set(_CANDIDATE_KINDS)
    assert _ts_string_set(source, "DISPLAY_FIELDS") == set(_DISPLAY_FIELDS)

    major = re.search(r"SUPPORTED_RESOLVER_SPEC_MAJOR\s*=\s*(\d+)", source)
    assert major, "потребитель не объявляет поддерживаемую мажорную версию"
    assert int(major.group(1)) == SUPPORTED_SPEC_MAJOR, (
        "половины границы умеют разные мажорные версии контракта — "
        "одна отвергнет то, что другая примет"
    )
