"""Выкладка идёт по ПРОВЕРЕННОМУ SHA, а не по голове dev — B-5 (5.2), DRF-1615.

``workflow_run`` несёт ``head_sha`` того прогона ``ci``, который был зелёным.
Голова ``dev`` в момент выкладки может быть уже другой: слияния идут чаще,
чем проходит ``ci`` (11.09.2026 — десятки за день). ``git pull dev`` на хосте
выкладывал бы то, что ещё никто не проверял, под зелёной галкой чужого
прогона.

Что держат эти тесты:

* на хосте **нет** ``git pull`` и ``git checkout dev`` — дерево ставится
  ``checkout --detach`` на ``DEPLOY_SHA``;
* ``DEPLOY_SHA`` берётся из шага ``verified``, а тот — из ``head_sha``
  прогона, не из ``github.sha`` (для ``workflow_run`` это SHA default-ветки
  на момент события, то есть как раз голова);
* перед checkout проверяется, что SHA лежит на ``origin/dev``; после — что
  дерево на нём; SHA дерева печатается РЯДОМ с проверенным;
* сборка Mini App и извещение берут **тот же** SHA: витрина и бот из одного
  коммита.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/deploy-dev.yml"


def code_lines(step: dict) -> list[str]:
    out = []
    for line in (step.get("run") or "").splitlines():
        bare = line.strip().lstrip("\\")
        if not bare.startswith("#"):
            out.append(bare)
    return out


@pytest.fixture(scope="module")
def steps() -> list[dict]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["deploy"]["steps"]


@pytest.fixture(scope="module")
def verified(steps: list[dict]) -> dict:
    step = next((s for s in steps if s.get("id") == "verified"), None)
    assert step is not None, "шага verified нет — выкладка снова по голове dev"
    return step


@pytest.fixture(scope="module")
def deploy(steps: list[dict]) -> dict:
    step = next((s for s in steps if "restart dev services" in (s.get("name") or "")), None)
    assert step is not None, "основной шаг выкладки не найден — сторож смотрит не туда"
    return step


def test_the_verified_sha_comes_from_the_run_not_from_the_branch_head(verified: dict) -> None:
    """``github.sha`` при ``workflow_run`` — голова default-ветки на момент
    события, то есть ровно то, от чего уходим. ``head_sha`` прогона — то,
    что проверено."""

    env = verified.get("env") or {}
    assert "github.event.workflow_run.head_sha" in str(env.get("RUN_SHA", "")), (
        "проверенный SHA не берётся из head_sha прогона ci"
    )
    body = "\n".join(code_lines(verified))
    assert "[0-9a-f]{40}" in body, "SHA не проверяется на форму — пустой дал бы checkout ''"
    assert "sha=" in body and "GITHUB_OUTPUT" in body, "SHA не отдаётся дальше как output"


def test_the_host_checks_out_the_verified_sha_and_never_pulls_dev(deploy: dict) -> None:
    lines = code_lines(deploy)
    env = deploy.get("env") or {}
    assert "steps.verified.outputs.sha" in str(env.get("DEPLOY_SHA", "")), (
        "DEPLOY_SHA не приходит из шага verified"
    )

    # Присутствие впереди отсутствия: checkout проверенного SHA есть.
    checkout = [ln for ln in lines if "git checkout --detach" in ln and "DEPLOY_SHA" in ln]
    assert len(checkout) == 1, f"checkout проверенного SHA: {checkout}"

    pulls = [ln for ln in lines if "git pull" in ln or "git checkout dev" in ln]
    assert pulls == [], f"хост снова ставит голову dev: {pulls}"


def test_the_sha_is_verified_to_be_on_dev_before_checkout_and_in_the_tree_after(
    deploy: dict,
) -> None:
    lines = code_lines(deploy)
    ancestor = next((i for i, ln in enumerate(lines) if "merge-base --is-ancestor" in ln), -1)
    checkout = next((i for i, ln in enumerate(lines) if "git checkout --detach" in ln), -1)
    tree = next((i for i, ln in enumerate(lines) if "TREE_HEAD=" in ln), -1)

    assert ancestor != -1, "SHA не проверяется на принадлежность dev — переписанный dev пройдёт"
    assert checkout != -1, "checkout не найден"
    assert tree != -1, "SHA дерева после checkout не читается"
    assert ancestor < checkout < tree, (
        f"порядок: is-ancestor ({ancestor}) < checkout ({checkout}) < TREE_HEAD ({tree})"
    )
    assert any("TREE_HEAD" in ln and "DEPLOY_SHA" in ln and "::notice::" in ln for ln in lines), (
        "SHA дерева не печатается рядом с проверенным — совпадение не видно глазом"
    )
    assert any("TREE_HEAD" in ln and "DEPLOY_SHA" in ln and "exit 1" in ln for ln in lines), (
        "расхождение дерева с проверенным SHA не является отказом"
    )


def test_the_miniapp_and_the_alert_use_the_same_verified_sha(steps: list[dict]) -> None:
    """Витрина и бот из одного коммита; извещение называет то, что выложено."""

    checkouts = [s for s in steps if (s.get("uses") or "").startswith("actions/checkout")]
    assert checkouts, "checkout на раннере не найден"
    for s in checkouts:
        ref = str((s.get("with") or {}).get("ref", ""))
        assert "steps.verified.outputs.sha" in ref, (
            f"checkout на раннере не по проверенному SHA: {ref!r}"
        )

    alert = next((s for s in steps if "SHA" in (s.get("env") or {})), None)
    assert alert is not None, "шаг с SHA в env не найден"
    assert "steps.verified.outputs.sha" in str(alert["env"]["SHA"]), (
        "извещение называет github.sha — голову, а не выложенное"
    )
