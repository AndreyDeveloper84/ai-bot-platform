"""Таксономия выбора NBA в тени — коды и словари как данные (DRF-1932, срез 6.3).

### Коды — решения владельца, байт в байт с каталогом

* target — H5: ``FACE_FRESHNESS``, ``PUFFINESS_REDUCTION``, ``RELAXATION``,
  ``BACK_COMFORT`` (каталог: ``recommendation.models.Target``, DRF-1922);
* action_type — H5: ``PROVIDER_SESSION``, ``SELF_CARE``, ``OBSERVE``, ``PLAN``
  (каталог: ``recommendation.models.ActionType``, DRF-1922);
* family — B9: ``ADDRESS``, ``SUPPORT``, ``RECOVER``, ``OBSERVE`` (каталог:
  ``Recommendation.Family``).

I1 (а), владелец 15.09: закрытой таблицы сочетаний нет — каждая ось проверяется по
своему множеству; ``family=OBSERVE`` и ``action_type=OBSERVE`` — разные оси без
автоматической связи. Роль варианта — как в каталоге: ``primary`` /
``alternative`` (``Recommendation.Role``).

### Словари — пустые до слова владельца

* :data:`TARGET_PHRASES` — «фраза → target» (J1);
* :data:`TARGET_DEFAULTS` — «target → (family, action_type)» (J2).

Правило выбора NBA — политика §31, и её значения утверждает владелец
(``OWNER_QUESTIONS_2026-09-12.md`` раздел J). Пока словари пусты, тень честно
пишет ``NBA_TARGET_NOT_RECOGNIZED``; сторож ``test_nba_taxonomy`` краснеет, если
значения въедут молча, без смены :data:`TAXONOMY_VERSION`.

### Признаки, при которых NBA не выбирается

* :data:`PAIN_SIGNAL_WORDS` — I2 (а), слова владельца дословно: «ноет», «болит»,
  «простреливает», «онемение», «отдаёт», «слабость». «И другие» словарь не
  покрывает — это названное слепое пятно, а не проход: признаки вне словаря
  ловит только вердикт безопасности хода.
* :data:`HEALTH_CONTEXT_PHRASES` — пакет 3 п.6: «у меня / меня беспокоят отёки».
  Название услуги («снятие отёков») признаком не является: слова «отёков» в
  словаре нет намеренно.

Реплика читается только здесь и только для этих словарей; наружу уходят коды.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: ``recommendation.models.Target`` (каталог, DRF-1922). Порядок — порядок H5; он
#: же порядок основной цели, когда распознано несколько.
TARGETS: tuple[str, ...] = ("FACE_FRESHNESS", "PUFFINESS_REDUCTION", "RELAXATION", "BACK_COMFORT")
#: ``Recommendation.Family`` (каталог, B9).
FAMILIES: tuple[str, ...] = ("ADDRESS", "SUPPORT", "RECOVER", "OBSERVE")
#: ``recommendation.models.ActionType`` (каталог, DRF-1922).
ACTION_TYPES: tuple[str, ...] = ("PROVIDER_SESSION", "SELF_CARE", "OBSERVE", "PLAN")

#: ``Recommendation.Role`` (каталог).
ROLE_PRIMARY = "primary"
ROLE_ALTERNATIVE = "alternative"

#: Метка версии таксономии (≤ 32 знаков, одна константа на метку). Коды H5 есть,
#: словаря фраз нет — версия не утверждает того, чего нет.
TAXONOMY_VERSION = "h5-codes:no-phrase-map"

#: J1 — ждёт слова владельца. Ключ — фраза, значение — код из :data:`TARGETS`.
TARGET_PHRASES: Mapping[str, str] = {}
#: J2 — ждёт слова владельца. target → (family, action_type).
TARGET_DEFAULTS: Mapping[str, tuple[str, str]] = {}

#: I2 (а), владелец 15.09 — дословно, в нормализованной форме (ё → е).
PAIN_SIGNAL_WORDS: frozenset[str] = frozenset(
    {"ноет", "болит", "простреливает", "онемение", "отдает", "слабость"}
)
#: Пакет 3 п.6 — «у меня / меня беспокоят отёки», в нормализованной форме.
HEALTH_CONTEXT_PHRASES: tuple[str, ...] = ("у меня отеки", "беспокоят отеки")

_WORD_RE = re.compile(r"\w+", re.UNICODE)


class TaxonomyError(ValueError):
    """Значение словаря вне утверждённого множества — отказ, не выдумка."""


@dataclass(frozen=True)
class Triple:
    """Тройка варианта рекомендации: одна цель, одно семейство, один тип действия."""

    target: str
    family: str
    action_type: str

    def as_record(self, role: str) -> dict[str, Any]:
        """Поля варианта в написании записи каталога."""

        return {
            "role": role,
            "target": self.target,
            "family": self.family,
            "action_type": self.action_type,
        }


@dataclass(frozen=True)
class TurnNeeds:
    """Что словари прочитали в реплике хода — только коды и признаки."""

    recognized_targets: tuple[str, ...] = ()
    pain_signal: bool = False
    health_context: bool = False


EMPTY_NEEDS = TurnNeeds()


def words(text: str | None) -> tuple[str, ...]:
    """Слова реплики: нижний регистр, ё → е."""

    return tuple(_WORD_RE.findall((text or "").casefold().replace("ё", "е")))


def _contains(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[i : i + len(needle)] == needle for i in range(len(haystack) - len(needle) + 1)
    )


def validate(phrases: Mapping[str, str], defaults: Mapping[str, tuple[str, str]]) -> None:
    """Каждое значение — из своего множества; фраза — непустая. Иначе отказ."""

    for phrase, target in phrases.items():
        if not words(phrase):
            raise TaxonomyError(f"фраза без слов: {phrase!r}")
        if target not in TARGETS:
            raise TaxonomyError(f"target вне H5: {target!r}")
    for target, pair in defaults.items():
        if target not in TARGETS:
            raise TaxonomyError(f"target вне H5: {target!r}")
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise TaxonomyError(f"умолчание {target}: нужна пара (family, action_type)")
        family, action_type = pair
        if family not in FAMILIES:
            raise TaxonomyError(f"family вне B9: {family!r}")
        if action_type not in ACTION_TYPES:
            raise TaxonomyError(f"action_type вне H5: {action_type!r}")


def read_turn_needs(
    message_text: str | None, *, phrases: Mapping[str, str] | None = None
) -> TurnNeeds:
    """Цели и признаки в реплике хода. Чистая функция; текст наружу не отдаёт."""

    phrases = TARGET_PHRASES if phrases is None else phrases
    validate(phrases, {})
    said = words(message_text)
    health = any(_contains(said, words(p)) for p in HEALTH_CONTEXT_PHRASES)
    pain = any(word in PAIN_SIGNAL_WORDS for word in said)
    found = {target for phrase, target in phrases.items() if _contains(said, words(phrase))}
    # п.6: при health-контексте цели нет вовсе — распознанную не пишем.
    targets = () if health else tuple(t for t in TARGETS if t in found)
    return TurnNeeds(recognized_targets=targets, pain_signal=pain, health_context=health)


def triples_for(
    targets: tuple[str, ...], *, defaults: Mapping[str, tuple[str, str]] | None = None
) -> tuple[Triple, ...]:
    """Тройки по умолчаниям J2 — только для целей, у которых умолчание есть."""

    defaults = TARGET_DEFAULTS if defaults is None else defaults
    validate({}, defaults)
    return tuple(Triple(t, *defaults[t]) for t in targets if t in defaults)


validate(TARGET_PHRASES, TARGET_DEFAULTS)


__all__ = [
    "ACTION_TYPES",
    "EMPTY_NEEDS",
    "FAMILIES",
    "HEALTH_CONTEXT_PHRASES",
    "PAIN_SIGNAL_WORDS",
    "ROLE_ALTERNATIVE",
    "ROLE_PRIMARY",
    "TARGETS",
    "TARGET_DEFAULTS",
    "TARGET_PHRASES",
    "TAXONOMY_VERSION",
    "TaxonomyError",
    "Triple",
    "TurnNeeds",
    "read_turn_needs",
    "triples_for",
    "validate",
    "words",
]
