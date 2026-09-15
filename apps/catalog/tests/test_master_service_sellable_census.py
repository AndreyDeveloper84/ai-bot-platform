"""Census-сторож: читатель ребра на пути продажи — через предикат (DRF-1964a).

До DRF-1964a у ``MasterService`` не было статусной колонки намеренно:
«строка есть = предлагается», и ни один читатель статус не фильтровал.
Колонка ``sellable`` делает это обещание ложным для каждого читателя, который
про неё не знает. Поэтому колонка приходит только вместе с этим сторожем.

Каждое упоминание зеркала рёбер в ``apps/`` (не тесты, не миграции) имеет
запись «файл → (число упоминаний, класс, причина)»:

* ``SELLABLE`` — путь продажи; файл обязан звать предикат
  (``.sellable()`` или ``sellable_edge_q(...)``);
* ``PENDING_1989`` — путь продажи второй половины (DRF-1989); причина рядом;
* ``NOT_SALE_PATH`` — писатели, провенанс, операторские экраны, служебное.

Новый файл-читатель или изменившееся число — красный тест: место надо
классифицировать. Сырое ``sellable=`` в фильтре вне ``apps/catalog/models.py``
— тоже красный: предикат один.

Упоминанием считается: ``MasterService.objects`` / ``MasterService.all_tenants``;
ключевой аргумент, начинающийся с ``services_offered`` / ``masters_offering``;
строка, начинающаяся с ``services_offered__`` / ``masters_offering__`` (кроме
аргумента самого ``sellable_edge_q``).

**Названный предел.** Сторож видит написание модели и связи в файле.
Queryset, собранный в другом файле и пришедший сюда переменной, не виден;
и требование предиката — пофайловое: файл, где предикат зовётся хотя бы раз,
проходит, даже если второй его читатель предикат не зовёт. Это закрывают
поведенческие тесты поверхностей (``test_*_sellable_1988.py``), не этот сторож.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APPS = ROOT / "apps"

LOOKUP_PREFIXES = ("services_offered", "masters_offering")
STRING_PREFIXES = tuple(f"{p}__" for p in LOOKUP_PREFIXES)
PREDICATE_NAMES = {"sellable", "sellable_edge_q"}
PREDICATE_HOME = "apps/catalog/models.py"

SELLABLE = "SELLABLE"
PENDING_1989 = "PENDING_1989"
NOT_SALE_PATH = "NOT_SALE_PATH"

#: Перепись бот ``dev`` ``8bf99f7c`` (15.09.2026): 14 файлов, 41 упоминание.
EXPECTED: dict[str, tuple[int, str, str]] = {
    # ── путь продажи, DRF-1964a ──
    "apps/marketplace/discovery.py": (
        12,
        SELLABLE,
        "поиск мастеров по услуге, мастера услуги, витрина",
    ),
    "apps/orchestrator/handoff.py": (
        4,
        SELLABLE,
        "тап по услуге, «не выполняет», меню услуг мастера",
    ),
    "apps/miniapp_api/views.py": (
        4,
        SELLABLE,
        "слоты, is_bookable витрины, подборщик, карточка мастера",
    ),
    # ── путь продажи, вторая половина DRF-1989 ──
    "apps/skills/booking/skill.py": (
        1,
        PENDING_1989,
        "ворота здоровья навыка записи — причина вместо «консультации»",
    ),
    "apps/booking/services/create.py": (1, PENDING_1989, "локальная запись — маппинг отказа"),
    "apps/booking/services/transitions.py": (1, PENDING_1989, "локальный перенос — маппинг отказа"),
    "apps/master_api/services/catalog.py": (
        1,
        PENDING_1989,
        "кабинет мастера — показ причины, не скрытие",
    ),
    "apps/master_api/views.py": (1, PENDING_1989, "кабинет мастера — показ причины, не скрытие"),
    "apps/admin_api/views.py": (
        1,
        PENDING_1989,
        "салонная админка, услуги мастера — показ причины",
    ),
    # ── не путь продажи ──
    "apps/admin_api/views_services_mapping.py": (
        6,
        NOT_SALE_PATH,
        "матрица MM4 — оператор пишет строки",
    ),
    "apps/admin_api/views_invite.py": (2, NOT_SALE_PATH, "сидер приглашения — писатель"),
    "apps/admin_api/services/master_deactivation.py": (
        2,
        NOT_SALE_PATH,
        "переназначение при уходе мастера — оператор",
    ),
    "apps/catalog/management/commands/cleanup_orphan_master_services.py": (
        4,
        NOT_SALE_PATH,
        "ремонт операторских строк",
    ),
    "apps/catalog/management/commands/seed_dev_formula_tela.py": (1, NOT_SALE_PATH, "dev-фикстура"),
}


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def scan(source: str) -> tuple[int, bool, int]:
    """(упоминаний зеркала, зовёт ли предикат, сырых ``sellable=`` в фильтрах)."""
    tree = ast.parse(source)
    predicate_args: set[int] = set()
    uses_predicate = False
    raw_sellable = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in PREDICATE_NAMES:
                uses_predicate = True
                predicate_args.update(id(a) for a in node.args)
            if name in {"filter", "exclude", "get", "update"}:
                raw_sellable += sum(1 for k in node.keywords if k.arg == "sellable")
    refs = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in {"objects", "all_tenants"}
            and isinstance(node.value, ast.Name)
            and node.value.id == "MasterService"
        ):
            refs += 1
        elif isinstance(node, ast.keyword) and node.arg and node.arg.startswith(LOOKUP_PREFIXES):
            refs += 1
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith(STRING_PREFIXES)
            and id(node) not in predicate_args
        ):
            refs += 1
    return refs, uses_predicate, raw_sellable


def violations(sources: dict[str, str], expected: dict[str, tuple[int, str, str]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for rel, source in sorted(sources.items()):
        refs, uses_predicate, raw_sellable = scan(source)
        if raw_sellable and rel != PREDICATE_HOME:
            out.append(f"{rel}: сырое sellable= в фильтре ({raw_sellable}) — только через предикат")
        if not refs:
            continue
        seen.add(rel)
        if rel not in expected:
            out.append(f"{rel}: {refs} упоминаний зеркала рёбер, файл не классифицирован")
            continue
        count, kind, _reason = expected[rel]
        if refs != count:
            out.append(f"{rel}: упоминаний {refs}, в переписи {count}")
        if kind == SELLABLE and not uses_predicate:
            out.append(f"{rel}: путь продажи без предиката sellable")
    for rel in sorted(set(expected) - seen):
        out.append(f"{rel}: в переписи, но упоминаний нет — убрать запись")
    return out


def _sources() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in APPS.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" in rel or "/migrations/" in rel or path.name.startswith("test_"):
            continue
        found[rel] = path.read_text(encoding="utf-8")
    return found


def test_every_mirror_reader_is_classified_and_sale_path_uses_the_predicate():
    sources = _sources()
    assert len(sources) > 100  # скан не пуст
    assert violations(sources, EXPECTED) == []


def test_guard_catches_a_sale_path_reader_without_the_predicate():
    """Положительная стража сторожа: синтетический читатель без предиката — пойман."""
    raw = "from apps.catalog.models import MasterService\nMasterService.objects.filter(master_id=1).exists()\n"
    fixed = (
        "from apps.catalog.models import MasterService\n"
        "MasterService.objects.filter(master_id=1).sellable().exists()\n"
    )
    expected = {"apps/x/views.py": (1, SELLABLE, "синтетика")}

    assert violations({"apps/x/views.py": raw}, expected) == [
        "apps/x/views.py: путь продажи без предиката sellable"
    ]
    assert violations({"apps/x/views.py": fixed}, expected) == []
    assert violations({"apps/x/views.py": fixed}, {}) == [
        "apps/x/views.py: 1 упоминаний зеркала рёбер, файл не классифицирован"
    ]
