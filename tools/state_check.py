#!/usr/bin/env python3
"""Проверялка самопроверяемых файлов состояния (``docs/state/STATE-*.md``).

Зачем она есть
--------------
У проекта много файлов состояния, и ни один из них нельзя проверить на
свежесть: они записывают **вывод**, но не записывают, **чем он снят** и **как
его перепроверить**. Только за 08.09.2026 протухшими оказались план дневника,
слитый в ``dev``; строка §33 в ``OPEN_DECISIONS.md`` про уже починенный дефект;
две трети очереди клиентского окна; комментарий в ``config/settings/base.py``
про три ручки, которых не существует ни одной.

Отсюда формат: каждый факт несёт команду своей перепроверки и ожидаемый от неё
результат. Эта программа исполняет команды и печатает три списка — что
держится, что ПРОТУХЛО и что не проверено. Код возврата ненулевой, если есть
протухшее: файл состояния, разошедшийся с кодом, — это поломка, а не заметка.

Что она НЕ делает
-----------------
Не ходит в сеть сама, не пишет ни в git, ни в файлы, не чинит найденное.
Исполняются только команды из белого списка (``git grep|show|ls-tree|log|
merge-base`` и ``gh run list`` / ``gh pr view|list``), и только через
``subprocess`` со списком аргументов — ``shell=True`` в этом файле нет и быть
не должно: поле ``проверка`` приходит из markdown-файла, который правит
человек, и склейка строки с шеллом сделала бы любой такой файл исполняемым
кодом.

Как читается ``ожидание``
-------------------------
* ``есть`` / ``нет`` — вывод непустой / пустой (для ``git ls-tree``: файл в
  дереве присутствует / отсутствует);
* число или сравнение (``0``, ``>0``, ``>=2``, ``=3``, ``<10``, ``!=1``) —
  сравнивается с числом, вынутым из вывода по правилу:

    1. если КАЖДАЯ непустая строка вывода кончается на ``:<цифры>`` (так
       печатает ``git grep -c``) — берётся сумма этих чисел;
    2. иначе, если весь вывод — одно целое число, берётся оно;
    3. иначе берётся количество непустых строк (``git ls-tree``,
       ``git grep -l``, ``git log --oneline``);

* любая другая строка — подстрока, которая обязана встретиться в выводе
  (можно взять в двойные кавычки, если важны пробелы по краям).

Запуск::

    python tools/state_check.py                 # все направления
    python tools/state_check.py --file client   # одно направление
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Белый список. Всё, чего здесь нет, не исполняется, а помечается как
# «проверка отклонена» — включая случай, когда команда выглядит безобидно.
# Разрешено только чтение; ни одна из этих подкоманд не меняет ни рабочее
# дерево, ни репозиторий, ни удалённую сторону.
# --------------------------------------------------------------------------
ALLOWED: dict[str, frozenset[str]] = {
    "git": frozenset({"grep", "show", "ls-tree", "log", "merge-base"}),
    # gh — только там, где факт про прогон CI или PR; иначе проверить такой
    # факт нечем, а выдумывать его нельзя.
    "gh": frozenset({"run", "pr"}),
}
GH_SECOND: dict[str, frozenset[str]] = {
    "run": frozenset({"list", "view"}),
    "pr": frozenset({"list", "view"}),
}

# Флаги, которые превращают читающую команду в исполняющую или пишущую, и
# проверяются на аргументах ПОСЛЕ подкоманды. `-O`/`--open-files-in-pager` у
# `git grep` запускает произвольный пейджер; `--ext-cmd` у `git log` зовёт
# внешний diff.
#
# Глобальных инъекций (`git -c core.pager=…`, `--exec-path`, `--git-dir`)
# здесь нет намеренно: они возможны только ДО подкоманды, а туда обязан
# попасть ровно один из разрешённых глаголов — иначе команда отвергается
# раньше, как «не в белом списке». Если положить их сюда, под запрет попадёт
# безобидный `git grep -c`, то есть основной инструмент этого файла.
DENIED_ARG_PREFIXES: tuple[str, ...] = (
    "-O",
    "--open-files-in-pager",
    "--output",
    "--upload-pack",
    "--receive-pack",
    "--ext-cmd",
)

NOTHING_TO_CHECK = "нечем"

FIELD_RE = re.compile(r"^(статус|где|проверка|ожидание|снято|задача|почему)\s*:\s*(.*)$")
HEADING_RE = re.compile(r"^###\s+(.*\S)\s*$")
SECTION_RE = re.compile(r"^##\s+(.*\S)\s*$")
TRAILING_COUNT_RE = re.compile(r":(\d+)$")
REPO_IN_TAKEN_RE = re.compile(r"\(([^()]+)\)\s*$")
COMPARATOR_RE = re.compile(r"^(>=|<=|!=|>|<|=)?\s*(\d+)$")

# Как называются репозитории в поле `снято` и где их искать, если имя не
# `ai-bot-platform`. Ayla-бэкенд лежит рядом и называется по-разному в разных
# документах — принимаем оба имени.
AYLA_REPO_NAMES = ("djangoproject-catalog", "beautygo_backend")

HOLDS, STALE, UNCHECKED, REJECTED = "держится", "протухло", "не проверено", "отклонено"


@dataclass
class Fact:
    title: str
    source: Path
    line_no: int
    section: str = ""
    status: str = ""
    where: str = ""
    check: str = ""
    expect: str = ""
    taken: str = ""
    task: str = ""
    why: str = ""

    @property
    def repo(self) -> str:
        m = REPO_IN_TAKEN_RE.search(self.taken)
        return m.group(1).strip() if m else "ai-bot-platform"

    @property
    def location(self) -> str:
        return f"{self.source.name}:{self.line_no}"


@dataclass
class Result:
    fact: Fact
    verdict: str
    reason: str = ""
    got: str = ""
    argv: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Разбор
# --------------------------------------------------------------------------
def parse_facts(text: str, source: Path) -> list[Fact]:
    """Разбирает один файл состояния в список фактов.

    Блок начинается заголовком третьего уровня и держится до следующего
    заголовка любого уровня. Строка без поля `проверка` фактом не считается —
    это правило формата, и оно проверяется отдельно (`missing_check`).
    """
    facts: list[Fact] = []
    current: Fact | None = None
    section = ""

    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()

        sec = SECTION_RE.match(line)
        if sec:
            section = sec.group(1)
            current = None
            continue

        head = HEADING_RE.match(line)
        if head:
            current = Fact(title=head.group(1), source=source, line_no=i, section=section)
            facts.append(current)
            continue

        if line.startswith("#"):  # заголовок любого другого уровня закрывает блок
            current = None
            continue

        if current is None:
            continue

        m = FIELD_RE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        setattr(
            current,
            {
                "статус": "status",
                "где": "where",
                "проверка": "check",
                "ожидание": "expect",
                "снято": "taken",
                "задача": "task",
                "почему": "why",
            }[key],
            value,
        )

    return facts


def missing_check(facts: list[Fact]) -> list[Fact]:
    """Факты без поля `проверка` — нарушение формата, а не «не проверено»."""
    return [f for f in facts if not f.check]


# --------------------------------------------------------------------------
# Белый список
# --------------------------------------------------------------------------
def classify_command(command: str) -> tuple[list[str] | None, str]:
    """Возвращает (argv, "") если команду можно исполнять, иначе (None, причина).

    Никаких склеек со строкой: разбор через ``shlex``, исполнение — списком
    аргументов без шелла. Поэтому конвейеры, редиректы и подстановки не
    «экранируются», а просто не работают: они приезжают сюда одним аргументом
    и отвергаются вместе со всей командой.
    """
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        return None, f"команду не разобрать: {exc}"

    if not argv:
        return None, "пустая команда"

    # Намеренно НЕ отвергаем «символы шелла» вроде `|` или `$`: конвейеров и
    # редиректов здесь не существует в принципе — команда исполняется списком
    # аргументов без шелла, поэтому `|` внутри `git grep "a\|b"` это просто
    # символ в регулярном выражении, а `a | b` без кавычек станет тремя
    # аргументами git и упадёт громко, а не выполнится. Отвергается только то,
    # что делает команду неоднозначной для человека, который её читает.
    for token in argv:
        if "\n" in token or "\x00" in token:
            return None, "команда не может быть многострочной"

    program = argv[0]
    if program not in ALLOWED:
        return None, f"{program!r} не в белом списке (разрешены git и gh)"

    if len(argv) < 2:
        return None, f"у {program} нет подкоманды"

    sub = argv[1]
    if sub not in ALLOWED[program]:
        allowed = ", ".join(sorted(ALLOWED[program]))
        return None, f"{program} {sub} не разрешена (разрешены: {allowed})"

    if program == "gh":
        if len(argv) < 3 or argv[2] not in GH_SECOND[sub]:
            allowed = ", ".join(sorted(GH_SECOND[sub]))
            return None, f"gh {sub} — разрешены только: {allowed}"

    for token in argv[2:]:
        for bad in DENIED_ARG_PREFIXES:
            # У короткого флага значение приклеено (`-O/bin/sh`), у длинного
            # отделено `=` или пробелом (`--ext-cmd=sh`). Проверять только
            # точное совпадение — значит пропустить именно ту форму, ради
            # которой флаг сюда и попал.
            glued = not bad.startswith("--")
            if token == bad or token.startswith(bad + "=") or (glued and token.startswith(bad)):
                return None, f"запрещённый флаг {token!r}"

    return argv, ""


# --------------------------------------------------------------------------
# Сравнение с ожиданием
# --------------------------------------------------------------------------
def number_from_output(output: str) -> int:
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    if not lines:
        return 0
    counts = [TRAILING_COUNT_RE.search(ln) for ln in lines]
    if all(counts):
        return sum(int(m.group(1)) for m in counts if m)
    if len(lines) == 1 and lines[0].isdigit():
        return int(lines[0])
    return len(lines)


def compare(expect: str, output: str) -> tuple[bool, str]:
    """Возвращает (сошлось, что получено человеческими словами)."""
    want = expect.strip()
    lines = [ln for ln in output.splitlines() if ln.strip()]

    if want == "есть":
        return bool(lines), f"строк вывода: {len(lines)}"
    if want == "нет":
        return not lines, f"строк вывода: {len(lines)}"

    m = COMPARATOR_RE.match(want)
    if m:
        op, number = m.group(1) or "=", int(m.group(2))
        got = number_from_output(output)
        ok = {
            "=": got == number,
            "!=": got != number,
            ">": got > number,
            ">=": got >= number,
            "<": got < number,
            "<=": got <= number,
        }[op]
        return ok, str(got)

    needle = want
    if len(needle) >= 2 and needle[0] == needle[-1] == '"':
        needle = needle[1:-1]
    if needle in output:
        return True, "строка найдена"
    head = " / ".join(lines[:2]) if lines else "(пусто)"
    return False, f"строки нет; вывод: {head[:160]}"


# --------------------------------------------------------------------------
# Исполнение
# --------------------------------------------------------------------------
def read_only_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GH_PAGER": "cat",
            "GH_PROMPT_DISABLED": "1",
            "NO_COLOR": "1",
            "CLICOLOR": "0",
        }
    )
    return env


def run_command(argv: list[str], cwd: Path, timeout: float) -> tuple[int, str, str]:
    proc = subprocess.run(  # noqa: S603 — argv из белого списка, shell=False
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=read_only_env(),
        shell=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def check_fact(fact: Fact, repos: dict[str, Path | None], timeout: float) -> Result:
    if fact.check.strip().lower() == NOTHING_TO_CHECK:
        return Result(fact, UNCHECKED, reason=fact.why or "проверка: нечем")

    argv, why = classify_command(fact.check)
    if argv is None:
        return Result(fact, REJECTED, reason=why)

    cwd = repos.get(fact.repo)
    if cwd is None:
        return Result(
            fact,
            UNCHECKED,
            reason=f"репозиторий {fact.repo!r} не найден на диске",
            argv=argv,
        )

    if argv[0] == "gh" and not shutil_which("gh"):
        return Result(fact, UNCHECKED, reason="gh не установлен", argv=argv)

    try:
        code, out, err = run_command(argv, cwd, timeout)
    except subprocess.TimeoutExpired:
        return Result(fact, STALE, reason=f"команда не уложилась в {timeout:g} с", argv=argv)
    except OSError as exc:
        return Result(fact, UNCHECKED, reason=f"команду не запустить: {exc}", argv=argv)

    # `git grep` отвечает 1 на «ничего не нашлось» — это результат, а не сбой.
    if code not in (0, 1):
        first = (err.strip().splitlines() or ["(без сообщения)"])[0]
        return Result(fact, STALE, reason=f"команда упала (код {code}): {first}", argv=argv)

    ok, got = compare(fact.expect, out)
    return Result(fact, HOLDS if ok else STALE, got=got, argv=argv)


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)


# --------------------------------------------------------------------------
# Репозитории
# --------------------------------------------------------------------------
def repo_root(start: Path) -> Path:
    """Корень чекаута, в котором лежит эта программа (работает и в worktree)."""
    return Path(__file__).resolve().parents[1]


def main_checkout(root: Path) -> Path:
    """Главный чекаут: для worktree это НЕ его собственный корень."""
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=15,
            env=read_only_env(),
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return root
    if out.returncode != 0 or not out.stdout.strip():
        return root
    return Path(out.stdout.strip()).parent


def discover_repos(root: Path, overrides: dict[str, Path]) -> dict[str, Path | None]:
    repos: dict[str, Path | None] = {"ai-bot-platform": root}
    parent = main_checkout(root).parent
    found: Path | None = None
    for name in AYLA_REPO_NAMES:
        candidate = parent / name
        if (candidate / ".git").exists():
            found = candidate
            break
    for name in AYLA_REPO_NAMES:
        repos[name] = found
    repos.update(overrides)
    return repos


def dev_head(path: Path) -> str:
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--short", "origin/dev"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=15,
            env=read_only_env(),
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "?"
    return out.stdout.strip() if out.returncode == 0 else "origin/dev отсутствует"


# --------------------------------------------------------------------------
# Вывод
# --------------------------------------------------------------------------
def render(results: list[Result], orphans: list[Fact], bases: list[str]) -> str:
    buckets: dict[str, list[Result]] = {HOLDS: [], STALE: [], UNCHECKED: [], REJECTED: []}
    for r in results:
        buckets[r.verdict].append(r)

    out: list[str] = []
    out.append("Проверка состояния: docs/state/STATE-*.md")
    for base in bases:
        out.append(f"  база: {base}")
    out.append("")

    out.append(f"ДЕРЖИТСЯ ({len(buckets[HOLDS])})")
    for r in buckets[HOLDS]:
        out.append(f"  [{r.fact.location}] {r.fact.title}")
    out.append("")

    out.append(f"ПРОТУХЛО ({len(buckets[STALE])})")
    for r in buckets[STALE]:
        out.append(f"  [{r.fact.location}] {r.fact.title}")
        out.append(f"      команда:  {r.fact.check}")
        out.append(f"      ожидание: {r.fact.expect}")
        out.append(f"      получено: {r.got or r.reason}")
        out.append(f"      снято:    {r.fact.taken}")
        if r.fact.task:
            out.append(f"      задача:   {r.fact.task}")
    out.append("")

    out.append(f"НЕ ПРОВЕРЕНО ({len(buckets[UNCHECKED])})")
    for r in buckets[UNCHECKED]:
        out.append(f"  [{r.fact.location}] {r.fact.title}")
        out.append(f"      почему: {r.reason}")
    out.append("")

    if buckets[REJECTED]:
        out.append(f"ПРОВЕРКА ОТКЛОНЕНА ({len(buckets[REJECTED])})")
        for r in buckets[REJECTED]:
            out.append(f"  [{r.fact.location}] {r.fact.title}")
            out.append(f"      команда: {r.fact.check}")
            out.append(f"      причина: {r.reason}")
        out.append("")

    if orphans:
        out.append(f"БЕЗ ПОЛЯ «проверка» ({len(orphans)})")
        for f in orphans:
            out.append(f"  [{f.location}] {f.title}")
        out.append("")

    total = len(results)
    out.append(
        f"Итого: {total} фактов — держится {len(buckets[HOLDS])}, "
        f"протухло {len(buckets[STALE])}, не проверено {len(buckets[UNCHECKED])}"
        + (f", отклонено {len(buckets[REJECTED])}" if buckets[REJECTED] else "")
        + (f", без проверки {len(orphans)}" if orphans else "")
    )
    return "\n".join(out)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Проверяет факты в docs/state/STATE-*.md их же командами перепроверки.",
    )
    p.add_argument(
        "--file",
        default=None,
        help="одно направление: имя файла или его кусок (admin, client, diary, master)",
    )
    p.add_argument(
        "--dir", default=None, help="каталог с файлами состояния (по умолчанию docs/state)"
    )
    p.add_argument("--timeout", type=float, default=60.0, help="таймаут на одну команду, секунд")
    p.add_argument(
        "--repo",
        action="append",
        default=[],
        metavar="ИМЯ=ПУТЬ",
        help="где лежит репозиторий с таким именем (можно повторять)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = repo_root(Path.cwd())
    state_dir = Path(args.dir) if args.dir else root / "docs" / "state"

    if not state_dir.is_dir():
        print(f"Каталог состояния не найден: {state_dir}", file=sys.stderr)
        return 2

    files = sorted(state_dir.glob("STATE-*.md"))
    if args.file:
        needle = args.file.lower().removesuffix(".md")
        files = [f for f in files if needle in f.name.lower()]
    if not files:
        print(f"Нечего проверять в {state_dir} (--file {args.file})", file=sys.stderr)
        return 2

    overrides: dict[str, Path] = {}
    for item in args.repo:
        name, _, path = item.partition("=")
        if not path:
            print(f"--repo ожидает ИМЯ=ПУТЬ, получено: {item}", file=sys.stderr)
            return 2
        overrides[name.strip()] = Path(path).expanduser().resolve()

    repos = discover_repos(root, overrides)

    facts: list[Fact] = []
    for f in files:
        facts.extend(parse_facts(f.read_text(encoding="utf-8"), f))

    orphans = missing_check(facts)
    checkable = [f for f in facts if f.check]

    results = [check_fact(f, repos, args.timeout) for f in checkable]

    # Базы печатаются только для тех фактов, чью команду мы действительно
    # запускаем: у блока с `проверка: нечем` поле `снято` описывает чужой
    # замер, а не репозиторий, и искать его на диске бессмысленно.
    used = sorted({f.repo for f in checkable if f.check.strip().lower() != NOTHING_TO_CHECK})
    bases = []
    for name in used:
        path = repos.get(name)
        bases.append(f"{name}: {dev_head(path) if path else 'не найден на диске'}")

    print(render(results, orphans, bases))

    stale = sum(1 for r in results if r.verdict == STALE)
    rejected = sum(1 for r in results if r.verdict == REJECTED)
    if stale:
        return 1
    if rejected or orphans:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
