"""Ярус разговора не ставит никто — и это записано (DRF-2557).

``Conversation.tier`` (``AI_CONTINUITY`` / ``HUMAN_SUPERVISED`` / ``HUMAN_LOCKED``)
и всё, что на нём висит, — поля ``tier_locked_at`` / ``tier_locked_by_master``,
событие ``conversation.tier_promoted_to_human_locked``, права
``promote/demote_human_locked``, настройка мастера «Срочно (HUMAN_LOCKED)» с
``CheckConstraint`` — остались в коде, а писателя нет. Три значения, три
разных вердикта:

* ``HUMAN_LOCKED`` — **потерянный писатель, снятый намеренно.**
  ``promote_to_human_locked`` появился 21.05 (6b92517c, M6.1) и снят 21.09
  (b97f6b7e, DRF-1528) вместе с перепиской мастер↔клиент по решению
  владельца 06.09 (OD-7). Восстановить его значит отменить это решение.
* ``HUMAN_SUPERVISED`` — **нерождённый**: не присваивался ни в одном коммите,
  в модели сам назван placeholder.
* ``AI_CONTINUITY`` — только ``default`` поля.

Узел держит это как факт: присваиваний яруса и отправок события в боевом коде
нет. Появится писатель — узел краснеет и называет место: включать механизм,
вокруг которого права, событие и обещание мастеру, — решение, а не правка.

Права и констрейнт не тронуты: они не лгут о том, что состояние ставится, —
лжёт только отсутствие писателя, и его охраняет этот узел.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

_TIER_VALUES = {"AI_CONTINUITY", "HUMAN_SUPERVISED", "HUMAN_LOCKED"}
_TIER_LITERALS = {"ai_continuity", "human_supervised", "human_locked"}
_LOCK_FIELDS = {"tier_locked_at", "tier_locked_by_master"}
_EVENT = "CONVERSATION_TIER_PROMOTED_TO_HUMAN_LOCKED"


def _is_tier_value(node: ast.AST) -> bool:
    """``Conversation.Tier.X`` / ``Tier.X`` / строковый литерал значения."""
    if isinstance(node, ast.Attribute) and node.attr in _TIER_VALUES:
        return "Tier" in ast.unparse(node.value)
    return isinstance(node, ast.Constant) and node.value in _TIER_LITERALS


def _writes_in(tree: ast.AST, rel: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and (
                    target.attr in _LOCK_FIELDS
                    or (target.attr == "tier" and _is_tier_value(node.value))
                ):
                    found.append(f"{rel}:{node.lineno} .{target.attr} =")
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in _LOCK_FIELDS or (kw.arg == "tier" and _is_tier_value(kw.value)):
                    found.append(f"{rel}:{node.lineno} {kw.arg}=")
        if isinstance(node, ast.Name) and node.id == _EVENT:
            found.append(f"{rel}:{node.lineno} {_EVENT}")
    return found


def _census() -> tuple[int, list[str], int]:
    scanned = 0
    writes: list[str] = []
    enum_refs = 0
    for path in (REPO / "apps").rglob("*.py"):
        rel = path.relative_to(REPO).as_posix()
        if "/tests/" in rel or "/migrations/" in rel or path.name.startswith("test_"):
            continue
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if rel == "apps/events/vocabulary.py":
            # Словарь событий объявляет имя — это не отправка.
            continue
        writes.extend(_writes_in(tree, rel))
        if rel == "apps/conversations/models.py":
            # Значения, объявленные в ``class Tier`` модели разговора.
            for cls in ast.walk(tree):
                if isinstance(cls, ast.ClassDef) and cls.name == "Tier":
                    enum_refs += sum(
                        1
                        for stmt in cls.body
                        if isinstance(stmt, ast.Assign)
                        for t in stmt.targets
                        if isinstance(t, ast.Name) and t.id in _TIER_VALUES
                    )
    return scanned, writes, enum_refs


def test_the_census_finds_a_writer_when_there_is_one():
    """Калибровка: тот же обход находит все четыре формы записи."""
    snippet = (
        "conv.tier = Conversation.Tier.HUMAN_LOCKED\n"
        "Conversation.objects.filter(pk=1).update(tier=Conversation.Tier.HUMAN_LOCKED, tier_locked_at=now)\n"
        "emit(CONVERSATION_TIER_PROMOTED_TO_HUMAN_LOCKED, properties={})\n"
        "x.update(tier='human_supervised')\n"
        # Чужой ярус с тем же именем поля — НЕ запись яруса разговора.
        "LoyaltyAccount.objects.exclude(tier=LoyaltyAccount.Tier.STARTER)\n"
    )
    found = sorted(_writes_in(ast.parse(snippet), "calibration.py"))

    assert found == [
        "calibration.py:1 .tier =",
        "calibration.py:2 tier=",
        "calibration.py:2 tier_locked_at=",
        "calibration.py:3 CONVERSATION_TIER_PROMOTED_TO_HUMAN_LOCKED",
        "calibration.py:4 tier=",
    ], found


def test_nobody_sets_the_conversation_tier():
    scanned, writes, enum_refs = _census()

    assert scanned > 300, f"просмотрено {scanned} файлов — перепись не туда смотрит"
    # Обход видит само перечисление в модели: ноль ниже — не слепота к Tier.
    assert enum_refs == 3, f"в class Tier модели найдено значений: {enum_refs}"
    assert len(writes) == 0, (
        f"просмотрено {scanned} файлов; ярус разговора снова кто-то ставит: {writes}. "
        "Писатель снят решением владельца (DRF-1528, OD-7) — включение механизма "
        "с правами, событием и обещанием мастеру требует решения, см. DRF-2557"
    )
