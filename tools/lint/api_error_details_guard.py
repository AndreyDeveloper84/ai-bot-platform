"""Отказ сервера доезжает до экрана целиком: `details` не роняется (DRF-2373).

# Что случилось до сторожа

`ApiError` умеет нести `details` — структурные подробности отказа, которые
прислал сервер. Клиентов, конструирующих `ApiError`, несколько, и каждый
разбирает тело отказа сам. Поле роняли, и чинили это **трижды, поодиночке**:

* `src/lib/api.ts` — DRF-1708 (`quote_changed` нёс пары, которые человек
  обязан увидеть);
* `src/lib/admin-api.ts` — DRF-2273 (каталожное «что сделать» ехало в
  `details.hint`, и админские экраны оставались с английским `detail`);
* `src/lib/master-api.ts` — DRF-2373 (сервер говорил `retriable`, мастерский
  экран этого не слышал вовсе).

Три года, три листа, один дефект. Каждый раз чинили **место**, а не правило,
и поэтому чинили заново.

# Почему сторож, а не «мы теперь помним»

Дефект невидим по построению: уронивший `details` клиент **работает**. Экран
просто не знает того, что сервер сказал, и ведёт себя как раньше. Ни типы, ни
прогон этого не ловят — оба зелены. Узнаёт об этом человек, читая два файла
подряд, как было со всеми тремя листами.

Следующий клиент уронит поле снова, потому что **ничто не мешает**.

# Правило — по РОЛИ, а не по списку файлов

Сторож не знает, кто «клиент»: список устарел бы к следующему листу. Он
смотрит на сам вызов:

    new ApiError(res.status, parsed.error, parsed.detail)          ← красное
    new ApiError(res.status, parsed.error, parsed.detail, parsed.details)  ← зелёное
    new ApiError(404, "bad_master_id", "ID мастера не задан")      ← не про него

Третий аргумент вида `<что-то>.detail` означает **разобранное тело отказа
сервера**: у него есть и `details`, и ронять его нельзя. Третий аргумент —
строковый литерал означает ошибку, **придуманную на месте** (её и сервера-то
не было), и требовать у неё `details` бессмысленно.

Поэтому экран, конструирующий свой `ApiError` из литералов, освобождён
**правилом**, а не именем в списке. Список освобождал бы и того, кто завтра
уронит настоящее поле в том же файле.

# Долг перечислен, а не забыт

`api_error_details_allow.txt` называет файлы, где непочиненные места ещё
есть, и **их число**. Число может только уменьшаться: починил — уменьши,
уронил новое — сторож красный. Пополнять список — решение, а не побочный
эффект правки.

Долг заведён листом DRF-2373 после переписи: чинить все клиенты в листе про
подтверждение значило бы спрятать восемь правок за чужим заголовком.

# Почему питон, а не vitest

Сторожу нужен обход исходников, то есть `node:fs`. В `apps/miniapp` нет
`@types/node`. Рядом уже живут три текстовых контракта над тем же деревом
(`miniapp_style_contract`, `miniapp_token_contrast`,
`schedule_vocabulary_guard`) — этот четвёртый.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CALL = "new ApiError("

#: Сколько непочиненных мест позволено файлу. Только уменьшается.
ALLOW_FILE = Path(__file__).with_name("api_error_details_allow.txt")

#: Нижняя граница переписи. Обход сломался — «нарушений нет» значило бы
#: «искать не в чем», и сторож зеленел бы на пустоте.
MIN_CALLS = 8


def _sources(root: Path) -> list[Path]:
    out: list[Path] = []
    for path in sorted((root / "src").rglob("*")):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        if path.name.endswith((".test.ts", ".test.tsx")):
            continue
        out.append(path)
    return out


def _args(text: str, start: int) -> str | None:
    """Аргументы вызова, начинающегося на ``start``; ``None`` — скобка не закрыта."""
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start + len(CALL) : i]
    return None


def _split(args: str) -> list[str]:
    """Аргументы верхнего уровня. Вложенные скобки и строки не режутся."""
    parts: list[str] = []
    depth = 0
    quote = ""
    current = ""
    for ch in args:
        if quote:
            current += ch
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'`":
            quote = ch
            current += ch
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
            continue
        current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


_PARSED_DETAIL = re.compile(r"^([A-Za-z_$][\w$]*)\.detail$")


def _violation(args: list[str]) -> str | None:
    """Нарушение этого вызова, или ``None``.

    Про вызов, чей третий аргумент — не ``<объект>.detail``, сторожу сказать
    нечего: тела отказа у него не было.
    """
    if len(args) < 3:
        return None
    match = _PARSED_DETAIL.match(args[2])
    if match is None:
        return None
    holder = match.group(1)
    if len(args) >= 4 and args[3] == f"{holder}.details":
        return None
    return f"{holder}.detail без {holder}.details"


def _allowance() -> dict[str, int]:
    if not ALLOW_FILE.is_file():
        return {}
    allowed: dict[str, int] = {}
    for raw in ALLOW_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        path, _, count = line.partition("::")
        allowed[path.strip()] = int(count.strip())
    return allowed


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: api_error_details_guard.py <apps/miniapp>", file=sys.stderr)
        return 2

    root = Path(argv[1])
    if not (root / "src").is_dir():
        print(f"api_error_details_guard: no src/ under {root}", file=sys.stderr)
        return 2

    seen = 0
    found: dict[str, list[str]] = {}
    for path in _sources(root):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(root).as_posix()
        for m in re.finditer(re.escape(CALL), text):
            seen += 1
            args = _args(text, m.start())
            if args is None:  # pragma: no cover — незакрытая скобка не компилируется
                continue
            problem = _violation(_split(args))
            if problem is not None:
                found.setdefault(rel, []).append(problem)

    if seen < MIN_CALLS:
        print(
            f"api_error_details_guard: перепись нашла {seen} вызовов при "
            f"минимуме {MIN_CALLS} — обход слеп, проверять не в чем.",
            file=sys.stderr,
        )
        return 2

    allowed = _allowance()
    violations: list[str] = []
    for rel, problems in sorted(found.items()):
        budget = allowed.get(rel, 0)
        if len(problems) > budget:
            violations.append(
                f"{rel}: роняет details в {len(problems)} мест(ах), "
                f"позволено {budget} ({problems[0]})"
            )
    for rel, budget in sorted(allowed.items()):
        actual = len(found.get(rel, []))
        if actual < budget:
            violations.append(
                f"{rel}: в долге записано {budget}, а осталось {actual} — "
                f"уменьшите число в {ALLOW_FILE.name}: долг только сокращается"
            )

    if not violations:
        print(
            f"api_error_details_guard: clean ({seen} вызовов ApiError; "
            f"непочиненных мест в долге: {sum(allowed.values())})."
        )
        return 0

    for line in violations:
        print(line)
    print(
        "\napi_error_details_guard: отказ сервера обязан доезжать целиком. "
        "Передавайте четвёртым аргументом `<то же>.details` — иначе экран не "
        "услышит того, что сервер сказал, и будет вести себя как раньше: "
        "ни типы, ни прогон этого не ловят, оба зелены.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
