"""Сторож класса: id мастера уходит в каталог только через колонку (DRF-1933).

Стережётся не написание ``specialist_id=str(master.id)``, а вызываемое:
каждый вызов метода клиента Ayla (``booking_client`` / ``salon_client``),
который принимает ``specialist_id``, обязан передать
``catalog_specialist_id(...)`` — если место не названо ниже поимённо.
Перепись 15.09 по написанию дала 27 мест, по вызываемому — 30: три
вызова ``schedule_frame._load_ayla`` шли через переменную.

Два списка — долг и граница, и оба с числом вызовов на (файл, метод):

* ``PENDING_1933B`` — ещё не переведённые места второй половины. 1933b
  сужает этот список, а не вводит сторожа; новое место в нём не появится
  без правки этого файла.
* ``NOT_A_MIRROR_ROW`` — id приходит не из строки зеркала (из записи
  каталога или из ответа каталога), и переводить нечего. Причина — рядом.

Предел сторожа (назван, а не спрятан): он видит только ключевой аргумент
``specialist_id``. Методы клиентов принимают его только по ключу — это
проверено отдельным тестом, иначе позиционный вызов обошёл бы сторожа.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CLIENT_MODULES = (
    "apps/integrations/ayla/booking_client.py",
    "apps/integrations/ayla/salon_client.py",
)
RESOLVER = "catalog_specialist_id"

CONVERTED = {
    ("apps/master_api/views.py", 17),
    ("apps/master_api/services/schedule_frame.py", 3),
}

PENDING_1933B: dict[tuple[str, str], int] = {
    ("apps/miniapp_api/views.py", "get_available_times"): 1,
    ("apps/miniapp_api/views.py", "create_appointment"): 1,
    ("apps/miniapp_api/views.py", "get_specialist_service_edges"): 1,
    ("apps/admin_api/services/availability.py", "create_specialist_time_off"): 1,
    ("apps/admin_api/views_availability_slots.py", "get_available_times"): 1,
    ("apps/admin_api/views_booking_create.py", "create_appointment"): 1,
    ("apps/admin_api/views_master_exceptions.py", "list_schedule_exceptions"): 1,
    ("apps/admin_api/views_master_exceptions.py", "list_time_off"): 1,
    ("apps/admin_api/views_schedule_impact.py", "get_schedule_impact"): 1,
    ("apps/catalog/services/schedule_confirmation.py", "get_master_schedule"): 1,
    # Консьерж: ``orchestrator/handoff.py`` кладёт pk карточки в
    # ``native_master_id``, адаптер шлёт его как ``staff_id``. 1933b чинит
    # это в одном месте (handoff), и адаптер попадает в NOT_A_MIRROR_ROW.
    ("apps/skills/booking/provider.py", "get_available_dates"): 1,
    ("apps/skills/booking/provider.py", "get_available_times"): 1,
    ("apps/skills/booking/provider.py", "create_appointment"): 1,
    ("apps/skills/booking/provider.py", "get_specialist_service_edges"): 2,
}

NOT_A_MIRROR_ROW: dict[tuple[str, str], tuple[int, str]] = {
    ("apps/booking/services/records.py", "get_available_times"): (1, "id из записи каталога"),
    ("apps/booking/services/records.py", "get_masters"): (1, "id из записи каталога"),
    ("apps/booking/services/records.py", "get_specialist_service_edges"): (
        2,
        "id из записи каталога",
    ),
    ("apps/miniapp_api/views.py", "cancel_appointment"): (
        1,
        "RemoteBookingProxy.specialist_id — из каталога",
    ),
    ("apps/skills/booking/provider.py", "get_masters"): (
        1,
        "фильтр get_staff: id из ответа каталога или None",
    ),
    ("apps/skills/booking/provider.py", "cancel_appointment"): (1, "id из записи каталога"),
    ("apps/skills/booking/provider.py", "reschedule_appointment"): (1, "id из записи каталога"),
}


def _parse(rel: str) -> ast.Module:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def _client_methods() -> dict[str, bool]:
    """Имя метода → принимает ли он ``specialist_id`` только по ключу."""
    out: dict[str, bool] = {}
    for rel in CLIENT_MODULES:
        for node in ast.walk(_parse(rel)):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        positional = [a.arg for a in item.args.posonlyargs + item.args.args]
                        kwonly = [a.arg for a in item.args.kwonlyargs]
                        if "specialist_id" in positional or "specialist_id" in kwonly:
                            ok = "specialist_id" in kwonly
                            out[item.name] = out.get(item.name, True) and ok
    return out


def _sites() -> list[tuple[str, str, bool]]:
    """(файл, метод, прошёл ли через резолвер) для каждого ключевого ``specialist_id``."""
    methods = _client_methods()
    found: list[tuple[str, str, bool]] = []
    for path in sorted((ROOT / "apps").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" in rel or "/migrations/" in rel or rel in CLIENT_MODULES:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in methods:
                continue
            for kw in node.keywords:
                if kw.arg == "specialist_id":
                    value = kw.value
                    if (
                        isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Name)
                        and value.func.id == "str"
                    ):
                        value = value.args[0] if value.args else value
                    resolved = (
                        isinstance(value, ast.Call)
                        and getattr(value.func, "id", getattr(value.func, "attr", "")) == RESOLVER
                    )
                    found.append((rel, name, resolved))
    return found


def test_the_scan_sees_the_class():
    """Положительная стража: пустой скан не читается как «нарушителей нет»."""
    methods = _client_methods()
    assert len(methods) >= 30, sorted(methods)
    assert all(methods.values()), [m for m, ok in methods.items() if not ok]
    sites = _sites()
    assert len(sites) >= 43, len(sites)
    listed = sum(PENDING_1933B.values()) + sum(n for n, _ in NOT_A_MIRROR_ROW.values())
    assert listed == 23


def test_every_catalog_call_sends_the_catalog_id_or_is_named():
    sites = _sites()
    unresolved = Counter((rel, name) for rel, name, resolved in sites if not resolved)
    expected = Counter(PENDING_1933B)
    expected.update({key: n for key, (n, _reason) in NOT_A_MIRROR_ROW.items()})

    assert unresolved == expected, {
        "не через резолвер и не названы": dict(unresolved - expected),
        "названы, но уже нет (сузьте список)": dict(expected - unresolved),
    }
    converted = Counter(rel for rel, _name, resolved in sites if resolved)
    assert set(converted.items()) == CONVERTED, dict(converted)


def test_named_sites_are_real_files():
    for rel, _name in list(PENDING_1933B) + list(NOT_A_MIRROR_ROW):
        assert (ROOT / rel).is_file(), rel
