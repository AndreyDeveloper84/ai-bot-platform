"""Tests for tools/lint/decision_reversal_guard.py — the DRF-1656 guard.

The guard's subject is a cancelled *decision*, and a cancelled decision
produces no diff, no failing test and no CI event. That makes this test
file the only place where the guard is ever proved to bite, so it is
built the way the repo's other lint calibrations are: every probe states
**how many matches it expects**, because a scanner that silently stops
matching reports «0 violations» in exactly the same words as a scanner
that inspected everything and found nothing wrong.

Four kinds of test:

**Calibration** pins the guard against the live registry: the 16
cancelling sections measured on ``dev@d5e3c75e`` must still be found and
must still resolve through the amnesty list. If that number collapses to
zero the markers have drifted, and the whole guard is decoration.

**Mechanics** prove the three rules — block present, paths real, zero
sections is a failure — each with the number of matches it produced.

**Silence** is tested as hard: the registry talks about cancelling
*bookings* constantly («## 6 … отгул, отменяющий записанных клиентов»,
«## 34. Системная отмена», state name ``ОТМЕНЕНО``). A guard that fires
on those gets switched off in a week.

**The named limit** is pinned too: an incomplete list of paths passes
green, on purpose. That is written in the guard's docstring, and a test
holds it in place so a later reader cannot mistake the guard for proof
that the border is closed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# `tools/` is not a package (no __init__.py) — import via path injection,
# same pattern as test_negative_assert_guard.py / test_import_boundaries.py.
sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "lint"))
import decision_reversal_guard as guard  # type: ignore[import-not-found]  # noqa: E402

_REGISTRY = _PROJECT_ROOT / "docs" / "OPEN_DECISIONS.md"

# Measured on dev@d5e3c75e (11.09.2026) by running the guard with an
# empty amnesty: 204 sections, 16 of them cancelling, 16 of those with
# no path block. The registry only grows, so this is a floor.
_MEASURED_CANCELLING = 16


def _scan(text: str, baseline: set[str] | None = None):
    return guard.scan(text, _PROJECT_ROOT, baseline or set())


# A path that exists in this repo and a path that does not. Both are
# asserted, so a repo reshuffle makes the test say so instead of
# quietly turning the "missing path" probe into a tautology.
_REAL_PATH = "tools/lint/decision_reversal_guard.py"
_FAKE_PATH = "apps/there/is/no/such/file.py"


def test_fixtures_are_what_they_claim() -> None:
    assert (_PROJECT_ROOT / _REAL_PATH).exists()
    assert not (_PROJECT_ROOT / _FAKE_PATH).exists()


# ---------------------------------------------------------------------------
# Calibration — against the live registry
# ---------------------------------------------------------------------------


def test_live_registry_still_has_its_cancelling_sections() -> None:
    """If this drops to zero the guard is blind, not the registry clean."""
    cancelling, _, _ = _scan(_REGISTRY.read_text(encoding="utf-8"))
    assert len(cancelling) >= _MEASURED_CANCELLING, (
        f"найдено {len(cancelling)}, замер 11.09.2026 давал {_MEASURED_CANCELLING}. "
        "Разметка отмен в реестре сменилась — чините _MARKER."
    )


def test_live_registry_is_green_under_the_shipped_amnesty() -> None:
    cancelling, violations, stale = _scan(
        _REGISTRY.read_text(encoding="utf-8"), guard.read_baseline()
    )
    assert stale == [], f"протухшая амнистия: {stale}"
    assert violations == [], "\n".join(v.render() for v in violations)
    assert len(cancelling) >= _MEASURED_CANCELLING


def test_shipped_amnesty_is_exactly_the_measured_sixteen() -> None:
    baseline = guard.read_baseline()
    assert len(baseline) == _MEASURED_CANCELLING


# ---------------------------------------------------------------------------
# Mechanics — rule 1: the block must be there and non-empty
# ---------------------------------------------------------------------------

_FORGED_NO_BLOCK = """\
# РЕЕСТР

## §900. ОТМЕНЕН §899: сужение объёма больше не действует

Удаляется весь набор, включая пол.
"""


def test_cancelling_section_without_a_block_is_red() -> None:
    cancelling, violations, _ = _scan(_FORGED_NO_BLOCK)
    assert len(cancelling) == 1, f"совпадений: {len(cancelling)}"
    assert len(violations) == 1, f"нарушений: {len(violations)}"
    assert violations[0].kind == "БЛОКА НЕТ"


def test_same_section_goes_green_once_paths_are_added() -> None:
    fixed = _FORGED_NO_BLOCK + f"\n**Затронутый код:**\n\n- `{_REAL_PATH}`\n"
    cancelling, violations, _ = _scan(fixed)
    assert len(cancelling) == 1, f"совпадений: {len(cancelling)}"
    assert violations == [], "\n".join(v.render() for v in violations)


def test_block_marker_with_no_paths_at_all_is_red() -> None:
    empty = _FORGED_NO_BLOCK + "\n**Затронутый код:**\n\n---\n"
    cancelling, violations, _ = _scan(empty)
    assert len(cancelling) == 1, f"совпадений: {len(cancelling)}"
    assert len(violations) == 1, f"нарушений: {len(violations)}"
    assert violations[0].kind == "БЛОК ПУСТ"


@pytest.mark.parametrize(
    "block",
    [
        f"**Затронутый код:** `{_REAL_PATH}`",
        f"Затронутый код: `{_REAL_PATH}`",
        f"**Затронутый код:**\n\n* `{_REAL_PATH}`",
        f"**Затронутый код:**\n\n1. `{_REAL_PATH}`",
        f"**Затронутый код:**\n\n- `{_REAL_PATH}:41-55`",
    ],
)
def test_accepted_spellings_of_the_block(block: str) -> None:
    cancelling, violations, _ = _scan(_FORGED_NO_BLOCK + "\n" + block + "\n")
    assert len(cancelling) == 1, f"совпадений: {len(cancelling)}"
    assert violations == [], "\n".join(v.render() for v in violations)


# ---------------------------------------------------------------------------
# Mechanics — rule 2: every path must exist
# ---------------------------------------------------------------------------


def test_path_that_does_not_exist_is_red() -> None:
    bad = _FORGED_NO_BLOCK + f"\n**Затронутый код:**\n\n- `{_FAKE_PATH}`\n"
    cancelling, violations, _ = _scan(bad)
    assert len(cancelling) == 1, f"совпадений: {len(cancelling)}"
    assert len(violations) == 1, f"нарушений: {len(violations)}"
    assert violations[0].kind == "ПУТИ НЕТ В РЕПОЗИТОРИИ"
    assert violations[0].paths == [_FAKE_PATH]


def test_one_missing_path_among_real_ones_is_still_red() -> None:
    mixed = _FORGED_NO_BLOCK + f"\n**Затронутый код:**\n\n- `{_REAL_PATH}`\n- `{_FAKE_PATH}`\n"
    _, violations, _ = _scan(mixed)
    assert len(violations) == 1, f"нарушений: {len(violations)}"
    assert violations[0].paths == [_FAKE_PATH]


def test_block_full_of_prose_instead_of_paths_is_red() -> None:
    prose = _FORGED_NO_BLOCK + "\n**Затронутый код:** `тот, что про пол`\n"
    _, violations, _ = _scan(prose)
    assert len(violations) == 1, f"нарушений: {len(violations)}"
    assert violations[0].kind == "НЕ ПУТЬ"


# ---------------------------------------------------------------------------
# Mechanics — "not applicable" must be distinguishable from emptiness
# ---------------------------------------------------------------------------


def test_not_applicable_with_a_reason_is_green() -> None:
    na = _FORGED_NO_BLOCK + "\n**Затронутый код:** НЕ ПРИМЕНИМО — решение о порядке работы окон.\n"
    cancelling, violations, _ = _scan(na)
    assert len(cancelling) == 1, f"совпадений: {len(cancelling)}"
    assert violations == [], "\n".join(v.render() for v in violations)


def test_not_applicable_without_a_reason_is_red() -> None:
    bare = _FORGED_NO_BLOCK + "\n**Затронутый код:** НЕ ПРИМЕНИМО\n"
    _, violations, _ = _scan(bare)
    assert len(violations) == 1, f"нарушений: {len(violations)}"
    assert violations[0].kind == "НЕ ПРИМЕНИМО БЕЗ ПРИЧИНЫ"


# ---------------------------------------------------------------------------
# Mechanics — rule 3: zero cancelling sections is a FAILURE
# ---------------------------------------------------------------------------


def test_zero_cancelling_sections_exits_non_zero(tmp_path: Path, capsys) -> None:
    """«Ничего не нарушено» и «ничего не осмотрено» пишутся одинаково."""
    quiet = tmp_path / "OPEN_DECISIONS.md"
    quiet.write_text("# РЕЕСТР\n\n## §900. Решение без отмен\n\nТекст.\n", encoding="utf-8")
    cancelling, violations, _ = _scan(quiet.read_text(encoding="utf-8"))
    assert len(cancelling) == 0, f"совпадений: {len(cancelling)}"
    assert violations == []  # нечего нарушать — и это ровно ловушка

    rc = guard.main(["decision_reversal_guard.py", str(quiet)])
    out = capsys.readouterr()
    assert rc == 1, "ноль секций с отменой обязан ронять прогон"
    assert "НОЛЬ секций с отменой" in out.err
    assert "найдено: 0" in out.out


def test_the_count_is_printed_on_a_green_run(capsys) -> None:
    rc = guard.main(["decision_reversal_guard.py", str(_REGISTRY)])
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert f"секций с отменой найдено: {_MEASURED_CANCELLING}" in out.out


# ---------------------------------------------------------------------------
# Mechanics — the amnesty can only shrink
# ---------------------------------------------------------------------------


def test_amnesty_entry_that_no_longer_resolves_is_red() -> None:
    _, violations, stale = _scan(_FORGED_NO_BLOCK, {"§900", "§777"})
    assert stale == ["§777"], f"протухших: {len(stale)}"
    assert violations == []  # §900 амнистирован, §777 — уже нет секции


def test_amnesty_cannot_be_used_to_cover_a_new_section() -> None:
    two = _FORGED_NO_BLOCK + "\n## §901. ОТМЕНЕН §900: снова наоборот\n\nТекст.\n"
    cancelling, violations, _ = _scan(two, {"§900"})
    assert len(cancelling) == 2, f"совпадений: {len(cancelling)}"
    assert len(violations) == 1, f"нарушений: {len(violations)}"
    assert violations[0].section.ordinal == "§901"


# ---------------------------------------------------------------------------
# Silence — the registry's booking vocabulary must not fire
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "heading",
    [
        "## 6. Право Ayla ставить мастеру отгул, отменяющий записанных клиентов",
        "## 34. Системная отмена — 100% возврат. РЕШЕНО владельцем 25.08",
        "## §42. Замер завершения визитов на пилоте",
        "### Что снимается с исполнителей прямо сейчас",
        "### Ловушка, снятая парной стражей",
    ],
)
def test_booking_vocabulary_in_a_heading_is_not_a_cancellation(heading: str) -> None:
    doc = f"# РЕЕСТР\n\n{heading}\n\nТекст без блока путей.\n"
    if heading.startswith("###"):
        doc = f"# РЕЕСТР\n\n## §900. Обычная секция\n\n{heading}\n\nТекст.\n"
    cancelling, violations, _ = _scan(doc)
    assert len(cancelling) == 0, f"совпадений: {len(cancelling)} — ложная тревога"
    assert violations == []


def test_cancellation_words_in_the_body_are_not_a_marker() -> None:
    """Проза — названный предел сторожа, а не его предмет."""
    doc = (
        "# РЕЕСТР\n\n## §900. ОТВЕЧЕН: пол входит в удаление\n\n"
        "Это снимает моё собственное сужение объёма до трёх полей, "
        "и прежнее решение отменяется.\n\n"
        "Запись отменена. Состояния: ИСПОЛНЕНО · ОТМЕНЕНО.\n"
    )
    cancelling, violations, _ = _scan(doc)
    assert len(cancelling) == 0, f"совпадений: {len(cancelling)}"
    assert violations == []


# ---------------------------------------------------------------------------
# The named limit — held in place on purpose
# ---------------------------------------------------------------------------


def test_an_incomplete_list_of_paths_passes_green() -> None:
    """Сторож проверяет НАЛИЧИЕ списка, а не его полноту.

    Отмена, которая на деле достаёт до девяти файлов, проходит с одним
    названным. Это записано в докстринге сторожа, и тест держит границу
    на месте, чтобы следующий читатель не принял зелёный прогон за
    доказательство закрытой границы.
    """
    one_of_many = _FORGED_NO_BLOCK + f"\n**Затронутый код:**\n\n- `{_REAL_PATH}`\n"
    _, violations, _ = _scan(one_of_many)
    assert violations == [], "неполный список обязан проходить — иначе сторож врёт о себе"

    irrelevant = _FORGED_NO_BLOCK + "\n**Затронутый код:**\n\n- `docs/adr`\n"
    _, violations, _ = _scan(irrelevant)
    assert violations == [], "связь пути с отменой сторож не проверяет и не притворяется"


def test_the_limit_is_written_in_the_guards_own_docstring() -> None:
    doc = guard.__doc__ or ""
    assert "presence check, not a completeness check" in doc
    assert "Prose cancellations are invisible" in doc
    assert "grandfathered" in doc
