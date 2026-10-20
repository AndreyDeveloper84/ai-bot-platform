"""DRF-2984 B2 — перепись дверей, через которые у тенанта появляется активный мастер.

Каждая функция продуктового кода, где ``CatalogMaster`` создаётся, принимается
(``invite_status = …ACCEPTED``), возвращается из архива (``archived_at = None``)
или включается (``is_active = True`` / ``= body[…]``), обязана либо сама звать
гейт подписки (:mod:`apps.tenancy.billing_gate`), либо стоять в
:data:`EXEMPT` с причиной. Новая дверь без гейта и без записи — красная;
запись о функции, которой больше нет, — тоже.

Скан — по строкам файлов, где упомянут ``CatalogMaster``; тесты, миграции и
management-команды вне переписи (сиды и тестовые приглашения — не продукт).
"""

from __future__ import annotations

import ast

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

CREATE_METHODS = frozenset({"create", "update_or_create", "get_or_create", "bulk_create"})
#: Получатели вызова создания: модель напрямую и обобщённый ``model_cls`` вебхука.
CREATE_RECEIVERS = frozenset({"CatalogMaster", "model_cls"})


def _activating_value(field: str, value: ast.expr) -> bool:
    """Значение, которое делает мастера принятым / вернувшимся / включённым."""

    if field == "invite_status":
        return isinstance(value, ast.Attribute) and value.attr == "ACCEPTED"
    if field == "archived_at":
        return isinstance(value, ast.Constant) and value.value is None
    if field == "is_active":
        return (isinstance(value, ast.Constant) and value.value is True) or (
            isinstance(value, ast.Subscript)
            and isinstance(value.value, ast.Name)
            and value.value.id == "body"
        )
    return False


def _is_write(node: ast.AST) -> bool:
    """Запись состояния мастера — не выборка: присваивание атрибута,
    ``.update(…)`` с тем же значением или создание строки."""

    if isinstance(node, ast.Assign):
        return any(
            isinstance(t, ast.Attribute) and _activating_value(t.attr, node.value)
            for t in node.targets
        )
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        method = node.func.attr
        if method == "update":
            return any(
                kw.arg is not None and _activating_value(kw.arg, kw.value) for kw in node.keywords
            )
        if method in CREATE_METHODS:
            manager = node.func.value
            return (
                isinstance(manager, ast.Attribute)
                and manager.attr in ("objects", "all_tenants")
                and isinstance(manager.value, ast.Name)
                and manager.value.id in CREATE_RECEIVERS
            )
    return False


GATE_CALLS = ("assert_can_add_master(", "master_add_verdict(")

#: ``путь::функция`` → почему гейта в ней нет.
EXEMPT: dict[str, str] = {
    "apps/catalog/services/upserter.py::upsert_specialists": (
        "зеркало синхронизации каталог → бот: отражает каталог, мастера не добавляет"
    ),
    "apps/catalog/webhooks/tasks.py::_handle_upsert": (
        "зеркало вебхука каталога: строка по external_id, без принятия и активации"
    ),
    "apps/identity/services/solo_onboarding.py::create_solo_provider": (
        "создаёт САМ тенант соло вместе с единственным мастером: подписки ещё нет"
    ),
    "apps/master_api/views.py::_land_on_glue_row": (
        "зовётся только из onboarding_accept после гейта (master_id=glue.id)"
    ),
}


def _hits() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in sorted((ROOT / "apps").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" in rel or "/migrations/" in rel or "/management/" in rel:
            continue
        src = path.read_text(encoding="utf-8")
        if "CatalogMaster" not in src:
            continue
        tree = ast.parse(src)
        funcs = [
            n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for write in (n for n in ast.walk(tree) if _is_write(n)):
            lineno = getattr(write, "lineno", 0)
            enclosing = [f for f in funcs if f.lineno <= lineno <= (f.end_lineno or f.lineno)]
            name = (
                min(enclosing, key=lambda f: (f.end_lineno or f.lineno) - f.lineno).name
                if enclosing
                else "<module>"
            )
            found.setdefault(f"{rel}::{name}", []).append(lineno)
    return found


def _source_of(key: str) -> str:
    rel, name = key.split("::")
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment((ROOT / rel).read_text(encoding="utf-8"), node) or ""
    return ""


def test_d1_the_scan_sees_the_known_doors():
    """Присутствие первым: скан, не нашедший ни одной двери, проверял бы пустоту."""

    hits = _hits()

    for door in (
        "apps/identity/services/staff_invites.py::link_master_to_person",
        "apps/master_api/views.py::onboarding_accept",
        "apps/admin_api/services/master_deactivation.py::reactivate_master",
        "apps/catalog/services/verification.py::_verify",
        "apps/admin_api/views.py::_master_update",
        "apps/catalog/admin.py::unarchive_masters",
        "apps/admin_api/views_invite.py::master_invite_create",
    ):
        assert door in hits, door


def test_d2_every_door_is_gated_or_exempt_with_a_reason():
    ungated = [
        f"{key} (строки {lines})"
        for key, lines in sorted(_hits().items())
        if key not in EXEMPT and not any(call in _source_of(key) for call in GATE_CALLS)
    ]

    # empty-assert-ok: двери есть — test_d1 требует их присутствия первым
    assert ungated == [], (
        "Дверь, через которую тенант получает активного мастера, без гейта подписки. "
        "Позовите apps.tenancy.billing_gate.assert_can_add_master или внесите в EXEMPT "
        "с причиной:\n" + "\n".join(ungated)
    )


def test_d3_no_stale_exemptions():
    stale = sorted(set(EXEMPT) - set(_hits()))

    # empty-assert-ok: EXEMPT непуст и сверяется с найденными дверями
    assert stale == [], f"EXEMPT называет функции, которых скан больше не находит: {stale}"
