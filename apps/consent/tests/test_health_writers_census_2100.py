"""Census: nobody outside tests writes a ``HEALTH`` consent row any more (DRF-2100).

Owner ruling 18.09 (§48 п.8б): «перестать создавать новые отдельные согласия,
сохранить совместимость со старыми». The rule is guarded by ROLE, not by
spelling: every call of ``record_person_consent(...)`` under ``apps/`` (tests
excluded) is found by AST and its ``consent_type`` argument is resolved —
an attribute chain naming ``HEALTH``, a string literal ``"health"``, or a
module-level name bound to either (that is exactly how ``health.py`` used to
spell it: ``_TYPE = ConsentRecord.ConsentType.HEALTH.value``). A writer
hidden behind a constant is still a writer.

Two guards on the guard: the census must find call sites at all (an empty
scan proves nothing), and a planted module must turn it red.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
APPS_ROOT = REPO_ROOT / "apps"

WRITER_NAME = "record_person_consent"
_HEALTH_MARKERS = ("HEALTH", '"health"', "'health'")


def _names_health(expr: ast.AST, module_constants: dict[str, ast.AST]) -> bool:
    """Does this expression denote the HEALTH consent type?"""

    if isinstance(expr, ast.Constant):
        return expr.value == "health"
    if isinstance(expr, ast.Name):
        bound = module_constants.get(expr.id)
        return bound is not None and _names_health(bound, module_constants)
    source = ast.unparse(expr)
    return any(marker in source for marker in _HEALTH_MARKERS)


def _module_constants(tree: ast.Module) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            out[node.targets[0].id] = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            out[node.target.id] = node.value
    return out


def _consent_type_arg(call: ast.Call) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == "consent_type":
            return kw.value
    return None


def _is_writer_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == WRITER_NAME
    if isinstance(func, ast.Attribute):
        return func.attr == WRITER_NAME
    return False


def census(source: str, *, filename: str = "<planted>") -> tuple[int, list[str]]:
    """Return (writer call sites seen, offences naming HEALTH) for one module."""

    tree = ast.parse(source, filename=filename)
    constants = _module_constants(tree)
    seen = 0
    offences: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_writer_call(node):
            continue
        seen += 1
        arg = _consent_type_arg(node)
        if arg is not None and _names_health(arg, constants):
            offences.append(f"{filename}:{node.lineno}: {ast.unparse(arg)}")
    return seen, offences


def _production_modules() -> list[Path]:
    return sorted(
        p
        for p in APPS_ROOT.rglob("*.py")
        if "tests" not in p.parts and "migrations" not in p.parts and p.name != "__init__.py"
    )


def _census_of_tree() -> tuple[int, list[str]]:
    seen_total = 0
    offences: list[str] = []
    for path in _production_modules():
        rel = path.relative_to(REPO_ROOT).as_posix()
        seen, found = census(path.read_text(encoding="utf-8"), filename=rel)
        seen_total += seen
        offences.extend(found)
    return seen_total, offences


class TestNoHealthWriterOutsideTests:
    def test_no_production_call_writes_a_health_consent(self) -> None:
        seen, offences = _census_of_tree()
        assert seen >= 2, (
            "the census found almost no record_person_consent call sites — wrong root?"
        )
        assert offences == [], (
            "a production path grants ConsentType.HEALTH — DRF-2100 stopped issuing it "
            "(the profile handle grants food-diary-v1 instead): " + "; ".join(offences)
        )

    def test_the_definition_itself_is_not_a_call_site(self) -> None:
        """``def record_person_consent`` is not a call; the writer primitive stays."""

        seen, _ = census("def record_person_consent(bot_user, *, consent_type, source): ...\n")
        assert seen == 0


class TestTheGuardOnTheGuard:
    def test_a_direct_attribute_spelling_is_caught(self) -> None:
        planted = (
            "from apps.consent.models import ConsentRecord\n"
            "from apps.consent.services import record_person_consent\n"
            "def grant(u):\n"
            "    record_person_consent(u, consent_type=ConsentRecord.ConsentType.HEALTH.value, source='x')\n"
        )
        seen, offences = census(planted)
        assert seen == 1 and len(offences) == 1

    def test_a_module_constant_hiding_the_type_is_caught(self) -> None:
        """The spelling health.py actually used until this ticket."""

        planted = (
            "from apps.consent.models import ConsentRecord\n"
            "from apps.consent import services\n"
            "_TYPE = ConsentRecord.ConsentType.HEALTH.value\n"
            "def grant(u):\n"
            "    services.record_person_consent(u, consent_type=_TYPE, source='x')\n"
        )
        seen, offences = census(planted)
        assert seen == 1 and len(offences) == 1

    def test_a_string_literal_is_caught_and_another_type_is_not(self) -> None:
        planted = (
            "from apps.consent.services import record_person_consent\n"
            "def a(u):\n"
            "    record_person_consent(u, consent_type='health', source='x')\n"
            "def b(u):\n"
            "    record_person_consent(u, consent_type='food_diary_processing', source='x')\n"
        )
        seen, offences = census(planted)
        assert seen == 2 and len(offences) == 1
