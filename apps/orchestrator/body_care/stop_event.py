"""Стоп-событие процедуры — контракт v0.2 §15 (BOT-7, DRF-2807).

Структура §15 дословно::

    event_type, severity, symptoms[], offering_id, component_id,
    occurred_at, reported_by

Что закреплено и что намеренно открыто:

* **Симптомы — закрытый реестр** (:class:`Symptom`) по списку §15. Контракт
  называет его «Candidate symptoms» — кандидатским, а не утверждённым,
  поэтому версия реестра :data:`SYMPTOM_REGISTRY_VERSION` несёт пометку
  ``candidate``: утверждение списка или его изменение — новая версия, не
  тихая правка. В событии симптомы без повторов и в порядке реестра:
  одинаковые события дают одинаковую запись.
* **``event_type`` и ``severity`` — открытые строки.** Контракт их значений не
  задаёт. Выдумать перечень здесь значило бы закрепить догадку как правило.
  ``severity = None`` — «ещё не классифицировано», а не «лёгкое»:
  классификация тяжести ждёт порогов R0/R1/R2 (D-4) и матрицы сигналов
  (D-5), и до них нечем отличить лёгкое от тяжёлого.
* ``component_id`` — у шага SPA-программы; у одиночной процедуры его нет.

Здесь нет отправки события в eventbus: имя события, его версия и потребители
решаются вместе с роутингом (§16). И нет никакой реакции на событие — что
делать при каком симптоме, решает политика, а не этот тип.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

#: Версия реестра симптомов: список §15 v0.2 помечен как кандидатский.
SYMPTOM_REGISTRY_VERSION: Final = "0.2.0-candidate"


class Symptom(StrEnum):
    """Симптомы стоп-события, «Candidate symptoms» §15. Значение равно имени."""

    PAIN = "PAIN"
    BURNING = "BURNING"
    STINGING = "STINGING"
    SWELLING = "SWELLING"
    BLISTERING = "BLISTERING"
    VISIBLE_INJURY = "VISIBLE_INJURY"
    NEW_REACTION = "NEW_REACTION"
    RESPIRATORY_SYMPTOMS = "RESPIRATORY_SYMPTOMS"
    LOSS_OF_CONSCIOUSNESS = "LOSS_OF_CONSCIOUSNESS"


_ORDER = {symptom: i for i, symptom in enumerate(Symptom)}


class StopEventError(ValueError):
    """Нарушен инвариант стоп-события. Программная ошибка, не сообщение человека."""


def _required(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StopEventError(f"{name} обязателен")
    return value


@dataclass(frozen=True)
class StopEvent:
    """Одно стоп-событие процедуры (§15)."""

    event_type: str
    severity: str | None
    symptoms: tuple[Symptom, ...]
    offering_id: str
    component_id: str | None
    occurred_at: datetime
    reported_by: str

    def __post_init__(self) -> None:
        _required("event_type", self.event_type)
        _required("offering_id", self.offering_id)
        _required("reported_by", self.reported_by)
        if self.severity is not None:
            _required("severity", self.severity)
        if self.component_id is not None:
            _required("component_id", self.component_id)
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise StopEventError("occurred_at — с часовым поясом")
        normalized = _normalize(self.symptoms)
        object.__setattr__(self, "symptoms", normalized)

    @property
    def is_classified(self) -> bool:
        """Есть ли оценка тяжести. ``False`` — не «лёгкое», а «не оценено»."""

        return self.severity is not None

    def as_record(self) -> dict[str, Any]:
        """Поля §15 в их порядке — для журнала (§20)."""

        return {
            "event_type": self.event_type,
            "severity": self.severity,
            "symptoms": [s.value for s in self.symptoms],
            "offering_id": self.offering_id,
            "component_id": self.component_id,
            "occurred_at": self.occurred_at.isoformat(),
            "reported_by": self.reported_by,
            "symptom_registry_version": SYMPTOM_REGISTRY_VERSION,
        }


def _normalize(symptoms: Iterable[Any]) -> tuple[Symptom, ...]:
    if isinstance(symptoms, str):
        # Одна строка — не список симптомов: иначе "PAIN" разобралось бы по буквам.
        raise StopEventError("symptoms — последовательность членов реестра, не строка")
    seen: set[Symptom] = set()
    for item in symptoms:
        if not isinstance(item, Symptom):
            raise StopEventError(f"симптом вне реестра §15: {item!r}")
        seen.add(item)
    return tuple(sorted(seen, key=_ORDER.__getitem__))


__all__ = [
    "SYMPTOM_REGISTRY_VERSION",
    "StopEvent",
    "StopEventError",
    "Symptom",
]
