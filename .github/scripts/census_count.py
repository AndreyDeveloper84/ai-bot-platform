"""Read the number of tests `pytest --collect-only` collected (DRF-1349).

WHY THIS IS A FILE AND NOT A HEREDOC IN ci.yml.

It used to be inline in the `apps/ collection census` step. Inline code is
code nothing can run a test against, and this parser is a guard: if it
misreads, the shard-sum check downstream either blocks a good run or, worse,
waves through a run that collected nothing. So it lives here, and
`tests/tools/test_census_parser.py` feeds it real captured pytest output.

THE TWO FORMS, AND WHY BOTH ARE NEEDED.

`--collect-only` output depends on the QUIET LEVEL, which is cumulative:

  quiet 1 (`-q`)   node ids, then `13177/13203 tests collected (26 deselected)`
  quiet 2 (`-qq`)  per-file counts (`apps/x/tests/test_y.py: 30`), NO total

The census step asked for `-q` on a project whose `pyproject.toml` addopts
already carries `-q` (there since the Sprint 0 scaffold, b1e8e7bd). The two
added up to quiet level 2, the total line vanished, and the guard reported
`CENSUS UNREADABLE` — correctly refusing, but for a reason nobody could read.

The step no longer adds its own `-q`, so form 1 is what CI actually gets.
Form 2 is still parsed, because the failure above was caused by a verbosity
level nobody set on purpose, and that can happen again from the other side —
someone changing addopts. Both forms yield the SAME number: per-file counts
are post-deselect, measured against the same suite as the total line.

WHY QUIET LEVEL 1 IS REQUIRED, NOT MERELY TIDIER.

`CENSUS EMPTY` must stay a distinct outcome from `CENSUS UNREADABLE` — an
empty census is the exact silent-green disaster the census exists to catch.
At quiet 2, a run that collects nothing prints NOTHING AT ALL and exits 0, so
zero and unparseable are the same observable and the distinction is lost. At
quiet 1 pytest prints `no tests collected`, which this parser reads as an
explicit 0. That is why the step's verbosity is pinned rather than left to
add up.
"""

from __future__ import annotations

import re
import sys

# `13177 tests collected in 41.20s` / `13177/13203 tests collected (26 deselected)`
_TOTAL = re.compile(r"(\d+)(?:/\d+)? tests? collected")
# `no tests collected in 0.01s` — an EXPLICIT zero, not an unreadable report.
_NO_TESTS = re.compile(r"\bno tests collected\b")
# Quiet-level-2 per-file counts: `apps/booking/tests/test_reschedule.py: 30`.
# The key must end in `.py`: a bare `\S+` also matches lines like
# `SomeWarning: 12`, and a census that silently counts warnings is worse
# than one that refuses.
_PER_FILE = re.compile(r"^(\S+\.py): (\d+)$", re.MULTILINE)


class CensusUnreadable(Exception):
    """Neither form parsed. Never treat this as zero — it is 'unknown'."""


def parse_census(text: str) -> int:
    """Tests collected, read from either quiet level.

    Raises CensusUnreadable when no form parsed. Returns 0 only when pytest
    explicitly said so; the caller decides that 0 is fatal.
    """
    # Normalise line endings first. `_PER_FILE` is anchored with `$`, which
    # in MULTILINE matches before a newline but NOT before the CR of a CRLF
    # pair, so a CRLF report would parse as "unreadable" — a fail-closed
    # answer that points at pytest instead of at the line endings. main()
    # reads in text mode and never sees CRLF, but parse_census is also
    # called directly.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    total = _TOTAL.findall(text)
    if total:
        return int(total[-1])

    if _NO_TESTS.search(text):
        return 0

    per_file = _PER_FILE.findall(text)
    if per_file:
        return sum(int(count) for _, count in per_file)

    raise CensusUnreadable(
        "no 'tests collected' line and no per-file collection counts in pytest output"
    )


def main(argv: list[str]) -> int:
    raw_path, out_path = argv[1], argv[2]
    text = open(raw_path, encoding="utf-8", errors="replace").read()

    try:
        n = parse_census(text)
    except CensusUnreadable as exc:
        print(f"CENSUS UNREADABLE — {exc}.")
        return 1

    print(f"CENSUS apps/ collected={n}")
    if n == 0:
        print("CENSUS EMPTY — 0 tests collected from apps/. Not a green run.")
        return 1

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(str(n))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
