"""DRF-2595 — пояс салона: одно правило, и сторож, чтобы оно им осталось.

До сведения правило было записано тринадцать раз: одиннадцать давали Москву,
одно — UTC, одно падало, и лишь два писали в журнал о битом поясе. Каждая
новая поверхность заводила новую запись и выбирала запасной пояс заново.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfoNotFoundError

import pytest

from apps.tenancy.timezones import FALLBACK_TZ, salon_iso, salon_zone

_ROOT = Path(__file__).resolve().parents[3]
_HOME = "apps/tenancy/timezones.py"


def _tenant(tz: str | None) -> SimpleNamespace:
    return SimpleNamespace(pk=2595, timezone=tz)


class TestTheRule:
    def test_a_real_zone_is_honoured(self) -> None:
        assert str(salon_zone(_tenant("Asia/Yekaterinburg"))) == "Asia/Yekaterinburg"

    def test_empty_and_missing_fall_back_to_the_named_zone_silently(self, caplog) -> None:
        with caplog.at_level("WARNING"):
            zones = {
                str(salon_zone(_tenant(""))),
                str(salon_zone(_tenant(None))),
                str(salon_zone(None)),
            }
        assert zones == {FALLBACK_TZ} == {"Europe/Moscow"}
        assert [
            r for r in caplog.records if "bad_tenant_tz" in r.getMessage()
        ] == []  # empty-assert-ok: три зоны выше — запасная; пусто не битое

    def test_a_broken_zone_falls_back_and_says_so(self, caplog) -> None:
        with caplog.at_level("WARNING"):
            zone = salon_zone(_tenant("Not/AZone"))
        assert str(zone) == "Europe/Moscow"
        assert any("tenancy.bad_tenant_tz" in r.getMessage() for r in caplog.records)

    def test_strict_raises_on_a_broken_zone_after_the_same_log_line(self, caplog) -> None:
        with caplog.at_level("WARNING"), pytest.raises(ZoneInfoNotFoundError):
            salon_zone(_tenant("Not/AZone"), strict=True)
        assert any("tenancy.bad_tenant_tz" in r.getMessage() for r in caplog.records)

    def test_strict_keeps_the_fallback_for_an_unset_zone(self) -> None:
        # strict — про БИТОЕ имя; незаданный пояс — default поля, не ошибка.
        assert str(salon_zone(_tenant(""), strict=True)) == "Europe/Moscow"


class TestSalonIso:
    def test_the_moment_is_kept_and_only_written_in_the_salon_zone(self) -> None:
        from datetime import datetime, timezone

        zone = salon_zone(_tenant("Asia/Yekaterinburg"))
        aware = datetime(2026, 9, 30, 4, 0, tzinfo=timezone.utc)
        assert salon_iso(aware, zone) == "2026-09-30T09:00:00+05:00"
        naive = datetime(2026, 9, 30, 9, 0)
        assert salon_iso(naive, zone) == "2026-09-30T09:00:00+05:00"
        assert salon_iso(None, zone) is None


# ─── сторож: одно определение ───────────────────────────────────────────────

#: Места, где ``ZoneInfo`` строится из ``.timezone`` тенанта мимо помощника, —
#: поимённо, с причиной. Пусто с части Б DRF-2595: последние четыре встроенных
#: места (создание записи, слоты, главная клиента, готовность мастера)
#: переведены на ``salon_zone``. Храповик в обе стороны: новое место — красно.
#: Что сканер вообще видит такие места, держит узел с посаженным правилом.
KNOWN_OUTSIDE: dict[tuple[str, str], str] = {}


def _reads_tenant_timezone(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr == "timezone":
            return True
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Name)
            and sub.func.id == "getattr"
            and any(isinstance(a, ast.Constant) and a.value == "timezone" for a in sub.args)
        ):
            return True
    return False


def _census(sources: dict[str, str]) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path, text in sources.items():
        tree = ast.parse(text)

        def visit(node: ast.AST, function: str, path: str = path) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = node.name
            if isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
                if name == "ZoneInfo" and any(_reads_tenant_timezone(a) for a in node.args):
                    found.add((path, function))
            for child in ast.iter_child_nodes(node):
                visit(child, function)

        visit(tree, "<module>")
    return found


def _production_sources() -> dict[str, str]:
    out: dict[str, str] = {}
    for top in ("apps", "config"):
        for path in (_ROOT / top).rglob("*.py"):
            rel = path.relative_to(_ROOT)
            if "tests" in rel.parts or "migrations" in rel.parts or rel.as_posix() == _HOME:
                continue
            out[rel.as_posix()] = path.read_text(encoding="utf-8")
    return out


class TestOneDefinition:
    def test_no_new_place_builds_the_salon_zone_by_itself(self) -> None:
        found = _census(_production_sources())
        assert len(found) == len(KNOWN_OUTSIDE)
        assert (
            found - set(KNOWN_OUTSIDE) == set()
        ), (  # empty-assert-ok: число мест утверждено строкой выше
            "новое место строит пояс салона мимо apps.tenancy.timezones.salon_zone — "
            "четырнадцатое правило; позовите помощник"
        )

    def test_the_known_list_has_no_stale_entries(self) -> None:
        found = _census(_production_sources())
        assert set(KNOWN_OUTSIDE) - found == set()  # empty-assert-ok: равенство чисел — узлом выше

    def test_a_planted_rule_is_caught_by_name(self) -> None:
        planted = (
            "from zoneinfo import ZoneInfo\n"
            "def _tz(tenant):\n"
            "    return ZoneInfo(getattr(tenant, 'timezone', '') or 'UTC')\n"
        )
        assert _census({"apps/skills/new/salon.py": planted}) == {
            ("apps/skills/new/salon.py", "_tz")
        }
