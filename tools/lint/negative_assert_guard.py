#!/usr/bin/env python3
"""Fail when a test asserts an absence over data it never proved was there.

# The defect

DRF-1406. ``apps/master_api/tests/test_pii_boundary.py`` sweeps every
master read endpoint and asserts no forbidden PII key appears in the
body::

    resp = client.get(url, ...)
    assert resp.status_code == 200
    found = find_forbidden_pii(resp.json())
    assert found == []

The fixture seeds its bookings at 2026-05-18/20/26. The ``schedule``
route returns only FUTURE bookings. Once May receded, that route began
returning an empty body — and ``find_forbidden_pii([])`` returns ``[]``,
so the assertion went on passing. The test stopped checking anything and
never went red. A leak introduced after that date would have shipped.

The date is how it happened here; the date is not the defect. The same
test passes vacuously against a fixture that silently stopped seeding, a
mock that returns nothing, a filter that quietly narrowed. What the test
lacks is not a relative date. It is **a reason to believe there was
anything to inspect.**

# The rule

    An assertion of absence needs an assertion of presence on the same
    data, ahead of it. «Nothing forbidden here» without «here is
    something» is not a check — it is a hope.

The tests that survive this rule already follow it, and the shape is
consistent (``apps/eventbus/tests/test_payment_consumer.py``)::

    victim_conv = _resolve_conversation(tenant=tenant, user_id=UID)
    assert victim_conv is not None      # <- presence, fails LOUD
    ...
    assert spoofer_conv is None         # <- absence, now meaningful

Starve that test of data and the presence assertion fails first, by name.
The absence assertion is never reached, so it never lies.

# Why a 200 is not a guard

``assert resp.status_code == 200`` reads like a presence check and is
not one: an empty body is a perfectly good 200. That single mistaken
guard is the whole of DRF-1406, so this module refuses to count any
assertion on ``.status_code`` / ``.status`` / ``.ok`` / ``.code`` as
proof that a body has content. See :data:`_NOT_A_GUARD_ATTRS`.

# What this guard does NOT do

Named here because a green run of a scanner that cannot see is
indistinguishable from an honest «nothing found» — the failure mode this
guard's own calibration test exists to prevent (see
``tests/tools/test_negative_assert_guard.py``).

* It reasons **inside one function body**. A presence assertion factored
  into a helper or a fixture is not seen, and such a test is reported
  though it may be sound. Silence it with the marker comment.
* It does not know that a guard and an absence assertion concern the
  same *values* — only that they share a root name. A test that guards
  ``resp`` and then asserts absence over an unrelated ``resp2`` reads as
  guarded.
* It says nothing about tests that assert absence over data that was
  never fetched (a plain local, a constant). Those are out of scope by
  construction: :func:`_fetched` decides what counts as fetched, and it
  errs narrow.
* It cannot tell a deliberately-empty expectation from a rotted one. A
  test that legitimately asserts «this endpoint returns nothing for an
  anonymous caller» must say so with the marker.

# Silencing

A reported site that is genuinely sound gets a marker comment on the
assertion line or the line above::

    assert found == []  # empty-assert-ok: anonymous caller sees nothing

The reason after the colon is required. A bare marker is itself a
violation — «somebody looked and said why» is the whole product here.


The prefix is deliberately not ``noqa``: ruff parses every ``# noqa``
comment and warns on one it cannot read, so a ``noqa-`` marker would
make every silenced site emit lint noise of its own.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

#: Attributes whose value proves nothing about whether a body has content.
#: ``assert resp.status_code == 200`` is the mistaken guard at the heart
#: of DRF-1406 — an empty body is a perfectly good 200.
_NOT_A_GUARD_ATTRS = frozenset({"status_code", "status", "ok", "code", "reason", "headers"})

#: A name that suggests the value was fetched rather than constructed.
#: Deliberately generous — :func:`_fetched` also accepts any call or
#: subscript, and a local assigned from one.
_FETCHED_NAME = re.compile(
    r"resp|response|body|payload|data|json|content|result|rows|items|found|leaked|matches",
    re.I,
)

#: Sites are keyed repo-relative. Invoking the guard with absolute paths
#: (as the pytest ratchet does) must produce the same keys as invoking it
#: with `apps/ tests/` from the repo root, or the baseline matches nothing
#: and the ratchet silently permits every site in the tree.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


_MARKER = re.compile(r"#\s*empty-assert-ok\s*:\s*(?P<reason>\S.*)$")
_BARE_MARKER = re.compile(r"#\s*empty-assert-ok\s*(?::\s*)?$")


@dataclass(frozen=True)
class Site:
    """One absence assertion with no presence assertion ahead of it."""

    path: str
    func: str
    lineno: int
    source: str

    def render(self) -> str:
        return f"{self.path}:{self.lineno}  {self.func}\n    {self.source}"


# --------------------------------------------------------------------------
# Assertion shapes
# --------------------------------------------------------------------------


def _roots(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _is_empty_collection(node: ast.AST) -> bool:
    """An empty COLLECTION literal, not merely a falsy value.

    The narrowing is deliberate and it is what keeps this guard usable.
    `== 0` also spells an exit code, an index and a counter; `is None`
    spells every optional scalar in the codebase. Admitting those took
    the scan from 88 sites to 1445 -- and a 1445-line baseline is a
    rubber stamp, not a ratchet. A lint that cries wolf gets switched
    off, and then it protects nothing.

    Collection-shaped absence is where vacuous passing actually hides:
    the query came back with nothing and the assertion shrugged.
    """
    if isinstance(node, ast.List) and not node.elts:
        return True
    if isinstance(node, ast.Dict) and not node.keys:
        return True
    if isinstance(node, ast.Set) and not node.elts:
        return True
    if isinstance(node, ast.Tuple) and not node.elts:
        return True
    return False


def _is_empty_literal(node: ast.AST) -> bool:
    """Used only to judge PRESENCE assertions, where breadth is safe:
    a wider notion of "empty" here can only make us call something a
    guard less often, never more."""
    if _is_empty_collection(node):
        return True
    return isinstance(node, ast.Constant) and node.value in (0, None, "", False)


def _is_literal(node: ast.AST) -> bool:
    """A value written out in the test, not fetched from anywhere.

    Collection displays count: `redact_data_for_dlq({}) == {}` is a pure
    unit test over an empty dict the author typed. Nothing there can
    quietly become empty, because it already is, on purpose.
    """
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Dict, ast.Set, ast.Tuple)):
        return all(_is_literal(e) for e in getattr(node, "elts", []) or []) and all(
            _is_literal(v) for v in getattr(node, "values", []) or []
        )
    return False


def _counts_rows(node: ast.AST) -> bool:
    """`x.count()` / `len(x)` / `x.exists()` -- a collection cardinality."""
    if isinstance(node, ast.Call):
        fn = node.func
        if getattr(fn, "id", "") == "len":
            return True
        if getattr(fn, "attr", "") in {"count", "exists", "first", "all"}:
            return True
    return False


def _is_cardinality(node: ast.AST) -> bool:
    """`qs.filter(...).count()` / `qs.exists()` -- asking a collection how
    big it is. Asserting THAT is zero is a statement about the collection
    itself, not a search inside it."""
    return _counts_rows(node)


def _searches_within(node: ast.AST) -> bool:
    """A scan applied TO some content, rather than the content itself.

    This is the whole discrimination the guard rests on, and getting it
    wrong in either direction is fatal:

    * ``assert body["items"] == []`` -- the fetched collection IS the
      subject. Emptiness is the fact under test; the test says what it
      means and a guard would be noise. 242 sites of this, all sound.
    * ``assert find_forbidden_pii(resp.json()) == []`` -- a needle hunted
      inside a haystack. An empty haystack yields an empty result and the
      hunt proves nothing. This is DRF-1406.

    So: a call that takes an argument, and is not a cardinality query.
    A call over literal arguments only (``parse_registry({}) == ()``) is
    a pure unit test with nothing fetched, and is excluded too.
    """
    if not isinstance(node, ast.Call) or _is_cardinality(node):
        return False
    args = list(node.args) + [k.value for k in node.keywords]
    if not args:
        return False
    return any(not _is_literal(a) for a in args)


def _absence_subject(test: ast.AST, is_search: "Callable[[ast.AST], bool]") -> ast.AST | None:
    """The expression an assertion claims is empty/absent, or None.

    ``is_search`` is passed in rather than called directly because the
    search may have happened a line earlier: ``found = scan(resp.json())``
    then ``assert found == []`` names a plain local. Resolving that hop
    needs the enclosing function, which lives in :func:`scan_file`. The
    calibration test exists because this exact hop was missed twice.
    """
    # `assert not scan(body)` -- a search that came back empty.
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return test.operand if is_search(test.operand) else None
    if isinstance(test, ast.Compare) and len(test.comparators) == 1:
        op, right, left = test.ops[0], test.comparators[0], test.left
        # `assert PHONE not in body` -- the needle/haystack shape spelled
        # with an operator instead of a function. The haystack is what
        # must be proved non-empty.
        if isinstance(op, ast.NotIn):
            return right
        # `assert find_forbidden_pii(resp.json()) == []`
        if isinstance(op, ast.Eq) and _is_empty_collection(right) and is_search(left):
            return left
    return None


def _implies_presence(test: ast.AST) -> ast.AST | None:
    """The expression an assertion proves is non-empty, or None.

    Anything resting on :data:`_NOT_A_GUARD_ATTRS` is refused here: that
    is the DRF-1406 mistake, and refusing it is this module's point.
    """
    subject: ast.AST | None = None

    if isinstance(test, ast.Compare) and len(test.comparators) == 1:
        op, right, left = test.ops[0], test.comparators[0], test.left
        if isinstance(op, ast.IsNot) and isinstance(right, ast.Constant) and right.value is None:
            subject = left
        elif isinstance(op, ast.In):
            subject = right
        elif isinstance(op, ast.NotEq) and _is_empty_literal(right):
            subject = left
        elif isinstance(op, (ast.Gt, ast.GtE)) and isinstance(right, ast.Constant):
            floor = 0 if isinstance(op, ast.Gt) else 1
            if isinstance(right.value, int) and right.value >= floor:
                subject = left
        elif isinstance(op, ast.Eq) and not _is_empty_literal(right):
            subject = left
    elif not isinstance(test, ast.UnaryOp):
        # Bare `assert x` / `assert x.method()` -- truthiness is presence.
        subject = test

    if subject is None:
        return None
    for n in ast.walk(subject):
        if isinstance(n, ast.Attribute) and n.attr in _NOT_A_GUARD_ATTRS:
            return None
    # len(x) > 0 proves x has content, so credit x itself.
    if isinstance(subject, ast.Call) and getattr(subject.func, "id", "") == "len" and subject.args:
        return subject.args[0]
    return subject


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------


def _derived_locals(fn: ast.AST) -> set[str]:
    """Locals assigned from a call/subscript/comprehension -- i.e. fetched.

    `found = find_forbidden_pii(resp.json())` is the canonical case: the
    absence assertion names a plain local, and the fetching happened one
    line above. A check that looks only at the asserted expression misses
    DRF-1406 entirely -- this function is that missing dataflow hop.
    """
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and isinstance(
            node.value, (ast.Call, ast.Subscript, ast.ListComp, ast.GeneratorExp, ast.DictComp)
        ):
            for target in node.targets:
                name = getattr(target, "id", None)
                if name:
                    out.add(name)
    return out


def _assignment_sources(fn: ast.AST) -> dict[str, ast.AST]:
    """local name -> the expression it was last assigned from."""
    out: dict[str, ast.AST] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                name = getattr(target, "id", None)
                if name:
                    out[name] = node.value
    return out


def _fetched(subject: ast.AST, source: str, derived: set[str]) -> bool:
    """Structural evidence that the value was FETCHED, not constructed.

    Name-shape ("it is called `data`") was tried and rejected: it admits
    every local anyone ever called `result` and took the scan to 772
    sites, most of which assert absence over something the test built
    itself. We require the fetch to be visible -- a call, a subscript
    into one, or a local assigned from one. That is exactly what makes
    an empty answer possible without the test noticing.
    """
    if isinstance(subject, (ast.Call, ast.Subscript)):
        return True
    return isinstance(subject, ast.Name) and subject.id in derived


def _rhs_roots(fn: ast.AST, name: str) -> set[str]:
    """Roots of whatever `name` was assigned from -- guard may name those."""
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == name for t in node.targets
        ):
            out |= _roots(node.value)
    return out


def _marker(lines: list[str], lineno: int) -> tuple[bool, bool]:
    """(silenced, bare) -- a marker with no reason is itself a violation."""
    for idx in (lineno - 1, lineno - 2):
        if 0 <= idx < len(lines):
            text = lines[idx]
            if _MARKER.search(text):
                return True, False
            if _BARE_MARKER.search(text):
                return True, True
    return False, False


def scan_file(path: Path, source: str) -> tuple[list[Site], list[Site]]:
    """Return (unguarded sites, bare-marker sites) for one test module."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [], []

    lines = source.splitlines()
    unguarded: list[Site] = []
    bare: list[Site] = []

    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not fn.name.startswith("test"):
            continue

        derived = _derived_locals(fn)
        rhs = _assignment_sources(fn)

        def is_search(node: ast.AST, _rhs: dict[str, ast.AST] = rhs) -> bool:
            """A search inside content -- following one assignment hop."""
            if isinstance(node, ast.Name) and node.id in _rhs:
                return _searches_within(_rhs[node.id])
            return _searches_within(node)

        asserts = sorted(
            (n for n in ast.walk(fn) if isinstance(n, ast.Assert)), key=lambda n: n.lineno
        )

        for node in asserts:
            subject = _absence_subject(node.test, is_search)
            if subject is None or not _fetched(subject, source, derived):
                continue

            wanted = _roots(subject)
            for name in list(wanted):
                if name in derived:
                    wanted |= _rhs_roots(fn, name)

            guarded = False
            for earlier in asserts:
                if earlier.lineno >= node.lineno:
                    break
                proof = _implies_presence(earlier.test)
                if proof is not None and _roots(proof) & wanted:
                    guarded = True
                    break
            if guarded:
                continue

            silenced, is_bare = _marker(lines, node.lineno)
            text = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else ""
            site = Site(path=_rel(path), func=fn.name, lineno=node.lineno, source=text[:140])
            if is_bare:
                bare.append(site)
            elif not silenced:
                unguarded.append(site)

    return unguarded, bare


def scan(roots: list[Path]) -> tuple[list[Site], list[Site]]:
    unguarded: list[Site] = []
    bare: list[Site] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            name = path.name
            if not (name.startswith("test_") or name.endswith("_test.py")):
                continue
            if any(part in {".venv", "__pycache__", "node_modules"} for part in path.parts):
                continue
            u, b = scan_file(path, path.read_text(encoding="utf-8", errors="ignore"))
            unguarded += u
            bare += b
    return unguarded, bare


# --------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------

_BASELINE = Path(__file__).with_name("negative_assert_guard_baseline.txt")


def _key(site: Site) -> str:
    """Path + function + assertion text. Deliberately NOT the line number:
    a baseline keyed on line numbers goes stale on the next edit above it
    and re-flags a site nobody touched."""
    return f"{site.path}::{site.func}::{site.source}"


def read_baseline(path: Path = _BASELINE) -> set[str]:
    if not path.is_file():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv[1:] if not a.startswith("-")]
    if not roots:
        print(
            "usage: negative_assert_guard.py <dir> [<dir>...] [--write-baseline]", file=sys.stderr
        )
        return 2
    for root in roots:
        if not root.is_dir():
            print(f"negative_assert_guard: not a directory: {root}", file=sys.stderr)
            return 2

    unguarded, bare = scan(roots)

    if "--write-baseline" in argv:
        body = "\n".join(sorted({_key(s) for s in unguarded}))
        _BASELINE.write_text(
            "# Absence assertions with no presence assertion ahead of them,\n"
            "# as they stood when tools/lint/negative_assert_guard.py landed\n"
            "# (DRF-1411). This file is a RATCHET, not a verdict: every line\n"
            "# is a test that may be sound and may be vacuous, and nobody has\n"
            "# checked which. Deleting a line is progress. Adding one is not\n"
            "# allowed -- the guard fails instead.\n" + body + "\n",
            encoding="utf-8",
        )
        print(f"negative_assert_guard: baseline written ({len(unguarded)} sites).")
        return 0

    baseline = read_baseline()
    new = [s for s in unguarded if _key(s) not in baseline]

    for site in bare:
        print(f"BARE MARKER (a reason is required)\n{site.render()}\n")
    for site in new:
        print(f"UNGUARDED ABSENCE ASSERTION\n{site.render()}\n")

    if new or bare:
        print(
            f"negative_assert_guard: {len(new)} new unguarded absence "
            f"assertion(s), {len(bare)} bare marker(s).\n"
            "An assertion of absence needs an assertion of presence on the "
            "same data, ahead of it. Add the presence assertion, or mark the "
            "site `# empty-assert-ok: <why this is genuinely empty>`.",
            file=sys.stderr,
        )
        return 1

    print(
        f"negative_assert_guard: clean ({len(unguarded)} known site(s) in baseline). "
        "Green means nothing NEW was added -- not that the baseline is sound."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
