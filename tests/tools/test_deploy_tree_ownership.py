"""The ownership guard must be able to see foreignness at all.

`0 foreign objects` is equally consistent with "the tree is clean" and "this
thing cannot tell foreign from own". Every test here exists to separate those
two readings, and the positive control below is the one that does it: the same
entries, judged against an expected uid that owns nothing, must come back
entirely foreign.

The guard splits `walk()` (which calls `stat`) from `classify()` (which
judges) precisely so these tests can run anywhere. On Windows `st_uid` is 0
for every object, so a guard that measured and judged in one pass could only
be tested on the box it guards — and would therefore never be tested.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.lint.deploy_tree_ownership import (
    CATEGORIES,
    GIT,
    OTHER,
    PYCACHE,
    STATICFILES,
    Entry,
    categorise,
    classify,
    read_baseline,
    report,
)

DEPLOY_UID = 1000
ROOT_UID = 0


def _tree() -> list[Entry]:
    return [
        Entry(".git/logs/refs/remotes/origin/measure/thing", ROOT_UID),
        Entry(".git/objects/ab/cdef", ROOT_UID),
        Entry("staticfiles/admin/css/base.css", ROOT_UID),
        Entry("tests/support/migration_graph.py", ROOT_UID),
        Entry("apps/identity/models.py", DEPLOY_UID),
        Entry("README.md", DEPLOY_UID),
    ]


# ─── the positive control, and it is the point ──────────────────────────────


def test_an_expected_uid_that_owns_nothing_makes_everything_foreign() -> None:
    """Проверяет не «сторож не упал», а что он **умеет** видеть чужое.

    Тот же набор путей, но владельцем объявлен uid, которому не принадлежит
    ничто. Сторож обязан насчитать все шесть. Без этой проверки «ноль чужих»
    в боевом прогоне одинаково означало бы «чисто» и «не различает».
    """
    entries = _tree()

    found = classify(entries, expected_uid=4242)

    assert sum(found.values()) == len(entries)


def test_a_tree_owned_entirely_by_the_deploy_user_counts_zero() -> None:
    """Отрицательная половина того же: своё не считается чужим.

    Стоит рядом с положительным контролем, потому что поодиночке они
    доказывают половину: один — что сторож что-то считает, другой — что он
    считает не всё подряд.
    """
    entries = [Entry(e.path, DEPLOY_UID) for e in _tree()]

    found = classify(entries, expected_uid=DEPLOY_UID)

    assert set(found) == set(CATEGORIES)
    assert sum(found.values()) == 0


# ─── the breakdown is the whole reason this is per category ─────────────────


def test_foreign_objects_land_in_the_right_buckets() -> None:
    found = classify(_tree(), expected_uid=DEPLOY_UID)

    assert found == {GIT: 2, STATICFILES: 1, PYCACHE: 0, OTHER: 1}


def test_every_category_is_reported_even_at_zero() -> None:
    """Пустую корзину нельзя опускать.

    Отчёт без корзины неотличим от отчёта, который в неё не смотрел — и
    `__pycache__ = 0` здесь именно измеренный ноль, а не пропуск: предсказание,
    что он окажется главным, не подтвердилось, и это факт, который отчёт обязан
    продолжать сообщать.
    """
    found = classify([], expected_uid=DEPLOY_UID)

    assert list(found) == list(CATEGORIES)
    assert found[PYCACHE] == 0


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (".git/config", GIT),
        ("apps/.git/nested", GIT),
        ("staticfiles/admin/css/base.css", STATICFILES),
        ("staticfiles_old/leftover.css", OTHER),
        ("apps/x/__pycache__/y.cpython-312.pyc", PYCACHE),
        ("apps/x/y.pyc", PYCACHE),
        ("tests/test_admin_humanize.py", OTHER),
        ("apps/gitignore_helper.py", OTHER),
    ],
)
def test_categorisation_matches_on_segments_not_substrings(path: str, expected: str) -> None:
    """`staticfiles_old` — не `staticfiles/`, а `gitignore_helper.py` — не `.git`.

    Подстрочное совпадение дало бы обе ошибки сразу: чужой каталог зачли бы в
    починенную корзину, а обычный исходник — в служебную. Шаблон ошибается в обе
    стороны, и разнести это можно только по сегментам пути.
    """
    assert categorise(path) == expected


# ─── the ratchet ────────────────────────────────────────────────────────────


def test_the_recorded_baseline_is_the_measured_one() -> None:
    """Храповик заведён числом с хоста, а не нулями «для начала».

    Нули означали бы, что дерево чистое, — и первый же прогон объявил бы 3315
    существующих объектов свежей регрессией, свалив вину на того, кто оказался
    рядом.
    """
    baseline = read_baseline()

    assert baseline[GIT] == 2130
    assert baseline[STATICFILES] == 174
    assert baseline[OTHER] == 1011
    assert sum(baseline.values()) == 3315


def test_growth_in_one_bucket_fails_even_when_the_total_shrinks() -> None:
    """Чинили одно, сломали другое — это провал, а не «в сумме лучше».

    Сравнение по сумме позволило бы починке `staticfiles/` оплатить новую
    регрессию в `.git/`, и обе стали бы невидимы.
    """
    baseline = {GIT: 2130, STATICFILES: 174, PYCACHE: 0, OTHER: 1011}
    found = {GIT: 2200, STATICFILES: 0, PYCACHE: 0, OTHER: 1011}

    assert sum(found.values()) < sum(baseline.values())

    text, failed = report(found, baseline)

    assert failed is True
    assert "ВЫРОСЛО" in text


def test_shrinking_is_reported_but_does_not_fail() -> None:
    baseline = {GIT: 2130, STATICFILES: 174, PYCACHE: 0, OTHER: 1011}
    found = {GIT: 2130, STATICFILES: 0, PYCACHE: 0, OTHER: 1011}

    text, failed = report(found, baseline)

    assert failed is False
    assert "опустите храповик" in text


def test_a_missing_baseline_is_an_error_not_an_empty_one(tmp_path: Path) -> None:
    """Ненайденный храповик читать как нули нельзя.

    «Никто ещё не мерил» превратилось бы в «дерево чистое» — самая громкая
    версия ровно того дефекта, ради которого файл и заведён.
    """
    with pytest.raises(SystemExit):
        read_baseline(tmp_path / "does-not-exist.txt")
