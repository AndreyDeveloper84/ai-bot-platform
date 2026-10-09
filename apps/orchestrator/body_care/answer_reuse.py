"""Можно ли взять прежний ответ скрининга — область ответа и запрет авто-реюза (§9, §10, BOT-2).

Контракт:

* §9 — «ответ про один продукт не переиспользуется автоматически для другого»;
* §10.1 — исходы перепроверки: ``REUSABLE / PARTIALLY_REUSABLE /
  REASK_REQUIRED / FULL_RESCREEN_REQUIRED``, и «runtime не должен
  самостоятельно объявлять старый ответ reusable без policy rule»;
* §18 — запрещено «reuse allergy answer across different products
  automatically» и «reuse area-specific answer on another area automatically»;
* фикстура F-BC-002 — аллергия, отвеченная про продукт A, для продукта B →
  ``REASK_REQUIRED``.

Правило здесь одно и узкое. **REUSABLE** — только тот же человек, тот же
вопрос, тот же контекст (совпадает ``answer_context_hash`` — значит, совпадают
все ключи области: продукт, зона, версия конфигурации…), ответ действительно
был дан (не ``NOT_ASKED``) и не истёк. Единственное «правило политики», на
которое это опирается, — срок годности, который назвал сам производитель
ответа (BOT-1 делает его обязательным).

Всё остальное — **REASK_REQUIRED**. ``PARTIALLY_REUSABLE`` и
``FULL_RESCREEN_REQUIRED`` этот модуль не выдаёт никогда: частичное
переиспользование или полный перескрининг при смене продукта, зоны, режима —
это и есть «объявить старый ответ reusable» по правилу, которого ещё нет.
Такие правила — перепроверка (BOT-3), за решением D-6.

Причина исхода — закрытая (:class:`ReuseReason`): журнал решения (§20) хранит,
почему ответ не взят, а не только что не взят.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from apps.orchestrator.body_care.screening_answer import (
    ScreeningAnswer,
    ScreeningAnswerValue,
    context_hash,
)


class RecheckResult(StrEnum):
    """Исходы перепроверки §10.1. Значение равно имени."""

    REUSABLE = "REUSABLE"
    PARTIALLY_REUSABLE = "PARTIALLY_REUSABLE"
    REASK_REQUIRED = "REASK_REQUIRED"
    FULL_RESCREEN_REQUIRED = "FULL_RESCREEN_REQUIRED"


class ReuseReason(StrEnum):
    """Почему исход такой. Значение равно имени."""

    SAME_CONTEXT_UNEXPIRED = "SAME_CONTEXT_UNEXPIRED"
    OTHER_PERSON = "OTHER_PERSON"
    OTHER_QUESTION = "OTHER_QUESTION"
    CONTEXT_CHANGED = "CONTEXT_CHANGED"
    NOT_ASKED = "NOT_ASKED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class ReuseVerdict:
    result: RecheckResult
    reason: ReuseReason


def reuse_verdict(
    answer: ScreeningAnswer,
    *,
    user_id: str,
    question_code: str,
    scope: Mapping[str, str],
    now: datetime,
) -> ReuseVerdict:
    """Можно ли взять ``answer`` для вопроса ``question_code`` в контексте ``scope``.

    Проверки идут от самой грубой к самой тонкой; первая несовпавшая и есть
    причина. Любая из них — ``REASK_REQUIRED``: молча переиспользованный
    ответ про другой продукт или зону — ровно то, что запрещает §18.
    """

    reask = RecheckResult.REASK_REQUIRED
    if answer.user_id != user_id:
        return ReuseVerdict(reask, ReuseReason.OTHER_PERSON)
    if answer.question_code != question_code:
        return ReuseVerdict(reask, ReuseReason.OTHER_QUESTION)
    if answer.answer_context_hash != context_hash(question_code, scope):
        return ReuseVerdict(reask, ReuseReason.CONTEXT_CHANGED)
    if answer.answer is ScreeningAnswerValue.NOT_ASKED:
        return ReuseVerdict(reask, ReuseReason.NOT_ASKED)
    if answer.is_expired(now):
        return ReuseVerdict(reask, ReuseReason.EXPIRED)
    return ReuseVerdict(RecheckResult.REUSABLE, ReuseReason.SAME_CONTEXT_UNEXPIRED)


#: Исходы, которые этот модуль выдаёт. Остальные два §10.1 — только по правилу
#: политики (BOT-3, D-6); тест держит это множество закрытым.
EMITTED_RESULTS: frozenset[RecheckResult] = frozenset(
    {RecheckResult.REUSABLE, RecheckResult.REASK_REQUIRED}
)


__all__ = [
    "EMITTED_RESULTS",
    "RecheckResult",
    "ReuseReason",
    "ReuseVerdict",
    "reuse_verdict",
]
