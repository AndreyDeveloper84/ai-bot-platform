"""Затвор утверждений, теневой режим — считает и пишет, ответ не меняет (DRF-2725).

Контракт — DRF-2718 (``docs/CONTRACT_CLAIM_ENFORCEMENT_GATE_2026-10-02.md``). Решение
владельца 02.10.2026: сначала теневой режим. Этот модуль — Фаза 3.1: проводка
лицензии знания до исходящего хука и первая теневая запись.

### Что этот модуль делает

По черновику ответа, который УЖЕ прошёл сторож формы, и по лицензии знания
этого хода (:mod:`apps.orchestrator.knowledge_licence`) считает, что о ходе
известно затвору, и пишет одну обезличенную строку в журнал.

### Чего он не делает — и это предел, а не недоделка

* **Не меняет ответ.** Ни при каком значении флага. Вердикт сторожа формы и
  текст, который прочитает человек, от этого модуля не зависят — узел
  ``test_claim_gate_shadow_2725.py`` сравнивает исход хука при выключенном
  и включённом флаге на одном и том же наборе черновиков.
* **Не судит утверждения.** Решение «это утверждение об эффекте покрыто
  лицензией / не покрыто» требует разобрать ПРОЗУ — понять, кто, что и о чём
  утверждает. По совпадению слов этого делать нельзя (решение владельца №7:
  не подменять безопасность наличием ключевых слов), а судьи в этом листе
  нет. Поэтому теневая запись 3.1 несёт только то, что вычислимо без разбора
  прозы: была ли лицензия, сколько в ней предметов и в каком они состоянии,
  сколько утверждений, сколько из них просрочено к моменту отправки, и какие
  категории формы сработали на этом же черновике.

  Следствие, названное прямо: **журнал 3.1 не является оценкой утверждений.**
  По нему видно, как часто ответ уходит без лицензии и как часто лицензия
  устаревает в пути, — и не видно, что Ayla сказала о процедуре.

### Флаг

``CLAIM_GATE_SHADOW_ENABLED``. Ключа по умолчанию нет, и по умолчанию
выключено — в этом порядке. Читается тем же способом, что теневой режим
DecisionReadiness: три разных «выключено» (ключа нет / выключено словом /
значение не из словаря), и значение не из словаря ничего не включает —
``bool("false")`` это ``True``.

### Почему журнал, а не событие в базе

Теневая запись делается на КАЖДЫЙ исходящий ответ. Строка в таблице событий
на каждую реплику — отдельное решение о хранении и его сроке; в этом листе
оно не принято. Строка журнала — то, что уже есть у каждого вызова хука.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from django.conf import settings
from django.utils import timezone

from apps.orchestrator.decision_readiness.shadow import FlagReading, FlagSource
from apps.orchestrator.knowledge_licence import KnowledgeLicence, SubjectState

logger = logging.getLogger(__name__)

#: Настройка, включающая теневую запись. По умолчанию ключа нет.
SHADOW_SETTING = "CLAIM_GATE_SHADOW_ENABLED"

#: Слова, которыми оператор включает и выключает флаг. Тот же словарь, что у
#: ``decision_readiness.shadow``: значение вне него — ``MALFORMED`` и «выкл».
_TRUE_WORDS = frozenset({"true", "1", "yes", "on"})
_FALSE_WORDS = frozenset({"false", "0", "no", "off", ""})

#: Лицензии у вызова нет: читателя знания в этом ходу не было.
LICENCE_ABSENT = "absent"
#: Лицензия есть, предметов в ней нет: читатель был, ни по чему не читал.
LICENCE_EMPTY = "empty"
#: Лицензия есть и несёт хотя бы один предмет.
LICENCE_PRESENT = "present"


def shadow_flag() -> FlagReading:
    """Прочитать флаг и сказать, откуда значение. Не бросает."""

    if not hasattr(settings, SHADOW_SETTING):
        return FlagReading(value=False, source=FlagSource.READ_DEFAULT)

    raw = getattr(settings, SHADOW_SETTING)
    if isinstance(raw, bool):
        return FlagReading(value=raw, source=FlagSource.SETTINGS)
    if isinstance(raw, str):
        word = raw.strip().lower()
        if word in _TRUE_WORDS:
            return FlagReading(value=True, source=FlagSource.SETTINGS)
        if word in _FALSE_WORDS:
            return FlagReading(value=False, source=FlagSource.SETTINGS)

    logger.warning(
        "safety.claim_gate.flag_unreadable setting=%s type=%s — resolved to off",
        SHADOW_SETTING,
        type(raw).__name__,
    )
    return FlagReading(value=False, source=FlagSource.MALFORMED)


@dataclass(frozen=True, slots=True)
class ShadowAssessment:
    """Что теневой затвор знает об одном исходящем ответе. Слов в нём нет.

    Все поля — счётчики, состояния и идентификаторы строк знания. Текст
    черновика и тексты утверждений сюда не попадают по построению: у этого
    типа нет поля, куда их положить.
    """

    licence: str
    subjects_known: int = 0
    subjects_unknown: int = 0
    subjects_unavailable: int = 0
    claims: int = 0
    #: Утверждения, истёкшие между чтением и отправкой.
    stale_claim_ids: tuple[str, ...] = field(default_factory=tuple)
    #: Сколько секунд прошло от чтения знания до этой проверки; ``None`` без лицензии.
    licence_age_s: int | None = None
    #: Категории сторожа формы, сработавшие на этом же черновике.
    form_categories: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_stale(self) -> bool:
        return bool(self.stale_claim_ids)


def assess(
    knowledge: KnowledgeLicence | None,
    *,
    form_categories: tuple[str, ...] = (),
    now: datetime | None = None,
) -> ShadowAssessment:
    """Посчитать теневую оценку. Чистая функция: ни журнала, ни настроек."""

    if knowledge is None:
        return ShadowAssessment(licence=LICENCE_ABSENT, form_categories=tuple(form_categories))

    now = now or timezone.now()
    age = int((now - knowledge.read_at).total_seconds())
    return ShadowAssessment(
        licence=LICENCE_PRESENT if knowledge.subjects else LICENCE_EMPTY,
        subjects_known=knowledge.subjects_in(SubjectState.KNOWN),
        subjects_unknown=knowledge.subjects_in(SubjectState.UNKNOWN),
        subjects_unavailable=knowledge.subjects_in(SubjectState.UNAVAILABLE),
        claims=len(knowledge.claims()),
        stale_claim_ids=knowledge.stale_claim_ids(now=now),
        licence_age_s=age,
        form_categories=tuple(form_categories),
    )


def shadow_observe(
    knowledge: KnowledgeLicence | None,
    *,
    surface: str,
    form_categories: tuple[str, ...] = (),
    form_blocked: bool = False,
    trace_id: object | None = None,
    now: datetime | None = None,
) -> ShadowAssessment | None:
    """Записать теневую оценку, если флаг включён. Возвращает её же или ``None``.

    Не бросает и ничего не решает: вызывающий хук своего вердикта от
    возвращаемого значения не меняет. Сбой здесь — строка в журнале, а не
    потерянный ответ: теневой затвор не должен стоить человеку реплики.
    """

    try:
        if not shadow_flag().value:
            return None
        verdict = assess(knowledge, form_categories=form_categories, now=now)
        logger.info(
            "safety.claim_gate.shadow surface=%s licence=%s subjects_known=%d "
            "subjects_unknown=%d subjects_unavailable=%d claims=%d stale=%d "
            "stale_claim_ids=%s licence_age_s=%s form_blocked=%s form_categories=%s trace=%s",
            surface,
            verdict.licence,
            verdict.subjects_known,
            verdict.subjects_unknown,
            verdict.subjects_unavailable,
            verdict.claims,
            len(verdict.stale_claim_ids),
            ",".join(verdict.stale_claim_ids),
            "" if verdict.licence_age_s is None else verdict.licence_age_s,
            form_blocked,
            ",".join(verdict.form_categories),
            trace_id,
        )
        return verdict
    except Exception:  # noqa: BLE001 — a shadow must never cost the reply
        logger.exception("safety.claim_gate.shadow_failed surface=%s", surface)
        return None


__all__ = [
    "LICENCE_ABSENT",
    "LICENCE_EMPTY",
    "LICENCE_PRESENT",
    "SHADOW_SETTING",
    "ShadowAssessment",
    "assess",
    "shadow_flag",
    "shadow_observe",
]
