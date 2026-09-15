"""Decision Policy v0 в тени (DRF-1904, срез 6.2 окна «Мозг»).

### Зачем

Контракт рекомендаций v0.4 §31: выбор NBA — детерминированные правила
(eligibility, exclusion, priority, sufficiency), а не модель; §32 — исход прохода
(``CLEAR_PRIMARY`` / ``MULTIPLE_SUITABLE`` / ``INSUFFICIENT_CONTEXT`` /
``SAFETY_BOUNDARY``). Производителя исхода в боте не было (замер 15.09,
``Ayla/docs/PLAN_SLICE6_NBA_PRODUCER_2026-09-15.md``). Здесь — v0: что решила бы
политика на этом ходу, одной строкой рядом с тенью DecisionReadiness. На ход не
влияет.

### Чего v0 не делает — и почему не притворяется

* **Не выбирает NBA и не ставит priority.** Таксономия (OQ-R11) не утверждена:
  любой ``direction_code`` был бы выдуман. Вместо выбора — названный пропуск
  ``NBA_SELECTION_PENDING_TAXONOMY``.
* **Не производит ``INSUFFICIENT_CONTEXT``.** §30: недостаточность — это
  недостающий факт, который политика объявила обязательным для NBA и который
  можно получить сейчас. Required facts объявляются на ``(family, target,
  action_type)`` — до таксономии их нет. Сегодняшний блок движка
  ``BLOCK_READINESS_INPUT_UNAVAILABLE`` — «вход недоступен», спросить его у
  человека нельзя; это ``POLICY_INPUT_UNAVAILABLE`` (решение главного окна
  15.09).
* **Не читает сигналы модели.** §31: LLM не задаёт eligibility, exclusions,
  safety, primary и reason codes. Вход — только вердикт безопасности и коды
  движка; ``model_signals_rejected`` и прочее в правила не входят.

### Статусы

Один статус §32 — ``SAFETY_BOUNDARY`` (§23: gate, не NBA). Остальные три —
**не §32**, живут только в тени и названы так, чтобы их нельзя было спутать со
словарём каталога. В каталог до таксономии уходит только ``SAFETY_BOUNDARY`` —
это не соглашение, а :func:`assert_catalog_writable`, которую обязан вызвать
писатель записи (6.4). Каталог держит вторую стену: неизвестный статус — 400.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

#: Версия правил. Меняется при любом изменении порядка или состава правил.
DECISION_POLICY_VERSION = "decision-policy-v0-shadow"


class PolicyStatus(str, Enum):
    #: §32 / §23 — сработал safety gate. Не NBA.
    SAFETY_BOUNDARY = "SAFETY_BOUNDARY"
    #: Не §32. Безопасность просит уточнения: пока вопрос безопасности открыт,
    #: политика не выбирает — но и границы нет.
    SAFETY_CLARIFICATION_PENDING = "SAFETY_CLARIFICATION_PENDING"
    #: Не §32. Вход политики недоступен (вердикт безопасности отсутствует или
    #: не читается; вход движка недоступен). Не ``INSUFFICIENT_CONTEXT``.
    POLICY_INPUT_UNAVAILABLE = "POLICY_INPUT_UNAVAILABLE"
    #: Не §32. Всё, что политика v0 может проверить, пройдено; выбор NBA ждёт
    #: утверждённой таксономии (OQ-R11).
    NBA_SELECTION_PENDING_TAXONOMY = "NBA_SELECTION_PENDING_TAXONOMY"


#: Словарь исходов §32 в каталоге (``recommendation/models.py`` ``ResultStatus``,
#: включая ``NO_ACTION`` по OD-9). Нужен сторожу: статусы тени с ним не пересекаются.
CONTRACT_RESULT_STATUSES: frozenset[str] = frozenset(
    {"CLEAR_PRIMARY", "MULTIPLE_SUITABLE", "INSUFFICIENT_CONTEXT", "SAFETY_BOUNDARY", "NO_ACTION"}
)

#: Что бот вправе записать в каталог до утверждения таксономии (главное окно 15.09).
CATALOG_WRITABLE_BEFORE_TAXONOMY: frozenset[str] = frozenset({PolicyStatus.SAFETY_BOUNDARY.value})

# Коды причин политики — свои, не коды движка: движок отвечает, готов ли разговор,
# политика — почему такой исход прохода.
POLICY_SAFETY_STOP = "POLICY_SAFETY_STOP"
POLICY_HANDOFF_REQUIRED = "POLICY_HANDOFF_REQUIRED"
POLICY_SAFETY_VERDICT_UNAVAILABLE = "POLICY_SAFETY_VERDICT_UNAVAILABLE"
POLICY_SAFETY_CLARIFY = "POLICY_SAFETY_CLARIFY"
POLICY_READINESS_INPUT_UNAVAILABLE = "POLICY_READINESS_INPUT_UNAVAILABLE"
POLICY_TAXONOMY_NOT_APPROVED = "POLICY_TAXONOMY_NOT_APPROVED"

#: Код движка, который означает «вход недоступен» (``reason_codes.py``).
_ENGINE_INPUT_UNAVAILABLE = "BLOCK_READINESS_INPUT_UNAVAILABLE"

#: Вердикты безопасности, при которых политика идёт дальше. ``unknown``, пустое
#: и любое нераспознанное значение — отсутствие ответа (§23: отсутствие данных
#: безопасности не превращается в подтверждение), не проход.
_SAFETY_ANSWERED = frozenset({"normal", "caution", "clarify", "not_applicable"})


class NotCatalogWritable(ValueError):
    """Статус не вправе уйти в запись каталога."""


@dataclass(frozen=True)
class PolicyVerdict:
    result_status: PolicyStatus
    reason_codes: tuple[str, ...]
    #: Какие входы правило фактически прочитало (вход WHY, §13/§31).
    facts_used: tuple[str, ...]
    decision_policy_version: str = DECISION_POLICY_VERSION

    @property
    def catalog_writable(self) -> bool:
        return self.result_status.value in CATALOG_WRITABLE_BEFORE_TAXONOMY


def _value(item: Any) -> str | None:
    if item is None:
        return None
    return str(getattr(item, "value", item))


def decide(evidence: Any, *, handoff: Any = None) -> PolicyVerdict:
    """Исход прохода по вердикту безопасности и кодам движка. Чистая функция.

    ``evidence`` — запись движка (``audit.DecisionEvidence``): читаются только
    ``safety["state"]`` и ``reason_codes``. ``handoff`` — обещание вердикта
    безопасности о следующем шаге (``Handoff``), в записи движка его нет.
    """

    safety_state = _value((getattr(evidence, "safety", None) or {}).get("state"))
    handoff_value = _value(handoff)

    boundary: list[str] = []
    if safety_state == "stop":
        boundary.append(POLICY_SAFETY_STOP)
    if handoff_value == "required":
        boundary.append(POLICY_HANDOFF_REQUIRED)
    if boundary:
        return PolicyVerdict(
            PolicyStatus.SAFETY_BOUNDARY, tuple(boundary), ("safety.state", "safety.handoff")
        )

    if safety_state not in _SAFETY_ANSWERED:
        return PolicyVerdict(
            PolicyStatus.POLICY_INPUT_UNAVAILABLE,
            (POLICY_SAFETY_VERDICT_UNAVAILABLE,),
            ("safety.state",),
        )
    if safety_state == "clarify":
        return PolicyVerdict(
            PolicyStatus.SAFETY_CLARIFICATION_PENDING, (POLICY_SAFETY_CLARIFY,), ("safety.state",)
        )

    facts = ("safety.state", "engine.reason_codes")
    if _ENGINE_INPUT_UNAVAILABLE in tuple(getattr(evidence, "reason_codes", ()) or ()):
        return PolicyVerdict(
            PolicyStatus.POLICY_INPUT_UNAVAILABLE, (POLICY_READINESS_INPUT_UNAVAILABLE,), facts
        )
    return PolicyVerdict(
        PolicyStatus.NBA_SELECTION_PENDING_TAXONOMY, (POLICY_TAXONOMY_NOT_APPROVED,), facts
    )


def assert_catalog_writable(status: Any) -> str:
    """Граница записи каталога: вернуть статус, если его можно писать, иначе отказ.

    Писатель записи (6.4) обязан пропустить статус через эту функцию. До
    таксономии проходит только ``SAFETY_BOUNDARY``; ``INSUFFICIENT_CONTEXT`` —
    тоже нет: v0 его не производит, и появиться он может только вместе с
    required facts.
    """

    value = _value(status)
    if value not in CATALOG_WRITABLE_BEFORE_TAXONOMY:
        raise NotCatalogWritable(f"{value}: не пишется в каталог до таксономии")
    return value


__all__ = [
    "CATALOG_WRITABLE_BEFORE_TAXONOMY",
    "CONTRACT_RESULT_STATUSES",
    "DECISION_POLICY_VERSION",
    "NotCatalogWritable",
    "PolicyStatus",
    "PolicyVerdict",
    "assert_catalog_writable",
    "decide",
]
