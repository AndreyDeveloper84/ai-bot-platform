"""Загрузчик артефакта реестра планировочных правил.

Источник истины — ``ayla-knowledge`` (владелец по канону §15.7, носитель по
решению владельца D-1 от 08.09.2026, ``docs/OPEN_DECISIONS.md`` §70).
Сюда артефакт попадает выгрузкой при сборке
(``scripts/sync_planning_rules_registry.py``), а не чтением чужого
репозитория в рантайме.

Инварианты, которые этот модуль обязан держать:

* **неизвестная major-версия отвергается до разбора тела** — образец
  ``apps/integrations/ayla/recommendation_resolver_client.py``
  (``SUPPORTED_SPEC_MAJOR``, «разбирать неизвестное запрещено»);
* **``UNKNOWN`` — значение, а не отсутствие поля** (контракт §4.1): запись
  со статусом ``UNKNOWN`` обязана нести ``reason/asked_source/as_of``;
* **загрузчик ничего не порождает**: ни одно значение правила не выводится,
  не подставляется и не вычисляется здесь — записи отдаются как есть;
* ``registry_version`` доступна потребителю — пересчёт планов при смене
  версии (контракт §9) запускает Plan Engine, эта точка приёма делает
  смену версии наблюдаемой.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_PATH = PACKAGE_DIR / "data" / "planning-rules-registry.yaml"

REGISTRY_NAME = "ayla.planning-rules-registry"
SUPPORTED_REGISTRY_MAJOR = 0

CLOSED_KINDS = frozenset(
    {
        "CAPABILITY_SEMANTICS",
        "SERVICE_CAPABILITY_MAPPING",
        "DURATION",
        "EVENT_WINDOW",
        "MIN_INTERVAL",
        "MAX_INTERVAL",
        "REPETITION",
        "SEQUENCE",
        "PRECONDITION",
        "COMPATIBILITY",
        "INCOMPATIBILITY",
        "RECOVERY_WINDOW",
        "SAFETY_CONSTRAINT",
    }
)
CLOSED_STATUSES = frozenset({"KNOWN", "UNKNOWN", "INTENTIONALLY_UNSUPPORTED"})
CLOSED_UNKNOWN_REASONS = frozenset(
    {
        "NO_RULE_EXISTS",
        "RULE_NOT_APPLICABLE",
        "SOURCE_UNREACHABLE",
        "SOURCE_STALE",
        "MAPPING_MISSING",
        "POLICY_WITHHELD",
    }
)
CLOSED_SCOPES = frozenset({"GENERAL", "TENANT", "MARKETPLACE"})
CLOSED_SUBJECT_KINDS = frozenset({"capability", "canonical_service", "tenant_offer", "category"})

VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")


class PlanningRegistryError(Exception):
    """Базовая ошибка точки приёма реестра планировочных правил."""


class PlanningRegistryUnavailableError(PlanningRegistryError):
    """Артефакт отсутствует или не читается — fail-closed."""


class PlanningRegistryVersionError(PlanningRegistryError):
    """Неизвестная major-версия реестра — отвергается до разбора тела."""


class PlanningRegistryInvalidError(PlanningRegistryError):
    """Артефакт нарушает форму контракта."""


@dataclass(frozen=True)
class PlanningRule:
    """Одна запись реестра. ``value`` для ``UNKNOWN`` — mapping

    ``{reason, asked_source, as_of}``; для ``INTENTIONALLY_UNSUPPORTED`` —
    ``None``; для ``KNOWN`` — значение правила из источника.
    """

    rule_id: str
    kind: str
    status: str
    value: Any
    unit: str | None
    applicability: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    note: str | None = None


@dataclass(frozen=True)
class PlanningRulesRegistry:
    """Разобранный и проверенный артефакт."""

    registry_version: str
    compatible_contract_version: str
    updated: str
    rules: tuple[PlanningRule, ...]

    def get_rule(self, rule_id: str) -> PlanningRule | None:
        for rule in self.rules:
            if rule.rule_id == rule_id:
                return rule
        return None

    def rules_by_kind(self, kind: str) -> tuple[PlanningRule, ...]:
        return tuple(rule for rule in self.rules if rule.kind == kind)


def _reject_unknown_major(data: dict[str, Any]) -> str:
    """Вернуть версию реестра или отвергнуть документ до разбора тела."""
    version = data.get("registry_version")
    if not isinstance(version, str):
        raise PlanningRegistryVersionError(
            f"registry_version отсутствует или не строка: {version!r}"
        )
    match = VERSION_RE.match(version)
    if match is None or int(match.group(1)) != SUPPORTED_REGISTRY_MAJOR:
        raise PlanningRegistryVersionError(
            f"неизвестная мажорная версия реестра {version!r}; точка приёма умеет "
            f"{SUPPORTED_REGISTRY_MAJOR}.x. Разбирать неизвестное запрещено (контракт §9)"
        )
    return version


def _validate_rule(raw: Any, problems: list[str]) -> None:
    if not isinstance(raw, dict):
        problems.append(f"rule: ожидался mapping, получено {type(raw).__name__}")
        return
    rule_id = raw.get("rule_id", "<без rule_id>")
    if not isinstance(rule_id, str) or not rule_id:
        problems.append("rule_id обязан быть непустой строкой")
    if raw.get("kind") not in CLOSED_KINDS:
        problems.append(f"{rule_id}: kind вне закрытого перечня §3: {raw.get('kind')!r}")
    status = raw.get("status")
    if status not in CLOSED_STATUSES:
        problems.append(f"{rule_id}: status вне закрытого перечня: {status!r}")
        return
    if "value" not in raw:
        problems.append(f"{rule_id}: отсутствие value — ошибка артефакта, а не UNKNOWN (§4.1)")
        return
    value = raw["value"]
    if status == "UNKNOWN":
        if not isinstance(value, dict):
            problems.append(
                f"{rule_id}: UNKNOWN обязан нести value={{reason, asked_source, as_of}}"
            )
        else:
            if value.get("reason") not in CLOSED_UNKNOWN_REASONS:
                problems.append(f"{rule_id}: value.reason вне закрытого перечня §4.2")
            if not isinstance(value.get("asked_source"), str) or not value["asked_source"]:
                problems.append(f"{rule_id}: value.asked_source обязан быть адресом источника")
            if not value.get("as_of"):
                problems.append(f"{rule_id}: value.as_of обязан быть заполнен — UNKNOWN протухает")
    elif status == "INTENTIONALLY_UNSUPPORTED":
        if value is not None:
            problems.append(
                f"{rule_id}: INTENTIONALLY_UNSUPPORTED требует value: null — "
                f"тип пуст по решению владельца, значение запрещено"
            )
    else:  # KNOWN
        if value is None:
            problems.append(f"{rule_id}: KNOWN требует заполненного value")
        if not raw.get("unit"):
            problems.append(f"{rule_id}: KNOWN требует заполненного unit")
    applicability = raw.get("applicability")
    if not isinstance(applicability, dict):
        problems.append(f"{rule_id}: applicability должен быть mapping")
    else:
        if applicability.get("scope") not in CLOSED_SCOPES:
            problems.append(f"{rule_id}: applicability.scope вне закрытого перечня §15.4")
        if applicability.get("subject_kind") not in CLOSED_SUBJECT_KINDS:
            problems.append(f"{rule_id}: applicability.subject_kind вне закрытого перечня")
    provenance = raw.get("provenance")
    if not isinstance(provenance, dict):
        problems.append(f"{rule_id}: provenance должен быть mapping")
    else:
        source = provenance.get("source")
        if not isinstance(source, str) or ":" not in source:
            problems.append(f"{rule_id}: provenance.source обязан быть адресом '<repo>:<artifact>'")
        if not provenance.get("version"):
            problems.append(f"{rule_id}: provenance.version обязан быть заполнен")


def _validate_document(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise PlanningRegistryInvalidError(
            f"артефакт должен быть mapping, получено {type(data).__name__}"
        )
    if data.get("registry") != REGISTRY_NAME:
        raise PlanningRegistryInvalidError(
            f"registry: ожидалось {REGISTRY_NAME!r}, получено {data.get('registry')!r}"
        )
    version = _reject_unknown_major(data)
    problems: list[str] = []
    rules = data.get("rules")
    if not isinstance(rules, list):
        raise PlanningRegistryInvalidError("rules: обязан быть списком записей")
    seen_ids: set[str] = set()
    for raw in rules:
        _validate_rule(raw, problems)
        if isinstance(raw, dict) and isinstance(raw.get("rule_id"), str):
            if raw["rule_id"] in seen_ids:
                problems.append(f"{raw['rule_id']}: дубликат rule_id")
            seen_ids.add(raw["rule_id"])
    if problems:
        raise PlanningRegistryInvalidError(
            "артефакт реестра невалиден целиком; нарушения: " + "; ".join(problems)
        )
    data["registry_version"] = version
    return data


def load_registry(path: Path | None = None) -> PlanningRulesRegistry:
    """Загрузить артефакт и вернуть проверенный реестр.

    Fail-closed: отсутствующий, нечитаемый, невалидный артефакт или
    неизвестная major-версия — исключение, а не деградация в умолчание.
    """
    artifact = path if path is not None else DATA_PATH
    try:
        raw_bytes = artifact.read_bytes()
    except OSError as exc:
        raise PlanningRegistryUnavailableError(
            f"артефакт реестра недоступен: {artifact}: {exc}"
        ) from exc
    try:
        parsed = yaml.safe_load(raw_bytes)
    except yaml.YAMLError as exc:
        raise PlanningRegistryInvalidError(f"артефакт не разбирается как YAML: {exc}") from exc
    data = _validate_document(parsed)
    rules = tuple(
        PlanningRule(
            rule_id=raw["rule_id"],
            kind=raw["kind"],
            status=raw["status"],
            value=raw["value"],
            unit=raw.get("unit"),
            applicability=raw.get("applicability") or {},
            provenance=raw.get("provenance") or {},
            note=raw.get("note"),
        )
        for raw in data["rules"]
    )
    updated = data.get("updated")
    if isinstance(updated, (datetime.date, datetime.datetime)):
        updated_str = updated.isoformat()
    else:
        updated_str = str(updated or "")
    return PlanningRulesRegistry(
        registry_version=data["registry_version"],
        compatible_contract_version=str(data.get("compatible_contract_version", "")),
        updated=updated_str,
        rules=rules,
    )
