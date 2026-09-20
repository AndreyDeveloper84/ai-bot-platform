"""Анонс памяти — «Запомнила: …» при первом сохранении зелёного факта (DRF-1292).

Решение владельца 19.09 (§52 В3, комментарий листа): бот запоминает молча
(решение 23.08), но человек должен узнать о памяти, не спрашивая о ней. Форма
выбрана одна — фраза при первой записи факта, один раз на факт, при
supersede — снова:

    Запомнила: ты придерживаешься веганского питания. Скажи «забудь про
    питание», если не надо.

Второй канал обнаружимости — экран «Что Ayla помнит» в Mini App (DRF-2133).
Пункт меню и строка в приветствии — не делаем (решение 19.09).

### Откуда фраза

Из тех же записанных строк, что и всё остальное: ``describe_green_content``
(«Помню, что ты …» в чате, ``label`` на экране) — одна формулировка, три
места. Факт без читаемой фразы не объявляется: строка «Запомнила: {json}»
хуже молчания.

### Чего здесь нет

* **Выведенного.** ``source != explicit`` (inferred / signal) — не сказанное,
  и объявлять его как сказанное нельзя (P0-3): такая строка — ложный вход, о
  ней тест. Выведенное видно в списке с пометкой «мы предположили».
* **Второй служебной строки на ход.** Вопрос памяти (``memory_ask``) и анонс
  — одна и та же полоса внизу ответа; вдвоём они превращают ответ в анкету.
  :func:`weave_service_line` — единственное место, где решается, что туда
  идёт: анонс приоритетнее (он про то, что только что произошло; вопрос
  подождёт — ``mark_asked`` при этом не ставится, кулдаун не тратится).

### Почему «забудь про питание», а не «забудь»

Голое «забудь» команда памяти не понимает (``_FORGET_FIELD_RE`` требует
объект, ``_FORGET_ALL_RE`` — «всё»/«меня»): строка обещала бы то, чего нет.
Доменное «забудь про {домен}» — существующая команда, снимает ВЕСЬ домен
(питание целиком, не одну строку) — так и сказано. Ярлыки доменов — те же,
что у команды (``memory_commands._DOMAIN_LABELS``); ключ без ярлыка → без
подсказки, только «Запомнила».
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

from apps.identity.models import MemoryEntry
from apps.orchestrator.discovery import DiscoveryReply
from apps.orchestrator.memory.write_sink import WriteSink

logger = logging.getLogger(__name__)

#: Бюджет на связь с Ayla, когда запись идёт ДО отправки ответа (решение
#: главного окна 20.09): дольше — факт пишется после отправки, без строки.
PRE_SEND_LINK_BUDGET_S = 1.0

ANNOUNCE_HEAD = "Запомнила: ты "
ANNOUNCE_TAIL = "Скажи «забудь», если не надо."
_TAIL_WITH_DOMAIN = "Скажи «забудь про {label}», если не надо."
_TAIL_WITH_DOMAINS = "Скажи {commands}, если не надо."


def _domain_label(entry: Any) -> str | None:
    from apps.persona.memory_commands import _DOMAIN_LABELS, _domain_of

    content = entry.content if isinstance(entry.content, dict) else {}
    key = content.get("key")
    if not isinstance(key, str) or not key:
        return None
    return _DOMAIN_LABELS.get(_domain_of(key))


def _tail(labels: list[str]) -> str:
    if not labels:
        return ANNOUNCE_TAIL
    if len(labels) == 1:
        return _TAIL_WITH_DOMAIN.format(label=labels[0])
    commands = " или ".join(f"«забудь про {label}»" for label in labels)
    return _TAIL_WITH_DOMAINS.format(commands=commands)


def announce_line(entries: Iterable[Any]) -> str | None:
    """Одна строка о том, что записано на этом ходу, или ``None``.

    Берёт только ``source == explicit`` и только строки с читаемой фразой.
    Несколько фактов — одна строка, фразы через «; », подсказка «забудь про»
    по каждому домену.
    """
    from apps.persona.memory_surface import describe_green_content

    phrases: list[str] = []
    labels: list[str] = []
    for entry in entries:
        if getattr(entry, "source", None) != MemoryEntry.SOURCE_EXPLICIT:
            continue
        content = entry.content if isinstance(entry.content, dict) else {}
        phrase = describe_green_content(content)
        if not phrase or phrase in phrases:
            continue
        phrases.append(phrase)
        label = _domain_label(entry)
        if label and label not in labels:
            labels.append(label)
    if not phrases:
        return None
    return f"{ANNOUNCE_HEAD}{'; '.join(phrases)}. {_tail(labels)}"


def weave_service_line(
    conversation: Any,
    bot_user: Any,
    reply: DiscoveryReply,
    *,
    written: Iterable[Any],
    allow_question: bool,
    weave_question: Callable[[Any, Any, DiscoveryReply], DiscoveryReply] | None = None,
) -> DiscoveryReply:
    """Ровно одна служебная строка под ответом: анонс, иначе — вопрос памяти.

    ``allow_question`` — вопрос уместен только на состоявшемся консьерж-ходу
    (как и до DRF-1292); анонс — на любом ответе, который дошёл до этой точки
    (сбойные и заблокированные ветки сюда не приходят с ``written``).
    Never raises: строка не стоит хода.
    """
    try:
        line = announce_line(written)
        if line:
            return DiscoveryReply(
                text=f"{reply.text}\n\n{line}",
                action_data=reply.action_data,
                persisted=reply.persisted,
            )
        if allow_question and weave_question is not None:
            return weave_question(conversation, bot_user, reply)
        return reply
    except Exception:  # noqa: BLE001 — a service line must never break the turn
        logger.exception("orchestrator.memory_announce.failed")
        return reply


def record_turn_facts(
    bot_user: Any,
    conversation: Any,
    text: str,
    *,
    tool_trace: Any = None,
    link_timeout_s: float | None = None,
) -> WriteSink:
    """Оба писателя зелёной памяти за один вызов; что записали — в ``sink``.

    Те же два писателя и тот же порядок, что стояли после отправки (M-B2 +
    бриф «Мозг» п.4); их гейты (согласие, дедуп, forget-all) — внутри них.
    """
    from apps.orchestrator.memory.personal_context import record_explicit_green_facts
    from apps.orchestrator.said_memory import record_said_facts

    sink = WriteSink()
    try:
        record_explicit_green_facts(bot_user, text, sink=sink, link_timeout_s=link_timeout_s)
        record_said_facts(
            bot_user,
            conversation,
            text,
            tool_trace=tool_trace,
            sink=sink,
            link_timeout_s=link_timeout_s,
        )
    except Exception:  # noqa: BLE001 — memory must never break the turn
        logger.exception("orchestrator.memory_announce.record_failed")
    return sink
