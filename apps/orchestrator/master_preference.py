"""Предпочтение мастера в разговоре — DRF-2829.

Решение владельца 06.10 (через главное окно, вопрос Q1 = а):

* «предпочитаю Анну» — МЯГКО: Анна поднимается в выдаче, остальные остаются;
* «только Анна» / «именно к Анне» — ЖЁСТКО: выдача сужается до Анны, другим
  мастером не отвечать;
* у Анны нет подходящего — объяснить и СПРОСИТЬ разрешения на других. Не
  предлагать молча и не подменять автоматически.

Почему жёсткий ответ детерминированный, без второго прохода модели: проход
модели над пустым результатом — ровно то место, где «Анны нет» превращалось в
список чужих мастеров. Разрешение спрашивает кнопка, и только она открывает
обычную выдачу (``cb:discover:more`` со смещением 0 — тот же поиск, что
«Показать ещё», без мастера).

Кто есть и кто что делает, решает каталог (``find_masters_by_name``), а не
модель. Имя мастера из реплики — сказанное сейчас, не память, поэтому
NEVER_CROSSES его не касается (вывод подтверждён главным окном 06.10).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Final

from apps.marketplace.discovery import MasterCard, find_masters_by_name
from apps.orchestrator.discovery import (
    _MAX_ECHOED_QUERY_CHARS,
    _MAX_MASTER_CARDS,
    CALLBACK_DISCOVER_MORE_PREFIX,
    DiscoveryReply,
    _render_master_cards,
    encode_more_ref,
    show_salons_button,
)
from apps.orchestrator.next_steps import menu_button, next_step_action_data

logger = logging.getLogger(__name__)

STRENGTH_HARD: Final = "hard"
STRENGTH_SOFT: Final = "soft"

#: Слова, которые делают предпочтение жёстким независимо от того, что
#: поставила модель: «только», «именно», «исключительно». Модель может
#: пропустить слово — реплика человека его не теряет.
_HARD_MARKER_RE = re.compile(r"(?<!\w)(только|именно|исключительно)(?!\w)", re.IGNORECASE)

#: Кнопка-разрешение. Нажатие — и есть согласие на альтернативы.
SHOW_OTHERS_LABEL: Final = "Показать других"

#: Несколько мастеров с этим именем — вопрос, а не выбор за человека
#: (решение владельца Q2, 06.10).
SEVERAL_TEXT: Final = "Нашла несколько мастеров с таким именем — выбери, к кому:"

_NOT_FOUND_TEXT: Final = "Не нашла мастера «{name}»{where}. Показать других мастеров{scope}?"
_NOT_FOUND_NO_SCOPE_TEXT: Final = (
    "Не нашла мастера «{name}». Назови услугу — покажу, кто её делает, или выбери салон."
)
_NO_SERVICE_TEXT: Final = (
    "У мастера «{name}» не нашла «{service}». Показать других мастеров по «{service}»?"
)


def is_hard(args: dict[str, Any], message_text: str) -> bool:
    """Жёсткое ли предпочтение: так сказала модель или так сказал человек."""

    if str(args.get("master_strength") or "").strip().lower() == STRENGTH_HARD:
        return True
    return bool(_HARD_MARKER_RE.search(message_text or ""))


def _echo(value: str | None) -> str:
    return (value or "").strip()[:_MAX_ECHOED_QUERY_CHARS]


def _ask_for_others(text: str, *, city: str | None, specialization: str | None) -> DiscoveryReply:
    """Объяснение + вопрос, ответить на который можно одним нажатием (§72)."""

    ref = encode_more_ref(offset=0, city=city, specialization=specialization)
    if ref:
        buttons = (
            {"label": SHOW_OTHERS_LABEL, "callback": f"{CALLBACK_DISCOVER_MORE_PREFIX}{ref}"},
            menu_button(),
        )
    else:
        buttons = (show_salons_button(), menu_button())
    return DiscoveryReply(text=text, action_data=next_step_action_data(*buttons))


def not_found_reply(name: str, *, city: str | None, specialization: str | None) -> DiscoveryReply:
    """Мастера с этим именем нет — сказать и спросить, без чужих карточек."""

    shown = _echo(name)
    spec = _echo(specialization)
    town = _echo(city)
    if not spec and not town:
        # Искать «других» не по чему: обычная выдача без критериев запрещена
        # каноном (BOT-003 §9). Выход — услуга словами или салоны.
        return DiscoveryReply(
            text=_NOT_FOUND_NO_SCOPE_TEXT.format(name=shown),
            action_data=next_step_action_data(show_salons_button(), menu_button()),
        )
    where = f" в городе {town}" if town else ""
    scope = f" по «{spec}»" if spec else where
    return _ask_for_others(
        _NOT_FOUND_TEXT.format(name=shown, where=where if spec else "", scope=scope),
        city=city,
        specialization=specialization,
    )


def no_service_reply(name: str, *, service: str, city: str | None) -> DiscoveryReply:
    """Мастер есть, а этой услуги у неё нет — сказать и спросить про других."""

    return _ask_for_others(
        _NO_SERVICE_TEXT.format(name=_echo(name), service=_echo(service)),
        city=city,
        specialization=service,
    )


def hard_reply(name: str, *, city: str | None, specialization: str | None) -> DiscoveryReply:
    """Жёсткое предпочтение: только названный мастер, иначе — вопрос."""

    spec = (specialization or "").strip() or None
    named = find_masters_by_name(
        name,
        city=city,
        service=spec,
        limit=_MAX_MASTER_CARDS,
        require_service=spec is not None,
    )
    logger.info(
        "orchestrator.master_preference.hard matched=%d with_service=%s",
        len(named),
        spec is not None,
    )
    if named:
        rendered = _render_master_cards(named, city=city, specialization=spec)
        if len(named) == 1:
            return rendered
        lines = [SEVERAL_TEXT, *(f"• {card.name}" for card in named)]
        return DiscoveryReply(text="\n".join(lines), action_data=rendered.action_data)
    if spec is not None and find_masters_by_name(name, city=city, limit=1):
        return no_service_reply(name, service=spec, city=city)
    return not_found_reply(name, city=city, specialization=spec)


def lift_named(
    cards: list[MasterCard],
    name: str,
    *,
    city: str | None,
    specialization: str | None,
    limit: int,
) -> list[MasterCard]:
    """Мягкое предпочтение: названный мастер первым, остальные следом.

    Названный ищется ТЕМ ЖЕ правилом услуги, что и выдача, — мягкость не
    повод поставить первым мастера, который этого не делает. Не нашёлся —
    выдача не меняется: мягкое не сужает и не объясняется.
    """

    spec = (specialization or "").strip() or None
    named = find_masters_by_name(
        name, city=city, service=spec, limit=limit, require_service=spec is not None
    )
    if not named:
        return cards
    lifted = {card.master_id for card in named}
    return [*named, *(card for card in cards if card.master_id not in lifted)][:limit]


__all__ = [
    "SEVERAL_TEXT",
    "SHOW_OTHERS_LABEL",
    "STRENGTH_HARD",
    "STRENGTH_SOFT",
    "hard_reply",
    "is_hard",
    "lift_named",
    "no_service_reply",
    "not_found_reply",
]
