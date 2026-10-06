"""``ScreeningAnswer`` — ответ человека на вопрос скрининга (контракт §8, BOT-1, DRF-2798).

Доменный тип, без хранения. Структура — §8 дословно::

    id, user_id, question_code, answer, answer_scope,
    answer_context_hash, source, answered_at, expires_at

Что здесь закреплено и почему:

* **Четыре ответа, закрыто.** ``YES / NO / NOT_SURE / NOT_ASKED``. ``NOT_SURE``
  — не ``NO`` (§8): «не знаю» не превращается в «нет». ``NOT_ASKED`` — не
  ``NO``: вопрос не задан, и молчание ничего не утверждает.
* **Ни одного помощника «ответ разрешает».** §18 запрещает считать ``NO``
  медицинским допуском; что ответ значит для решения — ``ScreeningResult``
  (BOT-4) и политика, не этот тип.
* **Ответ привязан к контексту** (§9): ``answer_scope`` — именованные ключи
  (``product_id``, ``area``, ``offering_configuration_version`` …), а
  ``answer_context_hash`` вычисляется из вопроса и области, а не передаётся
  снаружи — подделать «тот же контекст» нечем. Хэш — sha256 канонического
  JSON, не встроенный ``hash()`` (DRF-1158: он солится на процесс). Правило,
  когда ответ переиспользуется в другом контексте, — BOT-2, не здесь.
* **Срок годности обязателен.** Ответ без известного срока пришлось бы либо
  считать вечным — то есть разрешением из незнания, что запрещает §4
  («UNKNOWN не преобразуется в разрешение»), — либо придумать срок, что
  запрещает §18. Длительность — политика (D-7); производитель ответа обязан
  её назвать.

Чего здесь нет намеренно: хранения (это ответы о здоровье — особая
категория, где и как их хранить, решается до модели), формата
``question_code`` сверх непустоты и перечня ``source`` — контракт их не
определяет, а выдумывать их здесь значило бы закрепить догадку.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class ScreeningAnswerValue(StrEnum):
    """Ответ на вопрос скрининга (§8). Значение равно имени."""

    YES = "YES"
    NO = "NO"
    NOT_SURE = "NOT_SURE"
    NOT_ASKED = "NOT_ASKED"


class ScreeningAnswerError(ValueError):
    """Нарушен инвариант ``ScreeningAnswer``. Программная ошибка, не ответ человека."""


def context_hash(question_code: str, scope: Mapping[str, str]) -> str:
    """sha256 канонического JSON вопроса и области ответа (§8 ``answer_context_hash``).

    Канонический — ключи отсортированы, без пробелов, UTF-8: один и тот же
    контекст всегда даёт одну и ту же строку, в любом процессе и порядке
    ключей.
    """

    payload = json.dumps(
        {"question_code": question_code, "scope": dict(scope)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _aware(moment: datetime | None) -> bool:
    return moment is None or (moment.tzinfo is not None and moment.utcoffset() is not None)


@dataclass(frozen=True)
class ScreeningAnswer:
    """Один ответ на один вопрос скрининга в одном контексте (§8)."""

    id: str
    user_id: str
    question_code: str
    answer: ScreeningAnswerValue
    answer_scope: Mapping[str, str]
    source: str
    answered_at: datetime | None
    expires_at: datetime
    answer_context_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("id", "user_id", "question_code", "source"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ScreeningAnswerError(f"{name} обязателен")
        if not isinstance(self.answer, ScreeningAnswerValue):
            raise ScreeningAnswerError(f"ответ вне реестра §8: {self.answer!r}")
        scope = dict(self.answer_scope)
        if not scope:
            # Ответ без контекста переиспользовался бы где угодно (§9).
            raise ScreeningAnswerError("answer_scope обязателен — ответ привязан к контексту (§9)")
        for key, value in scope.items():
            if not isinstance(key, str) or not key or not isinstance(value, str):
                raise ScreeningAnswerError(f"answer_scope: строковые ключ и значение, не {key!r}")
        if not _aware(self.answered_at) or not _aware(self.expires_at):
            raise ScreeningAnswerError("моменты — с часовым поясом")
        if self.answer is ScreeningAnswerValue.NOT_ASKED:
            if self.answered_at is not None:
                raise ScreeningAnswerError("NOT_ASKED: вопрос не задан — момента ответа нет")
        elif self.answered_at is None:
            raise ScreeningAnswerError(f"{self.answer.value}: у ответа есть момент — answered_at")
        if self.answered_at is not None and self.expires_at <= self.answered_at:
            raise ScreeningAnswerError("expires_at должен быть позже answered_at")
        # Неизменяемая копия: область, на которой посчитан хэш, не меняется после.
        object.__setattr__(self, "answer_scope", MappingProxyType(scope))
        object.__setattr__(self, "answer_context_hash", context_hash(self.question_code, scope))

    def is_expired(self, now: datetime) -> bool:
        """Истёк ли ответ к ``now``. Граница — истёк (``now >= expires_at``)."""

        if not _aware(now):
            raise ScreeningAnswerError("now — с часовым поясом")
        return now >= self.expires_at

    def as_record(self) -> dict[str, Any]:
        """Поля §8 в их порядке — для журнала решения (§20)."""

        return {
            "id": self.id,
            "user_id": self.user_id,
            "question_code": self.question_code,
            "answer": self.answer.value,
            "answer_scope": dict(self.answer_scope),
            "answer_context_hash": self.answer_context_hash,
            "source": self.source,
            "answered_at": self.answered_at.isoformat() if self.answered_at else None,
            "expires_at": self.expires_at.isoformat(),
        }


__all__ = [
    "ScreeningAnswer",
    "ScreeningAnswerError",
    "ScreeningAnswerValue",
    "context_hash",
]
