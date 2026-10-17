"""``ScreeningResult`` — итог скрининга body-care (контракт §11, BOT-4, DRF-2814).

Ядро без клинических значений. Дизайн и границы —
``DESIGN_BOT4_SCREENING_RESULT_2026-10-06`` (документ репозитория Ayla).

Что здесь есть:

* :class:`ScreeningStatus` — пять статусов §11. Это «runtime decision outcome,
  а не новый medical diagnosis». Отдельный тип, не расширение ``SafetyState``
  движка готовности (аудит C4; §24.9 запрещает новый emergency-``SafetyState``).
* :class:`ScreeningResult` — §11 + шесть версий §19 + ссылки на ответы
  (id и хэш контекста, **не** сами ответы: это особая категория, их хранение —
  DRF-2802).
* :class:`NotEvaluated` — «оценки нет». Отдельный результат с именованной
  причиной, а не шестой статус: отсутствие оценки не должно читаться как
  оценка (§4 — «UNKNOWN не преобразуется в разрешение»), тем же приёмом, что
  ``SafetyResult.not_evaluated()``.
* :func:`evaluate` — агрегатор с внедряемой политикой (:class:`ScreeningPolicy`).

Что ядро делает само — только то, что задаёт контракт:

* нет политики → :class:`NotEvaluated` (``NO_POLICY``), никогда не допуск;
* у обязательного вопроса нет переиспользуемого ответа (BOT-2 ≠ ``REUSABLE``:
  не задан, истёк, другой контекст, другой человек) → :class:`NotEvaluated`
  (``ANSWERS_INCOMPLETE``) со списком, о чём спросить;
* ``S1_ESCALATION`` в кодах → ``S1_ROUTE``, что бы ни сказала политика
  (§16 «S1 has precedence»);
* политика вернула ``CLEAR_TO_PROCEED``, хотя среди ответов есть ``NOT_SURE`` →
  :class:`ScreeningPolicyViolation` (§4, §8 «NOT_SURE != NO»): это ошибка
  программы, а не решение.

Чего ядро НЕ делает (ждёт решений, не выдумывается): какие вопросы
обязательны (D-7), что значит ответ — таблица LIM §12 (D-5), пороги (D-4),
порядок строгости статусов и словарь ``required_actions`` (D-5),
соответствие статусов ``SafetyState`` (D-5). Всё это — работа политики;
``required_actions`` до утверждения словаря — открытые строки.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from apps.orchestrator.body_care.answer_reuse import RecheckResult, reuse_verdict
from apps.orchestrator.body_care.reason_codes import REGISTRY_VERSION, ReasonCode
from apps.orchestrator.body_care.screening_answer import ScreeningAnswer, ScreeningAnswerValue


class ScreeningStatus(StrEnum):
    """Пять статусов §11. Значение равно имени."""

    CLEAR_TO_PROCEED = "CLEAR_TO_PROCEED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    MEDICAL_ASSESSMENT_REQUIRED = "MEDICAL_ASSESSMENT_REQUIRED"
    DO_NOT_PROCEED = "DO_NOT_PROCEED"
    S1_ROUTE = "S1_ROUTE"


class NotEvaluatedReason(StrEnum):
    NO_POLICY = "NO_POLICY"
    ANSWERS_INCOMPLETE = "ANSWERS_INCOMPLETE"


class ScreeningResultError(ValueError):
    """Нарушен инвариант итога скрининга."""


class ScreeningPolicyViolation(ScreeningResultError):
    """Политика вернула то, что контракт запрещает. Ошибка программы."""


@dataclass(frozen=True)
class OfferingContext:
    """Что оценивается: услуга салона и версии её канона и конфигурации."""

    offering_id: str
    canonical_version: str
    offering_configuration_version: str


@dataclass(frozen=True)
class RequiredQuestion:
    """Вопрос, на который нужен ответ, и контекст, в котором он нужен (§9)."""

    question_code: str
    scope: Mapping[str, str]


@dataclass(frozen=True)
class AnswerRef:
    """Ссылка на ответ — без его содержания (DRF-2802)."""

    answer_id: str
    question_code: str
    answer_context_hash: str


#: Шесть версий §19 — порядок контракта.
VERSION_FIELDS = (
    "canonical_version",
    "clinical_policy_version",
    "offering_configuration_version",
    "questionnaire_version",
    "routing_version",
    "claim_policy_version",
)


class ScreeningPolicy(Protocol):
    """Политика скрининга — всё, что решают D-4 / D-5 / D-7. Ядро её не знает."""

    clinical_policy_version: str
    questionnaire_version: str
    routing_version: str
    claim_policy_version: str

    def required_questions(self, offering: OfferingContext) -> Sequence[RequiredQuestion]: ...

    def codes_for(self, answer: ScreeningAnswer) -> Iterable[ReasonCode]: ...

    def status_for(
        self, codes: frozenset[ReasonCode], answers: Sequence[ScreeningAnswer]
    ) -> ScreeningStatus: ...

    def actions_for(
        self, status: ScreeningStatus, codes: frozenset[ReasonCode]
    ) -> Iterable[str]: ...


@dataclass(frozen=True)
class ScreeningResult:
    """Итог оценки (§11). Существует, только если оценка была."""

    screening_status: ScreeningStatus
    reason_codes: tuple[ReasonCode, ...]
    required_actions: tuple[str, ...]
    source_answers: tuple[AnswerRef, ...]
    versions: Mapping[str, str]
    evaluated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.screening_status, ScreeningStatus):
            raise ScreeningResultError(f"статус вне §11: {self.screening_status!r}")
        for code in self.reason_codes:
            if not isinstance(code, ReasonCode):
                raise ScreeningResultError(f"код вне реестра: {code!r}")
        if (
            ReasonCode.S1_ESCALATION in self.reason_codes
            and self.screening_status is not ScreeningStatus.S1_ROUTE
        ):
            raise ScreeningResultError("S1_ESCALATION без S1_ROUTE: S1 имеет приоритет (§16)")
        missing = [name for name in VERSION_FIELDS if not self.versions.get(name)]
        if missing:
            raise ScreeningResultError(f"нет версий §19: {missing}")
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ScreeningResultError("evaluated_at — с часовым поясом")
        object.__setattr__(self, "reason_codes", tuple(sorted(set(self.reason_codes))))
        object.__setattr__(self, "versions", dict(self.versions))

    def as_record(self) -> dict[str, Any]:
        """Для журнала решения (§20): решение, коды, ссылки, версии — без ответов."""

        return {
            "screening_status": self.screening_status.value,
            "reason_codes": [c.value for c in self.reason_codes],
            "reason_code_registry_version": REGISTRY_VERSION,
            "required_actions": list(self.required_actions),
            "source_answers": [
                {
                    "answer_id": r.answer_id,
                    "question_code": r.question_code,
                    "answer_context_hash": r.answer_context_hash,
                }
                for r in self.source_answers
            ],
            **{name: self.versions[name] for name in VERSION_FIELDS},
            "evaluated_at": self.evaluated_at.isoformat(),
        }


@dataclass(frozen=True)
class NotEvaluated:
    """Оценки нет. Блокирует так же, как отказ, и не выдаёт себя за статус."""

    reason: NotEvaluatedReason
    pending: tuple[RequiredQuestion, ...] = ()


def evaluate(
    offering: OfferingContext,
    *,
    user_id: str,
    answers: Iterable[ScreeningAnswer],
    policy: ScreeningPolicy | None,
    now: datetime,
) -> ScreeningResult | NotEvaluated:
    """Итог скрининга для ``offering`` по ответам ``user_id`` на момент ``now``."""

    if policy is None:
        return NotEvaluated(NotEvaluatedReason.NO_POLICY)

    pool = list(answers)
    used: list[ScreeningAnswer] = []
    pending: list[RequiredQuestion] = []
    for question in policy.required_questions(offering):
        chosen = next(
            (
                answer
                for answer in pool
                if reuse_verdict(
                    answer,
                    user_id=user_id,
                    question_code=question.question_code,
                    scope=question.scope,
                    now=now,
                ).result
                is RecheckResult.REUSABLE
            ),
            None,
        )
        if chosen is None:
            pending.append(question)
        else:
            used.append(chosen)
    if pending:
        return NotEvaluated(NotEvaluatedReason.ANSWERS_INCOMPLETE, tuple(pending))

    codes = frozenset(code for answer in used for code in policy.codes_for(answer))
    status = policy.status_for(codes, used)
    if ReasonCode.S1_ESCALATION in codes:
        status = ScreeningStatus.S1_ROUTE
    if status is ScreeningStatus.CLEAR_TO_PROCEED and any(
        answer.answer is ScreeningAnswerValue.NOT_SURE for answer in used
    ):
        raise ScreeningPolicyViolation("CLEAR_TO_PROCEED при ответе NOT_SURE (§4, §8)")

    versions = {
        "canonical_version": offering.canonical_version,
        "clinical_policy_version": policy.clinical_policy_version,
        "offering_configuration_version": offering.offering_configuration_version,
        "questionnaire_version": policy.questionnaire_version,
        "routing_version": policy.routing_version,
        "claim_policy_version": policy.claim_policy_version,
    }
    return ScreeningResult(
        screening_status=status,
        reason_codes=tuple(codes),
        required_actions=tuple(policy.actions_for(status, codes)),
        source_answers=tuple(AnswerRef(a.id, a.question_code, a.answer_context_hash) for a in used),
        versions=versions,
        evaluated_at=now,
    )


__all__ = [
    "VERSION_FIELDS",
    "AnswerRef",
    "NotEvaluated",
    "NotEvaluatedReason",
    "OfferingContext",
    "RequiredQuestion",
    "ScreeningPolicy",
    "ScreeningPolicyViolation",
    "ScreeningResult",
    "ScreeningResultError",
    "ScreeningStatus",
    "evaluate",
]
