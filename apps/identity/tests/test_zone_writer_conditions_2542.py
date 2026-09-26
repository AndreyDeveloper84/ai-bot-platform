"""DRF-2542 — у жёлтой и красной зоны нет писателя, пока условия не закрыты.

Сегодня жёлтых и красных строк не бывает: ``_check_minor_protection`` всегда
бросает (ручки DOB нет, #597), вызывающий превращает это в тихий пропуск и
строку аудита. Это корректный отказ по умолчанию. Семь условий листа обязаны
быть закрыты ДО того, как заглушка уйдёт; сторож держит порядок:

* **писатель определяется по поведению**, а не по имени функции: проба зовёт
  оба санкционированных пути к зонам — ``write_entry`` и ``promote_zone`` — на
  настоящей строке UPC без ``minor_lock`` и смотрит, появилась ли в базе строка
  жёлтой или красной зоны. Заглушка может уйти вместе с переименованием —
  проба этого не заметит, и это правильно: она спрашивает не «есть ли
  заглушка», а «пишется ли зона»;
* **писатель есть → все условия закрыты**, иначе красно, и в сообщении названы
  ВСЕ незакрытые с адресом в листе;
* **писателя нет → узел не пропускается**, а печатает, чем это установлено.
"""

from __future__ import annotations

import ast
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.identity.models import MemoryEntry, RedZoneAccessLog, UserPersonalContext
from apps.identity.services import memory_writer
from apps.identity.services.exceptions import MinorProtectionLookupFailed
from apps.identity.services.memory_writer import (
    ZONE_WRITER_CONDITIONS,
    ZONE_WRITER_CONDITIONS_CLOSED,
    promote_zone,
    write_entry,
)

pytestmark = pytest.mark.django_db

_ZONES = (MemoryEntry.SENSITIVITY_YELLOW, MemoryEntry.SENSITIVITY_RED)


@dataclass
class WriterProbe:
    """Что показали оба пути к жёлтой и красной зоне на настоящей строке UPC."""

    outcomes: list[str] = field(default_factory=list)
    zone_rows: int = 0
    rejected_audit_rows: int = 0

    @property
    def writer_exists(self) -> bool:
        return self.zone_rows > 0

    def evidence(self) -> str:
        return (
            "; ".join(self.outcomes)
            + f"; строк жёлтой/красной зоны: {self.zone_rows}"
            + f"; аудит write_rejected_dob_lookup +{self.rejected_audit_rows}"
        )


def _probe() -> WriterProbe:
    """Позвать оба пути к зонам и посчитать, что легло в базу."""
    upc = UserPersonalContext.objects.create(user_id=uuid.uuid4())
    assert upc.minor_lock is False  # замок не мешает пробе: спрашиваем возраст, не замок
    audit_before = RedZoneAccessLog.objects.filter(
        access_type=RedZoneAccessLog.ACCESS_WRITE_REJECTED_DOB
    ).count()
    probe = WriterProbe()

    for zone in _ZONES:
        result = write_entry(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=zone,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="lifestyle",
            content={"probe": "DRF-2542"},
            request_id=uuid.uuid4(),
            purpose="DRF-2542: проба писателя зоны",
            consent_at=timezone.now(),
        )
        probe.outcomes.append(f"write_entry({zone}) → {'строка' if result else 'None'}")

    for zone in _ZONES:
        green = write_entry(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="lifestyle",
            content={"probe": "DRF-2542 green"},
            request_id=uuid.uuid4(),
            purpose="DRF-2542: зелёная строка для повышения",
        )
        assert green is not None, "зелёная запись не прошла — проба повышения мерила бы не то"
        try:
            promote_zone(
                entry=green,
                new_zone=zone,
                consent_token="drf-2542-probe",
                request_id=uuid.uuid4(),
                purpose="DRF-2542: проба повышения зоны",
            )
            probe.outcomes.append(f"promote_zone(green→{zone}) → повышено")
        except MinorProtectionLookupFailed:
            probe.outcomes.append(f"promote_zone(green→{zone}) → MinorProtectionLookupFailed")

    probe.zone_rows = MemoryEntry.objects.filter(
        user_id=upc.user_id, sensitivity_zone__in=_ZONES
    ).count()
    probe.rejected_audit_rows = (
        RedZoneAccessLog.objects.filter(
            access_type=RedZoneAccessLog.ACCESS_WRITE_REJECTED_DOB
        ).count()
        - audit_before
    )
    return probe


def _open_conditions(closed: frozenset[str]) -> list[str]:
    """Незакрытые условия — ВСЕ, в порядке записи, с адресом в листе."""
    return [
        f"{name}: {address}"
        for name, address in ZONE_WRITER_CONDITIONS.items()
        if name not in closed
    ]


def _guard(closed: frozenset[str] = ZONE_WRITER_CONDITIONS_CLOSED) -> str:
    """Сторож. Возвращает свидетельство, на котором он стоит."""
    unknown = sorted(closed - ZONE_WRITER_CONDITIONS.keys())
    assert not unknown, f"в закрытых есть имена, которых нет в условиях: {unknown}"

    probe = _probe()
    if probe.writer_exists:
        still_open = _open_conditions(closed)
        assert not still_open, (
            "DRF-2542: у жёлтой/красной зоны появился писатель "
            f"({probe.evidence()}), а условия не закрыты — заглушку #597 сняли "
            "раньше сторожа:\n  " + "\n  ".join(still_open)
        )
        return probe.evidence()

    # Писателя нет — доказать это, а не пропустить узел: все четыре пути отказали,
    # и каждый отказ оставил судебную строку.
    assert probe.zone_rows == 0
    assert probe.rejected_audit_rows == 4, probe.evidence()
    return probe.evidence()


class TestGuardOnTheRealTree:
    def test_no_zone_writer_or_every_condition_closed(self) -> None:
        evidence = _guard()
        print("DRF-2542:", evidence)


class TestRecord:
    def test_conditions_are_seven_and_addressed(self) -> None:
        # Запись машинно-читаема и полна: семь условий листа, у каждого адрес.
        assert len(ZONE_WRITER_CONDITIONS) == 7
        assert all(address.startswith("DRF-2542 §") for address in ZONE_WRITER_CONDITIONS.values())

    def test_closed_is_empty_by_construction(self) -> None:
        # Наличие впереди: условий семь — пустое множество закрытых значит «ничего
        # не закрыто», а не «нечего закрывать».
        assert len(ZONE_WRITER_CONDITIONS) == 7
        assert len(ZONE_WRITER_CONDITIONS_CLOSED) == 0


def _adult():
    """Подмена #597: проверка возраста пропускает — у зон появляется писатель."""
    return patch.object(memory_writer, "_check_minor_protection", return_value=None)


class TestSubstitution:
    def test_writer_appears_and_all_open_conditions_are_named(self) -> None:
        with _adult(), pytest.raises(AssertionError) as caught:
            _guard()
        message = str(caught.value)
        for name, address in ZONE_WRITER_CONDITIONS.items():
            assert f"{name}: {address}" in message, name
        # Все четыре пути написали зону — проба действительно видит писателя.
        assert "строк жёлтой/красной зоны: 4" in message

    def test_one_open_condition_is_named_alone(self) -> None:
        closed = frozenset(ZONE_WRITER_CONDITIONS) - {"ttl_purge_sweep"}
        with _adult(), pytest.raises(AssertionError) as caught:
            _guard(closed)
        message = str(caught.value)
        assert "ttl_purge_sweep: DRF-2542 §4" in message
        assert "consent_check_in_write_entry" not in message

    def test_all_closed_lets_the_writer_in(self) -> None:
        # Положительная пара: когда всё закрыто, писатель законен — иначе снять
        # заглушку было бы невозможно никогда.
        with _adult():
            evidence = _guard(frozenset(ZONE_WRITER_CONDITIONS))
        assert "строк жёлтой/красной зоны: 4" in evidence

    def test_unknown_closed_name_is_red(self) -> None:
        with pytest.raises(AssertionError, match="нет в условиях"):
            _guard(frozenset({"ttl_purge_swep"}))


# ─── перепись по исходнику: писатель в обход memory_writer ──────────────────
#
# Проба видит только санкционированные пути. Самый вероятный обход — прямая
# запись ``MemoryEntry`` в другом модуле: для пробы её нет вовсе. Поэтому вне
# ``memory_writer.py`` (и вне тестов/миграций) прямой записи ``MemoryEntry`` не
# должно быть НИКАКОЙ — зону по исходнику статически не узнать, а сегодня таких
# мест ноль. Нечитаемый файл — отдельный исход, а не «чисто» (DRF-2538).

_ROOT = Path(__file__).resolve().parents[3]
_WRITER = Path("apps/identity/services/memory_writer.py")
_WRITE_METHODS = frozenset({"create", "bulk_create", "get_or_create", "update_or_create"})


@dataclass
class Census:
    files: int = 0
    sites: list[str] = field(default_factory=list)  # "путь:строка вид"
    unreadable: list[str] = field(default_factory=list)


def _rooted_at_memory_entry(node: ast.AST) -> bool:
    while isinstance(node, (ast.Attribute, ast.Call)):
        node = node.value if isinstance(node, ast.Attribute) else node.func
    return isinstance(node, ast.Name) and node.id == "MemoryEntry"


def _sites(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "MemoryEntry":
                found.append((node.lineno, "MemoryEntry(...)"))
            elif isinstance(func, ast.Attribute):
                if func.attr in _WRITE_METHODS and _rooted_at_memory_entry(func.value):
                    found.append((node.lineno, f"MemoryEntry….{func.attr}"))
                elif func.attr == "update" and any(
                    k.arg == "sensitivity_zone" for k in node.keywords
                ):
                    found.append((node.lineno, ".update(sensitivity_zone=…)"))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "sensitivity_zone":
                    found.append((node.lineno, ".sensitivity_zone = …"))
    return sorted(found)


def _scan(sources: dict[str, str]) -> Census:
    census = Census()
    for path, text in sorted(sources.items()):
        census.files += 1
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            census.unreadable.append(f"{path}:{exc.lineno}")
            continue
        census.sites.extend(f"{path}:{line} {kind}" for line, kind in _sites(tree))
    return census


def _production_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
    for top in ("apps", "config"):
        for path in (_ROOT / top).rglob("*.py"):
            rel = path.relative_to(_ROOT)
            if "tests" in rel.parts or "migrations" in rel.parts:
                continue
            sources[rel.as_posix()] = path.read_text(encoding="utf-8")
    return sources


def _bypass(census: Census) -> list[str]:
    return [site for site in census.sites if not site.startswith(_WRITER.as_posix() + ":")]


class TestCensusBySource:
    def test_no_direct_memory_entry_write_outside_the_writer(self) -> None:
        census = _scan(_production_sources())
        print(f"DRF-2542 перепись: файлов {census.files}, мест записи {len(census.sites)}")
        # Охват не пуст и обход видит известное: писатель пишет ровно в трёх местах.
        assert census.files > 500, census.files
        assert not census.unreadable, (
            f"нечитаемые файлы — перепись по ним не сделана: {census.unreadable}"
        )
        in_writer = [s for s in census.sites if s.startswith(_WRITER.as_posix() + ":")]
        assert [s.split(" ", 1)[1] for s in in_writer] == [
            "MemoryEntry….create",
            ".sensitivity_zone = …",
            ".sensitivity_zone = …",
        ], in_writer
        bypass = _bypass(census)
        assert not bypass, (
            "DRF-2542: прямая запись MemoryEntry в обход memory_writer — проба писателя "
            "зоны её не видит:\n  " + "\n  ".join(bypass)
        )

    def test_walker_sees_known_writes_in_tests(self) -> None:
        # Положительный контроль вне писателя: в тестах прямые create есть — обход
        # обязан их находить, иначе «обхода нет» было бы правдой и о слепом обходе.
        tests_dir = _ROOT / "apps" / "identity" / "tests"
        sources = {
            p.relative_to(_ROOT).as_posix(): p.read_text(encoding="utf-8")
            for p in tests_dir.glob("test_*.py")
        }
        census = _scan(sources)
        assert census.files > 10
        assert any("MemoryEntry….create" in s for s in census.sites), census.files

    def test_substitution_bypass_is_named_with_file_and_line(self) -> None:
        planted = (
            "from apps.identity.models import MemoryEntry\n"
            "\n"
            "def leak(upc):\n"
            "    return MemoryEntry.objects.create(sensitivity_zone='yellow', personal_context=upc)\n"
        )
        census = _scan({_WRITER.as_posix(): "x = 1\n", "apps/orchestrator/leak.py": planted})
        assert _bypass(census) == ["apps/orchestrator/leak.py:4 MemoryEntry….create"]

    def test_unreadable_file_is_its_own_outcome(self) -> None:
        census = _scan({"apps/orchestrator/broken.py": "def (:\n"})
        assert census.files == 1
        assert census.unreadable == ["apps/orchestrator/broken.py:1"]
        assert (
            census.sites == []
        )  # empty-assert-ok: файл нечитаем — строкой выше это отдельный исход
