"""Positive proof for tools/lint/doc_refs_guard.py (DRF-2657, DRF-2682).

Every form is run in BOTH directions on a real git tree: a live reference and
the guard is SILENT, a dead one and it is RED. A guard proven only silent is
``assert True`` with a long name — the same words ``pii_guard`` uses about
itself.

Then the exception list is held to its own rules: a dead-basis exception
names its addressee (``пункт NN`` / ``ждёт номера``), every exception is
still needed, and the list may only shrink.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "lint"))
import doc_refs_guard as g  # type: ignore[import-not-found]  # noqa: E402

_DOC = """# A document
## 6. Six
### 5.1 Five point one
1. first item
2. second item
## 13. Thirteen
## T. Decisions
### Screen M1 — dashboard
"""


def _repo(tmp_path: Path, code: str, *, gitignore: str = "") -> list[str]:
    """A git tree with docs/D.md, the summary, and one code file citing them."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "D.md").write_text(_DOC, encoding="utf-8")
    (tmp_path / "docs" / "OWNER_DECISIONS_2026-09-11.md").write_text(_DOC, encoding="utf-8")
    (tmp_path / "docs" / "OWNER_WORDS_X.md").write_text(_DOC, encoding="utf-8")
    (tmp_path / "app.py").write_text(code, encoding="utf-8")
    if gitignore:
        (tmp_path / ".gitignore").write_text(gitignore, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return g.tracked_files(tmp_path)


def _run(tmp_path: Path, code: str, allow=(), gitignore: str = ""):
    files = _repo(tmp_path, code, gitignore=gitignore)
    return g.check(tmp_path, files, list(allow))


def _problems(tmp_path: Path, code: str, allow=(), gitignore: str = "") -> list[str]:
    return _run(tmp_path, code, allow, gitignore)[1]


# ── each form, both directions ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("live", "dead"),
    [
        ("# see docs/D.md", "# see docs/Gone.md"),  # path
        ("# see OWNER_WORDS_X.md", "# see OWNER_QUESTIONS.md"),  # family name
    ],
    ids=["path", "family"],
)
def test_document_live_is_silent_dead_is_red(tmp_path: Path, live: str, dead: str) -> None:
    seen, silent = _run(tmp_path / "a", live)
    assert seen  # the reference was counted — the silence is about it
    assert silent == []
    red = _problems(tmp_path / "b", dead)
    assert len(red) == 1 and "MISSING DOCUMENT" in red[0]


@pytest.mark.parametrize(
    ("live", "dead"),
    [
        ("# docs/D.md, раздел T", "# docs/D.md, раздел ZZ"),
        ("# docs/D.md §M1", "# docs/D.md §MM0"),
        ("# docs/D.md §5.1", "# docs/D.md §5.9"),
        ("# docs/D.md §5.1.2", "# docs/D.md §5.1.3"),  # item N of the section's list
        ("# docs/D.md §5.1.2.", "# docs/D.md §13.1"),  # item exists only under 5.1
    ],
    ids=["раздел", "screen-heading", "subsection", "list-item", "item-scoped"],
)
def test_section_live_is_silent_dead_section_is_red(tmp_path: Path, live: str, dead: str) -> None:
    """A dead section in a LIVE document — the failure a name-only check misses."""
    seen, silent = _run(tmp_path / "a", live)
    assert seen  # the reference was counted — the silence is about it
    assert silent == []
    red = _problems(tmp_path / "b", dead)
    assert len(red) == 1 and "MISSING SECTION" in red[0]
    assert "MISSING DOCUMENT" not in red[0]


@pytest.mark.parametrize(
    ("live", "dead"),
    [
        ("# §6 свода 11.09", "# §99 свода 11.09"),
        ("# свод владельца 2026-09-11 §13", "# свод владельца 2026-09-11 §3"),
    ],
    ids=["§N-svoda", "svod-then-§"],
)
def test_svod_section_live_is_silent_dead_is_red(tmp_path: Path, live: str, dead: str) -> None:
    seen, silent = _run(tmp_path / "a", live)
    assert seen  # the reference was counted — the silence is about it
    assert silent == []
    red = _problems(tmp_path / "b", dead)
    assert len(red) == 1 and "MISSING SECTION" in red[0]


def test_section_three_does_not_match_thirteen(tmp_path: Path) -> None:
    """The headings have «13.» but no «3.» — the bound must hold."""
    red = _problems(tmp_path, "# docs/D.md §3")
    assert len(red) == 1 and "«3»" in red[0]


def test_another_repository_is_not_checked_but_this_one_is(tmp_path: Path) -> None:
    assert len(_problems(tmp_path / "b", "# docs/Contract.md")) == 1
    other = _problems(tmp_path / "a", "# beautygo_backend/docs/Contract.md")
    assert other == []  # empty-assert-ok: the same path, local, is red just above


def test_ignored_output_is_not_a_missing_basis(tmp_path: Path) -> None:
    code = "# written to docs/generated/STATE.md at deploy"
    seen, silent = _run(tmp_path / "a", code, gitignore="docs/generated/\n")
    assert seen  # counted, then passed as declared output
    assert silent == []
    assert len(_problems(tmp_path / "b", code)) == 1


def test_section_exception_covers_only_that_section(tmp_path: Path) -> None:
    allow = [("docs/D.md§MM0", "ждёт номера")]
    code = "# docs/D.md §MM0\n# docs/D.md §MM9\n"
    red = _problems(tmp_path, code, allow)
    assert len(red) == 1 and "«MM9»" in red[0]


# ── form 5: a document named without its directory (DRF-2682) ───────────────


def _repo_with_nested(tmp_path: Path, code: str) -> list[str]:
    """The fixture tree plus a document in a subfolder and two namesakes."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    for rel, text in {
        "docs/architecture/event-contract.md": _DOC,
        "docs/a/README.md": "# A\n## 1. One\n",
        "docs/b/README.md": "# B\n## 2. Two\n",
        "docs/OPEN_DECISIONS.md": _DOC,
    }.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return _repo(tmp_path, code)


def _bare(tmp_path: Path, code: str) -> tuple[list, list[str]]:
    files = _repo_with_nested(tmp_path, code)
    return g.check(tmp_path, files, [])


@pytest.mark.parametrize(
    ("live", "dead", "needle"),
    [
        # NAME.md with no directory: any tracked .md of that name answers for it
        (
            "# per event-contract.md",
            "# per event-kontrakt.md",
            "MISSING DOCUMENT event-kontrakt.md",
        ),
        # NAME.md §X — the document is found by name, the section must exist in it
        ("# per event-contract.md §6", "# per event-contract.md §99", "MISSING SECTION «99»"),
        # NAME §X with no .md — the word before the section mark is a document
        ("# per event-contract §5.1", "# per event-contract §5.9", "MISSING SECTION «5.9»"),
        (
            "# (``event-contract``, раздел T)",
            "# (``event-contract``, раздел W)",
            "MISSING SECTION «W»",
        ),
        # the family name without .md — the measured case: 47 mentions of OPEN_DECISIONS §N
        ("# OPEN_DECISIONS §6", "# OPEN_DECISIONS §99", "MISSING SECTION «99»"),
        # a family name outlives its document: a dead one is still seen without .md
        (
            "# OWNER_WORDS_X §6",
            "# OWNER_QUESTIONS раздел S",
            "MISSING DOCUMENT docs/OWNER_QUESTIONS.md",
        ),
    ],
    ids=[
        "name",
        "name.md+section",
        "name+section",
        "name+раздел",
        "family-bare",
        "family-bare-dead",
    ],
)
def test_bare_name_live_is_silent_dead_is_red(
    tmp_path: Path, live: str, dead: str, needle: str
) -> None:
    refs, silent = _bare(tmp_path / "live", live)
    # Presence first: the live line WAS read as a bare-name reference.
    assert [r.form for r in refs if r.form.startswith("name")] != []
    assert silent == []  # empty-assert-ok: the reference was seen, asserted above
    red = _bare(tmp_path / "dead", dead)[1]
    assert len(red) == 1 and needle in red[0], red


def test_bare_name_is_not_counted_twice_next_to_a_path(tmp_path: Path) -> None:
    refs, problems = _bare(tmp_path, "# see docs/D.md §6 and OWNER_WORDS_X.md §6")
    assert sorted(r.form for r in refs) == ["path+section", "path+section"]
    assert problems == []  # empty-assert-ok: two references seen, asserted above


def test_a_namesake_exists_if_any_does_and_its_section_if_any_has_it(tmp_path: Path) -> None:
    """README.md is the file name of several documents: the guard asks all of them."""
    refs, silent = _bare(tmp_path / "live", "# see README.md §2")
    assert [r.doc for r in refs] == ["README.md"]
    assert silent == []  # empty-assert-ok: the reference was seen, asserted above
    red = _bare(tmp_path / "dead", "# see README.md §3")[1]
    assert (
        len(red) == 1
        and "MISSING SECTION «3»" in red[0]
        and "docs/a/README.md | docs/b/README.md" in red[0]
    )


def test_another_repository_named_before_a_bare_name_is_not_checked(tmp_path: Path) -> None:
    code = "# see beautygo_backend/docs/Gone.md §2 and mysite/docs/arch/Gone.md\n# and Gone.md here"
    refs, problems = _bare(tmp_path, code)
    assert [(r.where, r.doc) for r in refs] == [("app.py:2", "Gone.md")]
    assert len(problems) == 1 and "app.py:2: MISSING DOCUMENT Gone.md" in problems[0]


@pytest.mark.parametrize(
    "code",
    [
        'name = "note.md"',  # a value the code handles, not a citation
        "name = 'note.md'",
        "# `ayla-knowledge/07 UX/Ayla Master Schedule UX Contract.md`",  # a path with spaces
        'source = "ayla-knowledge:05 Architecture/ADR-0012 Dynamic User Model.md:N-04"',
    ],
    ids=["double-quoted-value", "single-quoted-value", "spaced-path", "spaced-path-in-yaml"],
)
def test_a_value_and_a_path_with_spaces_are_not_citations(tmp_path: Path, code: str) -> None:
    refs, problems = _bare(tmp_path / "skipped", code)
    assert refs == [] and problems == []
    # …and the same name written as prose IS a citation — the skip is not blanket silence.
    red = _bare(tmp_path / "prose", "# see note.md and Contract.md")[1]
    assert len(red) == 2 and all("MISSING DOCUMENT" in p for p in red)


def test_an_ordinary_word_that_is_also_a_document_name_is_not_a_reference(tmp_path: Path) -> None:
    """``D`` is a document here (docs/D.md). Alone it is a letter; before a section — a citation."""
    refs, problems = _bare(tmp_path / "letter", "# plan D for the on-call\nD = 1\n# D §6")
    assert [(r.where, r.doc, r.section) for r in refs] == [("app.py:3", "D.md", "6")]
    assert problems == []  # empty-assert-ok: one reference seen, asserted above
    # …and a word that is NOT a document does not become one by standing before a section.
    refs, problems = _bare(tmp_path / "word", "# решение §6, пункт §99\n# D §6")
    assert [(r.where, r.doc) for r in refs] == [("app.py:2", "D.md")]
    assert problems == []  # empty-assert-ok: one reference seen, asserted above


def test_a_slash_earlier_on_a_plain_line_does_not_hide_a_bare_name(tmp_path: Path) -> None:
    """Only a QUOTED path with spaces is skipped; ``apps/x.py … Gone.md`` is still a citation.

    The first cut of the rule looked for a slash anywhere before the name: it hid
    two dead references in this very tree and made the «another repository» rule
    unfalsifiable.
    """
    red = _bare(tmp_path, "# see apps/x/y.py and then Gone.md")[1]
    assert len(red) == 1 and "MISSING DOCUMENT Gone.md" in red[0]


def test_the_real_tree_holds_bare_name_references() -> None:
    """The form is not proven on fixtures only: the census of 02.10 counted 270 in this tree."""
    files = g.tracked_files(_PROJECT_ROOT)
    refs, _ = g.check(_PROJECT_ROOT, files, g.load_allowlist())
    bare = [r for r in refs if r.form in ("name", "name+section")]
    assert len(bare) > 200
    assert sum(r.doc == "event-contract.md" for r in bare) > 50
    # The measured case of the ticket: OPEN_DECISIONS cited without ``.md``.
    assert sum(r.doc == "docs/OPEN_DECISIONS.md" and r.form == "name+section" for r in bare) > 30


# ── the real exception list and the real tree ───────────────────────────────


def test_repository_is_clean_and_the_census_is_not_empty() -> None:
    files = g.tracked_files(_PROJECT_ROOT)
    refs, problems = g.check(_PROJECT_ROOT, files, g.load_allowlist())
    # Presence first: the guard sees hundreds of references, including the summary form.
    assert len(refs) > 400
    assert any(r.form == "svod" for r in refs)
    assert any(r.form == "path+section" for r in refs)
    assert problems == [], "\n".join(problems)


def test_a_dead_basis_exception_names_its_addressee() -> None:
    entries = g.load_allowlist()
    documents = [(p, r) for p, r in entries if g.is_document_entry(p)]
    assert documents, "the list holds dead-basis exceptions"
    unaddressed = [p for p, r in documents if not g.ADDRESSEE_RE.search(r)]
    assert unaddressed == []  # empty-assert-ok: `documents` is non-empty, asserted above


def test_every_exception_has_a_reason_and_a_structural_one_an_existing_path() -> None:
    entries = g.load_allowlist()
    assert entries
    assert all(reason for _p, reason in entries)
    structural = [p for p, _r in entries if not g.is_document_entry(p)]
    assert structural
    assert [
        p for p in structural if not (_PROJECT_ROOT / p).exists()
    ] == []  # empty-assert-ok: `structural` non-empty


def test_every_exception_is_still_needed() -> None:
    """A fixed reference must take its exception with it — otherwise nothing shrinks."""
    files = g.tracked_files(_PROJECT_ROOT)
    refs, _ = g.check(_PROJECT_ROOT, files, [])
    entries = g.load_allowlist()
    assert entries
    unused = [p for p, r in entries if not any(g.is_allowed(ref, [(p, r)]) for ref in refs)]
    assert unused == []  # empty-assert-ok: `entries` non-empty, asserted above


def test_the_list_may_only_shrink() -> None:
    """Raising the ceiling must show in the diff; removing an entry lowers it."""
    assert len(g.load_allowlist()) == g.ALLOW_CEILING
