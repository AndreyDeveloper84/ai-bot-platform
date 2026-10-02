#!/usr/bin/env python3
"""A reference to a document as a basis must lead somewhere (DRF-2657, DRF-2682).

Code in this repository justifies itself by pointing at documents: «решение
владельца R6 (docs/OWNER_QUESTIONS_2026-09-12.md, раздел T)», «§6 свода
11.09», «Raised for the owner in docs/REPORT_DRF1314.md». On 29.09 a census
found 50 such references pointing nowhere — one to a file that never existed
in any commit (`OWNER_QUESTIONS.md`), several to documents living outside git.
A reference into the void looks checkable and sends the next reader looking
for what is not there; worse, the owner's question it recorded disappears
with it. This guard makes a dead basis a red build.

# What it checks — five forms, two failures

Forms (the census that found them is in the PR that introduced this file):

1. **path** — ``docs/<...>.md`` written in code;
2. **family name** — a bare decision-document name (``OWNER_*``,
   ``CURRENT_DECISIONS*``, ``OPEN_DECISIONS*``, ``ayla-owner-decisions*``),
   read as ``docs/<name>``;
3. **path + section** — form 1 or 2 followed by ``, раздел X`` / ``§X``;
4. **«§N свода»** — the owner's summary cited by section only. In this
   repository «свод» is ``docs/OWNER_DECISIONS_2026-09-11.md``
   (:data:`SVOD_DOC`); the section must exist among its headings.
5. **bare name** (DRF-2682) — a document named without its directory:

   * ``NAME.md`` — must be the file name of some tracked ``.md``;
   * ``NAME §X`` / ``NAME.md §X`` — ``NAME`` is a document under ``docs/``
     (or a family name of form 2 without ``.md``), and the section must exist.

   On 02.10 a census found 270 such mentions next to the 513 references of
   forms 1–4 — a third of all citations were outside the guard. Every cited
   section resolved, but 18 ``NAME.md`` mentions led to no document in this
   repository.

Failures, reported apart because they are different defects:

* **missing document** — the path is not in the tree;
* **missing section** — the document exists, the cited section does not
  (a dead section in a live document; a name-only check passes it).

A path preceded by a repository name (``beautygo_backend/docs/X.md``) is a
reference to ANOTHER repository and is not checked here — that is the rule:
a cross-repository reference names its repository. A document ignored by git
(``git check-ignore``, e.g. ``docs/generated/``) is declared output written
at run time, not a basis, and is not checked either.

# What it deliberately does NOT check

* references with neither a path, a document name nor the word «свод» —
  «решение R6», «as the owner decided». Those are found only by content;
  green here does NOT mean the code has no dead bases.
* a bare name with neither ``.md`` nor a section (``see event-contract``):
  nothing tells it from an ordinary word (``Q1``, ``on-call``, ``README``,
  ``architecture`` are all document names here). And a bare ``NAME §X``
  whose document was DELETED: the name is recognised by the tree, so it
  leaves the guard's sight together with the file. Only ``NAME.md`` and the
  family names survive their document — write the ``.md``.
* a section cited as «п. 3» / «№1», or on the next line;
* a name that is the whole of a quoted string (``"note.md"``) — a value the
  code handles (a file it writes, a fixture), not a citation; and a name at
  the end of a path with spaces (``ayla-knowledge/07 UX/… Contract.md``) —
  the path names its own root, and this guard does not parse it.
* whether the cited document SAYS what the code claims — only that it and
  the section exist;
* documents in ``docs/`` citing other documents (prose, not code);
* other summaries: if a second «свод» appears, :data:`SVOD_DOC` must grow a
  way to tell them apart, or form 4 will check the wrong document.

# Exceptions

``doc_refs_allow.txt`` next to this file, the form of ``pii_guard_allow.txt``:
one prefix per line, reason after ``#``. Two kinds, told apart by the prefix:

* the prefix is a **referenced document** (``docs/X.md``) or a document
  section (``docs/X.md§S``) — a dead basis waiting for a decision. Its reason MUST name the register item
  (``пункт NN``) or say ``ждёт номера``: «remove after the owner decides»
  without an item has no addressee (29.09: a pii_guard exemption naming a
  question to the owner ran for 17 days while the question was never asked);
* the prefix is a **citing file or directory** — not a basis at all:
  frozen legacy, fixture strings inside guard tests, generated output.

The list may only SHRINK: :data:`ALLOW_CEILING` is the number of entries,
held by a node; lowering it is free, raising it shows in the diff.

# Positive proof

``tests/tools/test_doc_refs_guard.py`` runs the guard on a fixture tree in
both directions — a live reference and the guard is SILENT, a dead one and
it is RED — for every form and both failures. A guard proven only silent is
``assert True`` with a long name.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALLOW_FILE = HERE / "doc_refs_allow.txt"

#: Number of entries in doc_refs_allow.txt — the list may only shrink.
#: 16 → 20 with DRF-2682: the guard began to read bare names, and what it
#: found there had been dead all along — three documents living outside git
#: (register item 67) and one script that quotes the labels it rewrites.
ALLOW_CEILING = 20

#: The owner's summary that «§N свода» refers to (form 4).
SVOD_DOC = "docs/OWNER_DECISIONS_2026-09-11.md"

CODE_SUFFIXES = (".py", ".ts", ".tsx", ".yml", ".yaml", ".sh")

# (?<![\w/.-]) — a path preceded by «repo/» is another repository's.
_PATH = r"(?<![\w/.-])(docs/(?:[\w.-]+/)*[\w.-]+\.md)\b"
_FAMILY = r"(?<![\w/.-])((?:OWNER_[A-Z_]+|CURRENT_DECISIONS|OPEN_DECISIONS|ayla-owner-decisions)[\w.-]*\.md)\b"
_SECTION_AFTER = r"[`'\"]*,?\s*(?:раздел|§)\s*([A-ZА-Я0-9][\w.]*?)(?=[\s,;)`'\"]|\.(?!\w)|$)"
PATH_RE = re.compile(_PATH)
FAMILY_RE = re.compile(_FAMILY)
SECTION_AFTER_RE = re.compile(_SECTION_AFTER)
# Form 5. ``NAME.md`` with no directory in front of it …
BARE_MD_RE = re.compile(r"(?<![\w/.\\-])(\w[\w.-]*\.md)\b")
# … and the word standing right before a section mark: ``NAME §X``, ``NAME``, раздел X.
_WORD_BEFORE_SECTION_RE = re.compile(r"(?<![\w/.\\-])(\w[\w.-]*?)[`'\"]*,?\s*$")
_SECTION_MARK_RE = re.compile(r"(?:раздел|§)\s*[A-ZА-Я0-9]")
_FAMILY_BARE_RE = re.compile(
    r"(?:OWNER_[A-Z_]+|CURRENT_DECISIONS|OPEN_DECISIONS|ayla-owner-decisions)[\w.-]*"
)
_QUOTES = "\"'`"
# «§6 свода», «§7 свода владельца», «§2 п.6 свода», «свод владельца 11.09 §3»
SVOD_SECTION_RE = re.compile(
    r"§\s*(\d+(?:\.\d+)?)(?:\s*п\.\s*\d+)?\s+свод[а-яё]*"
    r"|свод[а-яё]*\s+владельца[^§\n]{0,40}?§\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Ref:
    where: str  # citing "file:line"
    doc: str  # referenced document path
    section: str | None
    form: str  # path | family | path+section | svod | name | name+section


def _is_a_value_or_a_spaced_path(line: str, start: int, end: int) -> bool:
    """``"note.md"`` is a value; ``repo/07 UX/A Contract.md`` is a path this guard cannot parse."""
    before, after = line[start - 1 : start], line[end : end + 1]
    if before and before in "\"'" and after == before:
        return True
    opened = max(line.rfind(q, 0, start) for q in _QUOTES)
    return "/" in line[opened + 1 : start]


def _bare_refs(where: str, line: str, taken: list[tuple[int, int]], stems: set[str]) -> list[Ref]:
    """Form 5: documents named without their directory (DRF-2682)."""
    refs: list[Ref] = []
    for m in BARE_MD_RE.finditer(line):
        if any(a <= m.start() < b for a, b in taken):
            continue  # forms 1–2 already read it
        if _is_a_value_or_a_spaced_path(line, m.start(), m.end()):
            continue
        taken.append(m.span())
        sec = SECTION_AFTER_RE.match(line, m.end())
        if sec:
            refs.append(Ref(where, m.group(1), sec.group(1).rstrip("."), "name+section"))
        else:
            refs.append(Ref(where, m.group(1), None, "name"))
    # ``NAME §X`` with no ``.md``: the word before the section mark must be a document.
    for mark in _SECTION_MARK_RE.finditer(line):
        word = _WORD_BEFORE_SECTION_RE.search(line, 0, mark.start())
        if not word or any(a <= word.start(1) < b for a, b in taken):
            continue
        name = word.group(1).rstrip(".-")
        sec = SECTION_AFTER_RE.match(line, word.end(1))
        if not sec or name.endswith(".md"):
            continue
        section = sec.group(1).rstrip(".")
        if _FAMILY_BARE_RE.fullmatch(name):
            # A family name outlives its document: a dead one is still seen.
            refs.append(Ref(where, f"docs/{name}.md", section, "name+section"))
        elif name in stems:
            refs.append(Ref(where, f"{name}.md", section, "name+section"))
    return refs


def iter_refs(rel: str, text: str, stems: set[str] | None = None) -> list[Ref]:
    """References in one file. ``stems`` — names of the documents under ``docs/``
    (without ``.md``); ``None`` switches form 5 off."""
    refs: list[Ref] = []
    for lineno, line in enumerate(text.split("\n"), 1):
        where = f"{rel}:{lineno}"
        taken: list[tuple[int, int]] = []
        for rx, form in ((PATH_RE, "path"), (FAMILY_RE, "family")):
            for m in rx.finditer(line):
                doc = m.group(1)
                taken.append(m.span())
                if form == "family":
                    if line[max(0, m.start() - 5) : m.start()].endswith("docs/"):
                        continue  # already counted as a path
                    doc = f"docs/{doc}"
                sec = SECTION_AFTER_RE.match(line, m.end())
                if sec:
                    refs.append(Ref(where, doc, sec.group(1).rstrip("."), "path+section"))
                else:
                    refs.append(Ref(where, doc, None, form))
        for m in SVOD_SECTION_RE.finditer(line):
            refs.append(Ref(where, SVOD_DOC, m.group(1) or m.group(2), "svod"))
        if stems is not None:
            refs.extend(_bare_refs(where, line, taken, stems))
    return refs


def section_exists(doc_text: str, section: str) -> bool:
    """The section token appears as a whole word in some heading line.

    Headings name sections in more than one way — ``## 6. OD-NUT-1``,
    ``### 5.1 Норма воды``, ``## T. Решения владельца``, ``### Screen M0 — …``
    — so the token is searched anywhere in the heading, bounded so that ``3``
    does not match ``13`` and ``5`` does not match ``5.1``.

    ``§3.1.4`` with no such heading is item 4 of a numbered list under the
    heading ``3.1`` — that item must exist in that section's body.
    """
    lines = doc_text.splitlines()
    heads = [i for i, line in enumerate(lines) if re.match(r"^#{1,6}\s", line)]

    def heading_index(tok: str) -> int | None:
        bounded = rf"(?<![\w.]){re.escape(tok)}(?![\w]|\.\d)"
        return next((i for i in heads if re.search(bounded, lines[i])), None)

    if heading_index(section) is not None:
        return True
    parent, _, item = section.rpartition(".")
    if not parent or not item.isdigit():
        return False
    start = heading_index(parent)
    if start is None:
        return False
    end = next((i for i in heads if i > start), len(lines))
    return any(re.match(rf"\s*{item}\.\s", line) for line in lines[start + 1 : end])


def load_allowlist(path: Path = ALLOW_FILE) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        prefix, _, reason = line.partition("#")
        entries.append((prefix.strip(), reason.strip()))
    return entries


#: A reason for a dead-basis exception must name its addressee.
ADDRESSEE_RE = re.compile(r"пункт\s+\d+|ждёт номера")


def is_document_entry(prefix: str) -> bool:
    """A dead-basis exception (document or document§section), not a citing file."""
    return prefix.startswith("docs/") and ("§" in prefix or prefix.endswith(".md"))


def is_allowed(ref: Ref, allow: list[tuple[str, str]]) -> bool:
    citing = ref.where.rsplit(":", 1)[0]
    for prefix, _ in allow:
        if "§" in prefix:
            doc, _, section = prefix.partition("§")
            if ref.doc == doc and ref.section == section:
                return True
        elif ref.doc == prefix or citing == prefix:
            return True
        elif prefix.endswith("/") and citing.startswith(prefix):
            return True
    return False


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout
    return [line for line in out.split("\n") if line]


def ignored_docs(root: Path, docs: set[str]) -> set[str]:
    if not docs:
        return set()
    # NUL-separated bytes: text mode on Windows turns "\n" into "\r\n", git then
    # reads paths ending in "\r" and matches none of them.
    out = subprocess.run(
        ["git", "check-ignore", "--stdin", "-z"],
        cwd=root,
        input="\0".join(sorted(docs)).encode("utf-8"),
        capture_output=True,
        check=False,
    ).stdout
    return {p for p in out.decode("utf-8").split("\0") if p}


def check(
    root: Path,
    files: list[str],
    allow: list[tuple[str, str]],
    ignored: set[str] | None = None,
) -> tuple[list[Ref], list[str]]:
    tracked = set(files)
    # Form 5 resolves a bare name by file name: any tracked ``.md`` answers for
    # ``NAME.md``; only a document under ``docs/`` makes ``NAME §X`` a citation.
    by_name: dict[str, list[str]] = {}
    for f in files:
        if f.endswith(".md"):
            by_name.setdefault(f.rsplit("/", 1)[-1], []).append(f)
    stems = {
        f.rsplit("/", 1)[-1][:-3] for f in files if f.startswith("docs/") and f.endswith(".md")
    }
    refs: list[Ref] = []
    for rel in files:
        if rel.startswith("docs/") or not rel.endswith(CODE_SUFFIXES):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        refs.extend(iter_refs(rel, text, stems))
    if ignored is None:
        ignored = ignored_docs(root, {r.doc for r in refs if "/" in r.doc and r.doc not in tracked})
    problems: list[str] = []
    doc_cache: dict[str, str] = {}

    def has_section(doc: str, section: str) -> bool:
        text = doc_cache.setdefault(doc, (root / doc).read_text(encoding="utf-8"))
        return section_exists(text, section)

    for ref in refs:
        if ref.doc in ignored or is_allowed(ref, allow):
            continue
        # A bare name may be the file name of several documents (README.md):
        # it exists if any does, and its section — if any of them has it.
        candidates = [ref.doc] if "/" in ref.doc else by_name.get(ref.doc, [])
        candidates = [c for c in candidates if c in tracked]
        if not candidates:
            problems.append(f"{ref.where}: MISSING DOCUMENT {ref.doc} ({ref.form})")
            continue
        if ref.section and not any(has_section(c, ref.section) for c in candidates):
            problems.append(
                f"{ref.where}: MISSING SECTION «{ref.section}» in {' | '.join(candidates)} ({ref.form})"
            )
    return refs, problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--stats", action="store_true", help="print the census by form")
    args = parser.parse_args()
    root = args.root
    files = tracked_files(root)
    refs, problems = check(root, files, load_allowlist())
    if args.stats:
        # Dead = what the guard would report with no exceptions at all.
        _, unexcepted = check(root, files, [], ignored=set())
        dead_at = {(p.split(": ", 1)[0], p.rsplit("(", 1)[-1].rstrip(")")) for p in unexcepted}
        by_form: dict[str, list[int]] = {}
        for r in refs:
            live = (r.where, r.form) not in dead_at
            by_form.setdefault(r.form, [0, 0])[0 if live else 1] += 1
        for form, (n_live, n_dead) in sorted(by_form.items()):
            print(f"  {form:<13} live={n_live:<4} dead={n_dead}")
    for p in problems:
        print(p)
    print(
        f"doc_refs_guard: {len(refs)} references to documents; "
        f"{len(problems)} dead and not excepted."
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
