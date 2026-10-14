"""Реестр планировочных правил в форме провода — для сборки плана в каталоге (DRF-2879).

Сборку плана делает каталог (``POST /internal/me/plan/decision/``), а реестр
правил лежит здесь: каталог его не хранит и требует в теле каждого запроса.
Форма провода живёт рядом с загрузчиком — у владельца данных, а не у того,
кто их шлёт.

Преобразований два, и оба названы:

* ``as_of`` у значения ``UNKNOWN`` после разбора YAML — объект даты; в JSON
  уходит строка ISO. Больше ничего в значении не трогается;
* ``note`` не уходит: это проза для людей, каталог её не читает, а всё, что
  едет по проводу, становится частью договора.

Остальные поля — один в один из :class:`~apps.planning_rules.registry.PlanningRule`.

Пустой или частичный реестр здесь не собирается: вход — только проверенный
:class:`~apps.planning_rules.registry.PlanningRulesRegistry`. Не загрузился
реестр — тела нет, и запрос в каталог не уходит; это решает вызывающий.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from apps.planning_rules.registry import PlanningRule, PlanningRulesRegistry

#: Поля правила на проводе — закрытый перечень. ``note`` в нём нет намеренно.
WIRE_RULE_FIELDS: tuple[str, ...] = (
    "rule_id",
    "kind",
    "status",
    "value",
    "unit",
    "applicability",
    "provenance",
)


def registry_wire_body(registry: PlanningRulesRegistry) -> dict[str, Any]:
    """Тело ``rules_registry`` запроса сборки: версия и все правила реестра."""
    return {
        "registry_version": registry.registry_version,
        "rules": [_rule_wire(rule) for rule in registry.rules],
    }


def _rule_wire(rule: PlanningRule) -> dict[str, Any]:
    return {
        "rule_id": rule.rule_id,
        "kind": rule.kind,
        "status": rule.status,
        "value": _json_safe(rule.value),
        "unit": rule.unit,
        "applicability": _json_safe(rule.applicability),
        "provenance": _json_safe(rule.provenance),
    }


def _json_safe(value: Any) -> Any:
    """Копия значения, пригодная для JSON: даты — строками ISO, остальное как есть."""
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


__all__ = ["WIRE_RULE_FIELDS", "registry_wire_body"]
