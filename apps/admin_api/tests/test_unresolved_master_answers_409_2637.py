"""DRF-2637: no handler of ``CatalogSpecialistUnresolved`` answers «not found».

«The master is not set up in the catalog yet» and «there is no such master»
are two different answers: the first is 409 ``catalog_profile_unresolved``
(or a named refusal of the surface), the second is 404. The census walks
every ``except CatalogSpecialistUnresolved`` in ``apps/`` by construction —
the AST, not the name of the file — and refuses a handler that answers 404
or ``not_found``: written in the handler, or one call deep into a helper of
the same module (``return _absent()``), or as ``Http404`` / DRF
``NotFound`` / ``HTTPStatus.NOT_FOUND``.

*Limits:* a helper in another module, a call deeper than one level, and an
aliased import of the exception (``import … as X``, none in ``apps/`` today)
are not followed. A bare ``404`` compared in a handler body would be a false
positive — none today.

Planted handlers of both old forms prove the census reads, not that it is
blind.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[3]

#: A 404 that is the surface's own answer, with the reason — not an oversight.
KNOWN = {
    # A public photo address (an <img src>): a master with no catalog profile
    # has no photo to serve — «photo not found», the same answer as a bad id.
    "apps/miniapp_api/master_media.py",
}

OLD_FORM = """
from django.http import JsonResponse
from apps.catalog.specialist_ref import CatalogSpecialistUnresolved, catalog_specialist_id

def view(request, master):
    try:
        catalog_specialist_id(master)
    except CatalogSpecialistUnresolved:
        return JsonResponse({"error": "not_found"}, status=404)
"""

HELPER_FORM = """
from django.http import JsonResponse
from apps.catalog.specialist_ref import CatalogSpecialistUnresolved, catalog_specialist_id

def _absent():
    return JsonResponse({"error": "not_found"}, status=404)

def view(request, master):
    try:
        catalog_specialist_id(master)
    except CatalogSpecialistUnresolved:
        return _absent()
"""

_NOT_FOUND_NAMES = {"Http404", "NotFound", "NOT_FOUND"}


def _catches_unresolved(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return False
    names = {
        n.id if isinstance(n, ast.Name) else n.attr
        for n in ast.walk(handler.type)
        if isinstance(n, ast.Name | ast.Attribute)
    }
    return "CatalogSpecialistUnresolved" in names


def _says_not_found(nodes: list[ast.stmt]) -> bool:
    for node in ast.walk(ast.Module(body=nodes, type_ignores=[])):
        if isinstance(node, ast.Constant) and node.value in (404, "not_found"):
            return True
        if isinstance(node, ast.Name) and node.id in _NOT_FOUND_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _NOT_FOUND_NAMES:
            return True
    return False


def _answers_not_found(handler: ast.ExceptHandler, helpers: dict[str, ast.FunctionDef]) -> bool:
    if _says_not_found(handler.body):
        return True
    called = {
        n.func.id
        for n in ast.walk(ast.Module(body=handler.body, type_ignores=[]))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    return any(_says_not_found(helpers[name].body) for name in called if name in helpers)


def census(files: list[pathlib.Path], root: pathlib.Path) -> tuple[int, list[str]]:
    """(handlers seen, handlers answering «not found» as ``relpath:line``)."""
    seen, bad = 0, []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        helpers = {f.name: f for f in tree.body if isinstance(f, ast.FunctionDef)}
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _catches_unresolved(node):
                seen += 1
                if _answers_not_found(node, helpers):
                    bad.append(f"{path.relative_to(root).as_posix()}:{node.lineno}")
    return seen, bad


def _app_sources() -> list[pathlib.Path]:
    return sorted(
        p
        for p in (ROOT / "apps").rglob("*.py")
        if not {"tests", "migrations"} & set(p.relative_to(ROOT).parts)
    )


def test_the_census_sees_both_old_forms(tmp_path) -> None:
    (tmp_path / "inline.py").write_text(OLD_FORM, encoding="utf-8")
    (tmp_path / "helper.py").write_text(HELPER_FORM, encoding="utf-8")

    seen, bad = census(sorted(tmp_path.glob("*.py")), tmp_path)

    assert (seen, bad) == (2, ["helper.py:11", "inline.py:8"])


def test_no_handler_in_apps_answers_not_found_but_the_recorded_ones() -> None:
    seen, bad = census(_app_sources(), ROOT)

    # 15 handlers on dev bb27a891. The floor, not the exact number: a new
    # handler is welcome, a vanished census is not.
    assert seen >= 15, seen
    assert {b.split(":")[0] for b in bad} == KNOWN, bad
