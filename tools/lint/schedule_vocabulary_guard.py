"""Одно состояние подтверждения расписания — одно имя и один вывод (§29.5, §83).

# Что случилось до сторожа

Состояния подтверждения жили в двух местах: пилотный экран «Расписание» и
карточка мастера в старой админке. Они **разошлись молча** — одно и то же
состояние называлось «Расписание не подтверждено» на одном экране и
«Расписание не подтверждено — салон его не заверял» на другом. Нашёл это
человек, читая оба файла подряд, а не прогон.

Сведение в `apps/miniapp/src/lib/schedule-confirmation-state.ts` чинит
сегодняшний день. Сторож нужен ради следующего: через месяц третья
формулировка появится в четвёртом месте, потому что **ничто не мешает**.

# Две половины, и вторая важнее

**Слова.** Формулировку состояния не пишет никто, кроме словаря.

**Вывод.** Даже с общими словами можно заново решить, что такое
«подтверждено», прочитав ``is_current`` у себя в разметке, — и получить два
ответа на один вопрос при одинаковом тексте. Ровно это и произошло с
``stale``: выражение ``Boolean(confirmed_at) && is_current === false`` стояло
в экране, а не в словаре, и повторить его было проще, чем импортировать.

# Почему питон, а не vitest

Сторожу нужен обход исходников, то есть ``node:fs``. В `apps/miniapp` нет
``@types/node``, и тянуть зависимость ради одной проверки дороже самой
проверки. Рядом уже живут два текстовых контракта над тем же деревом
(``miniapp_style_contract``, ``miniapp_token_contrast``) — этот третий.

# Что исключено и почему

**Тестовые файлы.** Тест, проверяющий текст на экране, обязан этот текст
называть, иначе он проверяет неизвестно что. Опасен не упоминающий тест, а
экран, сочиняющий свою формулировку.

**``admin-api.ts``** для второй половины: там ``is_current`` — имя поля в
объявлении типа ответа, а не вывод состояния. Список закрытый: пополнение —
решение, а не побочный эффект правки.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: Единственный файл, которому позволено называть состояния и выводить их.
VOCABULARY = Path("src/lib/schedule-confirmation-state.ts")

#: Файлы, где ``is_current`` — объявление поля, а не решение о состоянии.
FIELD_DECLARATION_ALLOWED = (Path("src/lib/admin-api.ts"),)

STATE_PHRASES = (
    "Расписание подтверждено",
    "Расписание не подтверждено",
    "Часы изменились после подтверждения",
)

DERIVATION_MARKER = "is_current"


def _sources(root: Path) -> list[Path]:
    out: list[Path] = []
    for path in sorted((root / "src").rglob("*")):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        if path.name.endswith((".test.ts", ".test.tsx")):
            continue
        out.append(path)
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: schedule_vocabulary_guard.py <apps/miniapp>", file=sys.stderr)
        return 2

    root = Path(argv[1])
    if not (root / "src").is_dir():
        print(f"schedule_vocabulary_guard: no src/ under {root}", file=sys.stderr)
        return 2

    files = _sources(root)

    # Положительный контроль. Сломайся обход — обе проверки ниже стали бы
    # зелёными от того, что искать не в чем, и это прочиталось бы как
    # «второго имени нет».
    if len(files) < 50:
        print(
            f"schedule_vocabulary_guard: обход дал {len(files)} файл(ов) — "
            "это не похоже на дерево мини-аппа; проверка не выполнялась",
            file=sys.stderr,
        )
        return 2
    if not (root / VOCABULARY).is_file():
        print(
            f"schedule_vocabulary_guard: словарь {VOCABULARY} не найден — "
            "сторожить нечего, и молчать об этом нельзя",
            file=sys.stderr,
        )
        return 2

    violations: list[str] = []
    for path in files:
        rel = path.relative_to(root)
        text = path.read_text(encoding="utf-8")

        if rel != VOCABULARY:
            for phrase in STATE_PHRASES:
                if phrase in text:
                    violations.append(f"{rel}: формулировка «{phrase}» написана вне словаря")

            if rel not in FIELD_DECLARATION_ALLOWED and DERIVATION_MARKER in text:
                violations.append(f"{rel}: {DERIVATION_MARKER} читается вне словаря")

    if not violations:
        print(
            f"schedule_vocabulary_guard: clean ({len(files)} файлов; "
            "состояние названо и выведено в одном месте)."
        )
        return 0

    for line in violations:
        print(line)
    print(
        f"\nschedule_vocabulary_guard: {len(violations)} нарушени(й). "
        "Зовите confirmationLabel() / confirmationState() из "
        "src/lib/schedule-confirmation-state.ts вместо того, чтобы писать "
        "текст или вывод состояния заново: два имени одного состояния "
        "расходятся молча.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
