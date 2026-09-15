"""Снимок контекста хода для записи Recommendation (DRF-1903, срез 6.1 окна «Мозг»).

### Зачем

Запись Recommendation (каталог, ``recommendation/records.py``) обязана ссылаться
на снимок фактов, на которые опиралось решение: ``context_snapshot_ref
{snapshot_id, snapshot_version, content_digest}`` (контракт §7). Производителя
снимка не было ни в боте, ни в каталоге (замер 15.09,
``Ayla/docs/PLAN_SLICE6_NBA_PRODUCER_2026-09-15.md``).

Решение главного окна 15.09: снимок **хранит каталог** той же ручкой, что пишет
набор (PR-A окна канона), в одной транзакции; бот отдаёт содержимое,
``content_digest`` и ``snapshot_version``. Этот модуль — сборщик содержимого.

### Что внутри — и чего внутри быть не может

Условие главного окна: только коды, ссылки и значения закрытых словарей —
**без исходного текста реплик и без смыслов здоровья**. Поэтому:

* ``said`` — факты «сказано в разговоре» (:mod:`apps.orchestrator.said_memory`):
  город в хранимом написании (из набора городов с мастерами) и
  ``visit_context`` из закрытого словаря; дата — без времени. Уже под гейтами
  согласия и стирания: после «забудь всё» их нет;
* ``answered_question`` — только ``question_id`` закрытого открытого вопроса;
  ни вопрос, ни ответ текстом не берутся. **Вопросы класса здоровья не
  берутся вовсе** (слово главного окна 15.09: сам факт вопроса о здоровье —
  смысл здоровья); класс определяется префиксом id
  (:data:`HEALTH_QUESTION_PREFIXES`);
* ``decision_readiness`` — ревизия состояния и ``readiness_state`` движка.

Безопасности в содержимом **нет**: она едет в записи отдельной ссылкой
``safety_evaluation_ref`` (слово главного окна 15.09); дублировать её в снимке
значило бы хранить её дважды.

Это не соглашение, а проверка: :func:`assert_codes_only` отвергает любой лист,
который не код / число / дата / значение закрытого словаря, и сборщик вызывает
её перед тем, как считать digest. Текст, попавший в снимок, не посчитается и не
уйдёт.

### Digest

sha256 от канонического JSON (сортированные ключи, без пробелов, UTF-8) —
одинаковые факты дают одинаковый digest независимо от порядка сборки; каталог
сверяет его при записи.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

#: Версия схемы содержимого. Меняется при любом изменении состава полей.
SNAPSHOT_VERSION = "turn-context-v1"

#: Код: латиница, цифры, ``_ . : / -``; без пробелов. Это же покрывает UUID,
#: ``rule_id`` вида ``pre_check:clarify`` и ``evidence_ref``.
_CODE_RE = re.compile(r"^[A-Za-z0-9_.:/\-]{1,200}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


#: Классы вопросов, чей id сам по себе — смысл здоровья. По префиксу, а не по
#: списку имён: новый вопрос скрининга не должен проскочить потому, что его
#: забыли дописать.
HEALTH_QUESTION_PREFIXES: tuple[str, ...] = ("health_", "screening", "safety_", "wellness_")


class SnapshotRejected(ValueError):
    """В снимок попало то, чему там не место (текст, неизвестный ключ)."""


@dataclass(frozen=True)
class TurnSnapshot:
    content: dict[str, Any]
    content_digest: str
    snapshot_version: str = SNAPSHOT_VERSION


def _closed_values() -> frozenset[str]:
    """Значения закрытых словарей, которые могут стоять в снимке не-кодом."""

    from apps.marketplace.discovery import _known_cities

    try:
        cities = frozenset(str(c) for c in _known_cities())
    except Exception:  # noqa: BLE001 — без набора городов город в снимок не пойдёт
        cities = frozenset()
    return cities


def assert_codes_only(value: Any, *, closed: frozenset[str], path: str = "$") -> None:
    """Каждый лист — код, число, bool/None, дата или значение закрытого словаря."""

    if value is None or isinstance(value, bool | int | float):
        return
    if isinstance(value, str):
        if _CODE_RE.match(value) or _DATE_RE.match(value) or value in closed:
            return
        raise SnapshotRejected(f"{path}: не код и не значение закрытого словаря")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not _CODE_RE.match(key):
                raise SnapshotRejected(f"{path}: ключ не код")
            assert_codes_only(item, closed=closed, path=f"{path}.{key}")
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            assert_codes_only(item, closed=closed, path=f"{path}[{index}]")
        return
    raise SnapshotRejected(f"{path}: тип {type(value).__name__} не допускается")


def content_digest(content: dict[str, Any]) -> str:
    canonical = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _said_section(bot_user: Any) -> list[dict[str, Any]]:
    from apps.orchestrator.said_memory import ORIGIN_CONVERSATION, said_facts

    rows = [
        {
            "key": fact.key,
            "value": fact.value,
            "origin": ORIGIN_CONVERSATION,
            "said_on": fact.said_at.date().isoformat() if fact.said_at else None,
        }
        for fact in said_facts(bot_user)
    ]
    return sorted(rows, key=lambda row: row["key"])


def _answered_section(conversation: Any) -> dict[str, Any] | None:
    from apps.orchestrator.open_question import ANSWERED_KEY

    state = getattr(conversation, "skill_state", None)
    row = state.get(ANSWERED_KEY) if isinstance(state, dict) else None
    if not isinstance(row, dict) or not row.get("question_id"):
        return None
    question_id = str(row["question_id"])
    if question_id.startswith(HEALTH_QUESTION_PREFIXES):
        return None
    # Только идентификатор вопроса: ни asked_text, ни answer_text в снимок не идут.
    return {"question_id": question_id}


def build_turn_snapshot(
    *,
    bot_user: Any,
    conversation: Any,
    dr_state: Any,
    readiness_state: str | None,
) -> TurnSnapshot:
    """Собрать снимок хода. Бросает :class:`SnapshotRejected`, если в содержимое
    попало то, чему там не место, — вызывающий решает, что делать с отказом."""

    content: dict[str, Any] = {
        "snapshot_version": SNAPSHOT_VERSION,
        "decision_readiness": {
            "state_revision": getattr(dr_state, "revision", None),
            "readiness_state": readiness_state,
        },
        "said": _said_section(bot_user) if bot_user is not None else [],
        "answered_question": _answered_section(conversation),
    }
    assert_codes_only(content, closed=_closed_values())
    return TurnSnapshot(content=content, content_digest=content_digest(content))


__all__ = [
    "HEALTH_QUESTION_PREFIXES",
    "SNAPSHOT_VERSION",
    "SnapshotRejected",
    "TurnSnapshot",
    "assert_codes_only",
    "build_turn_snapshot",
    "content_digest",
]
