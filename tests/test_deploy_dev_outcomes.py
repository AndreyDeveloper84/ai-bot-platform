"""Три исхода выкладки, различимые по одному значку — DRF-1605, часть 2 из #1486.

``success`` — выложили; ``skipped`` — ``ci`` красный, ничего не пытались;
``failure`` — пытались и не смогли, ВКЛЮЧАЯ незаданную цель. Раньше
незаданный ``DEV_HOST`` давал зелёный no-op: «выложили» и «выкладывать
некуда» выглядели одинаково, и снятый секрет остановил бы выкладки молча,
под зелёным.

Что держат эти тесты:

* три задания — ``preflight`` (есть ли цель), ``not-configured`` (красное,
  с именем, ``exit 1``), ``deploy`` (за ``needs`` и ``if`` по выходу
  preflight);
* гейт «только зелёный ci» стоит на ``preflight`` — иначе красный ``ci``
  запустил бы ``not-configured`` и прогон стал бы ``failure`` вместо
  ``skipped``;
* в ``deploy`` нет пошаговых ``if: steps.guard…`` — решение принято один
  раз, на уровне задания.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/deploy-dev.yml"


@pytest.fixture(scope="module")
def jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def test_the_three_jobs_exist(jobs: dict) -> None:
    assert {"preflight", "not-configured", "deploy"} <= set(jobs), sorted(jobs)


def test_an_unconfigured_target_is_a_red_job_with_a_name(jobs: dict) -> None:
    job = jobs["not-configured"]
    assert "NOT configured" in (job.get("name") or ""), "имя задания не называет исход"
    assert job.get("needs") == "preflight"
    assert "ready != 'true'" in (job.get("if") or "")
    body = "\n".join((s.get("run") or "") for s in job["steps"])
    assert "::error::" in body, "исход не назван словами в логе"
    assert "exit 1" in body, "задание не красное — зелёный no-op вернулся"


def test_deploy_runs_only_when_preflight_says_ready(jobs: dict) -> None:
    job = jobs["deploy"]
    assert job.get("needs") == "preflight"
    assert "ready == 'true'" in (job.get("if") or "")
    per_step = [s.get("name") for s in job["steps"] if "steps.guard" in str(s.get("if", ""))]
    assert per_step == [], f"решение размазано по шагам, а принято должно быть один раз: {per_step}"


def test_the_green_ci_gate_sits_on_preflight(jobs: dict) -> None:
    """Иначе красный ``ci`` доходил бы до ``not-configured`` и прогон читался
    бы ``failure`` — «пытались», хотя ничего не пытались."""

    gate = jobs["preflight"].get("if") or ""
    assert "workflow_run.conclusion == 'success'" in gate
    assert "workflow_dispatch" in gate
    assert "ready" in str((jobs["preflight"].get("outputs") or {}).get("ready", "")), (
        "preflight не отдаёт ready наверх — deploy и not-configured решать нечем"
    )
