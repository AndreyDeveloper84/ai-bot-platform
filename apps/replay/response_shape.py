"""Форма ответа вместо самого ответа — читаемый отказ golden-узла (DRF-2440).

Решение «текст ответа наружу не выносим» остаётся: фикстуры гоняются на живых
репликах, а отчёт CI читают люди, у которых нет права видеть переписку. До этого
листа цена была такая: сообщение падения печатало литерал ``<text>``, и по нему
нельзя было отличить **«навык ответил иначе»** от **«ответ подменили после
навыка»**. На этот вопрос 24.09 ушло полдня и две неверные версии подряд.

Здесь — середина, которой не было: не текст и не заглушка, а **форма**.

# Что несёт форма и почему каждое поле безопасно

* ``empty`` / ``chars`` / ``words`` — размер ответа. Пустой ответ и ответ в две
  строки — разные поломки, и сегодня они выглядели одинаково;
* ``matched`` / ``missing`` / ``hit`` — какие из **ожидаемых** подстрок нашлись.
  Это строки ФИКСТУРЫ, а не реплики: их написали мы, они лежат в репозитории, и
  назвать их — не вынести ответ;
* ``template`` — совпал ли ответ **целиком** с одним из известных служебных
  текстов (технический сбой, «не разобрала», «не получилось подготовить ответ»,
  кризисный и неотложный тексты). Это и есть ответ на вопрос «подменили ли»:
  когда вместо ответа навыка приходит ``outage_ru``, видно, что говорил не
  навык. Имя шаблона — наша константа, не содержимое переписки.

Чего в форме нет и не будет: ни одного фрагмента самого ответа — ни первых
символов, ни хвоста, ни «похожих» слов. Это стережёт
``apps/replay/tests/test_failure_shape_2440.py``.

# Почему реестр шаблонов — импорты, а не копии строк

Копия текста в этом файле разошлась бы с оригиналом молча, и ``template`` стал
бы отвечать «none» ровно тогда, когда подмена и случилась. Поэтому здесь только
адреса (модуль, имя), значение читается у источника, а сторож проверяет, что
каждый адрес ещё резолвится: переименование роняет тест, а не слепнет.
"""

from __future__ import annotations

from typing import Any, Iterable

#: Служебные тексты, которыми ответ навыка может оказаться ЗАМЕНЁН. Имя → адрес
#: константы у источника. Список намеренно короткий: сюда идёт то, что отвечает
#: на вопрос «говорил ли вообще навык», а не всякая известная строка.
TEMPLATES: dict[str, tuple[str, str]] = {
    "outage_ru": ("apps.orchestrator.llm.templates", "OUTAGE_RU"),
    "outage_en": ("apps.orchestrator.llm.templates", "OUTAGE_EN"),
    "not_parsed_ru": ("apps.orchestrator.llm.templates", "NOT_PARSED_RU"),
    "not_parsed_en": ("apps.orchestrator.llm.templates", "NOT_PARSED_EN"),
    "no_answer_ru": ("apps.orchestrator.llm.templates", "NO_ANSWER_RU"),
    "no_answer_en": ("apps.orchestrator.llm.templates", "NO_ANSWER_EN"),
    "medical_emergency_v2": (
        "apps.orchestrator.safety.medical_emergency",
        "MEDICAL_EMERGENCY_TEXT_V2",
    ),
}

#: Что печатается, когда ответ ни с одним шаблоном не совпал. Не «unknown»:
#: неизвестен он не нам, а вопросу — это ответ навыка или что-то своё.
NO_TEMPLATE = "none"


def _resolve(module: str, attr: str) -> str | None:
    """Значение константы у источника, или None, если адрес больше не живёт."""

    from importlib import import_module

    try:
        value = getattr(import_module(module), attr)
    except (ImportError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def template_registry() -> dict[str, str]:
    """Имя → живое значение. Неразрешённые адреса выпадают (их ловит сторож)."""

    resolved: dict[str, str] = {}
    for name, (module, attr) in TEMPLATES.items():
        value = _resolve(module, attr)
        if value:
            resolved[name] = value
    return resolved


def known_template(response_text: str) -> str | None:
    """Имя служебного текста, если ответ совпал с ним ЦЕЛИКОМ, иначе None.

    Сравнение по полному тексту, а не по вхождению: «ответ содержит слова
    шаблона» бывает и у честного ответа навыка, а «ответ ЕСТЬ шаблон» —
    это уже замена.
    """

    probe = response_text.strip()
    if not probe:
        return None
    for name, value in template_registry().items():
        if probe == value.strip():
            return name
    return None


def describe(
    response_text: str,
    needles: Iterable[Any] = (),
    *,
    case_folded: bool = True,
) -> str:
    """Форма ответа одной строкой — без единого фрагмента самого ответа.

    ``needles`` — подстроки ФИКСТУРЫ (их можно называть). ``case_folded``
    повторяет правило вызывающей проверки: ``response_contains_any`` / ``_all``
    складывают регистр, ``response_contains_exact`` — нет, и форма обязана
    отвечать про то же сравнение, иначе она объяснит не тот промах.
    """

    text = str(response_text)
    wanted = [str(n) for n in needles]
    haystack = text.casefold() if case_folded else text
    hits = [n for n in wanted if (n.casefold() if case_folded else n) in haystack]
    missing = [n for n in wanted if n not in hits]

    parts = [
        f"empty={'true' if not text.strip() else 'false'}",
        f"chars={len(text)}",
        f"words={len(text.split())}",
    ]
    if wanted:
        parts.append(f"matched={len(hits)}/{len(wanted)}")
        if missing:
            parts.append(f"missing={missing!r}")
        if hits:
            parts.append(f"hit={hits!r}")
    parts.append(f"template={known_template(text) or NO_TEMPLATE}")
    return "response(" + " ".join(parts) + ")"


__all__ = ["NO_TEMPLATE", "TEMPLATES", "describe", "known_template", "template_registry"]
