"""Что скрининг УЖЕ спросил в этом разговоре (DRF-1542).

Живой диалог владельца 06.09, пять ходов подряд — один и тот же экран
байт-в-байт:

    чел  …тянет поясницу
    бот  Понимаю. Уточню, чтобы посоветовать точно: 1. Где именно болит…
    чел  После тренировки
    бот  Понимаю. Уточню, чтобы посоветовать точно: 1. Где именно болит…
    чел  Поясница
    бот  Понимаю. Уточню, чтобы посоветовать точно: 1. Где именно болит…

Человек ответил на оба заданных вопроса — и получил их снова. Это не «бот
не понял»: он спросил, получил ответ и спросил то же самое.

``SOFT_PAIN_REPLY`` — модульная константа, и до этого тикета навык не
помнил ничего: каждый ход читался как первый. Здесь этот факт
записывается фактом — по образцу
:mod:`apps.orchestrator.refusal_memo` (DRF-1474), который тем же приёмом
закрыл повтор отказа каталога.

### Хранение

``Conversation.skill_state``, отдельный непересекающийся ключ
:data:`STATE_KEY` (``no_match`` принадлежит реестру отказов каталога,
коллизии нет). Запись — best-effort: потерять памятку хуже, чем потерять
ход, а поведение без неё — ровно то, что уже отгружено.

TTL тридцать минут — как у ``refusal_memo``, и по той же причине: это
срок одного разговора. Протухнет рано — человек получит вопросы ещё раз;
протухнет поздно — новая жалоба через час не получит их вовсе, а это
дороже.

### Чего эта памятка НЕ гасит

``RED_FLAG``. Решение владельца ``docs/OPEN_DECISIONS.md`` §35 п.5:
тревожный признак сразу включает безопасную ветку. Человек может
пожаловаться повторно и на втором заходе сказать то, что классификатор
прочитает как красный флаг. Промолчать про «сначала к врачу» ради
разрыва петли — дороже самой петли: петля раздражает, молчание на
тревожном признаке может стоить человеку здоровья.

Порядок поэтому зафиксирован в
:meth:`~apps.skills.health_screening.skill.HealthScreeningSkill.matches`
и покрыт тестом: сначала ``classify()``, ``RED_FLAG`` проходит всегда,
памятка гасит только повтор ``SOFT``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

#: Свой ключ в ``skill_state``. Не пересекается с ``no_match``
#: (:mod:`apps.orchestrator.refusal_memo`) — это разные факты о разговоре.
STATE_KEY = "health_screening_asked"

#: Тридцать минут — срок одного разговора, тот же, что у ``refusal_memo``.
STATE_TTL_SECONDS = 1800


def _state(conversation: Any) -> dict[str, Any]:
    raw = getattr(conversation, "skill_state", None)
    return raw if isinstance(raw, dict) else {}


def remember_screening_asked(conversation: Any) -> None:
    """Записать «диагностические вопросы заданы». Никогда не бросает.

    Пишется через :func:`apps.conversations.services.write_skill_state`,
    а не read-modify-write по всему JSON: соседние подключи
    (``nutrition_anketa``, ``no_match``, ``global_booking``) пишутся с
    того же хода, и полная перезапись колонки их бы затёрла.
    """

    if conversation is None:
        return
    try:
        from django.utils import timezone as dj_timezone

        from apps.conversations.services import write_skill_state

        write_skill_state(
            conversation,
            STATE_KEY,
            {"at": dj_timezone.now().isoformat()},
        )
    except Exception:  # noqa: BLE001 — памятка не стоит хода
        logger.exception("health_screening.memo.save_failed")


def screening_asked_recently(conversation: Any) -> bool:
    """Задавал ли скрининг свои вопросы за последние TTL. Не бросает.

    Отсутствие записи, мусор в записи и нечитаемая отметка времени —
    всё это «не задавал»: памятка гасит ответ, и ошибаться она обязана
    в сторону «ответить», а не «промолчать».
    """

    if conversation is None:
        return False
    try:
        from django.utils import timezone as dj_timezone

        row = _state(conversation).get(STATE_KEY)
        if not isinstance(row, dict):
            return False
        stamped = row.get("at")
        if not stamped:
            return False
        try:
            age = (dj_timezone.now() - datetime.fromisoformat(str(stamped))).total_seconds()
        except (TypeError, ValueError):
            return False
        # Отрицательный возраст — сдвинувшиеся часы, а не свежая запись.
        return -STATE_TTL_SECONDS <= age <= STATE_TTL_SECONDS
    except Exception:  # noqa: BLE001
        logger.exception("health_screening.memo.load_failed")
        return False
