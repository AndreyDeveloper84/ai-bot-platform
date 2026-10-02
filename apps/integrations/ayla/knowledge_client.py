"""Клиент ручки чтения знания о процедуре (DRF-2729, контракт DRF-2719 §5.1).

``GET /api/v1/internal/knowledge/procedures/?salon_service_id=…`` — что
каталог разрешает говорить человеку о процедуре (ручка — beautygo_backend
DRF-2724). Один запрос — один предмет.

### Что возвращает и чего не возвращает

:func:`read_subject` отдаёт :class:`LicensedSubject` — предмет, его состояние
и идентификаторы подтверждённых утверждений со сроками. **Текстов утверждений
он не возвращает вовсе**: в теневом листе их некому читать, а текст, которого
нет в ходе, не может попасть ни в промпт, ни в ответ, ни в журнал. Ответ
ручки разбирается целиком — чтобы проверить его инварианты, — и слова
остаются в этом модуле.

### Три состояния

``KNOWN`` и ``UNKNOWN`` говорит каталог. ``UNAVAILABLE`` говорит этот клиент —
каждый раз, когда спросить не удалось или ответу нельзя верить: каталог не
настроен, не ответил, ответил ошибкой, не знает такого предмета, прислал
ответ, нарушающий инварианты контракта. Ни один из этих случаев не читается
как «знания нет» и тем более как «ограничений нет».

Функция не бросает: теневой читатель не должен стоить человеку реплики.

### Бюджеты — допущения, не замер

Чтение идёт внутри хода, до ответа человеку, поэтому бюджеты короткие и
предохранитель строже, чем у пользовательских клиентов. Соединение — 4 с:
холодное TLS-рукопожатие на пилоте замерено в 3,77 с (см.
``goals_client``), и бюджет меньше него не дал бы соединению прогреться
никогда. Чтение — 1 с. Числа подобраны по этому единственному замеру чужого
клиента; собственного замера у ручки знания нет.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

import httpx
from django.conf import settings

from apps.integrations.ayla.request_id import with_request_id
from apps.integrations.ayla.url_builder import AylaUrlBuilder, AylaUrlError
from apps.orchestrator.knowledge_licence import LicensedClaim, LicensedSubject, SubjectState

logger = logging.getLogger(__name__)

PATH: Final[str] = "internal/knowledge/procedures/"

CONNECT_TIMEOUT_S: Final[float] = 4.0
READ_TIMEOUT_S: Final[float] = 1.0
KEEPALIVE_EXPIRY_S: Final[float] = 50.0

# Строже, чем у клиентов, которых ждёт человек: теневое чтение обязано
# отступить раньше, чем станет заметно.
CIRCUIT_FAILURE_WINDOW_S: Final[float] = 60.0
CIRCUIT_FAILURE_THRESHOLD: Final[int] = 3
CIRCUIT_OPEN_DURATION_S: Final[float] = 60.0


@dataclass
class _Circuit:
    """Предохранитель процесса — та же форма, что у ``goals_client``."""

    failures: list[float] = field(default_factory=list)
    opened_at: float | None = None

    def is_open(self, *, now: float) -> bool:
        if self.opened_at is None:
            return False
        if now - self.opened_at >= CIRCUIT_OPEN_DURATION_S:
            self.opened_at = None
            self.failures = []
            return False
        return True

    def record_failure(self, *, now: float) -> None:
        cutoff = now - CIRCUIT_FAILURE_WINDOW_S
        self.failures = [t for t in self.failures if t >= cutoff]
        self.failures.append(now)
        if len(self.failures) >= CIRCUIT_FAILURE_THRESHOLD and self.opened_at is None:
            self.opened_at = now
            logger.warning(
                "knowledge_client.circuit_opened failures=%d window_s=%.0f",
                len(self.failures),
                CIRCUIT_FAILURE_WINDOW_S,
            )

    def record_success(self) -> None:
        self.failures = []
        self.opened_at = None


_circuit = _Circuit()
_http: httpx.Client | None = None


def reset_knowledge_circuit() -> None:
    """Сбросить предохранитель — для изоляции тестов."""
    _circuit.record_success()


def _get_client() -> httpx.Client:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.Client(
            timeout=httpx.Timeout(
                connect=CONNECT_TIMEOUT_S,
                read=READ_TIMEOUT_S,
                write=READ_TIMEOUT_S,
                pool=CONNECT_TIMEOUT_S,
            ),
            limits=httpx.Limits(keepalive_expiry=KEEPALIVE_EXPIRY_S),
        )
    return _http


def close_knowledge_client() -> None:
    """Закрыть пул — для тестов и корректного завершения процесса."""
    global _http
    if _http is not None and not _http.is_closed:
        _http.close()
    _http = None


class _Malformed(Exception):
    """Ответ нарушает инвариант контракта. Сообщение — имя нарушения, без слов ответа."""


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _valid_until(provenance: dict[str, Any]) -> datetime | None:
    raw = provenance.get("valid_until")
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise _Malformed("valid_until_not_a_string")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise _Malformed("valid_until_unparseable") from exc
    if parsed.tzinfo is None:
        # Срок без пояса нельзя сравнить с «сейчас» — а молча принять его за
        # какой-то пояс значило бы сдвинуть срок годности на часы.
        raise _Malformed("valid_until_naive")
    return parsed


def _provenance(row: dict[str, Any]) -> dict[str, Any]:
    provenance = row.get("provenance")
    if not isinstance(provenance, dict):
        raise _Malformed("provenance_missing")
    if not _text(provenance.get("confirmed_at")) or not _text(provenance.get("source_ref")):
        raise _Malformed("provenance_incomplete")
    return provenance


def _claim(row: Any, *, kind: str, key_field: str) -> LicensedClaim:
    if not isinstance(row, dict):
        raise _Malformed("claim_not_an_object")
    if row.get("kind") != kind:
        raise _Malformed("claim_kind_unexpected")
    claim_id, key = _text(row.get("claim_id")), _text(row.get(key_field))
    if not claim_id or not key:
        raise _Malformed("claim_identity_missing")
    return LicensedClaim(
        claim_id=claim_id, kind=kind, key=key, valid_until=_valid_until(_provenance(row))
    )


def _goal_link(row: Any) -> LicensedClaim:
    claim = _claim(row, kind="goal_link", key_field="goal_key")
    # Курс и срок — только парой с оговоркой о разбросе (решение владельца
    # 02.10, блок B). Ручка держит пару; ответ, где она разорвана, — не тот
    # ответ, которому можно верить.
    qualified = bool(_text(row.get("variability_note")).strip())
    if not qualified and (_text(row.get("course_pattern")) or _text(row.get("result_horizon"))):
        raise _Malformed("course_or_horizon_without_variability_note")
    return claim


def _parse(document: Any, *, salon_service_id: str) -> LicensedSubject:
    """Ответ ручки → предмет лицензии. Бросает :class:`_Malformed`."""
    if not isinstance(document, dict):
        raise _Malformed("data_not_an_object")
    state, rows, subject = document.get("state"), document.get("claims"), document.get("subject")
    if state not in ("known", "unknown"):
        raise _Malformed("state_unexpected")
    if not isinstance(rows, list) or not isinstance(subject, dict):
        raise _Malformed("shape_unexpected")
    # known ⇔ утверждения есть. Пустой known читался бы как «знание есть, но
    # сказать нечего», непустой unknown — как знание без разрешения.
    if (state == "known") != bool(rows):
        raise _Malformed("known_without_claims" if state == "known" else "unknown_with_claims")

    claims: list[LicensedClaim] = []
    for row in rows:
        claims.append(_claim(row, kind="capability", key_field="key"))
        links = row.get("goal_links")
        if not isinstance(links, list):
            raise _Malformed("goal_links_not_a_list")
        claims.extend(_goal_link(link) for link in links)

    return LicensedSubject(
        template_id=_text(subject.get("template_id")),
        state=SubjectState.KNOWN if state == "known" else SubjectState.UNKNOWN,
        claims=tuple(claims),
        salon_service_id=salon_service_id,
    )


def _unavailable(salon_service_id: str, *, why: str) -> LicensedSubject:
    logger.info("knowledge_client.unavailable why=%s salon_service=%s", why, salon_service_id)
    return LicensedSubject(
        template_id="", state=SubjectState.UNAVAILABLE, salon_service_id=salon_service_id
    )


def read_subject(*, salon_service_id: str) -> LicensedSubject:
    """Спросить каталог об одной услуге салона. Не бросает — см. докстринг модуля."""
    base_url = getattr(settings, "AYLA_BASE_URL", "")
    token = getattr(settings, "AYLA_INTERNAL_API_TOKEN", "")
    if not base_url or not token:
        return _unavailable(salon_service_id, why="not_configured")
    try:
        url = AylaUrlBuilder(base_url).build(PATH)
    except AylaUrlError:
        return _unavailable(salon_service_id, why="bad_base_url")

    if _circuit.is_open(now=time.monotonic()):
        return _unavailable(salon_service_id, why="circuit_open")

    # Только Bearer: о человеке в запросе ничего нет, и заголовка субъекта у
    # этой ручки быть не должно.
    headers = with_request_id({"Authorization": f"Bearer {token}", "Accept": "application/json"})
    timeout = httpx.Timeout(
        connect=CONNECT_TIMEOUT_S,
        read=READ_TIMEOUT_S,
        write=READ_TIMEOUT_S,
        pool=CONNECT_TIMEOUT_S,
    )
    try:
        response = _get_client().get(
            url, params={"salon_service_id": salon_service_id}, headers=headers, timeout=timeout
        )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        _circuit.record_failure(now=time.monotonic())
        return _unavailable(salon_service_id, why=f"network:{type(exc).__name__}")

    if response.status_code >= 500:
        _circuit.record_failure(now=time.monotonic())
        return _unavailable(salon_service_id, why=f"http_{response.status_code}")
    if response.status_code != 200:
        # 4xx — каталог жив, спросили не то (в том числе 404: такого предмета
        # он не знает). Предохранитель не трогаем; «знания нет» из этого не
        # следует.
        return _unavailable(salon_service_id, why=f"http_{response.status_code}")

    try:
        body = response.json()
        if not isinstance(body, dict) or "data" not in body:
            raise _Malformed("no_data_envelope")
        subject = _parse(body["data"], salon_service_id=salon_service_id)
    except (_Malformed, ValueError) as exc:
        # Ответ пришёл, верить ему нельзя — это сбой контракта, а не сети;
        # в журнал идёт имя нарушения, не содержимое ответа.
        _circuit.record_failure(now=time.monotonic())
        return _unavailable(
            salon_service_id,
            why=f"malformed:{exc}" if isinstance(exc, _Malformed) else "malformed:json",
        )

    _circuit.record_success()
    return subject


__all__ = [
    "PATH",
    "close_knowledge_client",
    "read_subject",
    "reset_knowledge_circuit",
]
