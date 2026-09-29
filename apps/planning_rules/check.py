#!/usr/bin/env python3
"""Offline-проверка целостности артефакта реестра планировочных правил.

Вызывается шагом сборки образа (Dockerfile). Сборка обязана **сломаться**,
если артефакт отсутствует, подменён (хеш не совпал с ``data/source.json``)
или несёт неизвестную major-версию — молчаливая выкладка со старым или
чужим реестром хуже отказа (D-1, ``docs/HANDOFF_PLANNING_RULES_REGISTRY.md``).

Свежесть относительно источника здесь не проверяется — офлайн-шаг сборки не
ходит в сеть; дрейф ловит ``scripts/sync_planning_rules_registry.py --check``
отдельным workflow.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

# Работает и как `python apps/planning_rules/check.py` (Dockerfile), и как
# `python -m apps.planning_rules.check` — корень репозитория добавляется явно.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from apps.planning_rules.registry import (  # noqa: E402
    DATA_PATH,
    PlanningRegistryError,
    load_registry,
)

SOURCE_PATH = Path(__file__).resolve().parent / "data" / "source.json"

REQUIRED_SOURCE_FIELDS = {"repository", "path", "ref", "sha256", "synced_at"}


def verify_packaged_registry() -> list[str]:
    """Вернуть список нарушений целостности; пустой список — артефакт цел."""
    problems: list[str] = []
    try:
        source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"data/source.json не читается: {exc}"]
    missing = REQUIRED_SOURCE_FIELDS - set(source)
    if missing:
        problems.append(f"data/source.json: отсутствуют поля {sorted(missing)}")
        return problems
    try:
        raw_bytes = DATA_PATH.read_bytes()
    except OSError as exc:
        return [f"артефакт реестра отсутствует: {exc}"]
    digest = hashlib.sha256(raw_bytes).hexdigest()
    if digest != source["sha256"]:
        problems.append(
            f"артефакт подменён или source.json устарел: sha256 {digest} != {source['sha256']}"
        )
        return problems
    try:
        load_registry()
    except PlanningRegistryError as exc:
        problems.append(f"артефакт не проходит проверку загрузчика: {exc}")
    return problems


def main() -> int:
    problems = verify_packaged_registry()
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1
    registry = load_registry()
    print(
        f"OK: реестр планировочных правил цел — "
        f"registry_version {registry.registry_version}, {len(registry.rules)} записей, "
        f"источник {json.loads(SOURCE_PATH.read_text(encoding='utf-8'))['repository']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
