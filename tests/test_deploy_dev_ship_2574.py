"""Отправка Mini App на машину — DRF-2574.

28.09 ``deploy-dev`` трижды умирал на отправке сборки: везли 5,2 МБ, из них
4,29 МБ — карта кода ``index-….js.map``, при канале ~4,5 КБ/с. Задание
упиралось в свой предел и кончалось «отменён» — без тревоги, как шум очереди.
А проверка перед подменой («``index.html`` непуст и в ``assets`` хоть один
файл») частичную передачу пропустила бы — 28.09 спас порядок прихода файлов.

Что держат эти узлы:

* карта не едет на машину (``tar --exclude='*.map'``), но и не выброшена —
  она артефакт прогона, и шаг артефакта стоит ДО отправки;
* у шага отправки свой предел, меньше предела задания: падает шаг, а не
  задание целиком;
* подмена ``dist.new → dist`` идёт только после сверки полноты — числа
  файлов и суммы байт, посчитанных на раннере ДО передачи;
* провал отправки назван отдельным шагом словами;
* обратное чтение дерева бэкенда не зависит от судьбы Mini App.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/deploy-dev.yml"


@pytest.fixture(scope="module")
def job() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["deploy"]


def _step(job: dict, *, sid: str | None = None, name_part: str | None = None) -> tuple[int, dict]:
    for i, step in enumerate(job["steps"]):
        if sid is not None and step.get("id") == sid:
            return i, step
        if name_part is not None and name_part in (step.get("name") or ""):
            return i, step
    raise AssertionError(f"шаг не найден: id={sid} name~{name_part}")


def test_the_source_map_is_not_shipped(job: dict) -> None:
    _, ship = _step(job, sid="ship")
    tar_lines = [ln for ln in ship["run"].splitlines() if "tar " in ln and "-cf -" in ln]
    assert len(tar_lines) == 1, tar_lines
    assert "--exclude='*.map'" in tar_lines[0]


def test_the_source_map_is_kept_as_an_artifact_before_shipping(job: dict) -> None:
    ship_at, _ = _step(job, sid="ship")
    art_at, art = _step(job, name_part="source map")
    assert (art.get("uses") or "").startswith("actions/upload-artifact@")
    assert "*.map" in art["with"]["path"]
    assert art_at < ship_at, "карту сохраняют до отправки, а не после её возможного провала"


def test_ship_has_its_own_limit_below_the_job_limit(job: dict) -> None:
    _, ship = _step(job, sid="ship")
    assert isinstance(ship.get("timeout-minutes"), int)
    assert ship["timeout-minutes"] < job["timeout-minutes"]


def test_swap_happens_only_after_a_completeness_check(job: dict) -> None:
    lines = _step(job, sid="ship")[1]["run"].splitlines()

    def first(pred) -> int:
        return next((i for i, ln in enumerate(lines) if pred(ln)), -1)

    expect = first(lambda ln: ln.strip().startswith("EXPECT_FILES="))
    tar = first(lambda ln: "tar " in ln and "-cf -" in ln)
    compare = first(
        lambda ln: "GOT_FILES" in ln and "EXPECT_FILES" in ln and ln.strip().startswith("if ")
    )
    swap = first(lambda ln: "mv dist.new dist" in ln)
    assert -1 not in (expect, tar, compare, swap), (expect, tar, compare, swap)
    assert expect < tar < compare < swap, "опись до передачи, сверка до подмены"
    assert "EXPECT_BYTES" in lines[compare] and "GOT_BYTES" in lines[compare], (
        "сверяются и число, и байты"
    )


def test_a_failed_ship_is_named(job: dict) -> None:
    ship_at, _ = _step(job, sid="ship")
    rep_at, rep = _step(job, name_part="not delivered")
    assert rep_at > ship_at
    assert "failure()" in rep["if"] and "steps.ship.outcome == 'failure'" in rep["if"]
    assert "::error::" in rep["run"]


def test_readback_does_not_depend_on_the_mini_app(job: dict) -> None:
    _, rebuild = _step(job, sid="rebuild")
    assert "restart dev services" in rebuild["name"]
    _, readback = _step(job, name_part="Readback")
    cond = readback.get("if") or ""
    assert "steps.rebuild.outcome == 'success'" in cond
    assert "!cancelled()" in cond
