"""Версия Node для Mini App — из одного места, ``apps/miniapp/.nvmrc`` — DRF-2692.

До этой правки ``deploy-dev`` собирал Mini App на Node 20 (литерал в шаге),
а ``ci`` и ``miniapp-drift`` — на версии из ``.nvmrc`` (22). То есть бандл,
который уезжал людям, собирался не тем Node, на котором PR проходил проверки
и на котором сторож расхождения строит эталон для сравнения стилей.

01.10.2026 это не давало разницы: сборки на 20.20.2 и на 22.23.x дали
побайтно одинаковые js и css (замер в DRF-2690). Но равенство держалось
совпадением, а не устройством: первый же пакет, чей вывод зависит от версии
Node, сделал бы сторожа красным без единой правки исходников — или, хуже,
выложил бы людям бандл, которого CI не собирал.

Что держат эти узлы: каждый шаг ``actions/setup-node`` в каждом workflow
берёт версию из ``.nvmrc`` и не задаёт её литералом.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
NVMRC = "apps/miniapp/.nvmrc"


def _setup_node_steps() -> list[tuple[str, str, dict]]:
    found: list[tuple[str, str, dict]] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_id, job in (doc.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                if str(step.get("uses") or "").startswith("actions/setup-node@"):
                    found.append((path.name, job_id, step))
    return found


def test_the_census_sees_the_known_node_steps() -> None:
    """Перепись не пуста и видит все три известных места.

    Без этого узла остальные прошли бы на пустом списке: поменяйся имя
    действия или раскладка файлов — и «ни одного литерала» стало бы правдой
    о нуле шагов.
    """
    where = {(name, job_id) for name, job_id, _ in _setup_node_steps()}
    assert {"ci.yml", "deploy-dev.yml", "miniapp-drift.yml"} <= {name for name, _ in where}
    assert len(where) >= 3


@pytest.mark.parametrize(
    ("workflow", "job_id", "step"),
    _setup_node_steps(),
    ids=lambda v: v if isinstance(v, str) else "step",
)
def test_node_version_comes_from_nvmrc(workflow: str, job_id: str, step: dict) -> None:
    with_ = step.get("with") or {}
    assert "node-version" not in with_, (
        f"{workflow}:{job_id}: версия Node задана литералом "
        f"({with_.get('node-version')!r}) — должна читаться из {NVMRC}"
    )
    assert with_.get("node-version-file") == NVMRC, (
        f"{workflow}:{job_id}: node-version-file={with_.get('node-version-file')!r}, ждали {NVMRC}"
    )


def test_nvmrc_names_a_bare_major() -> None:
    """``.nvmrc`` — голая мажорная линия, как и объяснено в ``ci.yml``.

    setup-node разрешает её в ПОСЛЕДНЮЮ версию линии; конкретный старый патч
    здесь опустил бы среду ниже пола ``engines`` у зависимостей vitest.
    """
    value = (ROOT / NVMRC).read_text(encoding="utf-8").strip()
    assert value.isdigit(), f"{NVMRC}: {value!r} — ждали мажорную линию без патча"
