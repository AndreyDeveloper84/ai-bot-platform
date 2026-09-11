"""Пищевой дневник не читается с салонной, мастерской и админской поверхности (§6).

# Правило и почему оно было без сторожа

Последняя строка §6 свода владельца 11.09 (OD-NUT-1): **«Пищевой дневник не
передаётся салону или мастеру».** До этого файла правило держалось тем, что
никто такого читателя не написал. Это совпадение, а не сторож: первый же
«полезный виджет» для мастера — «клиент на этой неделе записывал еду» —
прошёл бы ревью как забота о клиенте, и никто не открыл бы свод, чтобы
проверить строку, которая нигде в коде не стоит.

# Замер до постройки — бот ``dev = db49a65e``, 11.09.2026

Десять поверхностей, где сидит салон, мастер или админ. Считались прямые
обращения к модулям и данным питания — импорты и вызовы, комментарии и
строки не считались.

```
поверхность                                    файлов   читателей питания
apps/master_api/**                               28          0
apps/admin_api/**                                31          0
apps/adminconsole/**                             21          0
apps/catalog/**   (зеркало каталога)             30          0
apps/notifications/**   (уведомления мастеру)     4          0
apps/channels/max/salon_handler.py                1          0
apps/booking/master_notify.py                     1          0
apps/booking/services/master_gate.py              1          0
apps/integrations/ayla/salon_client.py            1          0
apps/integrations/ayla/salon_surface.py           1          0
                                                ───        ───
                                                119          0

Мини-апп: экранов мастера/салона/админа (ts, tsx)   47, импортов питания   0
Шина событий (apps/eventbus, apps/events): исходящих событий питания   0
```

Положительный контроль прибора — тот же поиск по законным потребителям:
``apps/orchestrator/personal_surface.py`` — 10, ``apps/miniapp_api/views.py``
— 5. Ноль на поверхностях — настоящий.

Единственные совпадения были прозой: докстринг ``apps/notifications/
proactive.py:14-15,81`` ссылается на модуль питания как на образец ключей,
и докстринг ``apps/integrations/ayla/salon_client.py:29`` объясняет, что
он *не* про питание. Упоминание — не чтение, поэтому сторож вырезает
комментарии и строки токенайзером, как его образец
(``test_master_address_has_no_readers.py``).

# ЧТО НУЖНО ЗНАТЬ, ЕСЛИ ЭТОТ СТОРОЖ У ВАС ПОКРАСНЕЛ

Вы написали первого читателя данных питания на поверхности салона,
мастера или админа. Прежде чем продолжить:

1. **Это решение владельца, а не техническое ограничение.** §6 свода
   11.09: дневник не передаётся салону или мастеру. Снять можно только
   решением владельца, не ревью.

2. **«Полезно мастеру» — не основание.** Записи еды, фотографии, вода,
   ориентиры и профиль тела — данные о человеке, собранные под согласием
   на *его* дневник (``ConsentType.NUTRITION_DIARY``, §92). Согласия на
   передачу третьему лицу в этом составе нет.

3. **Если читатель написан по решению владельца** — впишите файл в
   ``_ALLOWED`` с номером задачи и параграфом решения. Не удаляйте
   сторожа: остальные поверхности он продолжает держать.

# Что этот сторож НЕ проверяет — названо, а не подразумевается

* **Транзитивные импорты.** Поверхность, импортирующая ``concierge``,
  который импортирует ``personal_surface``, здесь не краснеет: сторож
  смотрит на прямые обращения. Цепочку через модель разбирает
  ``tools/lint/import_boundaries.py`` по своим правилам.
* **Каталог (``beautygo_backend``).** Его читатели ``FoodLog`` /
  ``WaterLog`` вне ``nutrition/`` замерены отдельно: ``notifications/
  tasks.py`` шлёт **самому человеку**, ``tenants/…/backfill_tenants`` —
  миграция. Мастерских читателей там ноль, но сторож на той стороне
  нужен свой.
* **LLM-промпт мастерской поверхности.** Если контекст питания однажды
  попадёт в промпт салонного бота через общий сборщик контекста, это
  будет чтение через модель, а не через импорт. Предел назван.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Поверхности салона, мастера и админа. Каталоги — рекурсивно, файлы —
#: поимённо. Список открытый: новая мастерская поверхность обязана сюда
#: попасть, иначе сторож её не видит — и это названо в докстринге.
_SURFACES: tuple[str, ...] = (
    "apps/master_api",
    "apps/admin_api",
    "apps/adminconsole",
    "apps/catalog",
    "apps/notifications",
    "apps/channels/max/salon_handler.py",
    "apps/booking/master_notify.py",
    "apps/booking/services/master_gate.py",
    "apps/integrations/ayla/salon_client.py",
    "apps/integrations/ayla/salon_surface.py",
)

#: Обращение к питанию: импорт модуля питания либо вызов/чтение данных
#: дневника. Признак питания обязателен — иначе в улов попадёт
#: ``get_profile`` салонного клиента, у которого читатели законные.
_NUTRITION_READ = re.compile(
    r"(?:"
    r"apps\.(?:nutrition_proactive|nutrition_coach|wellness_proactive)\b"
    r"|apps\.integrations\.ayla\.nutrition_client\b"
    r"|apps\.orchestrator\.(?:food_history|personal_surface|nutrition_context|nutrition_global)\b"
    r"|apps\.skills\.(?:food_scanner|food_clarify|food_correction|nutrition_anketa|water)\b"
    r"|apps\.orchestrator\.memory\.food\b"
    r"|\bget_nutrition_client\s*\("
    r"|\.(?:daily_summary|get_water_today|weekly_deficits|scan_photo|log_meal|log_water)\s*\("
    r"|\b(?:FoodScan|FoodLog|WaterLog|NutritionProfile)\b"
    r"|\.food_logs\b"
    r")"
)

_SKIPPED_PARTS = ("migrations", "tests", ".venv", "venv", "__pycache__", "node_modules", ".git")

#: Известные читатели. Пусто НАМЕРЕННО — это состояние, которое §6 требует
#: и которое сторож охраняет. Появился читатель по решению владельца —
#: впишите файл, номер задачи и параграф.
_ALLOWED: dict[str, str] = {}


def _code_only(source: str) -> str:
    """Комментарии и строковые литералы забиты пробелами, номера строк целы."""
    lines = source.splitlines(keepends=True)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return source
    for tok in tokens:
        if tok.type not in (tokenize.COMMENT, tokenize.STRING):
            continue
        (row0, col0), (row1, col1) = tok.start, tok.end
        for row in range(row0, row1 + 1):
            line = lines[row - 1]
            start = col0 if row == row0 else 0
            end = col1 if row == row1 else len(line.rstrip("\r\n"))
            lines[row - 1] = line[:start] + " " * (end - start) + line[end:]
    return "".join(lines)


def _surface_files():
    for surface in _SURFACES:
        root = REPO_ROOT / surface
        paths = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in paths:
            if any(part in _SKIPPED_PARTS for part in path.parts):
                continue
            yield path.relative_to(REPO_ROOT).as_posix(), path


def _reads(source: str) -> list[tuple[int, str]]:
    code = _code_only(source)
    return [(code[: m.start()].count("\n") + 1, m.group(0)) for m in _NUTRITION_READ.finditer(code)]


def test_every_surface_in_the_list_exists():
    """Положительная стража к самому списку: поверхность, которой нет,
    даёт ноль читателей и ноль смысла. Переименовали каталог — краснеем
    здесь, а не молчим там."""
    missing = [s for s in _SURFACES if not (REPO_ROOT / s).exists()]
    assert not missing, f"поверхностей нет на диске: {missing}"


def test_nutrition_has_no_salon_master_or_admin_readers():
    """§6: дневник не передаётся салону или мастеру. Первый читатель
    обязан краснить билд и прочесть докстринг модуля."""
    offenders = []
    scanned = 0
    for rel, path in _surface_files():
        if rel in _ALLOWED:
            continue
        scanned += 1
        source = path.read_text(encoding="utf-8", errors="ignore")
        offenders.extend(f"{rel}:{line} — {frag}" for line, frag in _reads(source))
    # Число просмотренных печатается рядом с числом нарушений: «ноль
    # читателей» при нуле просмотренных файлов — не сторож, а тишина.
    assert scanned >= 100, f"просмотрено всего {scanned} файлов — список поверхностей пуст?"
    assert not offenders, (
        "данные питания читаются с салонной / мастерской / админской поверхности:\n  "
        + "\n  ".join(offenders)
        + "\n\nПРОЧТИТЕ ДОКСТРИНГ ЭТОГО МОДУЛЯ ПЕРЕД ТЕМ, КАК ПРОДОЛЖИТЬ.\n"
        + "Коротко: §6 свода 11.09 — пищевой дневник не передаётся салону или мастеру. "
        + "Это решение владельца, а не техническое ограничение; «полезно мастеру» — "
        + "не основание. Согласие на дневник дано человеком на СВОЙ дневник.\n"
        + "Если читатель написан по решению владельца — впишите файл в _ALLOWED "
        + "с номером задачи и параграфом."
    )


def test_the_guard_actually_fires():
    """Встроенный targeted proof: ловит код, не ловит прозу, не трогает
    законного соседа."""
    reader = "from apps.integrations.ayla import get_nutrition_client\n\ndef w(u):\n    return get_nutrition_client().daily_summary(external_user_id=u)\n"
    hits = _reads(reader)
    assert [frag for _, frag in hits] == ["get_nutrition_client(", ".daily_summary("], hits

    module_import = "from apps.nutrition_proactive.render import render_daily_report\n"
    assert _reads(module_import) == [(1, "apps.nutrition_proactive")]

    prose = '# было: get_nutrition_client()\ndef w():\n    """см. FoodLog"""\n    return 1\n'
    assert _reads(prose) == [], "сторож краснеет на упоминании — упоминание не чтение"

    salon = "from apps.integrations.ayla import get_salon_client\n\ndef w(t):\n    return get_salon_client().get_profile(tenant=t)\n"
    assert _reads(salon) == [], "сторож трогает салонный клиент, у которого читатели законные"
