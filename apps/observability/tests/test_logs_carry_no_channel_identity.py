"""Логи не несут идентификатор канала и имя человека (DRF-2009; одно правило с DRF-2006).

**Правило.** В строке лога — только псевдонимный внутренний ключ: ``bot_user.pk``, id тенанта,
pk черновика. Ни id человека в канале (``channel_user_id``, ``verified.user_id``), ни имя
(``first_name`` / ``last_name`` / ``display``), ни телефон. ``tools/lint/pii_guard.py`` читает
файлы коммита, а ``PIIRedactingFilter`` маскирует только телефоны, e-mail и карты — имя и id
канала в логе не видит ни тот, ни другой.

**Сторож — AST.** Вызов ``logger.*`` в production-коде ``apps/`` (без ``tests`` и ``migrations``),
в аргументах которого встречается имя класса как переменная или атрибут, либо атрибут
``user_id`` у переменной ``verified`` (``VerifiedInitData``: там ``user_id`` — MAX id человека).

**Пределы, названные честно** — сторож их не видит:

* значение через промежуточную переменную с другим именем: ``uid = event.channel_user_id``;
* строка, заранее собранная в переменную и переданная в лог готовой;
* логгер под именем без «log»; ``print``, ``emit``, structlog;
* соседние имена ``user_id`` / ``chat_id`` / ``name`` / ``username`` (перепись 15.09: 73 вызова).
  Там смесь идентификаторов канала и внутренних ключей, имя их не различает. Разбор по файлам —
  лист DRF-2010; в этом сторожe их нет.

**Третий класс утечки — текст исключения.** ``logger.exception`` печатает трассу вместе с
сообщением исключения, поэтому строка, чистая по аргументам, продолжает течь, если класс имён
попал в текст ``raise`` — и ловит это уже не проверка вызова логгера, а
``test_no_raise_message_carries_a_channel_identity_or_a_name``. Предел и у неё есть: сообщение,
собранное не f-строкой (конкатенация, ``%``, ``.format``), и текст чужого исключения из
библиотеки сторожу не видны.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

#: Имена, которые в аргументе лога называют человека или его id в канале.
CLASS_NAMES: frozenset[str] = frozenset(
    {"channel_user_id", "first_name", "last_name", "display", "phone"}
)
#: Переменные, у которых ``.user_id`` — id человека в канале (``VerifiedInitData``).
VERIFIED_NAMES: frozenset[str] = frozenset({"verified"})
LOG_METHODS: frozenset[str] = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
)
SKIP_PARTS: frozenset[str] = frozenset({"tests", "migrations"})

#: Строки, которые чинит #1783 (DRF-2006): файл → точное число нарушений в нём сегодня.
#: Кто сливается ВТОРЫМ (этот PR или #1783), тот снимает запись: после слияния обоих
#: нарушений в файле 0, и ``test_the_lines_fixed_in_1783_are_still_counted`` краснеет.
#: 17.09: #1783 слит вторым (eeb3e5f2) — запись снята, в файле 0 нарушений.
FIXED_IN_1783: dict[str, int] = {}


def _is_logger_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr not in LOG_METHODS:
        return False
    owner = node.func.value
    if isinstance(owner, ast.Name):
        name = owner.id
    elif isinstance(owner, ast.Attribute):
        name = owner.attr
    else:
        return False
    return "log" in name.lower()


def _identity_tokens(expr: ast.AST) -> list[str]:
    found = []
    for sub in ast.walk(expr):
        if isinstance(sub, ast.Name) and sub.id in CLASS_NAMES:
            found.append(sub.id)
        elif isinstance(sub, ast.Attribute):
            if sub.attr in CLASS_NAMES:
                found.append(sub.attr)
            elif (
                sub.attr == "user_id"
                and isinstance(sub.value, ast.Name)
                and sub.value.id in VERIFIED_NAMES
            ):
                found.append(f"{sub.value.id}.user_id")
    return found


def _violations(tree: ast.AST) -> tuple[int, list[tuple[int, str]]]:
    calls = 0
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not _is_logger_call(node):
            continue
        assert isinstance(node, ast.Call)
        calls += 1
        tokens: list[str] = []
        for arg in [*node.args, *(kw.value for kw in node.keywords)]:
            tokens.extend(_identity_tokens(arg))
        if tokens:
            hits.append((node.lineno, ",".join(sorted(set(tokens)))))
    return calls, hits


def _production_files() -> list[Path]:
    apps = ROOT / "apps"
    return [
        path
        for path in sorted(apps.rglob("*.py"))
        if not (SKIP_PARTS & set(path.relative_to(ROOT).parts))
    ]


def _scan() -> tuple[int, int, dict[str, list[tuple[int, str]]]]:
    files = _production_files()
    total_calls = 0
    found: dict[str, list[tuple[int, str]]] = {}
    for path in files:
        calls, hits = _violations(ast.parse(path.read_text(encoding="utf-8")))
        total_calls += calls
        if hits:
            found[path.relative_to(ROOT).as_posix()] = hits
    return len(files), total_calls, found


def _raise_violations(tree: ast.AST) -> tuple[int, int, list[tuple[int, str]]]:
    """``raise`` с f-строкой в сообщении: сколько всего и какие несут класс имён."""
    raises = 0
    fstrings = 0
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        raises += 1
        joined = [sub for sub in ast.walk(node) if isinstance(sub, ast.JoinedStr)]
        if not joined:
            continue
        fstrings += 1
        tokens: list[str] = []
        for piece in joined:
            tokens.extend(_identity_tokens(piece))
        if tokens:
            hits.append((node.lineno, ",".join(sorted(set(tokens)))))
    return raises, fstrings, hits


def _scan_raises() -> tuple[int, int, int, dict[str, list[tuple[int, str]]]]:
    files = _production_files()
    total_raises = 0
    total_fstrings = 0
    found: dict[str, list[tuple[int, str]]] = {}
    for path in files:
        raises, fstrings, hits = _raise_violations(ast.parse(path.read_text(encoding="utf-8")))
        total_raises += raises
        total_fstrings += fstrings
        if hits:
            found[path.relative_to(ROOT).as_posix()] = hits
    return len(files), total_raises, total_fstrings, found


def test_the_scan_is_not_vacuous():
    """Квантор «ни одного нарушения» пуст на пустом скане — нижняя граница."""
    files, calls, _found = _scan()
    assert files >= 600, f"скан нашёл {files} файлов — не тот корень"
    assert calls >= 1000, f"скан нашёл {calls} вызовов логгера — шаблон сломан"


def test_the_pattern_sees_the_class_and_leaves_internal_keys():
    sample = ast.parse(
        "logger.info('a %s', event.channel_user_id)\n"
        "log.warning('b %s', verified.user_id)\n"
        "self.logger.error('c %r %s', display, user.first_name)\n"
        "logger.info('d %s %s', bot_user.pk, tenant.id)\n"
        "logger.info('e %s', row.user_id)\n"
    )
    _calls, hits = _violations(sample)
    assert hits == [
        (1, "channel_user_id"),
        (2, "verified.user_id"),
        (3, "display,first_name"),
    ]


def test_no_log_call_carries_a_channel_identity_or_a_name():
    files, calls, found = _scan()
    assert files and calls
    unlisted = {path: hits for path, hits in found.items() if path not in FIXED_IN_1783}
    assert unlisted == {}, "\n".join(
        f"{path}:{line} {tokens}" for path, hits in unlisted.items() for line, tokens in hits
    )


def test_no_raise_message_carries_a_channel_identity_or_a_name():
    """Трасса — аргумент, которого нет в вызове логгера.

    ``except SoloOnboardingError: logger.exception(...)`` печатает текст исключения, поэтому
    ``channel_user_id`` в сообщении ``raise`` попадает в лог даже из «чистой» строки. Нижняя
    граница здесь своя: на пустом скане квантор «ни одного» пуст.
    """
    files, raises, fstrings, found = _scan_raises()
    assert files >= 600, f"скан нашёл {files} файлов — не тот корень"
    assert raises >= 900, f"скан нашёл {raises} raise — шаблон сломан"
    assert fstrings >= 450, f"скан нашёл {fstrings} raise с f-строкой — шаблон сломан"
    assert found == {}, "\n".join(
        f"{path}:{line} {tokens}" for path, hits in found.items() for line, tokens in hits
    )


def test_the_lines_fixed_in_1783_are_still_counted():
    """Счётчик реестра точный: слияние #1783 обнуляет файл — запись снимает второй."""
    _files, _calls, found = _scan()
    counted = {path: len(found.get(path, [])) for path in FIXED_IN_1783}
    assert counted == FIXED_IN_1783
