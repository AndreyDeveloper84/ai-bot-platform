"""Соединение итога скрининга с поведением — D-5 в два слоя (BOT-4b, DRF-2985).

Действующая модель — проект D-5 ``d5-matrix-draft-0.1`` (каркас подтверждён
владельцем 20.10, запись — решения владельца от 20.10, раздел о Body Care, в
документах репозитория Ayla). Старый §5 дизайна BOT-4 («не оценено» → ``UNKNOWN``,
DNP/MAR → ``STOP`` хода) заменён: движок блокирует ход целиком
(``decision_readiness/engine``), и запрет одного пилинга закрыл бы весь каталог.

Два слоя:

* **слой процедуры** — :func:`candidate_effect`: итог влияет только на ОДНУ
  услугу, через код кандидата каталожного реестра (имена — за резолвером,
  реестр 1.8.0);
* **слой хода** — :func:`turn_effect`: общий ``SafetyState`` меняет ТОЛЬКО
  ``S1_ROUTE`` → существующий ``STOP`` + ``handoff=REQUIRED`` (§16 R3). Нового
  emergency-состояния нет (§24.9). «Не оценено» ход не трогает.

Порядок строгости :data:`STRICTNESS` — предложение D-5 под версией
:data:`ROUTING_VERSION`; профильное ревью P-05 может переставить DNP / MAR /
CLAR — тогда новая версия, не тихая правка.

Решения владельца 20.10, зашитые здесь как решения, а не как настройки:

* A5 — нет пройденного опроса (``NotEvaluated``) → показать с пометкой;
* В-4 — ``MEDICAL_ASSESSMENT_REQUIRED`` → показать с пометкой «нужна
  консультация», не скрывать. «Нужен опрос» и «нужна консультация» различает
  ``screening_status`` в записи итога, а не отдельный код кандидата.

Чего здесь нет: предметной матрицы «сигнал → исход» (P-05) — она в политике
(:class:`~apps.orchestrator.body_care.screening_result.ScreeningPolicy`);
исхода P1 «проверка не требуется» — признака в каталоге нет, и такая услуга
сегодня получает пометку (строже, не мягче). Показ с пометкой ≠ запись без
скрининга: запрет автоподтверждения на дверях записи — отдельная работа.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from apps.orchestrator.body_care.screening_result import (
    NotEvaluated,
    OfferingContext,
    ScreeningResult,
    ScreeningStatus,
)
from apps.orchestrator.decision_readiness.safety_input import Handoff, SafetyState

#: Версия этого соединения — ``routing_version`` §19.
ROUTING_VERSION: Final = "d5-matrix-draft-0.1"

#: Маркер «не оценено» в порядке строгости: это не статус §11, а отсутствие оценки.
NOT_EVALUATED: Final = "NOT_EVALUATED"

#: От строжайшего к мягчайшему. Место ``NOT_EVALUATED`` между MAR и CLAR —
#: «не знаю» строже «надо уточнить», но мягче явного запрета (D-5 §1).
STRICTNESS: Final[tuple[str, ...]] = (
    ScreeningStatus.S1_ROUTE.value,
    ScreeningStatus.DO_NOT_PROCEED.value,
    ScreeningStatus.MEDICAL_ASSESSMENT_REQUIRED.value,
    NOT_EVALUATED,
    ScreeningStatus.CLARIFICATION_REQUIRED.value,
    ScreeningStatus.CLEAR_TO_PROCEED.value,
)

_RANK = {name: i for i, name in enumerate(STRICTNESS)}

Outcome = ScreeningResult | NotEvaluated


class ScreeningRoutingError(ValueError):
    """Вход, по которому соединение не может ответить честно. Ошибка программы."""


def _key(outcome: Outcome) -> str:
    if isinstance(outcome, NotEvaluated):
        return NOT_EVALUATED
    if isinstance(outcome, ScreeningResult):
        return outcome.screening_status.value
    raise ScreeningRoutingError(f"не итог скрининга: {outcome!r}")


def strictest(outcomes: Iterable[Outcome]) -> Outcome:
    """Строжайший из итогов одной услуги (компоненты SPA, несколько оценок).

    Пустой вход — ошибка, а не допуск: «оценок нет» не значит «всё чисто».
    При равной строгости — первый: итоги одного ранга равны для поведения.
    """

    items = list(outcomes)
    if not items:
        raise ScreeningRoutingError("нет ни одного итога — нечего сворачивать")
    return min(items, key=lambda o: _RANK[_key(o)])


class CandidateCode(StrEnum):
    """Коды кандидата каталожного реестра (``recommendation/_reason_codes``, 1.8.0)."""

    ELIG_SHOWN_REQUIRES_CHECK = "ELIG_SHOWN_REQUIRES_CHECK"
    ELIG_EXCLUDED_SAFETY = "ELIG_EXCLUDED_SAFETY"
    ELIG_SAFETY_CLEARED = "ELIG_SAFETY_CLEARED"


#: Слой процедуры, D-5 §1 / §5a с решениями A5 и В-4. Ключ — элемент :data:`STRICTNESS`.
CANDIDATE_EFFECT: Final[dict[str, CandidateCode]] = {
    # S1 — слой хода; по кандидату — исключение, fail-closed.
    ScreeningStatus.S1_ROUTE.value: CandidateCode.ELIG_EXCLUDED_SAFETY,
    ScreeningStatus.DO_NOT_PROCEED.value: CandidateCode.ELIG_EXCLUDED_SAFETY,
    # В-4, 20.10: показать с «нужна консультация».
    ScreeningStatus.MEDICAL_ASSESSMENT_REQUIRED.value: CandidateCode.ELIG_SHOWN_REQUIRES_CHECK,
    # A5, 20.10: показать с «нужен опрос».
    NOT_EVALUATED: CandidateCode.ELIG_SHOWN_REQUIRES_CHECK,
    ScreeningStatus.CLARIFICATION_REQUIRED.value: CandidateCode.ELIG_SHOWN_REQUIRES_CHECK,
    ScreeningStatus.CLEAR_TO_PROCEED.value: CandidateCode.ELIG_SAFETY_CLEARED,
}


def candidate_effect(outcome: Outcome) -> CandidateCode:
    """Что итог делает с ЭТОЙ услугой в рекомендации."""

    return CANDIDATE_EFFECT[_key(outcome)]


@dataclass(frozen=True)
class TurnEffect:
    """Что итог делает с общим ходом. Существующие состояние и handoff, не новые."""

    state: SafetyState
    handoff: Handoff


#: Единственное, что поднимается в слой хода (§16 R3).
_S1_TURN = TurnEffect(SafetyState.STOP, Handoff.REQUIRED)


def turn_effect(outcome: Outcome) -> TurnEffect | None:
    """Меняет ли итог общий ход. ``None`` — нет: ход идёт как без Body Care."""

    return _S1_TURN if _key(outcome) == ScreeningStatus.S1_ROUTE.value else None


def wire_entry(offering: OfferingContext, outcome: Outcome) -> dict[str, str] | None:
    """Запись поля ``screening[]`` запроса резолвера (спецификация 1.4.0).

    ``NotEvaluated`` не шлётся: отсутствие записи и есть «не оценено». Версия
    конфигурации — та, по которой итог принят, а не текущая: устаревший итог
    каталог распознает сам.
    """

    if isinstance(outcome, NotEvaluated):
        return None
    if not isinstance(outcome, ScreeningResult):
        raise ScreeningRoutingError(f"не итог скрининга: {outcome!r}")
    return {
        "offering_ref": offering.offering_id,
        "offering_configuration_version": outcome.versions["offering_configuration_version"],
        "screening_status": outcome.screening_status.value,
    }


__all__ = [
    "CANDIDATE_EFFECT",
    "NOT_EVALUATED",
    "ROUTING_VERSION",
    "STRICTNESS",
    "CandidateCode",
    "ScreeningRoutingError",
    "TurnEffect",
    "candidate_effect",
    "strictest",
    "turn_effect",
    "wire_entry",
]
