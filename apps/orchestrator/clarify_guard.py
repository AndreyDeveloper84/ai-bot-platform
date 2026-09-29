"""Сторож границы C02 (DRF-1765): опция уточнения смысла — не услуга, не
мастер, не салон и не цена.

Макет C02 (DRF-1176): «Не показываем услуги, мастеров, салоны, цены,
рейтинги и расписание»; уточнение — про желаемый результат, не про
способ и не про исполнителя. Опции ``ask_clarification`` формулирует
модель (``discovery.py::ASK_CLARIFICATION_TOOL_SPEC``), и склонность
называть услуги у неё есть (``reground_specialization`` существует ради
этого). Здесь опция, нарушившая границу, снимается ДО рендера; если
сняты все — вопрос уходит без клавиатуры (режим ``free``): человек
отвечает своими словами, как макет и велит.

Что считается нарушением — по роли, не по написанию:

* **цена** — число с валютой / «руб» / «р.» или слова «цена», «стоимость»;
* **услуга** — опция, которая после того же разбора, что у поиска
  (``query_stems`` → ``parse_stems``), совпадает с именем услуги каталога
  как МНОЖЕСТВО стемов (равенство, не вхождение: «свежий вид» не станет
  «уходом за лицом» из-за одного общего слова);
* **мастер / салон** — опция дословно (casefold) равна имени бронируемого
  мастера или салона.

Сбой чтения каталога — опция остаётся (fail-open) с записью в журнал:
сторож границы не должен превращать живой вопрос в пустой из-за БД.
Названный предел: имена мастеров/салонов сравниваются дословно —
«к Анне» пройдёт; ловить падежи здесь значило бы стеречь написание.
"""

from __future__ import annotations

import logging
import re

from apps.marketplace.discovery import (
    discover_masters,
    discover_salons,
    discover_services,
    parse_stems,
    query_stems,
)

logger = logging.getLogger(__name__)

#: Цена в любом написании: «1500 ₽», «1 500 руб», «от 2000 р.», слова
#: «цена»/«стоимость». Число без валюты ценой не считается («2 недели»).
PRICE_RE = re.compile(
    r"(\d[\d\s]*\s*(₽|руб\.?|р\.)(?!\w))|(\bцен[аыу]\b)|(\bстоимост)",
    re.IGNORECASE,
)

#: Сколько имён услуг спросить у каталога на одну опцию.
_SERVICE_SCAN_LIMIT = 10
#: Сколько мастеров/салонов сравнить по имени (пилот — десятки).
_NAME_SCAN_LIMIT = 200

Reason = str  # "price" | "service" | "master" | "salon"


def _names_service(option: str) -> bool:
    stems = parse_stems(query_stems(option)).stems
    if not stems:
        return False
    wanted = set(stems)
    for card in discover_services(query=option, limit=_SERVICE_SCAN_LIMIT):
        if set(parse_stems(query_stems(card.name)).stems) == wanted:
            return True
    return False


def _names_master_or_salon(option: str) -> Reason | None:
    # Через ``apps.marketplace.discovery`` — единственный санкционированный
    # кросс-тенантный читатель каталога (MKT1, #1018); сюда доезжают только
    # бронируемые мастера и салоны с ними, т.е. ровно те имена, которые
    # человек мог бы увидеть на C05.
    folded = option.casefold()
    masters = discover_masters(limit=_NAME_SCAN_LIMIT)
    if any((card.name or "").strip().casefold() == folded for card in masters):
        return "master"
    salons = discover_salons(limit=_NAME_SCAN_LIMIT)
    if any((card.name or "").strip().casefold() == folded for card in salons):
        return "salon"
    return None


def clarification_option_violation(option: str) -> Reason | None:
    """Почему опция не может стоять в уточнении смысла; ``None`` — может."""
    if PRICE_RE.search(option):
        return "price"
    try:
        if _names_service(option):
            return "service"
        return _names_master_or_salon(option)
    except Exception:  # noqa: BLE001 — сторож границы не роняет вопрос
        logger.warning("clarify_guard.catalog_unreadable — option kept")
        return None


def filter_clarification_options(options: list[str]) -> tuple[list[str], list[Reason]]:
    """``(оставшиеся, причины снятых)``. Порядок оставшихся — прежний."""
    kept: list[str] = []
    dropped: list[Reason] = []
    for option in options:
        reason = clarification_option_violation(option)
        if reason is None:
            kept.append(option)
        else:
            dropped.append(reason)
    if dropped:
        # Причины, не тексты: опция — слова модели, журналу они не нужны.
        logger.info(
            "clarify_option_dropped reasons=%s kept=%d dropped=%d",
            ",".join(dropped),
            len(kept),
            len(dropped),
        )
    return kept, dropped
