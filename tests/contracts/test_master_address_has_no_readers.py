"""У ``CatalogMaster.address`` читателей НОЛЬ — и это не случайность (DRF-1589).

# Зачем сторож на отсутствие

Поле пишется синхронизацией (DRF-1588), читателей у него нет ни одного, а
трёхзначность объявлена **только в комментарии** к модели. Комментарий
прочтёт тот, кто ищет; автор первого читателя откроет файл, увидит
``address``, напишет ``or ""`` и уедет — потому что различие живёт в
прозе, а проза не краснеет.

Сторож зелен сегодня и краснеет в день появления первого читателя —
заставляя автора прочесть то, что ниже, ДО того, как он схлопнет
состояния.

# ЧТО НУЖНО ЗНАТЬ, ЕСЛИ ЭТОТ СТОРОЖ У ВАС ПОКРАСНЕЛ

Вы пишете первого читателя адреса мастера. Прежде чем писать:

1. **Поле трёхзначно, и это не формальность.**

   * ``address is None`` — ключа ``address`` в слепке НЕ БЫЛО. Мы не
     знаем. Три пилотные строки из 34 именно такие (замер 08.09.2026);
   * ``address == ""``   — ключ был и нёс пустую строку. Источник
     ответил «адреса нет». Это ОТВЕТ, а не молчание;
   * непустая строка     — адрес известен.

   ``or ""`` схлопывает первое во второе, то есть выдаёт наше незнание
   за ответ источника.

2. **Смысл поля НЕ ОПРЕДЕЛЁН, пока нет DRF-1589.**

   Адрес есть и у салона (``Tenant.address``), и у мастера
   (``CatalogMaster.address``). Правило старшинства «салон против
   мастера» — предмет DRF-1589, и его не существует ни в каком виде.

   Значит вопрос не в том, КАК читать трёхзначное поле, а в том, **чей
   адрес главнее**. Читатель, написанный до этого решения, ответит на
   этот вопрос молча и за владельца — и ответ разъедется с тем, что
   решат потом.

3. **Как правильно снять сторожа.** Внести файл в ``_ALLOWED`` с
   номером задачи, под которой читатель появился. Не удалять сторожа
   целиком: остальные места он продолжает держать.

# Почему сторож смотрит на код, а не на текст файла

Первая редакция искала совпадения по всему файлу и краснела на двух
комментариях, которые как раз ОБЪЯСНЯЮТ отсутствие читателя
(``apps/catalog/models.py`` и ``apps/marketplace/discovery.py``).
Прикрыть их исключением по имени файла значило бы ослепить сторожа
ровно в том файле, где настоящий читатель вероятнее всего и появится.
Поэтому комментарии и строковые литералы вырезаются токенайзером:
упоминание — не чтение.

# Что этот сторож НЕ проверяет

Он не смотрит на ``Tenant.address`` — у салонного адреса читатели есть
и они разобраны (``apps/marketplace/discovery.py`` — DRF-1609,
``apps/miniapp_api/views.py`` — DRF-1611). Предмет здесь ровно один:
адрес МАСТЕРА, у которого читателей нет.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Обращение к адресу мастера. Признак мастера обязателен рядом — иначе в
#: улов попадёт салонный адрес, у которого читатели законные.
_MASTER_ADDRESS_READ = re.compile(
    r"(?:"
    r"master\w*\.address\b"  # master.address, master_row.address
    r"|specialist\w*\.address\b"  # specialist.address
    r"|CatalogMaster[^\n]{0,80}\.address\b"
    r"|_master_address\b"  # снятый хелпер: вернётся — краснеем
    r")"
)

_SKIPPED_PARTS = (
    "migrations",
    "tests",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".git",
    "scripts",
)

#: Известные читатели. Пусто НАМЕРЕННО: сегодня их ноль, и это то
#: состояние, которое сторож охраняет. Появился читатель — впишите файл
#: и номер задачи, под которой он появился.
_ALLOWED: dict[str, str] = {}


def _code_only(source: str) -> str:
    """Тот же текст, но комментарии и строковые литералы забиты пробелами.

    Номера строк сохраняются — иначе адрес в сообщении об ошибке укажет
    не туда, а сторож, который врёт про место, хуже молчания. Файл,
    который не токенизируется, возвращается как есть: пусть лучше сторож
    перестрахуется, чем молча пропустит.
    """
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


def _python_files():
    for path in (REPO_ROOT / "apps").rglob("*.py"):
        if any(part in _SKIPPED_PARTS for part in path.parts):
            continue
        yield path.relative_to(REPO_ROOT).as_posix(), path


def _reads(source: str) -> list[tuple[int, str]]:
    code = _code_only(source)
    return [
        (code[: m.start()].count("\n") + 1, m.group(0)) for m in _MASTER_ADDRESS_READ.finditer(code)
    ]


def test_master_address_has_no_readers():
    """Первый читатель адреса мастера обязан краснить билд.

    Не потому, что читать запрещено, а потому, что читать НЕЧЕМ: правило
    старшинства «салон против мастера» (DRF-1589) не принято, и смысл
    поля не определён. Полный разбор — в докстринге модуля; он написан
    для того, кто увидит это красное.
    """
    offenders = []
    for rel, path in _python_files():
        if rel in _ALLOWED:
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        offenders.extend(f"{rel}:{line} — {frag}" for line, frag in _reads(source))
    assert not offenders, (
        "появился читатель `CatalogMaster.address`:\n  "
        + "\n  ".join(offenders)
        + "\n\nПРОЧТИТЕ ДОКСТРИНГ ЭТОГО МОДУЛЯ ПЕРЕД ТЕМ, КАК ПРОДОЛЖИТЬ.\n"
        + "Коротко: поле трёхзначно (None — источник промолчал, пустая строка — "
        + "источник сказал, что адреса нет), а правило старшинства "
        + "салон-против-мастера (DRF-1589) НЕ ПРИНЯТО, то есть неизвестно, чей "
        + "адрес главнее. Подстановка пустой строки вместо None здесь выдаёт "
        + "наше незнание за ответ источника.\n"
        + "Если читатель написан осознанно — впишите файл в _ALLOWED с номером задачи."
    )


def test_the_guard_actually_fires():
    """Встроенный targeted proof: сторож ловит код и не ловит прозу.

    Сторож, который не проверили на срабатывание, — это ``assert True`` с
    длинным именем. Проверка живёт внутри файла, поэтому её нельзя
    забыть повторить: если кто-то сузит регулярку или сломает вырезание
    комментариев, покраснеет здесь.
    """
    reader = "def show(master):\n    return master.address\n"
    assert _reads(reader) == [(2, "master.address")], "сторож не видит настоящего читателя"

    prose = '# было: master.address\ndef show(m):\n    """см. master.address"""\n    return 1\n'
    assert _reads(prose) == [], "сторож краснеет на упоминании — упоминание не чтение"

    salon = "def show(tenant):\n    return tenant.address\n"
    assert _reads(salon) == [], "сторож трогает салонный адрес, у которого читатели законные"


def test_the_field_still_exists():
    """Положительная стража: сторож охраняет существующее трёхзначное поле.

    Без неё «читателей нет» стало бы верным и в день, когда колонку
    удалят или сделают двузначной, — сторож зеленел бы, охраняя пустоту.
    """
    from apps.catalog.models import CatalogMaster

    field = CatalogMaster._meta.get_field("address")
    assert field.null is True, (
        "`CatalogMaster.address` перестал быть трёхзначным: без null=True "
        "«источник промолчал» неотличимо от «адреса нет», и охранять нечего"
    )


def test_every_allowance_names_its_task():
    """Пустой аллоулист — утверждение, а не забытая заготовка."""
    for rel, reason in _ALLOWED.items():
        assert reason.startswith("DRF-"), (
            f"{rel}: исключение обязано называть задачу, под которой появился "
            f"читатель. Получено: {reason!r}"
        )
        path = REPO_ROOT / rel
        assert path.exists(), f"исключение {rel} ссылается на несуществующий файл"
        assert _reads(path.read_text(encoding="utf-8", errors="ignore")), (
            f"{rel}: исключению больше нечего прикрывать — читателя в файле нет. Удалите строку."
        )
