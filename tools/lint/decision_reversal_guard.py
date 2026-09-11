#!/usr/bin/env python3
"""Fail when a section of ``docs/OPEN_DECISIONS.md`` cancels an earlier
ruling and does not say which code the cancellation touches.

# The defect

DRF-1656. §120 (10.09) told us to delete three body parameters. The task
was written exactly to it and deliberately **kept** ``gender`` — with a
stated reason. §144 (11.09) cancelled that narrowing: the whole survey
set is deleted, ``gender`` included. By then the task was merged.

Nothing went red, and nothing could have:

    the code did not change   -> no diff, so no review
    the tests did not change  -> no failing assertion
    the decision changed      -> no event CI reacts to

What caught the drift was a **measurement**, not a check: a report put
``под удаление 4`` next to ``с полом 6`` and the two numbers disagreed.

A cancelled decision is a silent, dateless defect in already-merged
code. The only moment at which anyone still knows which code it breaks
is the moment the cancellation is written down.

# The rule

    A section that cancels an earlier decision must name the code the
    cancellation reaches — or say, explicitly, that it reaches none.

So the section must carry a block::

    **Затронутый код:**

    - `apps/nutrition/services/survey.py`
    - `apps/api/v1/serializers/profile.py`

and a purely procedural ruling must say so in words rather than by
saying nothing::

    **Затронутый код:** НЕ ПРИМЕНИМО — решение о порядке работы окон,
    кода не касается.

Emptiness and "not applicable" are different states and must look
different. Silence is the state this guard refuses: it is the one that
reads as "checked, nothing found" while meaning "nobody looked".

# The third outcome: zero sections found

If the marker regex ever stops matching — the registry renames its
verdicts, someone reformats the headings — then "no violations" and
"nothing was inspected" render identically, and the guard turns into a
green light for exactly the thing it was built to stop. So the count of
cancelling sections is **printed on every run**, and a run that finds
zero of them **fails**. See :func:`main`.

# What this guard does NOT do — the named limit

This is a presence check, not a completeness check.

    **It verifies that a list of paths exists. It cannot verify that
    the list is right.**

A section that cancels a ruling touching nine files and lists one of
them passes. A section that lists nine files, none of which have
anything to do with the ruling, passes. Every path is checked for
existence and for nothing else. Do not read a green run as "the code
reached by this cancellation is known" — read it as "somebody was made
to write an answer down". Whether the answer is true is still a human
job, and no line of this file does it.

Two more limits, named because an unnamed one reads as a closed border:

* **Prose cancellations are invisible.** The marker is looked for in
  **heading lines only** (``#`` … ``######``). §144 — the section that
  exposed the whole defect — cancels §120 in a body sentence («Это
  снимает моё собственное сужение объёма до трёх полей») and this guard
  does not see it. Headings are where the registry states verdicts, and
  the body is where it uses the same words about bookings («клиент
  отменяет запись», «одна отменена», state name ``ОТМЕНЕНО``); a body
  scan measured 22 sections, most of them that noise. The limit is
  real and is the price of not crying wolf: **write the cancellation
  into a heading, or this guard is not watching it.**

* **The pre-existing sections are grandfathered, not judged.** 16
  cancelling sections predate the rule and are listed by ordinal in
  :data:`_BASELINE`. They are exempt; nobody has checked what code they
  touch. The list can only shrink — an entry that no longer resolves to
  a cancelling section fails the run, so the file cannot rot into a
  blanket amnesty.

Usage::

    python tools/lint/decision_reversal_guard.py [<path to md>]
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_TARGET = _PROJECT_ROOT / "docs" / "OPEN_DECISIONS.md"
_BASELINE_FILE = Path(__file__).resolve().parent / "decision_reversal_baseline.txt"

# --------------------------------------------------------------------------
# Markers
# --------------------------------------------------------------------------
#
# Measured against docs/OPEN_DECISIONS.md at dev@d5e3c75e (11.09.2026),
# 204 sections. The forms the registry actually uses to cancel a ruling,
# and where they sit:
#
#   ОТМЕНЕН / ОТМЕНЁН / ОТМЕНЕНО / ОТМЕНА  — 5 section titles
#   отменяет / отменяется / отменено       — 11 sub-headings («### Что
#                                            это отменяет»)
#   СНЯТ / снимается / снят                — 33 hits, and 30 of them are
#                                            not cancellations at all
#                                            («### Ловушка, снятая
#                                            парной стражей», «### Что
#                                            снимается» about menu
#                                            items). NOT a marker.
#
# Two shapes are deliberately excluded because the registry uses the
# same root for booking cancellation, which is a domain word and not a
# verdict:
#
#   отменяющ- (participle)  «## 6. … отгул, отменяющий записанных клиентов»
#   отмена (lowercase)      «## 34. Системная отмена — 100% возврат»
#
# Uppercase ОТМЕНА is kept: it is the registry's own verdict voice
# («## 21-ter. ОТМЕНА 21-bis»).
_MARKER = re.compile(
    r"ОТМЕН[ЕЁ]Н(?:О|Ы|А)?\b"  # ОТМЕНЕН/ОТМЕНЁН/ОТМЕНЕНО/ОТМЕНЕНЫ/ОТМЕНЕНА
    r"|\bОТМЕНА\b"
    r"|\bОТМЕНЯЕТ(?:СЯ)?\b"
    r"|\bотмен(?:яет|яется|яют|ено|ены|[её]н)\b"
)

_HEADING = re.compile(r"^(#{1,6})\s+(\S.*)$")
# A section is a top-level registry entry: `#` or `##`. Deeper headings
# are parts of a section, and that is exactly what makes «### Что это
# отменяет» a marker *of its parent section*.
_SECTION_LEVEL = 2

# --------------------------------------------------------------------------
# The required block
# --------------------------------------------------------------------------
_BLOCK_MARKER = re.compile(r"^\s*\**\s*Затронутый\s+код\s*:?\**\s*:?\s*(.*)$", re.IGNORECASE)
_NOT_APPLICABLE = re.compile(r"^\**\s*НЕ\s+ПРИМЕНИМО\**\s*(?:[—–-]\s*(?P<reason>.+))?$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_BACKTICKED = re.compile(r"`([^`\n]+)`")
# A backticked token is treated as a path when it looks like one: at
# least one `/`, and nothing but path characters. `:12` / `:12-40` line
# suffixes are stripped — the registry writes them when pointing at a
# specific place.
_PATHISH = re.compile(r"^[\w./@+-]+(?::\d+(?:-\d+)?)?$")

_BLOCK_SYNTAX = (
    "**Затронутый код:**\n"
    "\n"
    "- `apps/<...>.py`\n"
    "- `tests/<...>.py`\n"
    "\n"
    "или, если решение кода не касается:\n"
    "\n"
    "**Затронутый код:** НЕ ПРИМЕНИМО — <почему>"
)


@dataclass
class Section:
    """One registry entry: its heading line and everything under it."""

    line_no: int
    heading: str
    body: list[str]
    body_start: int

    @property
    def ordinal(self) -> str:
        """`§56`, `21-bis`, `19` — the stable half of a heading.

        Headings get reworded; the number does not. Falls back to the
        whole normalised heading when a section has no number.
        """
        text = _HEADING.match(self.heading).group(2)  # type: ignore[union-attr]
        m = re.match(r"(§?\s*[\dIVX]+(?:[-–][\w]+)*(?:\.\d+)*)\s*[.．]?", text)
        if m:
            return m.group(1).replace(" ", "")
        return re.sub(r"\s+", " ", text.strip("# ~*").strip())[:60]

    def heading_lines(self) -> list[str]:
        return [self.heading] + [line for line in self.body if _HEADING.match(line)]

    def marker_hits(self) -> list[str]:
        """Heading lines in this section that state a cancellation."""
        return [h for h in self.heading_lines() if _MARKER.search(h)]


@dataclass
class Violation:
    section: Section
    kind: str
    detail: str
    paths: list[str] = field(default_factory=list)

    def render(self) -> str:
        head = re.sub(r"\s+", " ", self.section.heading).strip()
        return f"  {self.section.line_no:>5}: {head[:100]}\n         {self.kind}: {self.detail}"


def parse_sections(lines: list[str]) -> list[Section]:
    starts = [
        i
        for i, line in enumerate(lines)
        if (m := _HEADING.match(line)) and len(m.group(1)) <= _SECTION_LEVEL
    ]
    out: list[Section] = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        out.append(
            Section(
                line_no=start + 1,
                heading=lines[start],
                body=lines[start + 1 : end],
                body_start=start + 2,
            )
        )
    return out


def _collect_block(section: Section) -> tuple[list[str], str | None] | None:
    """Find the `Затронутый код:` block. Returns (paths, na_reason).

    ``None`` when the section carries no such block at all.
    ``na_reason`` is the text after ``НЕ ПРИМЕНИМО``; ``""`` when the
    token is there but the reason is missing.
    """
    for idx, line in enumerate(section.body):
        m = _BLOCK_MARKER.match(line)
        if not m:
            continue
        tail = m.group(1).strip()

        na = _NOT_APPLICABLE.match(tail)
        if na:
            return [], (na.group("reason") or "").strip()

        # Paths may sit on the marker line itself, and/or in the list
        # that follows it (one optional blank line in between).
        raw = list(_BACKTICKED.findall(tail))
        rest = section.body[idx + 1 :]
        if rest and not rest[0].strip():
            rest = rest[1:]
        for nxt in rest:
            if not nxt.strip() or _HEADING.match(nxt):
                break
            if not _LIST_ITEM.match(nxt) and not raw:
                # Prose directly under a bare marker: still scanned, so
                # `**Затронутый код:** `a.py`, `b.py`` on the next line
                # works. Stops at the first line that yields nothing.
                found = _BACKTICKED.findall(nxt)
                if not found:
                    break
                raw.extend(found)
                continue
            if not _LIST_ITEM.match(nxt):
                break
            raw.extend(_BACKTICKED.findall(nxt))
        return [t.strip() for t in raw if t.strip()], None
    return None


def _is_pathish(token: str) -> bool:
    return "/" in token and bool(_PATHISH.match(token))


def read_baseline() -> set[str]:
    if not _BASELINE_FILE.exists():
        return set()
    return {
        line.strip()
        for line in _BASELINE_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def scan(
    text: str, repo_root: Path, baseline: set[str] | None = None
) -> tuple[list[Section], list[Violation], list[str]]:
    """Returns (cancelling sections, violations, stale baseline entries)."""
    baseline = set() if baseline is None else baseline
    lines = text.split("\n")
    sections = parse_sections(lines)
    cancelling = [s for s in sections if s.marker_hits()]

    ordinals = [s.ordinal for s in cancelling]
    stale = sorted(e for e in baseline if ordinals.count(e) != 1)

    violations: list[Violation] = []
    for section in cancelling:
        if section.ordinal in baseline and section.ordinal not in stale:
            continue
        block = _collect_block(section)
        if block is None:
            violations.append(
                Violation(section, "БЛОКА НЕТ", "секция отменяет решение и молчит о коде")
            )
            continue
        paths, na_reason = block
        if na_reason is not None:
            if not na_reason:
                violations.append(
                    Violation(
                        section,
                        "НЕ ПРИМЕНИМО БЕЗ ПРИЧИНЫ",
                        "после «НЕ ПРИМЕНИМО» обязано стоять « — <почему>»",
                    )
                )
            continue
        if not paths:
            violations.append(
                Violation(
                    section,
                    "БЛОК ПУСТ",
                    "ни одного пути; если кода нет — напишите «НЕ ПРИМЕНИМО — <почему>»",
                )
            )
            continue
        not_paths = [p for p in paths if not _is_pathish(p)]
        if not_paths:
            violations.append(
                Violation(
                    section,
                    "НЕ ПУТЬ",
                    "в блоке нет ни одного пути; на путь не похоже: "
                    + ", ".join(repr(p) for p in not_paths[:5]),
                )
                if len(not_paths) == len(paths)
                else Violation(
                    section,
                    "МУСОР В БЛОКЕ",
                    "не путь: " + ", ".join(repr(p) for p in not_paths[:5]),
                )
            )
            continue
        missing = [p for p in paths if not (repo_root / p.split(":")[0]).exists()]
        if missing:
            violations.append(
                Violation(
                    section,
                    "ПУТИ НЕТ В РЕПОЗИТОРИИ",
                    ", ".join(missing[:5]),
                    paths=missing,
                )
            )
    return cancelling, violations, stale


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else _DEFAULT_TARGET
    if not target.is_file():
        print(f"decision_reversal_guard: нет файла {target}", file=sys.stderr)
        return 2
    repo_root = _PROJECT_ROOT

    baseline = read_baseline()
    cancelling, violations, stale = scan(
        target.read_text(encoding="utf-8"), repo_root, baseline
    )

    # Printed on every run, green or red. A count is the only thing that
    # tells «nothing violated» apart from «nothing inspected».
    print(
        f"decision_reversal_guard: {target.name} — секций с отменой найдено: "
        f"{len(cancelling)} (из них по амнистии: "
        f"{sum(1 for s in cancelling if s.ordinal in baseline)})"
    )
    for section in cancelling:
        mark = "amnesty" if section.ordinal in baseline else "checked"
        hit = re.sub(r"\s+", " ", section.marker_hits()[0]).strip()
        print(f"    [{mark}] {section.ordinal:>10}  L{section.line_no:<6} {hit[:88]}")

    # THE THIRD OUTCOME. Zero matches is not silence, it is blindness:
    # the markers were measured against this very file and cannot all
    # vanish while the registry still cancels decisions.
    if not cancelling:
        print(
            "decision_reversal_guard: НОЛЬ секций с отменой.\n"
            "Это не «всё чисто» — это «сторож ничего не осмотрел». Либо разметка "
            "отмен в реестре сменилась, либо файл не тот. Почините регулярку "
            "_MARKER или путь, но не принимайте этот прогон за зелёный.",
            file=sys.stderr,
        )
        return 1

    if stale:
        print(
            "decision_reversal_guard: протухшая амнистия — "
            + ", ".join(stale)
            + "\nЭти номера в decision_reversal_baseline.txt больше не резолвятся "
            "ровно в одну секцию с отменой. Список амнистии может только "
            "сокращаться: уберите строку осознанно.",
            file=sys.stderr,
        )

    if violations:
        print("\nНАРУШЕНИЯ:", file=sys.stderr)
        for v in violations:
            print(v.render(), file=sys.stderr)
        print(
            f"\ndecision_reversal_guard: {len(violations)} секц(ия/ии) отменяют прежнее "
            "решение, не назвав затронутый код.\n"
            "Отмена решения не даёт диффа и не роняет тестов — единственный момент, "
            "когда ещё известно, какой код она ломает, это момент записи отмены.\n\n"
            + _BLOCK_SYNTAX,
            file=sys.stderr,
        )

    if violations or stale:
        return 1

    print(
        "decision_reversal_guard: чисто. ВНИМАНИЕ: проверено НАЛИЧИЕ списка путей, "
        "а не его полнота — неполный список проходит зелёным."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
