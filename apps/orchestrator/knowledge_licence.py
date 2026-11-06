"""Лицензия знания на ход — что читатель знания отдал в ЭТОТ ход (DRF-2725).

Контракты: затвор утверждений — DRF-2718, путь чтения знания — DRF-2719.
Лицензия — их общий объект: создаёт её читатель знания (клиент каталога,
отдельный лист), читает исходящий затвор
(:func:`apps.orchestrator.safety.gate.guard_outbound`).

Здесь — ТОЛЬКО тип и вопросы, которые затвор вправе ему задать. Читателя в
этом листе нет: до его появления лицензию на живом пути никто не создаёт, и
каждый ход несёт ``None`` — «в этот ход знания не читали».

### Почему это объект, а не пара полей в ответе

Ответ консьержа проверяется дважды — консьержем до записи в историю и каналом
перед отправкой. Знание читает только консьерж. Лицензия, оставшаяся его
локальной переменной, до канальной проверки не доедет, и та будет судить
вслепую. Поэтому лицензия — часть РЕЗУЛЬТАТА хода и едет рядом с трассой
инструментов (``DiscoveryReply`` → ``TurnReply`` → канал).

### Три состояния предмета, не два

``UNKNOWN`` — спросили, подтверждённого знания нет. ``UNAVAILABLE`` — спросить
не удалось. Человеку в обоих случаях обещать нечего, но чинятся они
по-разному, и в журнале обязаны читаться порознь — тот же довод, что у
``check_failed`` в исходящем стороже.

### Чего в типе нет намеренно

* **Ничего о человеке.** Утверждение лицензии — общее знание по построению;
  персонального вида у неё нет (решение владельца 29.09: population-level
  knowledge ≠ personal prescription).
* **Текстов утверждений.** Этому листу они не нужны: теневой затвор считает
  состояние, а не сверяет слова. Поля текста добавит читатель — в этот же
  тип, а не во второй рядом.
* **Запрещённого.** Покидает ли запрещённое утверждение каталог — решение
  владельца, ещё не принятое.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SubjectState(str, Enum):
    """Что читатель узнал о предмете (процедуре) в этот ход."""

    #: Есть хотя бы одно подтверждённое утверждение.
    KNOWN = "known"
    #: Спросили — подтверждённого знания нет.
    UNKNOWN = "unknown"
    #: Спросить не удалось (каталог не ответил, ответ нарушил инвариант).
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class LicensedClaim:
    """Одно подтверждённое утверждение — ровно столько, сколько нужно затвору.

    ``claim_id`` — идентификатор строки знания в каталоге; в журнал идёт он,
    а не слова. ``valid_until`` — срок подтверждения; ``None`` — бессрочно.
    """

    claim_id: str
    #: ``capability`` — что процедура умеет; ``goal_link`` — какой цели помогает.
    kind: str
    #: Стабильный ключ смысла (``ProcedureCapability.key``), не текст.
    key: str
    valid_until: datetime | None = None

    def is_stale(self, *, now: datetime) -> bool:
        """Истекло ли подтверждение к моменту ``now``.

        Граница та же, что у читателя каталога: в сам момент ``valid_until``
        утверждение УЖЕ не говорится (``valid_until > now`` — условие годности).
        """
        return self.valid_until is not None and self.valid_until <= now


@dataclass(frozen=True, slots=True)
class LicensedSubject:
    """Один предмет чтения — процедура канона — и что о ней известно."""

    #: Идентификатор процедуры канона в каталоге.
    template_id: str
    state: SubjectState
    claims: tuple[LicensedClaim, ...] = field(default_factory=tuple)
    #: DRF-2729 — услуга салона, от которой читали (id в каталоге); пусто,
    #: когда читали от самой процедуры. При ``UNAVAILABLE`` это единственное,
    #: что о предмете известно: каталог не ответил и шаблона не назвал.
    salon_service_id: str = ""


@dataclass(frozen=True, slots=True)
class KnowledgeLicence:
    """Неизменяемый снимок знания, выданного в этот ход.

    Пустой список предметов — допустимая лицензия: читатель был вызван и ни по
    одному предмету не читал. Это НЕ то же, что ``None`` у вызывающего
    («читателя в этом ходу не было вовсе»), и в журнале они различаются.
    """

    #: Когда читатель снял этот снимок. Затвор не доверяет снимку бессрочно.
    read_at: datetime
    subjects: tuple[LicensedSubject, ...] = field(default_factory=tuple)

    def claims(self) -> tuple[LicensedClaim, ...]:
        """Все утверждения лицензии, по порядку предметов."""
        return tuple(claim for subject in self.subjects for claim in subject.claims)

    def stale_claim_ids(self, *, now: datetime) -> tuple[str, ...]:
        """Утверждения, чей срок истёк между чтением и ``now``."""
        return tuple(claim.claim_id for claim in self.claims() if claim.is_stale(now=now))

    def subjects_in(self, state: SubjectState) -> int:
        return sum(1 for subject in self.subjects if subject.state is state)


__all__ = [
    "KnowledgeLicence",
    "LicensedClaim",
    "LicensedSubject",
    "SubjectState",
]
