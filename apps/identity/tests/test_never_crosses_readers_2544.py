"""DRF-2544 — ключ, который не переходит между салонами, не может молча попасть к салону.

Решение владельца 24.08 (``docs/OD_MEMORY.md`` §3): любимые мастера и
наблюдения салона между салонами НЕ переходят; механизм — ``source_tenant_id``,
проверяемый при чтении. Список этих ключей уже лежит машинно-читаемо —
``apps.identity.personal_fields.NEVER_CROSSES``.

Предикат при чтении **не строится**: сегодня нет ни одного читателя, который
собирает память ДЛЯ салона. Всё, что кладёт личную память в подсказку модели,
стоит на глобальной поверхности (глобальный обработчик и консьерж); остальные
чтения — своя память человека (экран, команды, выгрузка), путь записи или чтение
по конкретному ключу, куда ``favorite_masters`` не входит.

Сторож держит день рождения читателя: каждое место вызова читателей памяти вне
тестов и миграций известно поимённо — файл, объемлющая функция, вызываемый — с
причиной, почему оно не салонное. Новое место → красно, и в сообщении названы
ключи ``NEVER_CROSSES``, которые потекут через него без предиката.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from apps.identity.personal_fields import NEVER_CROSSES

_ROOT = Path(__file__).resolve().parents[3]

#: Функции, отдающие зелёную память (или её отрисовку в подсказку).
_READERS = frozenset(
    {
        "read_green_entries",
        "read_current_view",
        "read_personal_context",
        "render_current_personal_context",
        "build_concierge_memory_block",
        "render_said_block",
        "said_facts",
    }
)

_GLOBAL = "глобальная поверхность — Ayla не салон (решение главного окна, DRF-2544)"
_OWN = "своя память человека: экран, команды, выгрузка 152-ФЗ, удаление"
_WRITE = "путь записи/сборки внутри слоя памяти, не подсказка"
_KEYED = "чтение по конкретным ключам коррекции блюд — NEVER_CROSSES туда не входит"

#: (файл, объемлющая функция, вызываемый) → почему это не чтение для салона.
KNOWN_READERS: dict[tuple[str, str, str], str] = {
    (
        "apps/channels/max/handler.py",
        "_handle_global_max_event_inner",
        "build_concierge_memory_block",
    ): _GLOBAL,
    (
        "apps/channels/max/handler.py",
        "_handle_global_max_event_inner",
        "render_current_personal_context",
    ): _GLOBAL,
    ("apps/orchestrator/concierge.py", "_concierge_turn", "render_said_block"): _GLOBAL,
    ("apps/orchestrator/concierge.py", "_has_said_facts", "said_facts"): _GLOBAL,
    ("apps/orchestrator/context_snapshot.py", "_said_section", "said_facts"): _GLOBAL,
    ("apps/orchestrator/memory_block.py", "_merge_inferred", "read_current_view"): _GLOBAL,
    ("apps/orchestrator/said_memory.py", "render_said_block", "said_facts"): _GLOBAL,
    ("apps/orchestrator/said_memory.py", "confirm_offer", "said_facts"): _GLOBAL,
    ("apps/orchestrator/said_memory.py", "confirm_said_fact", "said_facts"): _GLOBAL,
    ("apps/orchestrator/said_memory.py", "said_tap_labels", "said_facts"): _GLOBAL,
    (
        "apps/persona/memory_surface.py",
        "render_current_personal_context",
        "read_current_view",
    ): _GLOBAL,
    ("apps/identity/services/privacy.py", "delete_personal_data", "read_green_entries"): _OWN,
    ("apps/identity/services/privacy.py", "export_personal_data", "read_green_entries"): _OWN,
    ("apps/miniapp_api/views_memory.py", "_green_ids_to_forget", "read_green_entries"): _OWN,
    ("apps/miniapp_api/views_memory.py", "customer_memory", "read_green_entries"): _OWN,
    ("apps/persona/memory_commands.py", "handle_memory_command", "read_current_view"): _OWN,
    ("apps/persona/memory_commands.py", "handle_memory_command", "read_green_entries"): _OWN,
    ("apps/persona/memory_commands.py", "memory_show_chips", "read_current_view"): _OWN,
    ("apps/persona/memory_commands.py", "render_memory_summary", "read_current_view"): _OWN,
    (
        "apps/identity/services/memory_inferred.py",
        "record_inferred_green_facts",
        "read_personal_context",
    ): _WRITE,
    (
        "apps/identity/services/memory_key_policy.py",
        "read_current_view",
        "read_green_entries",
    ): _WRITE,
    (
        "apps/identity/services/memory_reader.py",
        "read_personal_context",
        "read_green_entries",
    ): _WRITE,
    (
        "apps/orchestrator/memory/personal_context.py",
        "record_explicit_green_facts",
        "read_green_entries",
    ): _WRITE,
    (
        "apps/orchestrator/memory/personal_context.py",
        "record_explicit_green_facts",
        "read_personal_context",
    ): _WRITE,
    ("apps/orchestrator/said_memory.py", "_write_said_fact", "read_current_view"): _WRITE,
    ("apps/orchestrator/said_memory.py", "_write_said_fact", "read_green_entries"): _WRITE,
    ("apps/orchestrator/said_memory.py", "said_facts", "read_current_view"): _WRITE,
    ("apps/orchestrator/memory/food.py", "recall_corrections", "read_current_view"): _KEYED,
    ("apps/orchestrator/memory/food.py", "remember_correction", "read_green_entries"): _KEYED,
}


@dataclass
class Census:
    files: int = 0
    sites: list[tuple[str, str, str, int]] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


def _scan(sources: dict[str, str]) -> Census:
    census = Census()
    for path, text in sorted(sources.items()):
        census.files += 1
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            census.unreadable.append(f"{path}:{exc.lineno}")
            continue

        def visit(node: ast.AST, function: str, path: str = path) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = node.name
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else None
                )
                if name in _READERS:
                    census.sites.append((path, function, name, node.lineno))
            for child in ast.iter_child_nodes(node):
                visit(child, function)

        visit(tree, "<module>")
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


def _unknown(census: Census, known: dict[tuple[str, str, str], str]) -> list[str]:
    keys = ", ".join(sorted(NEVER_CROSSES))
    return [
        f"{path}:{line} {function}() → {callee}: если это сборка для салона, через "
        f"него потекут {keys} — нужен предикат source_tenant_id (DRF-2544, OD_MEMORY §3)"
        for path, function, callee, line in census.sites
        if (path, function, callee) not in known
    ]


class TestEveryMemoryReaderIsKnown:
    def test_no_unknown_memory_reader(self) -> None:
        census = _scan(_production_sources())
        print(f"DRF-2544 перепись: файлов {census.files}, мест чтения памяти {len(census.sites)}")
        assert census.files > 500, census.files
        assert not census.unreadable, (
            f"нечитаемые файлы — перепись по ним не сделана: {census.unreadable}"
        )
        # Наличие впереди: обход видит все известные места, а не «ноль, потому что слеп».
        seen = {(p, f, c) for p, f, c, _ in census.sites}
        assert set(KNOWN_READERS) <= seen, sorted(set(KNOWN_READERS) - seen)
        unknown = _unknown(census, KNOWN_READERS)
        assert not unknown, "DRF-2544: новое место чтения личной памяти:\n  " + "\n  ".join(unknown)

    def test_known_list_has_no_stale_entries(self) -> None:
        # Храповик в обе стороны: место, которого больше нет, из списка уходит —
        # иначе список разрастается мёртвыми строками и перестаёт что-то значить.
        census = _scan(_production_sources())
        seen = {(p, f, c) for p, f, c, _ in census.sites}
        stale = sorted(set(KNOWN_READERS) - seen)
        assert len(KNOWN_READERS) == 29
        assert stale == [], stale  # empty-assert-ok: число известных мест утверждено строкой выше

    def test_never_crosses_names_favorite_masters(self) -> None:
        # Список не переходящих ключей — тот же, что читает personal_field_guard;
        # второй копии нет. Сегодня в нём мастер салона.
        assert "memory_key:favorite_masters" in NEVER_CROSSES


class TestSubstitution:
    def test_salon_skill_reading_memory_is_named_with_the_key(self) -> None:
        planted = (
            "from apps.identity.services.memory_key_policy import read_current_view\n"
            "\n"
            "def build_prompt(user_id):\n"
            "    return read_current_view(user_id).green_facts\n"
        )
        census = _scan({"apps/skills/booking/salon_prompt.py": planted})
        unknown = _unknown(census, KNOWN_READERS)
        assert len(unknown) == 1
        assert unknown[0].startswith(
            "apps/skills/booking/salon_prompt.py:4 build_prompt() → read_current_view"
        )
        assert "memory_key:favorite_masters" in unknown[0]

    def test_same_callee_in_a_new_function_of_a_known_file_is_named(self) -> None:
        # Точность до функции: handler.py держит и салонный путь. Вызов вне
        # _handle_global_max_event_inner — новое место, даже в известном файле.
        planted = (
            "def _handle_tenant_max_event(bot_user, ayla_user_id):\n"
            "    return render_current_personal_context(ayla_user_id)\n"
        )
        census = _scan({"apps/channels/max/handler.py": planted})
        unknown = _unknown(census, KNOWN_READERS)
        assert len(unknown) == 1
        assert "_handle_tenant_max_event()" in unknown[0]

    def test_unreadable_file_is_its_own_outcome(self) -> None:
        census = _scan({"apps/skills/booking/broken.py": "def (:\n"})
        assert census.files == 1
        assert census.unreadable == ["apps/skills/booking/broken.py:1"]
        assert (
            census.sites == []
        )  # empty-assert-ok: файл нечитаем — строкой выше это отдельный исход
