"""The collection census must read the output pytest actually prints (DRF-1349).

WHAT BROKE. The `lint + guards + cross-cutting tests + mypy` job died with:

    CENSUS UNREADABLE — no 'tests collected' line in pytest output.

The guard was right — there was no such line. It just did not understand why.
The tail of `census-raw.txt` was:

    apps/workers/tests/test_run_strict_flip_drill.py: 30
    apps/workers/tests/test_subscriber_audit.py: 19

That is `--collect-only` at QUIET LEVEL 2: per-file counts and no total.
Quiet level is cumulative, and the step asked for `-q` on a project whose
`pyproject.toml` addopts already carries one. Nobody chose `-qq`; it was
arithmetic.

WHAT THIS FILE ASSERTS. Every sample under `census_samples/` is real captured
output from pytest 9.0.3 — the version in `uv.lock`, i.e. the one CI runs —
not hand-typed text that only resembles it. The load-bearing assertion is the
CROSS-CHECK: quiet 1 and quiet 2 of the SAME collection must yield the SAME
number. If they ever disagree, summing per-file counts is not a valid
substitute for the total line and this guard must fail rather than pick one.

The parser under test is the file CI executes, imported by path. A copy of
the parser would prove nothing about the parser.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PARSER_PATH = REPO / ".github" / "scripts" / "census_count.py"
SAMPLES = Path(__file__).parent / "census_samples"


def _load_parser():
    """Import the exact file the workflow runs."""
    assert PARSER_PATH.is_file(), (
        f"{PARSER_PATH} is missing — the census step in .github/workflows/ci.yml "
        "invokes it by path, so losing it breaks CI, not just this test."
    )
    spec = importlib.util.spec_from_file_location("census_count", PARSER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["census_count"] = module
    spec.loader.exec_module(module)
    return module


census = _load_parser()


def _sample(name: str) -> str:
    path = SAMPLES / name
    assert path.is_file(), f"missing captured sample {path}"
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Form 1 — quiet level 1, the total line.
# --------------------------------------------------------------------------


def test_quiet1_reads_the_total_line() -> None:
    assert census.parse_census(_sample("quiet1_plain.txt")) == 7


def test_quiet1_reads_the_post_deselect_total() -> None:
    """`5/7 tests collected (2 deselected)` means 5 ran, not 7.

    The shard sum downstream is also post-deselect, so the census must be.
    """
    assert census.parse_census(_sample("quiet1_with_deselect.txt")) == 5


# --------------------------------------------------------------------------
# Form 2 — quiet level 2, per-file counts. The form that broke the run.
# --------------------------------------------------------------------------


def test_quiet2_sums_per_file_counts() -> None:
    assert census.parse_census(_sample("quiet2_plain.txt")) == 7


def test_quiet2_per_file_counts_are_post_deselect() -> None:
    assert census.parse_census(_sample("quiet2_with_deselect.txt")) == 5


def test_the_exact_tail_that_failed_the_run_now_parses() -> None:
    """Verbatim from `census-raw.txt` in run 34557386291."""
    tail = (
        "apps/workers/tests/test_run_strict_flip_drill.py: 30\n"
        "apps/workers/tests/test_subscriber_audit.py: 19\n"
    )
    assert census.parse_census(tail) == 49


# --------------------------------------------------------------------------
# The cross-check: both forms, same collection, same number.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("quiet1", "quiet2"),
    [
        ("quiet1_plain.txt", "quiet2_plain.txt"),
        ("quiet1_with_deselect.txt", "quiet2_with_deselect.txt"),
    ],
)
def test_both_forms_agree_on_the_same_collection(quiet1: str, quiet2: str) -> None:
    """Summing per-file counts is only a valid substitute while this holds."""
    assert census.parse_census(_sample(quiet1)) == census.parse_census(_sample(quiet2))


# --------------------------------------------------------------------------
# Zero stays a separate outcome from unreadable.
# --------------------------------------------------------------------------


def test_quiet1_empty_collection_is_zero_not_unreadable() -> None:
    """`no tests collected` is pytest stating zero, not failing to state."""
    assert census.parse_census(_sample("quiet1_empty.txt")) == 0


def test_zero_is_refused_by_main_as_census_empty(tmp_path, capsys) -> None:
    raw = tmp_path / "raw.txt"
    raw.write_text(_sample("quiet1_empty.txt"), encoding="utf-8")
    out = tmp_path / "apps-census.txt"

    assert census.main(["census_count.py", str(raw), str(out)]) == 1
    assert "CENSUS EMPTY" in capsys.readouterr().out
    assert not out.exists(), "an empty census must not publish a number downstream"


def test_quiet2_cannot_express_zero_and_so_refuses(tmp_path, capsys) -> None:
    """A known, named limitation — not an oversight.

    At quiet level 2 a collection of nothing prints nothing and exits 0, so
    zero and garbage are the same observable. The parser therefore refuses
    rather than inventing a 0. This is why the census step pins quiet level 1
    instead of letting `-q` flags add up.
    """
    with pytest.raises(census.CensusUnreadable):
        census.parse_census(_sample("quiet2_empty.txt"))

    raw = tmp_path / "raw.txt"
    raw.write_text(_sample("quiet2_empty.txt"), encoding="utf-8")
    out = tmp_path / "apps-census.txt"
    assert census.main(["census_count.py", str(raw), str(out)]) == 1
    assert "CENSUS UNREADABLE" in capsys.readouterr().out
    assert not out.exists()


# --------------------------------------------------------------------------
# Garbage still refuses. The point of the guard.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "junk",
    [
        pytest.param("", id="empty"),
        pytest.param("ERROR: could not import config.settings\n", id="import-error"),
        pytest.param("Traceback (most recent call last):\n  File x\n", id="traceback"),
        pytest.param("collected some tests, probably\n", id="prose"),
        pytest.param("INTERNALERROR> KeyboardInterrupt\n", id="internal-error"),
    ],
)
def test_unparseable_output_is_refused(junk: str) -> None:
    with pytest.raises(census.CensusUnreadable):
        census.parse_census(junk)


def test_a_colon_number_line_that_is_not_a_test_file_is_not_counted() -> None:
    r"""A bare non-space key also matches warning summaries and timing lines.

    A census that silently counts those is worse than one that refuses, so
    the per-file key must end in `.py`.
    """
    with pytest.raises(census.CensusUnreadable):
        census.parse_census("DeprecationWarning: 12\nrootdir: 99\n")


def test_main_publishes_the_number_on_a_good_run(tmp_path, capsys) -> None:
    raw = tmp_path / "raw.txt"
    raw.write_text(_sample("quiet1_with_deselect.txt"), encoding="utf-8")
    out = tmp_path / "apps-census.txt"

    assert census.main(["census_count.py", str(raw), str(out)]) == 0
    assert "CENSUS apps/ collected=5" in capsys.readouterr().out
    assert out.read_text(encoding="utf-8") == "5"


def test_crlf_report_is_not_mistaken_for_unreadable() -> None:
    """A fail-closed answer for the wrong reason is still a wrong answer.

    `$` in MULTILINE matches before `\n` but not before the `\r` of a CRLF
    pair, so an un-normalised CRLF report would be reported as unreadable and
    the blame would land on pytest rather than on line endings.
    """
    crlf = "apps/a/test_x.py: 30\r\napps/b/test_y.py: 19\r\n"
    assert census.parse_census(crlf) == 49
    assert census.parse_census(crlf.replace("\r\n", "\n")) == 49
