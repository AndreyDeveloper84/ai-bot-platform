"""DRF-2729 — клиент ручки знания: предмет лицензии из ответа каталога.

Фикстура провода (``fixtures/knowledge_procedures_live.json``) СНЯТА с живой
ручки каталога (beautygo_backend ``77de9bcd``), а не написана от руки: обе
стороны данных, построенные одним автором, сходятся друг с другом, а не с
каталогом. Нарушения инвариантов ниже получены правкой этого же живого
ответа — по одному полю за раз.

Узлы:

* k1–k3 — три ответа каталога: ``known``, ``unknown``, ``404``;
* k4 — каталог не ответил → ``UNAVAILABLE`` (не ``UNKNOWN``);
* k5 — ответ нарушил инвариант → ``UNAVAILABLE``; близнец — тот же ответ без
  правки даёт ``KNOWN`` (k1), и правка, инварианта не нарушающая, тоже;
* k6 — не настроено → ``UNAVAILABLE`` без единого запроса;
* k7 — форма запроса: метод, путь, параметр, Bearer, без заголовка субъекта;
* k8 — предохранитель: сбои открывают, 404 — нет;
* k9 — слова утверждений клиент не выносит ни в предмет, ни в журнал;
* k10 — бюджеты времени — литералом.
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Callable, Generator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from apps.integrations.ayla import knowledge_client as kc
from apps.orchestrator.knowledge_licence import LicensedClaim, SubjectState

_LIVE = json.loads(
    (Path(__file__).parent / "fixtures" / "knowledge_procedures_live.json").read_text(
        encoding="utf-8"
    )
)["responses"]

KNOWN_BODY: dict[str, Any] = _LIVE["known_by_salon_service"]["body"]
UNKNOWN_BODY: dict[str, Any] = _LIVE["unknown_by_salon_service"]["body"]
NOT_FOUND_BODY: dict[str, Any] = _LIVE["not_found"]["body"]

# Идентификаторы живого ответа — литералами, не чтением из фикстуры: узел,
# собранный из того же файла, что и вход, не заметил бы перепутанных полей.
VERIFIED_SERVICE = "d98cfcc4-ba87-4270-bef5-c459f69f0f2e"
REVIEW_SERVICE = "85533e18-94a5-4b5d-9dd0-c5bb6080fa7a"
ABSENT_SERVICE = "00000000-0000-4000-8000-000000002729"
TEMPLATE = "e92856eb-c51f-43fc-baff-0d2d30061b46"
CAPABILITY_CLAIM = "d698ee16-a81f-4aef-b9ea-85ba27e6eb8c"
GOAL_LINK_CLAIM = "e961f272-9fed-4217-b796-d5f700078782"

TOKEN = "knowledge-token-sentinel"  # noqa: S105  # pragma: allowlist secret
BASE = "https://ayla.test"

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def wire(settings: Any) -> Generator[Callable[[Handler], list[httpx.Request]], None, None]:
    """Настоящий клиент поверх подменённого транспорта; возвращает журнал запросов."""
    settings.AYLA_BASE_URL = BASE
    settings.AYLA_INTERNAL_API_TOKEN = TOKEN
    kc.close_knowledge_client()
    kc.reset_knowledge_circuit()

    def install(handler: Handler) -> list[httpx.Request]:
        seen: list[httpx.Request] = []

        def recording(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        kc.close_knowledge_client()
        kc._http = httpx.Client(transport=httpx.MockTransport(recording))
        return seen

    yield install
    kc.close_knowledge_client()
    kc.reset_knowledge_circuit()


def _answers(status: int, body: Any) -> Handler:
    return lambda request: httpx.Response(status, json=body)


def _known(edit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Живой ответ ``known`` с одной правкой."""
    body = copy.deepcopy(KNOWN_BODY)
    edit(body["data"])
    return body


# ─── k1–k3: три ответа каталога ──────────────────────────────────────────────


def test_k1_known_answer_becomes_a_known_subject_with_both_claims(wire: Any) -> None:
    wire(_answers(200, KNOWN_BODY))

    subject = kc.read_subject(salon_service_id=VERIFIED_SERVICE)

    assert subject.state is SubjectState.KNOWN
    assert subject.template_id == TEMPLATE
    assert subject.salon_service_id == VERIFIED_SERVICE
    assert subject.claims == (
        LicensedClaim(
            claim_id=CAPABILITY_CLAIM,
            kind="capability",
            key="even-tone",
            valid_until=datetime(2027, 10, 1, 9, 0, tzinfo=timezone.utc),
        ),
        LicensedClaim(
            claim_id=GOAL_LINK_CLAIM, kind="goal_link", key="probe-skin", valid_until=None
        ),
    )


def test_k2_unknown_answer_becomes_an_unknown_subject_without_claims(wire: Any) -> None:
    wire(_answers(200, UNKNOWN_BODY))

    subject = kc.read_subject(salon_service_id=REVIEW_SERVICE)

    assert subject.state is SubjectState.UNKNOWN
    assert subject.claims == ()
    assert subject.template_id == TEMPLATE
    assert subject.salon_service_id == REVIEW_SERVICE


def test_k3_not_found_is_unavailable_and_never_unknown(wire: Any) -> None:
    """404 — каталог такого предмета не знает. «Знания нет» из этого не следует."""
    seen = wire(_answers(404, NOT_FOUND_BODY))

    subject = kc.read_subject(salon_service_id=ABSENT_SERVICE)

    assert len(seen) == 1
    assert subject.state is SubjectState.UNAVAILABLE
    assert subject.claims == ()
    assert subject.salon_service_id == ABSENT_SERVICE


# ─── k4: каталог не ответил ──────────────────────────────────────────────────


def _raises(exc_type: type[httpx.TransportError]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type("down", request=request)

    return handler


@pytest.mark.parametrize(
    "handler",
    [
        _raises(httpx.ReadTimeout),
        _raises(httpx.ConnectTimeout),
        _raises(httpx.ConnectError),
        _answers(500, {}),
        _answers(503, {}),
        _answers(401, {"error": {"code": "UNAUTHORIZED"}}),
        _answers(400, {"error": {"code": "VALIDATION_ERROR"}}),
        lambda request: httpx.Response(200, content=b"<html>gateway</html>"),
        _answers(200, ["not", "an", "object"]),
        _answers(200, {"state": "known"}),  # ответ без конверта data
        _answers(200, {"data": None}),
    ],
    ids=[
        "read-timeout",
        "connect-timeout",
        "connect-error",
        "http-500",
        "http-503",
        "http-401",
        "http-400",
        "not-json",
        "top-level-list",
        "no-envelope",
        "data-null",
    ],
)
def test_k4_catalog_did_not_answer_is_unavailable(wire: Any, handler: Handler) -> None:
    seen = wire(handler)

    subject = kc.read_subject(salon_service_id=VERIFIED_SERVICE)

    assert len(seen) == 1  # спросили — и не получили; а не «не спрашивали»
    assert subject.state is SubjectState.UNAVAILABLE
    assert subject.claims == ()


# ─── k5: ответ нарушил инвариант ─────────────────────────────────────────────


def _capability(data: dict[str, Any]) -> dict[str, Any]:
    return data["claims"][0]


def _link(data: dict[str, Any]) -> dict[str, Any]:
    return data["claims"][0]["goal_links"][0]


_VIOLATIONS: dict[str, Callable[[dict[str, Any]], None]] = {
    "known-without-claims": lambda d: d.__setitem__("claims", []),
    "unknown-with-claims": lambda d: d.__setitem__("state", "unknown"),
    "state-outside-vocabulary": lambda d: d.__setitem__("state", "partial"),
    "claims-not-a-list": lambda d: d.__setitem__("claims", {"0": "x"}),
    "subject-missing": lambda d: d.pop("subject"),
    "claim-without-provenance": lambda d: _capability(d).pop("provenance"),
    "provenance-without-source-ref": lambda d: _capability(d)["provenance"].__setitem__(
        "source_ref", ""
    ),
    "provenance-without-confirmed-at": lambda d: _capability(d)["provenance"].__setitem__(
        "confirmed_at", None
    ),
    "link-without-provenance": lambda d: _link(d).pop("provenance"),
    "claim-without-id": lambda d: _capability(d).__setitem__("claim_id", ""),
    "claim-without-key": lambda d: _capability(d).pop("key"),
    "claim-of-unexpected-kind": lambda d: _capability(d).__setitem__("kind", "prohibited"),
    "link-of-unexpected-kind": lambda d: _link(d).__setitem__("kind", "capability"),
    "goal-links-not-a-list": lambda d: _capability(d).__setitem__("goal_links", None),
    "course-without-variability-note": lambda d: _link(d).__setitem__("variability_note", ""),
    "horizon-without-variability-note": lambda d: _link(d).update(
        variability_note="   ", course_pattern=""
    ),
    "valid-until-without-zone": lambda d: _capability(d)["provenance"].__setitem__(
        "valid_until", "2027-10-01T09:00:00"
    ),
    "valid-until-unparseable": lambda d: _capability(d)["provenance"].__setitem__(
        "valid_until", "next year"
    ),
    "valid-until-not-a-string": lambda d: _capability(d)["provenance"].__setitem__(
        "valid_until", 1790000000
    ),
}


@pytest.mark.parametrize("violation", sorted(_VIOLATIONS))
def test_k5_answer_breaking_an_invariant_is_unavailable(wire: Any, violation: str) -> None:
    wire(_answers(200, _known(_VIOLATIONS[violation])))

    subject = kc.read_subject(salon_service_id=VERIFIED_SERVICE)

    assert subject.state is SubjectState.UNAVAILABLE
    assert subject.claims == ()


_HARMLESS: dict[str, Callable[[dict[str, Any]], None]] = {
    # Курса и срока нет — оговорка не обязательна: пара не разорвана.
    "link-with-neither-course-nor-note": lambda d: _link(d).update(
        variability_note="", course_pattern="", result_horizon=""
    ),
    "capability-without-links": lambda d: _capability(d).__setitem__("goal_links", []),
    "unknown-field-added": lambda d: d.__setitem__("later_field", {"any": "thing"}),
}


@pytest.mark.parametrize("edit", sorted(_HARMLESS))
def test_k5_twin_an_edit_that_breaks_no_invariant_stays_known(wire: Any, edit: str) -> None:
    """Близнец k5: клиент отвергает НАРУШЕНИЯ, а не всякий изменённый ответ."""
    wire(_answers(200, _known(_HARMLESS[edit])))

    subject = kc.read_subject(salon_service_id=VERIFIED_SERVICE)

    assert subject.state is SubjectState.KNOWN
    assert subject.claims[0].claim_id == CAPABILITY_CLAIM


# ─── k6: не настроено ────────────────────────────────────────────────────────


@pytest.mark.parametrize("missing", ["AYLA_BASE_URL", "AYLA_INTERNAL_API_TOKEN"])
def test_k6_not_configured_is_unavailable_without_a_request(
    wire: Any, settings: Any, missing: str
) -> None:
    seen = wire(_answers(200, KNOWN_BODY))
    # Близнец в том же узле: настроенный клиент запрос ШЛЁТ — иначе «запроса
    # не было» выполнялось бы и у подмены, которая запросов не видит.
    configured = kc.read_subject(salon_service_id=VERIFIED_SERVICE)
    assert configured.state is SubjectState.KNOWN
    assert len(seen) == 1

    setattr(settings, missing, "")
    subject = kc.read_subject(salon_service_id=VERIFIED_SERVICE)

    assert subject.state is SubjectState.UNAVAILABLE
    assert len(seen) == 1  # журнал не вырос: второй вызов в сеть не пошёл


# ─── k7: форма запроса ───────────────────────────────────────────────────────


def test_k7_request_shape(wire: Any) -> None:
    seen = wire(_answers(200, KNOWN_BODY))

    kc.read_subject(salon_service_id=VERIFIED_SERVICE)

    (request,) = seen
    assert request.method == "GET"
    assert request.url.path == "/api/v1/internal/knowledge/procedures/"
    assert dict(request.url.params) == {"salon_service_id": VERIFIED_SERVICE}
    assert request.headers["authorization"] == "Bearer knowledge-token-sentinel"
    assert request.headers["x-request-id"]
    # О человеке в запросе ничего нет — и заголовка субъекта быть не должно.
    assert "x-external-user-id" not in request.headers


# ─── k8: предохранитель ──────────────────────────────────────────────────────


def test_k8_three_failures_open_the_circuit_and_the_fourth_call_stays_home(wire: Any) -> None:
    seen = wire(_answers(500, {}))

    for _ in range(3):
        kc.read_subject(salon_service_id=VERIFIED_SERVICE)
    assert len(seen) == 3

    subject = kc.read_subject(salon_service_id=VERIFIED_SERVICE)

    assert len(seen) == 3  # четвёртый вызов в сеть не пошёл
    assert subject.state is SubjectState.UNAVAILABLE


def test_k8_twin_not_found_does_not_open_the_circuit(wire: Any) -> None:
    """404 — каталог жив. Пять «не нашёл» подряд не должны глушить чтение остальных."""
    seen = wire(_answers(404, NOT_FOUND_BODY))

    for _ in range(5):
        kc.read_subject(salon_service_id=ABSENT_SERVICE)

    assert len(seen) == 5


def test_k8_malformed_answers_feed_the_circuit(wire: Any) -> None:
    seen = wire(_answers(200, _known(_VIOLATIONS["known-without-claims"])))

    for _ in range(4):
        kc.read_subject(salon_service_id=VERIFIED_SERVICE)

    assert len(seen) == 3


def test_k8_success_clears_earlier_failures(wire: Any) -> None:
    answers = iter([500, 500, 200, 500, 500, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(answers)
        return httpx.Response(status, json=KNOWN_BODY if status == 200 else {})

    seen = wire(handler)

    states = [kc.read_subject(salon_service_id=VERIFIED_SERVICE).state for _ in range(6)]

    assert len(seen) == 6  # ни разу не набралось трёх сбоев подряд
    assert states[2] is SubjectState.KNOWN
    assert states[5] is SubjectState.KNOWN


# ─── k9: слова остаются в клиенте ────────────────────────────────────────────


def test_k9_claim_wording_never_leaves_the_client(
    wire: Any, caplog: pytest.LogCaptureFixture
) -> None:
    # Положительный контроль: слова в ответе каталога ЕСТЬ — иначе их
    # отсутствие в предмете ничего бы не значило.
    assert json.dumps(KNOWN_BODY).count("SYNTHETIC") == 8
    caplog.set_level(logging.DEBUG)

    wire(_answers(200, KNOWN_BODY))
    known = kc.read_subject(salon_service_id=VERIFIED_SERVICE)
    wire(_answers(200, _known(_VIOLATIONS["course-without-variability-note"])))
    broken = kc.read_subject(salon_service_id=VERIFIED_SERVICE)

    assert known.state is SubjectState.KNOWN
    assert broken.state is SubjectState.UNAVAILABLE
    assert "SYNTHETIC" not in repr(known) + repr(broken)
    assert "SYNTHETIC" not in caplog.text
    # Отказ в журнале назван по имени — близнец: журнал не пуст.
    assert "malformed:course_or_horizon_without_variability_note" in caplog.text


# ─── k10: бюджеты ────────────────────────────────────────────────────────────


def test_k10_time_budgets_are_the_decided_numbers(wire: Any) -> None:
    seen = wire(_answers(200, KNOWN_BODY))

    kc.read_subject(salon_service_id=VERIFIED_SERVICE)

    assert seen[0].extensions["timeout"] == {
        "connect": 4.0,
        "read": 1.0,
        "write": 1.0,
        "pool": 4.0,
    }
