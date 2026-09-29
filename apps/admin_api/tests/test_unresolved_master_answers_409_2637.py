"""DRF-2637: no handler of ``CatalogSpecialistUnresolved`` answers «not found».

«The master is not set up in the catalog yet» and «there is no such master»
are two different answers: the first is 409 ``catalog_profile_unresolved``
(or a named refusal of the surface), the second is 404. The census walks
every ``except CatalogSpecialistUnresolved`` in ``apps/`` by construction —
the AST, not the name of the file — and refuses a handler whose body answers
404 or ``not_found``. A planted handler of the old form proves the census
reads, not that it is blind.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[3]

OLD_FORM = """
from django.http import JsonResponse
from apps.catalog.specialist_ref import CatalogSpecialistUnresolved, catalog_specialist_id

def view(request, master):
    try:
        catalog_specialist_id(master)
    except CatalogSpecialistUnresolved:
        return JsonResponse({"error": "not_found"}, status=404)
"""


def _catches_unresolved(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return False
    names = {
        n.id if isinstance(n, ast.Name) else n.attr
        for n in ast.walk(handler.type)
        if isinstance(n, ast.Name | ast.Attribute)
    }
    return "CatalogSpecialistUnresolved" in names


def _answers_not_found(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
        if isinstance(node, ast.Constant) and node.value in (404, "not_found"):
            return True
    return False


def census(files: list[pathlib.Path]) -> tuple[int, list[str]]:
    """(handlers seen, handlers answering «not found»)."""
    seen, bad = 0, []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _catches_unresolved(node):
                seen += 1
                if _answers_not_found(node):
                    bad.append(f"{path.as_posix()}:{node.lineno}")
    return seen, bad


def _app_sources() -> list[pathlib.Path]:
    return sorted(
        p
        for p in (ROOT / "apps").rglob("*.py")
        if "tests" not in p.parts and "migrations" not in p.parts
    )


def test_no_handler_answers_not_found_and_the_census_reads(tmp_path) -> None:
    planted = tmp_path / "planted_old_form.py"
    planted.write_text(OLD_FORM, encoding="utf-8")

    seen, bad = census([*_app_sources(), planted])

    # 15 handlers in apps/ on dev bb27a891 + the planted one. The floor, not
    # the exact number: a new handler is welcome, a vanished census is not.
    assert seen >= 16, seen
    assert bad == [f"{planted.as_posix()}:8"], bad
