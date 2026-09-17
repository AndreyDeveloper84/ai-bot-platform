"""Decision Policy в тени (DRF-1904 — срез 6.2, DRF-1932 — срез 6.3 окна «Мозг»).

### Зачем

Контракт рекомендаций v0.4 §31: выбор NBA — детерминированные правила
(eligibility, exclusion, priority, sufficiency), а не модель; §32 — исход прохода
(``CLEAR_PRIMARY`` / ``MULTIPLE_SUITABLE`` / ``INSUFFICIENT_CONTEXT`` /
``SAFETY_BOUNDARY``). Здесь — что решила бы политика на этом ходу, одной строкой
рядом с тенью DecisionReadiness. На ход не влияет.

### Порядок правил

1. **Граница безопасности** (STOP / handoff REQUIRED) → ``SAFETY_BOUNDARY``.
2. **Вердикт безопасности не получен** → ``POLICY_INPUT_UNAVAILABLE``: §23,
   отсутствие данных безопасности не превращается в подтверждение.
3. **Безопасность просит уточнения** → ``SAFETY_CLARIFICATION_PENDING``.
4. **Признаки в реплике** (:mod:`apps.orchestrator.nba_taxonomy`): health-контекст
   пакета 3 п.6 — ``SAFETY_CLARIFICATION_PENDING`` без цели; признак боли I2 —
   ``SAFETY_CLARIFICATION_PENDING``, распознанная цель пишется, NBA нет.
5. **Вход готовности недоступен** → ``POLICY_INPUT_UNAVAILABLE``. Исход не
   меняется; распознанная цель и её тройка пишутся отдельно, как
   ``candidate_nba`` с причиной «не actionable» (порядок наблюдения, главное окно
   15.09, :data:`CANDIDATE_NBA_WHEN_INPUT_UNAVAILABLE`).
6. **Выбор**: цели нет → ``NBA_TARGET_NOT_RECOGNIZED``; цель есть, тройки нет →
   ``NBA_TRIPLE_NOT_DEFINED``; одна тройка → ``CLEAR_PRIMARY``; больше →
   ``MULTIPLE_SUITABLE``, основная по порядку H5, альтернатив не больше двух.

### Чего политика не делает — и почему не притворяется

* **Не выдумывает словари.** «Фраза → target» (J1) и «target → family,
  action_type» (J2) — политика владельца (решение 15.09, DRF-1945): только
  явный список; фраза вне его — ``NBA_TARGET_NOT_RECOGNIZED``.
* **Не производит ``INSUFFICIENT_CONTEXT``.** §30: недостаточность — недостающий
  факт, который политика объявила обязательным для NBA. Required facts пока не
  объявлены; блок движка ``BLOCK_READINESS_INPUT_UNAVAILABLE`` — «вход
  недоступен», спросить его у человека нельзя.
* **Не читает сигналы модели.** §31: LLM не задаёт eligibility, exclusions,
  safety, primary и reason codes.

### Статусы и каталог

§32 — ``SAFETY_BOUNDARY``, ``CLEAR_PRIMARY``, ``MULTIPLE_SUITABLE``. Остальные —
**не §32**, живут только в тени и названы так, чтобы их нельзя было спутать со
словарём каталога. В каталог до решения о записи NBA уходит только
``SAFETY_BOUNDARY`` — это :func:`assert_catalog_writable`, которую обязан вызвать
писатель записи (6.4). ``candidate_nba`` в запись не идёт никогда.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from apps.orchestrator.nba_taxonomy import (
    EMPTY_NEEDS,
    ROLE_ALTERNATIVE,
    ROLE_PRIMARY,
    TAXONOMY_VERSION,
    Triple,
    TurnNeeds,
    triples_for,
)

#: Версия правил. Меняется при любом изменении порядка или состава правил.
DECISION_POLICY_VERSION = "decision-policy-v1-shadow"

#: Порядок наблюдения (главное окно 15.09, вариант (а)): при недоступном входе
#: готовности распознанная цель и её тройка пишутся в тень как ``candidate_nba``.
#: Исход прохода от этого не меняется; ``False`` — прежнее правило v0.
CANDIDATE_NBA_WHEN_INPUT_UNAVAILABLE = True

#: Альтернатив в ``MULTIPLE_SUITABLE`` — не больше (контракт, B4).
MAX_ALTERNATIVES = 2


class PolicyStatus(str, Enum):
    #: §32 / §23 — сработал safety gate. Не NBA.
    SAFETY_BOUNDARY = "SAFETY_BOUNDARY"
    #: §32 — одна тройка.
    CLEAR_PRIMARY = "CLEAR_PRIMARY"
    #: §32 — основная и до двух альтернатив.
    MULTIPLE_SUITABLE = "MULTIPLE_SUITABLE"
    #: Не §32. Безопасность просит уточнения, или в реплике признак боли (I2) /
    #: health-контекст (пакет 3 п.6): политика не выбирает — но и границы нет.
    SAFETY_CLARIFICATION_PENDING = "SAFETY_CLARIFICATION_PENDING"
    #: Не §32. Вход политики недоступен (вердикт безопасности отсутствует или
    #: не читается; вход движка недоступен). Не ``INSUFFICIENT_CONTEXT``.
    POLICY_INPUT_UNAVAILABLE = "POLICY_INPUT_UNAVAILABLE"
    #: Не §32. Всё проверено, но словарь целей в реплике ничего не нашёл.
    NBA_TARGET_NOT_RECOGNIZED = "NBA_TARGET_NOT_RECOGNIZED"
    #: Не §32. Цель распознана, умолчания family/action_type для неё нет (J2).
    NBA_TRIPLE_NOT_DEFINED = "NBA_TRIPLE_NOT_DEFINED"


#: Словарь исходов §32 в каталоге (``recommendation/models.py`` ``ResultStatus``,
#: включая ``NO_ACTION`` по OD-9). Нужен сторожу: статусы тени с ним не пересекаются.
CONTRACT_RESULT_STATUSES: frozenset[str] = frozenset(
    {"CLEAR_PRIMARY", "MULTIPLE_SUITABLE", "INSUFFICIENT_CONTEXT", "SAFETY_BOUNDARY", "NO_ACTION"}
)

#: Исходы, у которых есть NBA — и только у них есть тройка.
NBA_STATUSES: frozenset[PolicyStatus] = frozenset(
    {PolicyStatus.CLEAR_PRIMARY, PolicyStatus.MULTIPLE_SUITABLE}
)

#: Что бот вправе записать в каталог до решения о записи NBA (главное окно 15.09).
CATALOG_WRITABLE_BEFORE_TAXONOMY: frozenset[str] = frozenset({PolicyStatus.SAFETY_BOUNDARY.value})

# Коды причин политики — свои, не коды движка: движок отвечает, готов ли разговор,
# политика — почему такой исход прохода.
POLICY_SAFETY_STOP = "POLICY_SAFETY_STOP"
POLICY_HANDOFF_REQUIRED = "POLICY_HANDOFF_REQUIRED"
POLICY_SAFETY_VERDICT_UNAVAILABLE = "POLICY_SAFETY_VERDICT_UNAVAILABLE"
POLICY_SAFETY_CLARIFY = "POLICY_SAFETY_CLARIFY"
POLICY_HEALTH_SENSITIVE_CONTEXT = "POLICY_HEALTH_SENSITIVE_CONTEXT"
POLICY_PAIN_SIGNAL = "POLICY_PAIN_SIGNAL"
POLICY_READINESS_INPUT_UNAVAILABLE = "POLICY_READINESS_INPUT_UNAVAILABLE"
POLICY_TARGET_NOT_RECOGNIZED = "POLICY_TARGET_NOT_RECOGNIZED"
POLICY_TRIPLE_NOT_DEFINED = "POLICY_TRIPLE_NOT_DEFINED"
POLICY_SINGLE_TARGET = "POLICY_SINGLE_TARGET"
POLICY_SEVERAL_TARGETS = "POLICY_SEVERAL_TARGETS"

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
    #: Цели, которые словарь распознал в реплике (по порядку H5).
    recognized_targets: tuple[str, ...] = ()
    #: Тройка основной рекомендации — только у исходов из :data:`NBA_STATUSES`.
    primary: Triple | None = None
    alternatives: tuple[Triple, ...] = ()
    #: Не NBA: распознанная тройка при недоступном входе готовности. В запись
    #: каталога и в ответ человеку не идёт.
    candidate_nba: Triple | None = None
    candidate_not_actionable_reason: str | None = None
    taxonomy_version: str = TAXONOMY_VERSION
    #: DRF-1945: вердикт безопасности, который политика прочитала (минимум строки
    #: тени владельца, §4). Проставляет :func:`decide`.
    safety_state: str | None = None

    @property
    def catalog_writable(self) -> bool:
        return self.result_status.value in CATALOG_WRITABLE_BEFORE_TAXONOMY

    def nba_fields(self) -> dict[str, Any]:
        """Поля выбора NBA для строки тени — в написании записи каталога."""

        chosen = self.primary or self.candidate_nba
        candidate: dict[str, Any] | None = None
        if self.candidate_nba is not None:
            candidate = {
                **self.candidate_nba.as_record(ROLE_PRIMARY),
                "actionable": False,
                "not_actionable_reason": self.candidate_not_actionable_reason,
            }
        return {
            "taxonomy_version": self.taxonomy_version,
            "recognized_targets": list(self.recognized_targets),
            "primary": self.primary.as_record(ROLE_PRIMARY) if self.primary else None,
            "alternatives": [a.as_record(ROLE_ALTERNATIVE) for a in self.alternatives],
            "candidate_nba": candidate,
            # DRF-1945 — минимум строки тени владельца (§4), плоско. Тройка — основной
            # рекомендации, а без неё — кандидата; у исхода без обеих — пусто.
            "target": chosen.target if chosen else None,
            "family": chosen.family if chosen else None,
            "action_type": chosen.action_type if chosen else None,
            "decision_status": self.result_status.value,
            "safety_state": self.safety_state,
            "reason_code": self.reason_codes[0] if self.reason_codes else None,
        }


def _value(item: Any) -> str | None:
    if item is None:
        return None
    return str(getattr(item, "value", item))


def _decide(
    evidence: Any,
    *,
    handoff: Any = None,
    needs: TurnNeeds | None = None,
    defaults: Any = None,
) -> PolicyVerdict:
    """Исход прохода. Чистая функция.

    ``evidence`` — запись движка (``audit.DecisionEvidence``): читаются только
    ``safety["state"]`` и ``reason_codes``. ``handoff`` — обещание вердикта
    безопасности о следующем шаге (``Handoff``), в записи движка его нет.
    ``needs`` — что словари прочитали в реплике (:func:`nba_taxonomy.read_turn_needs`);
    ``None`` — реплики у вызывающего нет. ``defaults`` — умолчания J2, по
    умолчанию боевые.
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

    read = needs if needs is not None else EMPTY_NEEDS
    phrase_fact = ("turn.phrase",) if needs is not None else ()

    if safety_state == "clarify":
        return PolicyVerdict(
            PolicyStatus.SAFETY_CLARIFICATION_PENDING,
            (POLICY_SAFETY_CLARIFY,),
            ("safety.state", *phrase_fact),
            recognized_targets=read.recognized_targets,
        )
    if read.health_context:
        return PolicyVerdict(
            PolicyStatus.SAFETY_CLARIFICATION_PENDING,
            (POLICY_HEALTH_SENSITIVE_CONTEXT,),
            ("safety.state", *phrase_fact),
        )
    if read.pain_signal:
        return PolicyVerdict(
            PolicyStatus.SAFETY_CLARIFICATION_PENDING,
            (POLICY_PAIN_SIGNAL,),
            ("safety.state", *phrase_fact),
            recognized_targets=read.recognized_targets,
        )

    triples = triples_for(read.recognized_targets, defaults=defaults)
    facts = ("safety.state", "engine.reason_codes", *phrase_fact)

    if _ENGINE_INPUT_UNAVAILABLE in tuple(getattr(evidence, "reason_codes", ()) or ()):
        candidate = triples[0] if CANDIDATE_NBA_WHEN_INPUT_UNAVAILABLE and triples else None
        return PolicyVerdict(
            PolicyStatus.POLICY_INPUT_UNAVAILABLE,
            (POLICY_READINESS_INPUT_UNAVAILABLE,),
            facts,
            recognized_targets=read.recognized_targets,
            candidate_nba=candidate,
            candidate_not_actionable_reason=(
                POLICY_READINESS_INPUT_UNAVAILABLE if candidate is not None else None
            ),
        )

    if not read.recognized_targets:
        return PolicyVerdict(
            PolicyStatus.NBA_TARGET_NOT_RECOGNIZED, (POLICY_TARGET_NOT_RECOGNIZED,), facts
        )
    if not triples:
        return PolicyVerdict(
            PolicyStatus.NBA_TRIPLE_NOT_DEFINED,
            (POLICY_TRIPLE_NOT_DEFINED,),
            facts,
            recognized_targets=read.recognized_targets,
        )
    if len(triples) == 1:
        return PolicyVerdict(
            PolicyStatus.CLEAR_PRIMARY,
            (POLICY_SINGLE_TARGET,),
            facts,
            recognized_targets=read.recognized_targets,
            primary=triples[0],
        )
    return PolicyVerdict(
        PolicyStatus.MULTIPLE_SUITABLE,
        (POLICY_SEVERAL_TARGETS,),
        facts,
        recognized_targets=read.recognized_targets,
        primary=triples[0],
        alternatives=triples[1 : 1 + MAX_ALTERNATIVES],
    )


def decide(
    evidence: Any,
    *,
    handoff: Any = None,
    needs: TurnNeeds | None = None,
    defaults: Any = None,
) -> PolicyVerdict:
    """Исход прохода (:func:`_decide`) с прочитанным вердиктом безопасности.

    DRF-1945: ``safety_state`` — в минимуме строки тени владельца; политика
    называет то, что прочитала сама, а не то, что лежит рядом в записи движка.
    """

    verdict = _decide(evidence, handoff=handoff, needs=needs, defaults=defaults)
    read = _value((getattr(evidence, "safety", None) or {}).get("state"))
    return replace(verdict, safety_state=read)


def assert_catalog_writable(status: Any) -> str:
    """Граница записи каталога: вернуть статус, если его можно писать, иначе отказ.

    Писатель записи (6.4) обязан пропустить статус через эту функцию. До
    решения о записи NBA проходит только ``SAFETY_BOUNDARY``; ``INSUFFICIENT_CONTEXT``
    — тоже нет: политика его не производит, и появиться он может только вместе с
    required facts.
    """

    value = _value(status)
    if value not in CATALOG_WRITABLE_BEFORE_TAXONOMY:
        raise NotCatalogWritable(f"{value}: не пишется в каталог до решения о записи NBA")
    return value


__all__ = [
    "CANDIDATE_NBA_WHEN_INPUT_UNAVAILABLE",
    "CATALOG_WRITABLE_BEFORE_TAXONOMY",
    "CONTRACT_RESULT_STATUSES",
    "DECISION_POLICY_VERSION",
    "MAX_ALTERNATIVES",
    "NBA_STATUSES",
    "NotCatalogWritable",
    "PolicyStatus",
    "PolicyVerdict",
    "assert_catalog_writable",
    "decide",
]
