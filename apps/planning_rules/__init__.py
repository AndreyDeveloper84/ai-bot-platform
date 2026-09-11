"""Точка приёма реестра планировочных правил (решение владельца D-1).

Источник истины — ``ayla-knowledge/03 AI System/Contracts/planning-rules-registry.yaml``,
экспортируется при сборке (``scripts/sync_planning_rules_registry.py``) и кладётся
в ``data/``. Пакет ничего не вычисляет и не додумывает: он разбирает артефакт,
отвергает неизвестную major-версию до разбора тела и отдаёт записи как есть.
"""

from apps.planning_rules.registry import (
    PlanningRegistryError,
    PlanningRegistryInvalidError,
    PlanningRegistryUnavailableError,
    PlanningRegistryVersionError,
    PlanningRule,
    PlanningRulesRegistry,
    load_registry,
)

__all__ = [
    "PlanningRegistryError",
    "PlanningRegistryInvalidError",
    "PlanningRegistryUnavailableError",
    "PlanningRegistryVersionError",
    "PlanningRule",
    "PlanningRulesRegistry",
    "load_registry",
]
