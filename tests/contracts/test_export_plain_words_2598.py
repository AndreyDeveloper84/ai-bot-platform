"""DRF-2598: выгрузка персональных данных объясняет человеку его данные его словами.

Раздел ``coverage`` выгрузки (``apps/identity/export_coverage.py``) — текст,
который получает САМ субъект: ``explanation``, причина по каждой невыгруженной
строке (``withheld[].reason``) и ``known_limits``. Читается он в одной
ситуации — человек насторожён и спрашивает «что вы обо мне храните». Текст,
объясняющий его данные через устройство нашего кода («JSON-мешок», модуль,
константа, номер листа), не объясняет ничего. Тот же класс, что «слот» в
DRF-2593 (``test_no_slot_word_for_people_2593.py``), на другой поверхности.

# Что сторож считает текстом субъекту — признаком, а не файлом

Не исходник, а то, что СОБИРАЕТ ``build_coverage_section()``: строки
``explanation``, ``withheld[].reason``, ``known_limits``. Ключи ``field`` и
``included`` — машинная часть формата, их сторож не читает и не трогает.

# Что запрещено

Внутреннее устройство: имена форматов и хранилищ (JSON, Redis, Postgres, TTL,
RFM/LTV), пути модулей и классов, константы, идентификаторы ``snake_case``,
номера листов ``DRF-…``, файлы ``*.md`` и жаргон разработки («мешок», «промпт»,
«гейт», «свип», «сборка» …).

Отдельный признак — **наше внутреннее состояние решения**: «стоит вопросом к
владельцу», «отдельное решение владельца», «никто не принял решения». Человеку,
который спросил, что мы о нём храним, не сообщают, кто у нас что ещё не
решил (главное окно 29.09: «нет, никогда»).

Разрешено: имена разделов самой выгрузки — человек видит их ключами в том же
файле (``memory``, ``consents``, ``ayla`` и разделы ``ayla``), и названия
продуктов (Ayla, MAX, BeautyGO).

# Ждут слова владельца

Своих слов в код не ставим (решение 28.09, п.10: «новых фраз не выдумывать»).
``AWAITING_OWNER`` — снимок того, что сегодня есть в каждой строке: строка
ждёт формулировки владельца, вопрос с нашим предложением несёт главное окно.
Сторож краснеет, если в строке появилось НОВОЕ техническое слово, и если
запись больше ничего не находит (строку переписали — запись снять).
"""

from __future__ import annotations

import re

from apps.identity.export_coverage import CATALOG_EXPORT_SECTIONS, build_coverage_section

#: Разделы, которые человек видит ключами в том же файле выгрузки.
VISIBLE_SECTION_KEYS = frozenset(
    {
        "memory",
        "consents",
        "ayla",
        "personal_context",
        "preferences",
        "recommendations",
        "nutrition_notification_settings",
        "coverage",
        *CATALOG_EXPORT_SECTIONS,
    }
)
PRODUCT_NAMES = frozenset({"Ayla", "MAX", "BeautyGO"})

FORMATS = re.compile(r"\b(JSON|Redis|Postgres|PostgreSQL|TTL|RFM|LTV|API|SQL|FK)\b")
DOTTED = re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b")
CONSTANT = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b")
SNAKE = re.compile(r"\b[a-z][a-z0-9]*_[a-z0-9_]+\b")
CAMEL = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b")
TICKET = re.compile(r"\bDRF-\d+\b")
MD_FILE = re.compile(r"\b\w+\.md\b")
JARGON = re.compile(
    r"мешок|мешка|промпт|гейт|свип|форензик|надгробие|развёртк|сборк|"
    r"оболочк|чатовый путь|аудируем|каскад|недо-отчёт|вышестоящ",
    re.IGNORECASE,
)
#: Наше незавершённое решение, показанное человеку.
UNDECIDED = re.compile(
    r"вопрос\w* к владельцу|стоит вопросом|отдельн\w* решени\w* владельца|"
    r"не принял\w* решени|до слова владельца|решит владелец",
    re.IGNORECASE,
)


def technical_words(text: str) -> set[str]:
    found: set[str] = set()
    for pattern in (FORMATS, DOTTED, CONSTANT, CAMEL, TICKET, MD_FILE, JARGON, UNDECIDED):
        found.update(m.group(0) for m in pattern.finditer(text))
    for m in SNAKE.finditer(text):
        if m.group(0) not in VISIBLE_SECTION_KEYS:
            found.add(m.group(0))
    return {w for w in found if w not in PRODUCT_NAMES}


def subject_texts() -> dict[str, str]:
    """Адрес → текст, который выгрузка отдаёт субъекту."""
    coverage = build_coverage_section()
    texts = {"explanation": coverage["explanation"]}
    for row in coverage["withheld"]:
        texts[f"withheld[{row['field']}]"] = row["reason"]
    for i, text in enumerate(coverage["known_limits"]):
        texts[f"known_limits[{i}]"] = text
    return texts


#: Снимок 29.09 (бот ``origin/dev`` ``a5019535``): дословное начало текста →
#: технические слова, которые в нём уже есть и ждут формулировки владельца.
#: Адрес — кусок текста, а не номер строки выгрузки: одна причина стоит у
#: многих полей (у 13 полей «Вычисленный снимок…»), номера плывут. Не
#: исключения по сути — по ответу текст переписывается, запись снимается.
AWAITING_OWNER: dict[str, frozenset[str]] = {
    "Неотправленный черновик ответа мас": frozenset(["чатовый путь"]),
    "Переписка целиком: каждое сообщени": frozenset(["отдельное решение владельца", "форензик"]),
    "Диктовки сотрудника салонному асси": frozenset(["Каскад"]),
    "Контактные и профильные значения н": frozenset(["JSON", "оболочк"]),
    "JSON-мешок «флагов персонализации»": frozenset(["JSON", "мешка", "мешок"]),
    "Вычисленный снимок RFM/LTV/риска:": frozenset(
        ["LTV", "RFM", "churn_risk", "low_rating_flag", "sentiment_score"]
    ),
    "Красная зона — специальная категор": frozenset(["отдельное решение владельца"]),
    "Жёлтая зона — личные факты с обяза": frozenset(["TTL", "вопрос к владельцу"]),
    "Состояние текущего разговора у дви": frozenset(["Redis", "чатовый путь"]),
    "Входящие сообщения из MAX в том ви": frozenset(["Redis", "чатовый путь"]),
    "Техническая обратная карта «токен": frozenset(
        ["Postgres", "Redis", "промпт", "свип", "чатовый путь"]
    ),
    "Кратковременная память диалога в R": frozenset(["Redis", "чатовый путь"]),
    "Разделы memory и personal_context": frozenset(
        ["гейт", "надгробие", "недо-отчёт", "промпт", "развёртк"]
    ),
    "Раздел ayla отдаётся вышестоящей с": frozenset(["вышестоящ"]),
}


def test_the_surface_is_read_and_the_detector_sees() -> None:
    texts = subject_texts()
    # Положительный контроль обхода: без строк «ничего не найдено» — не вердикт.
    assert "explanation" in texts
    assert sum(k.startswith("withheld[") for k in texts) >= 10
    assert sum(k.startswith("known_limits[") for k in texts) >= 1
    # Положительный контроль признака: каждое правило ловит свой пример.
    assert technical_words("JSON-мешок без схемы") >= {"JSON", "мешок"}
    assert "personal_fields.POLICY_DEBT" in technical_words("см. personal_fields.POLICY_DEBT")
    assert "ANONYMIZED_DIALOGUE_RETENTION_DAYS" in technical_words(
        "(ANONYMIZED_DIALOGUE_RETENTION_DAYS)"
    )
    assert "RedZoneAccessLog" in technical_words("пишет RedZoneAccessLog")
    assert "DRF-1369" in technical_words("(DRF-1369)")
    assert "churn_risk" in technical_words("часть из них (churn_risk)")
    assert "стоит вопросом" in technical_words("сам срок стоит вопросом к владельцу")
    assert "отдельное решение владельца" in technical_words("— отдельное решение владельца")
    # Разрешённое не ловится: раздел выгрузки и название продукта.
    assert technical_words("выгружается в разделе memory и в разделе food_diary у Ayla") == set()


def _awaiting_for(text: str) -> frozenset[str]:
    for fragment, words in AWAITING_OWNER.items():
        if text.startswith(fragment):
            return words
    return frozenset()


def test_no_new_technical_words_in_text_for_the_subject() -> None:
    new = {}
    for address, text in subject_texts().items():
        extra = technical_words(text) - _awaiting_for(text)
        if extra:
            new[address] = sorted(extra)
    assert new == {}, f"техническое в тексте субъекту (DRF-2598): {new}"


def test_every_awaiting_entry_still_matches() -> None:
    texts = set(subject_texts().values())
    stale = {}
    for fragment, words in AWAITING_OWNER.items():
        text = next((t for t in texts if t.startswith(fragment)), "")
        gone = words - technical_words(text)
        if gone:
            stale[fragment] = sorted(gone)
    assert stale == {}, f"строку переписали — снять из AWAITING_OWNER: {stale}"
