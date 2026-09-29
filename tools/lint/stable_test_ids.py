#!/usr/bin/env python3
"""Test ids must be the same, in the same order, in every process (DRF-2633).

# Why

``pytest-xdist`` collects the suite in EVERY worker and requires the
collections to be EQUAL AS ORDERED LISTS (``xdist/report.py``:
``from_collection == to_collection``). An id that differs between processes —
or the same ids in a different order — makes the workers disagree, and the run
dies at COLLECTION: in a shard, far from the cause, while one local process is
green. It cost two windows on 29.09: a ``str(uuid.uuid4())`` in a
``parametrize`` (#2176, closed), and memory addresses ``<function … at 0x…>``
in ``tests/contracts/test_consumer_tenant_verification_mandate.py``.

# Three checks — each closes a hole the others leave

1. **Double collection** (the main one). ``pytest --collect-only`` twice, in
   separate processes with different ``PYTHONHASHSEED``, and the two ORDERED
   lists are compared — ids that differ, and the same ids in another order
   (a set of strings iterated into ``parametrize``, filesystem order).
   Catches any way of making an id volatile without guessing the way.
   *Limit:* it only sees what actually differs between the two runs. Object
   addresses are allocated in the same order by a deterministic collection
   and CAN coincide between processes (measured different on Windows 29.09;
   not verifiable for the Linux runner) — then this check is blind to them.
2. **Address pattern.** Closes exactly that hole: an id carrying
   ``0x`` + six or more hex digits, or ``object at``, is a memory address —
   underived from the source — whether or not the two runs agreed.
   A match written in the test file itself is a literal, not an address,
   and is not reported.
   *Limit:* it knows one shape. UUIDs and timestamps are NOT pattern-checked:
   computed-but-stable values (``NOW + timedelta(...)`` from a constant)
   match those shapes and are fine; volatile ones are left to check 1, where
   a uuid4 or a ``now()`` practically never repeat. A decimal ``id(obj)`` is
   left to check 1 as well.
3. **Tuple-minded ``ids`` callable** — the cause, found without a run. With
   two or more argnames pytest calls ``ids`` once per VALUE, never with the
   tuple; an ``ids`` written to receive the tuple (``isinstance(x, tuple)``,
   indexing) and stringifying the raw value otherwise (``str``/``repr``/
   f-string/``%``/``format``) prints ``<… at 0x…>`` for objects. Stringifying
   is fine only under the BODY of an ``isinstance(param, <scalars>)`` check.
   *Limit:* static — a callable defined in another module or built
   dynamically is not followed.

The census covers the repo's ``python_files`` (``test_*.py``, ``*_test.py``)
and ``conftest.py`` (``metafunc.parametrize``).

Usage::

    python tools/lint/stable_test_ids.py [--] [pytest paths/args...]
"""

from __future__ import annotations

import ast
import os
import pathlib
import re
import subprocess
import sys
import warnings

ADDRESS = re.compile(r"0x[0-9a-fA-F]{6,}|\bobject at\b")


def collect(args: list[str], *, seed: str, cwd: str | None = None) -> list[str]:
    """Node ids, IN ORDER, from one ``--collect-only`` in a fresh process.

    No ``-q`` here: the repo's addopts already carry one, and quiet level 2
    prints per-file counts and no ids at all. An empty or failed collection
    is refused loudly — «nothing collected twice» compares equal to itself.
    """
    env = {**os.environ, "PYTHONHASHSEED": seed}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-p", "no:cacheprovider", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=cwd,
    )
    ids = [
        line.strip()
        for line in proc.stdout.splitlines()
        if "::" in line and not line.startswith(" ")
    ]
    if proc.returncode != 0 or not ids:
        raise SystemExit(
            f"stable_test_ids: collection failed or empty (rc={proc.returncode}, ids={len(ids)}):\n"
            + proc.stdout[-2000:]
            + proc.stderr[-2000:]
        )
    return ids


def volatile(first: list[str], second: list[str]) -> list[str]:
    """Ids present in one collection and not the other."""
    return sorted(set(first) ^ set(second))


def reordered(first: list[str], second: list[str]) -> list[str]:
    """Same ids, different order — xdist refuses that too. The first
    disagreeing position is enough to find the test."""
    if set(first) != set(second) or first == second:
        return []
    for i, (a, b) in enumerate(zip(first, second, strict=True)):
        if a != b:
            return [f"position {i}: {a}  <->  {b}"]
    return []


def address_ids(ids: set[str] | list[str], *, root: str | None = None) -> list[str]:
    """Ids whose parameter part carries a memory address.

    A match that is written in the test file itself (``0xFFFFFFFF`` in a code
    snippet under test) is derived from the source, not an address: a real
    address is never in the source text.
    """
    sources: dict[str, str] = {}
    found = set()
    for node_id in ids:
        if "[" not in node_id:
            continue
        matches = ADDRESS.findall(node_id[node_id.index("[") :])
        if not matches:
            continue
        path = node_id.split("::", 1)[0]
        if path not in sources:
            try:
                sources[path] = (pathlib.Path(root or ".") / path).read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                sources[path] = ""
        if any(m not in sources[path] for m in matches):
            found.add(node_id)
    return sorted(found)


# ── check 3: tuple-minded ids callables ─────────────────────────────────────

_SCALARS = {"str", "int", "float", "bool", "bytes"}


def _argcount(node: ast.AST) -> int:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return len([x for x in node.value.split(",") if x.strip()])
    if isinstance(node, ast.Tuple | ast.List):
        return len(node.elts)
    return 1


def _param_name(body: ast.AST) -> str | None:
    args = getattr(body, "args", None)
    return args.args[0].arg if args is not None and args.args else None


def _is_param(node: ast.AST, param: str) -> bool:
    return isinstance(node, ast.Name) and node.id == param


def _stringifies(node: ast.AST, param: str) -> bool:
    """``str(p)``, ``repr(p)``, ``format(p)``, ``f"{p}"``, ``"%s" % p``."""
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") in {"str", "repr", "format"}:
        return bool(node.args) and _is_param(node.args[0], param)
    if isinstance(node, ast.FormattedValue):
        return _is_param(node.value, param)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        right = node.right
        return _is_param(right, param) or (
            isinstance(right, ast.Tuple) and any(_is_param(e, param) for e in right.elts)
        )
    return False


def _under_scalar_guard(node: ast.AST, param: str, parents: dict[ast.AST, ast.AST]) -> bool:
    """True only in the BODY (not the ``else``) of ``isinstance(param, <scalars>)``."""
    child, cur = node, parents.get(node)
    while cur is not None:
        if isinstance(cur, ast.If | ast.IfExp):
            test = cur.test
            in_body = child is cur.body or (isinstance(cur.body, list) and child in cur.body)
            if (
                in_body
                and isinstance(test, ast.Call)
                and getattr(test.func, "id", "") == "isinstance"
                and len(test.args) == 2
                and _is_param(test.args[0], param)
            ):
                names = {n.id for n in ast.walk(test.args[1]) if isinstance(n, ast.Name)}
                if names and names <= _SCALARS:
                    return True
        child, cur = cur, parents.get(cur)
    return False


def _tuple_minded(body: ast.AST) -> bool:
    param = _param_name(body)
    if param is None:
        return False
    expects_tuple = any(
        (isinstance(n, ast.Subscript) and _is_param(n.value, param))
        or (
            isinstance(n, ast.Call)
            and getattr(n.func, "id", "") == "isinstance"
            and len(n.args) == 2
            and _is_param(n.args[0], param)
            and "tuple" in ast.unparse(n.args[1])
        )
        for n in ast.walk(body)
    )
    if not expects_tuple:
        return False
    parents = {child: node for node in ast.walk(body) for child in ast.iter_child_nodes(node)}
    return any(
        _stringifies(n, param) and not _under_scalar_guard(n, param, parents)
        for n in ast.walk(body)
    )


def tuple_minded_ids(source: str, name: str) -> list[str]:
    """``parametrize`` with 2+ argnames whose ``ids`` callable expects the tuple
    and stringifies the raw value otherwise."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source)
    funcs = {f.name: f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)}
    found: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "parametrize"):
            continue
        argnames = (
            node.args[0]
            if node.args
            else next((k.value for k in node.keywords if k.arg == "argnames"), None)
        )
        if argnames is None or _argcount(argnames) < 2:
            continue
        ids = next((k.value for k in node.keywords if k.arg == "ids"), None)
        body: ast.AST | None = None
        if isinstance(ids, ast.Lambda):
            body = ids
        elif isinstance(ids, ast.Name) and ids.id in funcs:
            body = funcs[ids.id]
        if body is not None and _tuple_minded(body):
            found.append(f"{name}:{node.lineno}")
    return found


def tuple_minded_census(paths: list[str]) -> list[str]:
    found: list[str] = []
    for root in paths:
        base = pathlib.Path(root)
        if base.is_file():
            files = [base]
        else:
            files = sorted(
                {*base.rglob("test_*.py"), *base.rglob("*_test.py"), *base.rglob("conftest.py")}
            )
        for path in files:
            if any(part in {".venv", "node_modules"} for part in path.parts):
                continue
            try:
                found += tuple_minded_ids(path.read_text(encoding="utf-8-sig"), path.as_posix())
            except (SyntaxError, UnicodeDecodeError):
                continue
    return found


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--"] or ["apps", "tests"]
    first = collect(args, seed="1")
    second = collect(args, seed="2")
    diff = volatile(first, second)
    order = reordered(first, second)
    addresses = address_ids([*first, *second])
    tuple_minded = tuple_minded_census(
        [a for a in args if not a.startswith("-") and pathlib.Path(a).exists()]
    )
    print(
        f"stable_test_ids: {len(first)} ids collected twice; volatile {len(diff)}, "
        f"reordered {len(order)}, address-bearing {len(addresses)}, tuple-minded ids {len(tuple_minded)}"
    )
    for title, items in (
        ("VOLATILE (differ between processes)", diff),
        ("REORDERED (same ids, different order — xdist refuses this too)", order),
        ("ADDRESS in id", addresses),
        ("ids CALLABLE expects the tuple (2+ argnames) and stringifies the rest", tuple_minded),
    ):
        if items:
            print(f"\n{title}:")
            for item in items[:50]:
                print(f"  {item}")
    return 1 if (diff or order or addresses or tuple_minded) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
