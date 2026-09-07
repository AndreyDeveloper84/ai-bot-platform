"""Клиентская половина границы резолвера — контракт §9.4, §9.4.1.

Проверяется не «клиент разбирает JSON», а три свойства, отсутствие которых
сделало DEFECT-C-02 невидимым:

* недоступность и расхождение контрактов **не сливаются** в одну ветку;
* различие между ними **структурное**: обычный отказ физически не доходит
  до проверки формы, поэтому ложного `contract_violation` быть не может;
* частично конформный ответ отвергается **целиком** (§53.1).
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from apps.integrations.ayla import recommendation_resolver_client as rrc
from apps.integrations.ayla.recommendation_resolver_client import (
    decision_contract_violation,
    resolve_recommendation,
)
from apps.integrations.ayla.recommendations_client import reset_recommendations_circuit


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.AYLA_BASE_URL = "https://ayla.test"
    settings.AYLA_INTERNAL_API_TOKEN = "token"  # noqa: S105  # pragma: allowlist secret
    reset_recommendations_circuit()
    yield
    reset_recommendations_circuit()


def _candidate(**overrides) -> dict:
    candidate = {
        "candidate": {"kind": "PROVIDER", "id": "11111111-1111-1111-1111-111111111111"},
        "rank": 1,
        "tier": 1,
        "reason_codes": ["ELIG_ACTIVE_OFFER", "MATCH_SERVICE_EXACT"],
        "evidence": [],
        "stage_verdicts": {"S2": "TIED"},
    }
    candidate.update(overrides)
    return candidate


def _body(*candidates, version: str = "1.0.0") -> dict:
    return {
        "data": {
            "decision_id": "d-1",
            "request_id": "r-1",
            "resolver_spec_version": version,
            "ordered": list(candidates) or [_candidate()],
            "excluded": [],
            "stage_activity": [],
            "reason_codes": [],
            "policy_versions": {"resolver_spec_version": version},
            "computed_at": "2026-09-07T00:00:00Z",
        }
    }


def _respond(status: int = 200, json_body: dict | None = None):
    request = httpx.Request("POST", "https://ayla.test/api/v1/internal/recommendation/resolve/")
    return httpx.Response(
        status, json=json_body if json_body is not None else _body(), request=request
    )


# ---------------------------------------------------------------------------
# Три исхода
# ---------------------------------------------------------------------------


def test_conformant_answer_is_ok():
    with patch.object(httpx.Client, "post", return_value=_respond()):
        outcome = resolve_recommendation(external_user_id="bot:1", payload={})
    assert outcome.state == "ok"
    assert outcome.decision["request_id"] == "r-1"


def test_network_failure_is_unavailable_not_a_contract_violation():
    """Молчание тут законно: подбор — необязательное украшение.

    И, что важнее, ложного `contract_violation` при обычном отказе быть
    не может **по построению**: отказ падает в транспортный `try` и до
    проверки формы не доходит.
    """
    with patch.object(httpx.Client, "post", side_effect=httpx.ConnectError("boom")):
        outcome = resolve_recommendation(external_user_id="bot:1", payload={})
    assert outcome.state == "unavailable"


def test_source_not_bound_is_unavailable():
    """503 «источник не привязан» — недоступность, а не пустая выдача."""
    with patch.object(
        httpx.Client, "post", return_value=_respond(503, {"error": {"code": "SERVICE_UNAVAILABLE"}})
    ):
        outcome = resolve_recommendation(external_user_id="bot:1", payload={})
    assert outcome.state == "unavailable"


def test_wrong_shape_is_loud_and_named():
    """Источник ответил, но не тем. Это «мы и они разошлись», и это громко."""
    with patch.object(httpx.Client, "post", return_value=_respond(200, {"recommendations": []})):
        outcome = resolve_recommendation(external_user_id="bot:1", payload={})
    assert outcome.state == "contract_violation"
    assert "data" in outcome.detail


# ---------------------------------------------------------------------------
# §53.1 — конформность целиком
# ---------------------------------------------------------------------------


def test_one_broken_element_invalidates_the_whole_answer():
    """Девятнадцать годных из двадцати не показываются. Цена названа и принята.

    Разреши мы «пропустить годные» — потребитель начал бы решать, какие
    из присланных рекомендаций увидит человек. Это отбор, то есть политика,
    то есть четвёртый авторитет, живущий в фильтре.
    """
    good = [_candidate() for _ in range(19)]
    broken = _candidate(rank="второй")
    violation = decision_contract_violation(_body(*good, broken))
    assert violation is not None
    assert "целиком" in violation


def test_violation_message_points_at_the_element_as_diagnostics():
    """Невалиден ответ — но сказать, ГДЕ источник нарушил, обязаны.

    Разница практическая: по такому сигналу чинят источник, а не экран.
    """
    violation = decision_contract_violation(_body(_candidate(), _candidate(reason_codes=[])))
    assert "ordered[1].reason_codes" in violation


def test_candidate_without_reason_codes_is_a_violation():
    """Кандидат без объяснения — строка, про которую нельзя сказать, почему она здесь."""
    assert decision_contract_violation(_body(_candidate(reason_codes=[]))) is not None


# ---------------------------------------------------------------------------
# Версия контракта
# ---------------------------------------------------------------------------


def test_unknown_major_version_is_refused_not_parsed():
    """§9.4: разбирать неизвестное запрещено — именно так расхождение доезжает молча."""
    violation = decision_contract_violation(_body(version="2.0.0"))
    assert violation is not None and "мажорная версия" in violation


def test_minor_version_bump_is_accepted():
    """Минорная версия совместима: иначе каждая правка политики требовала бы релиза бота."""
    assert decision_contract_violation(_body(version="1.7.3")) is None


# ---------------------------------------------------------------------------
# Строка для показа
# ---------------------------------------------------------------------------


def test_display_string_in_the_answer_is_a_violation():
    """Источник, снова собравший фразу за потребителя, нарушает §7.

    «Рейтинг 4.9» пришёл человеку именно такой строкой — из поля
    `reasoning_text`, которое источник посчитал своим делом.
    """
    body = _body(_candidate(reasoning_text="Рейтинг 4.9"))
    violation = decision_contract_violation(body)
    assert violation is not None and "строку для показа" in violation


def test_display_string_is_caught_at_any_depth():
    body = _body(_candidate(evidence=[{"kind": "RATING", "why_text": "почти пять звёзд"}]))
    assert decision_contract_violation(body) is not None


# ---------------------------------------------------------------------------
# Структурность различия
# ---------------------------------------------------------------------------


def test_transport_try_wraps_transport_only():
    """Ложного `contract_violation` не может быть — не потому, что стараются.

    Проверка не на поведении, а на устройстве: внутри `try` стоит ровно
    один вызов — транспорт. Появись там разбор ответа, TypeError разбора
    оказался бы «источник не ответил», и мы вернули бы себе ровно тот
    дефект, ради которого писался DRF-1556.
    """
    with open(rrc.__file__, encoding="utf-8") as fh:
        source = fh.read()
    guarded = source.split("    try:\n        with httpx.Client", 1)[1].split("    except", 1)[0]

    # Сначала утверждение О НАЛИЧИИ: без него две проверки ниже прошли бы
    # вхолостую в тот день, когда срез перестанет находить блок, — и тест
    # молча перестал бы что-либо стеречь. Ровно тот класс дефекта, который
    # ловит AST-гард репозитория.
    assert "http.post(" in guarded, "срез не нашёл транспортный вызов — тест смотрит не туда"

    assert "json()" not in guarded, "разбор ответа заехал внутрь транспортного try"
    assert "violation" not in guarded, "проверка формы заехала внутрь транспортного try"
