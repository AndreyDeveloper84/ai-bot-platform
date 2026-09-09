"""Сторож: ориентир питания не подставляется числом (§82, §85).

# Зачем он есть

Владелец 09.09.2026 снял обе фикции дневника дословно: «Текущая плоская
норма калорий для всех удаляется. Формула воды `30 мл × вес` и прибавки
за беременность или кормление не используются без отдельно утверждённой
методики». До утверждения методики (§85 — Миффлин — Сан Жеор, справочные
2200/3000 мл по полу) ориентира нет **ни у кого**, и дневник работает в
режиме «без ориентира»: факт показан, цели, шкалы и процента нет.

Место без ориентира читается как недоделка. Следующий добросовестный
человек найдёт «разумное умолчание» и вернёт его одной строкой, искренне
считая, что чинит. Это не гипотеза — так уже было **дважды**:

* восьмёрку стаканов выкинули из клиента («норму воды не придумываем,
  восемь — число ниоткуда»), а она вернулась по проводу с бэкенда —
  `NUTRITION_DEFAULT_WATER_GOAL_ML = 2000` при стакане 250 мл;
* её сняли и там, после чего ориентир стал читаться из анкеты, где
  лежал результат той же формулы `30 мл × вес`.

Оба раза дефект прошёл ревью: подстановка выглядит как забота.

# Что ловится

Присвоение ЧИСЛА имени, несущему ориентир, в любой из четырёх форм,
встречавшихся в этом репозитории живьём::

    calories_target = 2000                     # присваивание
    WellnessToday(calories_target=2100)        # аргумент
    {"calories_goal": 2000}                    # ключ словаря
    body.get("calories_goal") or 2000          # «умолчание» через or

# Что НЕ ловится, и это надо знать

**Переименование.** Заведут `daily_energy_target` — список ниже протухнет
молча. Лечится только тем, что новое имя допишут сюда руками.

**Ноль.** Он разрешён намеренно: ноль СЪЕДЕННОГО настоящий, а ноль в
ориентире внутри модулей давно читается как «нет» и проверяется на
положительность у каждого потребителя. Запрещено класть в ориентир
осмысленное число, а не объявлять поле пустым.

**Поверхность за границей `apps/`.** `legacy_maxbot/` не сканируется: он
не импортируется из `apps/`, а его читатели уже проверяют цель на
`> 0` и без неё второе число не рисуют.

**TypeScript.** Стабы Mini App этот сканер не видит — он про Python.
Их держат `apps/miniapp/src/**/*.test.tsx`, где отсутствие ключа
проверяется поведением экрана.

Парный сторож на стороне Ayla — `nutrition/tests/test_targets_stay_absent.py`
в `djangoproject-catalog`: там же запрещено чтение столбца
`NutritionProfile.daily_water_ml`, в котором лежит выход снятой формулы.

Замер 09.09.2026, снят `uv run python tools/lint/nutrition_target_guard.py
apps/`: подставить `calories_target = 2000` вместо
`summary_res.calories_goal or None` в `apps/miniapp_api/views.py` →
`1 violation(s)` с указанием строки 2915; вернуть как было → `0`.
Подмена проверена на применение (ровно одно совпадение в файле), иначе
доказывалось бы свойство несуществующего кода.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path


#: Имена, несущие ОРИЕНТИР. Имён факта (`calories_eaten`,
#: `calories_total`, `water_ml`, `today_total_ml`) здесь нет намеренно.
TARGET_NAMES = frozenset(
    {
        "calories_goal",
        "calories_target",
        "water_goal_ml",
        "water_pct",
        "today_norm_ml",
        "today_norm_water_ml",
        "today_progress_pct",
        "water_glasses_target",
        "norm_ml",
    }
)

#: Файлы, которым подстановка разрешена: это стабы и фикстуры, чья
#: работа — изображать состояние «ориентир ЕСТЬ». §85 вернул шкалу и
#: процент для режимов «Рассчитано Ayla» и «Установлено клиентом», и
#: вёрстку этого состояния надо на чём-то развивать.
#:
#: Список нарочно короткий и путями, а не шаблоном: `**/tests/**` пустило
#: бы сюда любой будущий тест, а тест — самое частое место, где выдумка
#: заводится «на время».
ALLOWED = frozenset(
    {
        "apps/nutrition_proactive/tests/test_render.py",
        "apps/nutrition_proactive/tests/test_tasks.py",
        "apps/orchestrator/tests/test_coach_observation.py",
        "apps/orchestrator/tests/test_food_history.py",
        "apps/orchestrator/tests/test_personal_surface.py",
        "apps/miniapp_api/tests/test_wellness_today.py",
        "apps/integrations/ayla/tests/test_nutrition_client.py",
    }
)


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    detail: str

    def format(self) -> str:
        return f"{self.path}:{self.line}: ориентир подставлен значением — {self.detail}"


def _is_number(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
        and node.value != 0
    )


def _names_of(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Attribute):
        return {target.attr}
    return set()


def scan_source(source: str, *, path: str) -> list[Violation]:
    out: list[Violation] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out

    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        for t in targets:
            hit = _names_of(t) & TARGET_NAMES
            if hit and value is not None and _is_number(value):
                out.append(
                    Violation(
                        path,
                        node.lineno,
                        f"{sorted(hit)[0]} = {ast.unparse(value)}",
                    )
                )

        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in TARGET_NAMES and _is_number(kw.value):
                    out.append(
                        Violation(
                            path,
                            node.lineno,
                            f"{kw.arg}={ast.unparse(kw.value)}",
                        )
                    )

        # {"calories_goal": 2000} — та же подстановка, только в теле
        # ответа или в payload. Форма отдельная: ключ здесь СТРОКА, и
        # разбор имён её не видит.
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value in TARGET_NAMES and _is_number(v):
                    out.append(
                        Violation(
                            path,
                            node.lineno,
                            f'"{k.value}": {ast.unparse(v)}',
                        )
                    )

        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            text = ast.unparse(node)
            if any(n in text for n in TARGET_NAMES) and any(_is_number(v) for v in node.values):
                out.append(Violation(path, node.lineno, f"or-умолчание: {text}"))

    return out


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _detect_repo_root(start: Path) -> Path:
    current = start.resolve() if start.is_absolute() else (Path.cwd() / start).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "apps").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate
    return current


def scan_file(path: Path, *, repo_root: Path) -> list[Violation]:
    rel = _rel(path, repo_root)
    if rel in ALLOWED:
        return []
    if path.name == "nutrition_target_guard.py":
        # Свой собственный исходник: имена перечислены как ДАННЫЕ, а
        # заведомо виноватый пример ниже существует ровно затем, чтобы
        # сканер было чем проверить.
        return []
    return scan_source(path.read_text(encoding="utf-8"), path=rel)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: nutrition_target_guard.py <path> [<path> ...]", file=sys.stderr)
        return 2

    repo_root = _detect_repo_root(Path(argv[1]))
    found: list[Violation] = []
    for arg in argv[1:]:
        target = Path(arg)
        if not target.exists():
            print(f"nutrition_target_guard: path does not exist: {target}", file=sys.stderr)
            continue
        if target.is_file():
            found.extend(scan_file(target, repo_root=repo_root))
        else:
            for py in sorted(target.rglob("*.py")):
                found.extend(scan_file(py, repo_root=repo_root))

    if not found:
        return 0

    for v in found:
        print(v.format())
    print(
        f"\nnutrition_target_guard: {len(found)} violation(s). Ориентира нет ни у "
        "кого до утверждения методики (§82, §85). Отсутствие доезжает "
        "отсутствием ключа — не нулём, не прочерком и не «разумным "
        "умолчанием».",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
