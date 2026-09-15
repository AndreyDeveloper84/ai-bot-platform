"""Строка «по твоим словам» над показом мастеров в DM (DRF-1908, бриф «Мозг», п.6).

### Зачем

Макет C04 обещает причину рядом с тем, что показано (R02/R16). В DM поверхности
рекомендации нет — ``show_masters`` показывает мастеров, и человек не видит,
по каким его словам их искали. Решение владельца 24.08
(``docs/OD_C04_GROUNDED_WHY.md``):

* §1 — P0 WHY только пересказ сказанного в разговоре, без «это для тебя самое
  важное»;
* §2 — объяснять нечем → строки нет, механически;
* §3 — не изображать направление: это пересказ ПОИСКА, не рекомендации.

Форма и место согласованы с окном клиентской поверхности (ayla-4c, 15.09):
«Искала по твоим словам: массаж · Пенза · после работы», последней строкой текста
прямо над списком мастеров. Никаких «подобрала для тебя / подходит тебе / Ayla
рекомендует» — до полки Stage 2 это запрещено (OD-PILOT-9).

### Из чего строка — и чего в ней не бывает

* **услуга** — дословные слова человека из этой реплики, которые словарь услуг
  (:func:`apps.skills.menu.matching.mentions_service`, тот же, что у быстрого
  пути) признаёт услугой; клауза со смыслом здоровья отбрасывается целиком
  («ноет спина» не попадает, как в памяти сказанного); цифры не проходят по
  построению — телефон в строку не попадает; длина ≤ 40 знаков, обрезка по
  слову. Если модель искала другое, чем назвал человек, услуги в строке нет;
* **город** — только названный человеком словами в этом пути: на быстром пути —
  в самой реплике (по ней и искали), на пути модели — в репликах человека за
  два часа (срок контекста разговора, B13). Город из сохранённой памяти или
  выбранный моделью сам — не «твои слова», и в строку не идёт;
* **когда удобно** — закрытым словарём памяти сказанного
  (:func:`apps.orchestrator.said_memory.visit_context_from_text`).

Весь ответ, в том числе эта строка, проходит исходящий сторож глобального
пути (``handler.guard_outbound``) — строка не отдельная дверь наружу.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

logger = logging.getLogger(__name__)

RECAP_PREFIX = "Искала по твоим словам: "
RECAP_SEPARATOR = " · "
MAX_SERVICE_CHARS = 40

#: Окно «этого пути» — срок пригодности контекста разговора (владелец, B13).
PATH_WINDOW = timedelta(hours=2)
_PATH_USER_TURNS = 12

#: Слово — буквы (с дефисом внутри). Цифры словом не считаются: телефон,
#: номер дома, «2 раза» в строку не попадут.
_WORD_SPAN_RE = re.compile(r"[А-Яа-яЁёA-Za-z]+(?:-[А-Яа-яЁёA-Za-z]+)*")

#: Общая часть основы, по которой услуга человека совпадает с поиском модели.
_STEM_CHARS = 5


def _clip_by_word(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    out = ""
    for word in text.split():
        candidate = f"{out} {word}".strip()
        if len(candidate) > limit:
            break
        out = candidate
    return out.rstrip(",")


def service_fragment(message_text: str) -> str | None:
    """Дословные слова услуги из реплики — или None. Без смысла здоровья, без цифр."""

    from apps.orchestrator.said_memory import _CLAUSE_SPLIT_RE, _has_health_meaning
    from apps.skills.menu.matching import mentions_service

    runs: list[str] = []
    for raw_clause in _CLAUSE_SPLIT_RE.split(message_text or ""):
        clause = raw_clause.strip()
        if not clause or _has_health_meaning(clause):
            continue
        start: int | None = None
        end = 0
        for match in _WORD_SPAN_RE.finditer(clause):
            if mentions_service(match.group(0)):
                if start is None:
                    start = match.start()
                end = match.end()
            elif start is not None:
                runs.append(clause[start:end])
                start = None
        if start is not None:
            runs.append(clause[start:end])
    distinct = list(dict.fromkeys(run for run in runs if run))
    if not distinct:
        return None
    return _clip_by_word(", ".join(distinct), MAX_SERVICE_CHARS) or None


def _stems(text: str) -> set[str]:
    return {w.casefold()[:_STEM_CHARS] for w in _WORD_SPAN_RE.findall(text or "")}


def _path_user_turns(conversation: Any) -> list[str]:
    """Реплики человека в этом пути (окно B13). Наружу уходит только город."""

    if conversation is None:
        return []
    try:
        from django.utils import timezone

        from apps.conversations.models import Message

        rows = (
            Message.all_tenants.filter(
                conversation=conversation,
                role="user",
                created_at__gte=timezone.now() - PATH_WINDOW,
            )
            .order_by("-created_at")
            .values_list("content", flat=True)[:_PATH_USER_TURNS]
        )
        return [row for row in rows if row]
    except Exception:  # noqa: BLE001 — без истории остаётся «только эта реплика»
        logger.warning("orchestrator.search_recap.history_failed", exc_info=True)
        return []


def _named_cities(texts: list[str]) -> list[str]:
    from apps.marketplace.discovery import _split_known_cities

    tokens = [w.casefold() for text in texts for w in _WORD_SPAN_RE.findall(text or "")]
    _service_tokens, named = _split_known_cities(tokens)
    return named


def render_search_recap(
    message_text: str,
    conversation: Any = None,
    *,
    city: str | None = None,
    specialization: str | None = None,
) -> str | None:
    """Строка «Искала по твоим словам: …» — или None, если объяснять нечем. Не бросает.

    ``city`` / ``specialization`` — то, что искали (аргументы ``show_masters``).
    ``city=None`` — быстрый путь: город искали по самой реплике.
    """

    try:
        text = message_text or ""
        path_texts = [text, *_path_user_turns(conversation)]
        parts: list[str] = []

        service = service_fragment(text)
        if service and (specialization is None or _stems(service) & _stems(specialization)):
            parts.append(service)

        if city:
            wanted = city.strip().casefold()
            named = next((c for c in _named_cities(path_texts) if c.casefold() == wanted), None)
        else:
            named = next(iter(_named_cities([text])), None)
        if named:
            parts.append(named)

        from apps.orchestrator.said_memory import VISIT_CONTEXT_LABELS, visit_context_from_text

        visit = next((v for v in map(visit_context_from_text, path_texts) if v), None)
        if visit:
            parts.append(VISIT_CONTEXT_LABELS.get(visit, visit))

        if not parts:
            return None
        return RECAP_PREFIX + RECAP_SEPARATOR.join(parts)
    except Exception:  # noqa: BLE001 — объяснение не стоит хода
        logger.exception("orchestrator.search_recap.render_failed")
        return None


__all__ = [
    "MAX_SERVICE_CHARS",
    "PATH_WINDOW",
    "RECAP_PREFIX",
    "render_search_recap",
    "service_fragment",
]
