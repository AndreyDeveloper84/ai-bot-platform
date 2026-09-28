"""DRF-2593: «слот» человеку не показывается — решение владельца 28.09, п.10.

«Внутренние технические термины, включая «слот», не выводить на
пользовательские поверхности» (``docs/OWNER_WORDS_DECISIONS_2026-09-28.md``,
п.10, OWNER APPROVED). Решение про то, что Ayla этим словом НЕ ГОВОРИТ, а не
про то, что она его НЕ ПОНИМАЕТ: человек, написавший «есть свободные слоты?»,
по-прежнему понят.

# Что сторож считает видимым текстом

* Python — строковые литералы, кроме докстрингов (AST): комментарий и
  докстринг человеку не уходят, литерал — может;
* TS/TSX Mini App — текст вне комментариев ``//`` и ``/* */``: подпись кнопки,
  JSX-текст, ``aria-label``, аргумент ``setNotice(…)``.

Имена в коде и CSS-класс ``slot-grid`` латиницей и слову не совпадают по
построению. Тесты, миграции, ``docs/`` и мёртвый ``legacy_maxbot/`` (импортирует
пакет ``maxbot``, которого в дереве нет) не сканируются.

# Как сторож отличает видимый текст — признаком, а не списком файлов

* **Распознавание** — литерал, переданный в ``re.compile`` / ``re.search`` /
  ``re.match`` / ``re.fullmatch``: это шаблон слов ЧЕЛОВЕКА, не наша речь.
  Узнаётся по месту в дереве разбора, а не по имени файла.
* **Имена в коде** (``slot-grid``, ``show_slots``) — латиница, слову не
  совпадают по построению.
* Остальное — закрытый список ``EXCEPTIONS``: адрес (файл + дословный кусок
  текста, не номер строки — номера плывут) и причина в той же строке. Новая
  запись в нём — решение, а не способ позеленеть; запись, которая больше
  ничего не находит, краснит ``test_every_exception_still_matches``.
"""

from __future__ import annotations

import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORD = re.compile(r"слот", re.IGNORECASE)

#: (файл, дословный кусок текста, причина).
EXCEPTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "apps/skills/menu/matching.py",
        "какие слоты",
        "сигнал РАСПОЗНАВАНИЯ слов человека («какие слоты»), не наша речь",
    ),
    (
        "apps/persona/voice.py",
        "в том числе слово",
        "запрет модели называет слово, которое запрещает (NO_INTERNAL_TERMS_RULE) — "
        "человеку не показывается",
    ),
    (
        "apps/orchestrator/intent_resolution.py",
        "resolver намерений Ayla",
        "промпт классификатора; «слот» там — поле разбора намерения, выход — "
        "JSON, человеку не показывается",
    ),
)

#: Ждут слова владельца (DRF-2593: 8, 10, 11 — главное окно несёт вопрос).
#: Не исключения по сути: по ответу эти строки правятся, запись отсюда
#: снимается, и сторож продолжает держать их, как остальные.
AWAITING_OWNER: tuple[tuple[str, str, str], ...] = (
    (
        "apps/miniapp/src/screens/CustomerMasterDetailScreen.tsx",
        "Ближайшие слоты",
        "п.10 снимает прежний выбор Tau §8 F2 — решает владелец",
    ),
    (
        "apps/miniapp/src/screens/admin/AdminSalonDayScreen.tsx",
        "точное время этого слота",
        "готового слова у владельца нет — вопрос",
    ),
    (
        "apps/miniapp/src/screens/admin/SalonPilotScheduleScreen.tsx",
        "а не готовый слот",
        "готового слова у владельца нет — вопрос",
    ),
)

_REGEX_CALLS = {"compile", "search", "match", "fullmatch", "findall", "finditer", "sub", "split"}


def _skipped(rel: str) -> bool:
    return (
        "/tests/" in rel
        or rel.startswith(("tests/", "docs/", "legacy_maxbot/"))
        or "/migrations/" in rel
        or ".test." in rel
        or "__tests__" in rel
        or "node_modules" in rel
        or ".venv" in rel
    )


def python_hits(source: str) -> list[tuple[int, str]]:
    """Строковые литералы со словом: без докстрингов и без шаблонов ``re``."""
    tree = ast.parse(source)
    not_speech: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            not_speech.add(id(node.value))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _REGEX_CALLS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "re"
            and node.args
        ):
            for sub in ast.walk(node.args[0]):
                not_speech.add(id(sub))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in not_speech
        and WORD.search(node.value)
    ]


#: Строка раньше комментария: «/*» в ``accept="image/*"`` или ``path="/x/*"`` —
#: не начало комментария. Без этого до следующего «*/» пропадал живой JSX
#: (ревью DRF-2593: ~28 строк FoodScannerCaptureScreen, маршруты App.tsx).
_TS_STRING_OR_COMMENT = re.compile(
    r"(\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)"
    r"|(/\*.*?\*/|//[^\n]*)",
    re.DOTALL,
)


def _blank_comments(source: str) -> str:
    def repl(m: re.Match[str]) -> str:
        if m.group(1) is not None:
            return m.group(1)
        return re.sub(r"[^\n]", " ", m.group(2))

    return _TS_STRING_OR_COMMENT.sub(repl, source)


def ts_hits(source: str) -> list[tuple[int, str]]:
    """Строки TS/TSX со словом вне комментариев; номера строк сохраняются."""
    blanked = _blank_comments(source)
    return [
        (n, line.strip()) for n, line in enumerate(blanked.splitlines(), 1) if WORD.search(line)
    ]


def _scan() -> tuple[list[tuple[str, int, str]], dict[str, int]]:
    found: list[tuple[str, int, str]] = []
    scanned = {"py": 0, "ts": 0}
    for path in (ROOT / "apps").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if _skipped(rel):
            continue
        scanned["py"] += 1
        for line, text in python_hits(path.read_text(encoding="utf-8-sig")):
            found.append((rel, line, text))
    for pattern in ("*.ts", "*.tsx"):
        for path in (ROOT / "apps" / "miniapp" / "src").rglob(pattern):
            rel = path.relative_to(ROOT).as_posix()
            if _skipped(rel):
                continue
            scanned["ts"] += 1
            for line, text in ts_hits(path.read_text(encoding="utf-8")):
                found.append((rel, line, text))
    return found, scanned


def _covered(rel: str, text: str) -> bool:
    return any(rel == r and snippet in text for r, snippet, _ in EXCEPTIONS + AWAITING_OWNER)


def test_no_slot_word_reaches_a_person():
    found, scanned = _scan()
    # Нижние границы — ПОРОЗНЬ: Python один перекрывал общую, и пустой обход
    # TS (сломанный glob, переезд src/) зеленел бы молча (ревью DRF-2593).
    assert scanned["py"] > 500, scanned
    assert scanned["ts"] > 150, scanned
    speech = [
        f"{rel}:{line}: {text.strip()[:90]}" for rel, line, text in found if not _covered(rel, text)
    ]
    assert speech == [], (
        "«слот» в тексте, который видит человек (решение владельца 28.09, п.10, "
        "docs/OWNER_WORDS_DECISIONS_2026-09-28.md). Нужно слово владельца, не новое "
        "своё; распознавание слов человека — через re.* или EXCEPTIONS с причиной:\n"
        + "\n".join(speech)
    )


def test_every_exception_still_matches():
    """Запись, которая ничего не находит, — лишняя дыра в стороже."""
    found, _ = _scan()
    for rel, snippet, _reason in EXCEPTIONS + AWAITING_OWNER:
        assert any(r == rel and snippet in text for r, _, text in found), (
            f"{rel}: «{snippet}» больше не найдено — убрать запись"
        )


class TestTheGuardSeesWhatAPersonSees:
    """Самопроверка: без неё «чисто» зеленело бы и у сторожа, не видящего ничего."""

    def test_a_button_label_is_seen(self):
        src = 'export const X = () => <button>{v ? "Дальше" : "Выберите слот"}</button>;'
        assert ts_hits(src)

    def test_an_aria_label_is_seen(self):
        assert ts_hits('<div className="slot-grid" aria-label="Свободные слоты">')

    def test_a_recognition_pattern_is_not_speech_by_construction(self):
        src = (
            "import re\n"
            'ASK = re.compile(r"свободн\\w*\\s+(?:слот\\w*)", re.I)\n'
            'REPLY = "Свободные слоты на завтра:"\n'
        )
        assert [line for line, _ in python_hits(src)] == [3]

    def test_comments_and_latin_names_are_not(self):
        src = (
            "// слот в комментарии\n"
            "/* и слоты\n в блоке */\n"
            'const slotGrid = "slot-grid";\n'
            "<b>Выберите слот</b>\n"
        )
        # Присутствие: видимая строка найдена — сторож не слепой; комментарии и
        # латинское имя рядом с ней — нет.
        assert [line for line, _ in ts_hits(src)] == [5]

    def test_a_slash_star_inside_a_string_does_not_open_a_comment(self):
        src = (
            '<input type="file" accept="image/*" />\n'
            "<b>Выберите слот</b>\n"
            "{/* закрывающий комментарий */}\n"
        )
        assert [line for line, _ in ts_hits(src)] == [2]

    def test_a_python_reply_is_seen_and_a_docstring_is_not(self):
        src = (
            "def f():\n"
            '    """Докстринг про слот."""\n'
            "    # комментарий про слот\n"
            '    return "Свободные слоты на завтра:"\n'
        )
        assert [line for line, _ in python_hits(src)] == [4]


class TestTheModelIsToldNotToSayIt:
    """Строка запрета стоит в обоих промптах, которые отвечают о времени записи.

    ПРЕДЕЛ, и он главный: узел охраняет ОБЕЩАНИЕ от удаления, но не его
    ИСПОЛНЕНИЕ. Модель может ослушаться, а в CI модели нет.

    Замер 28.09 через обвязку ``apps/replay/tests/test_live_path_gate.py``
    (канареечная модель): «есть свободные слоты?» на живом пути —
    ``llm_called=True``, ответ даёт консьерж, детерминированной ветки нет.
    Replay-фикстуры для этого входа НЕТ намеренно: контракт набора требует,
    чтобы запрещённое не пересекалось со словами человека (сверка с эхом), а
    здесь запрещено именно слово человека. Да и гейт на ходе модели лишь
    пропускает фикстуру по имени. Голос модели проверяется прогоном на
    стенде, не здесь.
    """

    def test_the_concierge_prompt_carries_the_rule(self):
        from apps.orchestrator.concierge import build_concierge_system_prompt
        from apps.persona.voice import NO_INTERNAL_TERMS_RULE

        prompt = build_concierge_system_prompt()

        assert NO_INTERNAL_TERMS_RULE in prompt

    def test_the_booking_prompt_carries_the_rule(self):
        from apps.persona.voice import NO_INTERNAL_TERMS_RULE
        from apps.skills.booking.prompts import BrandVoiceConfig, build_booking_prompt

        messages = build_booking_prompt(
            brand_voice=BrandVoiceConfig(persona="Алина"), query="есть свободные слоты?"
        )

        assert NO_INTERNAL_TERMS_RULE in messages[0]["content"]


class TestUnderstoodButNotSaid:
    """Пара: человек со словом «слот» понят — и ответ этого слова не содержит.

    Узел «слова нет в ответе» в одиночку прошёл бы и на сломанном
    распознавании: ответ без слова легко получить, не поняв вопроса.
    """

    def test_the_person_saying_slots_is_understood(self):
        from apps.skills.booking.lookup import is_booking_request
        from apps.skills.menu.matching import looks_like_booking_request

        assert looks_like_booking_request("какие слоты есть на завтра?")
        assert is_booking_request("свободные слоты есть на завтра?")

    def test_the_reply_does_not_say_it(self):
        from apps.skills.booking.tools import SlotCandidate, _format_slots_text

        empty = _format_slots_text([], "2026-10-01")
        some = _format_slots_text(
            [SlotCandidate(datetime="2026-10-01T14:00:00+03:00", duration_minutes=60)],
            "2026-10-01",
        )

        # Присутствие: ответ собран и называет дату и время.
        assert "2026-10-01" in empty
        assert "14:00" in some
        assert not WORD.search(empty)
        assert not WORD.search(some)
