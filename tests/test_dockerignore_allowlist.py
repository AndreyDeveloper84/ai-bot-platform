"""Страж списка позволений в ``.dockerignore``.

``.dockerignore`` перевёрнут в список позволений (DRF-1440): первая строка
``*`` запрещает всё, строки ``!`` возвращают ровно то, что нужно образу.
Это снимает целый класс аварий — новый каталог, созданный на хосте
(``.bak``, ``.chromadb``, ``repomix-output.xml``, ``staticfiles``), больше
не попадает в контекст сборки молча.

Цена конструкции ровно одна: новый элемент верхнего уровня В РЕПОЗИТОРИИ
теперь тоже не попадает в образ молча. Этот файл превращает «молча» в
падающий тест. Он не решает, что должно быть в образе — он требует, чтобы
решение было записано: каждый отслеживаемый git'ом элемент верхнего уровня
обязан быть либо в ``!``-строках ``.dockerignore``, либо в ``NOT_IN_IMAGE``
ниже, с причиной.

Почему именно верхний уровень: ``.dockerignore`` в этом репозитории
разрешает деревья целиком (``!apps``), поэтому файл внутри разрешённого
дерева проблемы не создаёт. Новый элемент появляется у корня.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERIGNORE = REPO_ROOT / ".dockerignore"

# Отслеживаемые элементы верхнего уровня, которых в образе быть НЕ должно.
# Каждая строка — сознательное решение, а не умолчание.
NOT_IN_IMAGE: dict[str, str] = {
    ".claude": "конфигурация агентов разработки; сюда же ложатся рабочие "
    "деревья .claude/worktrees, каждое со своим .venv",
    ".dockerignore": "читается демоном, внутрь образа не нужен",
    ".gitattributes": "правила git, в рантайме не читаются",
    ".githooks": "хуки git — только рабочая копия разработчика",
    ".github": "workflow'ы CI выполняются на раннере, не в образе",
    ".gitignore": "правила git, в рантайме не читаются",
    ".mcp.json.example": "шаблон конфига инструментов разработчика",
    ".pre-commit-config.yaml": "линтеры гоняются на раннере и локально",
    ".secrets.baseline": "базовая линия detect-secrets, инструмент CI",
    ".env.example": "шаблон окружения; load_dotenv ищет только .env",
    ".env.staging.template": "шаблон окружения staging (значения — на VPS)",
    "CLAUDE.md": "документация",
    "Dockerfile": "передаётся демону отдельно от контекста",
    "docker-compose.yml": "читается docker compose НА ХОСТЕ",
    "docker-compose.staging.yml": "читается docker compose НА ХОСТЕ",
    "docs": "документация; 8 МБ, которые никто не открывает из контейнера",
}


def _tracked_top_level() -> set[str]:
    """Элементы верхнего уровня, которые git считает частью репозитория."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("git ls-files недоступен — не рабочая копия git")
    paths = [p for p in result.stdout.split("\0") if p]
    if not paths:
        pytest.skip("git ls-files ничего не вернул")
    return {p.split("/", 1)[0] for p in paths}


def _allowed_top_level() -> set[str]:
    """Элементы, возвращённые в контекст строками ``!`` в .dockerignore."""
    allowed: set[str] = set()
    for raw in DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("!"):
            continue
        pattern = line[1:].strip().strip("/")
        # Разрешения вида `!apps/miniapp/src` покрывают тот же верхний
        # уровень, что и `!apps` — для этого стража важен первый сегмент.
        allowed.add(pattern.split("/", 1)[0])
    return allowed


def test_dockerignore_denies_everything_first() -> None:
    """Первая значащая строка — ``*``: без неё список позволений не список."""
    lines = [
        ln.strip()
        for ln in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert lines, ".dockerignore пуст"
    assert lines[0] == "*", (
        "первая значащая строка .dockerignore должна быть `*` — иначе "
        "строки `!` ничего не возвращают, а файл снова становится списком "
        "запретов, который защищает только от прошлой аварии"
    )


def test_every_tracked_top_level_entry_has_a_verdict() -> None:
    """Ни один отслеживаемый элемент верхнего уровня не остаётся без решения."""
    tracked = _tracked_top_level()
    allowed = _allowed_top_level()
    # Утверждение присутствия ПЕРЕД утверждением отсутствия: пустой
    # `tracked` или пустой `allowed` сделали бы проверку ниже
    # бессодержательно зелёной.
    assert tracked, "git не вернул ни одного элемента верхнего уровня"
    assert allowed, ".dockerignore не содержит ни одной строки `!` — образ был бы пуст"
    undecided = sorted(tracked - allowed - set(NOT_IN_IMAGE))
    assert not undecided, (
        "новые элементы верхнего уровня не попадут в образ, и никто этого "
        f"не решал: {undecided}. Внесите каждый либо в .dockerignore "
        "строкой `!<имя>` (нужен образу), либо в NOT_IN_IMAGE в этом файле "
        "с причиной (не нужен)."
    )


def test_not_in_image_list_has_no_stale_entries() -> None:
    """NOT_IN_IMAGE не должен переживать удалённые из репозитория файлы."""
    tracked = _tracked_top_level()
    assert tracked, "git не вернул ни одного элемента верхнего уровня"
    assert NOT_IN_IMAGE, "список NOT_IN_IMAGE пуст — сверять нечего"
    stale = sorted(set(NOT_IN_IMAGE) - tracked)
    assert not stale, f"NOT_IN_IMAGE называет элементы, которых больше нет в git: {stale}"


def test_allowlist_covers_what_the_build_reads() -> None:
    """Минимум, без которого Dockerfile не соберётся, — всегда разрешён."""
    allowed = _allowed_top_level()
    required = {
        "pyproject.toml",  # uv sync --locked
        "uv.lock",  # uv sync --locked
        "README.md",  # pyproject: readme = "README.md" (DRF-1437)
        "manage.py",  # CMD образа
        "apps",  # packages.find include = ["config*", "apps*"]
        "config",  # то же
        "tools",  # RUN python tools/env_guard.py --against-lock
    }
    assert allowed, ".dockerignore не содержит ни одной строки `!` — образ был бы пуст"
    missing = sorted(required - allowed)
    assert not missing, (
        f".dockerignore перестал пропускать в образ то, что читает сборка: {missing}"
    )
