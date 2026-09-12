"""Открытый вопрос бота — «бот знает, что спросил» (DRF-1779).

### Живой ход, который это закрывает

Диалог владельца 12.09 (03:01–03:09 UTC, замер DRF-1754):

    чел  … хочу расслабиться вечером и у меня ноет спина
    бот  Понимаю. Уточню, чтобы посоветовать точно:
         1. Где именно болит — конкретное место?
         2. Это после нагрузки / сидячей работы или с утра после сна?
    чел  1. Спина, 2. После работы
    бот  Не разобрала эту фразу. Напиши её иначе — что и сколько, обычными словами.
    чел  ну я назвал тебе конкретное место - спина
    бот  Ты прав, прости! Сейчас проверю.
    чел  и?
    бот  Прости за задержку — запускаю проверку.

Бот задал два вопроса и получил на них ответ — и НИКТО этого ответа не ждал.
Памятка скрининга (DRF-1542) помнит «вопросы заданы» и гасит их повтор, но
состояния «жду ответа на вопрос X» не было: реплика «1. Спина, 2. После
работы» ушла модели как новая тема, модель выбрала единственный инструмент
про боль (``health_screening``), исполнитель детерминированно отказал по
памятке, и в чат ушёл fallback nutrition-парсера, никакого отношения к
вопросу не имеющий.

### Что здесь есть

Одно состояние — открытый вопрос бота — и три операции над ним:

* :func:`open_question` — пишет тот, кто спросил (скрининг при SOFT, инструмент
  ``ask_clarification``);
* :func:`pending_question` — есть ли открытый вопрос и не протух ли;
* :func:`close_question` — следующая реплика человека ЗАКРЫВАЕТ вопрос: он
  снимается, ответ записывается рядом (``last_answered``) — это тот факт из
  разговора, который дальше подхватит память сказанного (п.4 очереди окна).

И один рендер — :func:`render_answer_block`: абзац system-prompt на ход
ответа. Написан как факт плюс запреты — по образцу
:func:`apps.orchestrator.refusal_memo.render_refusal_block`, и по той же
причине: транскрипт модель на 12.09 уже читала и всё равно переспросила;
инструкция читается как инструкция.

### Хранение и срок

``Conversation.skill_state``, отдельный ключ :data:`STATE_KEY` — тот же
носитель, что у памяток DRF-1474/1542, через
:func:`apps.conversations.services.write_skill_state` (атомарно по подключу,
соседей не затирает). DecisionReadiness ``ConversationState`` (Redis) к
живому пути не подключён — когда подключится (п.5 очереди), открытый вопрос
отзеркалится туда.

TTL два часа — решение владельца B13 (12.09, пакет 2): два часа — срок
пригодности контекста разговора для прямого продолжения. Протухший вопрос
читается как «не спрашивал»: человек, вернувшийся через три часа с «спина»,
получит вопросы снова, а не будет прочитан как отвечающий на забытое.

### Чего это НЕ делает

Не решает за модель, что реплика — ответ. Человек мог сменить тему («а
сколько стоит маникюр?»). Блок говорит модели, что вопрос был и что это,
скорее всего, ответ на него; тему меняет человек, и модель идёт за ним.
Решение «инструмент, который исполнитель отвергнет, модели не предлагать»
живёт не здесь, а в :mod:`apps.orchestrator.concierge`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

#: Свой ключ в ``skill_state``. Не пересекается с ``no_match`` (DRF-1474) и
#: ``health_screening_asked`` (DRF-1542) — это третий факт о разговоре.
STATE_KEY = "awaiting_answer"

#: Куда ложится ответ на закрытый вопрос — для памяти сказанного (п.4).
ANSWERED_KEY = "last_answered"

#: Два часа — actionability TTL контекста разговора (владелец, B13 12.09).
STATE_TTL_SECONDS = 2 * 60 * 60

#: Сколько знаков вопроса и ответа уносится в prompt и в состояние.
_MAX_TEXT_CHARS = 400


@dataclass(frozen=True)
class OpenQuestion:
    question_id: str
    asked_text: str
    asked_at: datetime


@dataclass(frozen=True)
class AnsweredQuestion:
    question: OpenQuestion
    answer_text: str


def _state(conversation: Any) -> dict[str, Any]:
    raw = getattr(conversation, "skill_state", None)
    return raw if isinstance(raw, dict) else {}


def _write(conversation: Any, subkey: str, value: Any | None) -> None:
    """``write_skill_state`` в области тенанта разговора.

    Глобальный путь идёт при ``current_tenant()=None`` по замыслу (инвариант
    CrossTenantError), а ``write_skill_state`` требует область — как
    ``record_global_message``, область входится на время одной записи и
    берётся у самого разговора, а не выбирается здесь.
    """

    from apps.conversations.services import write_skill_state
    from apps.tenancy.context import current_tenant, tenant_scope

    if current_tenant() is not None:
        write_skill_state(conversation, subkey, value)
        return
    with tenant_scope(conversation.tenant):
        write_skill_state(conversation, subkey, value)


def _fresh(stamped: Any) -> datetime | None:
    """Отметка времени, если она читается и укладывается в TTL; иначе None."""

    from django.utils import timezone as dj_timezone

    if not stamped:
        return None
    try:
        at = datetime.fromisoformat(str(stamped))
    except (TypeError, ValueError):
        return None
    age = (dj_timezone.now() - at).total_seconds()
    # Отрицательный возраст — сдвинувшиеся часы, а не свежая запись.
    if not (-STATE_TTL_SECONDS <= age <= STATE_TTL_SECONDS):
        return None
    return at


def open_question(conversation: Any, question_id: str, *, asked_text: str = "") -> None:
    """Записать «бот спросил ``question_id`` и ждёт ответа». Никогда не бросает.

    Второй открытый вопрос подряд ЗАМЕЩАЕТ первый: у разговора один открытый
    вопрос — решение B6 (один decision-changing вопрос), и последнее
    сказанное ботом важнее прежнего.
    """

    if conversation is None or not question_id:
        return
    try:
        from django.utils import timezone as dj_timezone

        _write(
            conversation,
            STATE_KEY,
            {
                "question_id": str(question_id),
                "asked_text": str(asked_text or "")[:_MAX_TEXT_CHARS],
                "at": dj_timezone.now().isoformat(),
            },
        )
        logger.info(
            "orchestrator.open_question.opened question=%s conversation=%s",
            question_id,
            getattr(conversation, "id", None),
        )
    except Exception:  # noqa: BLE001 — состояние не стоит хода
        logger.exception("orchestrator.open_question.save_failed question=%s", question_id)


def pending_question(conversation: Any) -> OpenQuestion | None:
    """Открытый и не протухший вопрос — или None. Не бросает.

    Мусор в записи и нечитаемая отметка — «не спрашивал»: ошибаться здесь
    надо в сторону «прочитать реплику как новую», а не «принять за ответ
    на неизвестно что».
    """

    if conversation is None:
        return None
    try:
        row = _state(conversation).get(STATE_KEY)
        if not isinstance(row, dict):
            return None
        question_id = str(row.get("question_id") or "")
        at = _fresh(row.get("at"))
        if not question_id or at is None:
            return None
        return OpenQuestion(
            question_id=question_id,
            asked_text=str(row.get("asked_text") or ""),
            asked_at=at,
        )
    except Exception:  # noqa: BLE001
        logger.exception("orchestrator.open_question.read_failed")
        return None


def close_question(conversation: Any, answer_text: str) -> AnsweredQuestion | None:
    """Снять открытый вопрос этой репликой и записать ответ рядом.

    Возвращает закрытый вопрос с ответом или None, если открытого не было.
    Ответ ложится в ``skill_state[last_answered]`` — факт из разговора для
    памяти сказанного (п.4); сам вопрос из состояния удаляется, чтобы
    следующая реплика не читалась как второй ответ на него.
    """

    question = pending_question(conversation)
    if question is None:
        return None
    try:
        from django.utils import timezone as dj_timezone

        _write(conversation, STATE_KEY, None)
        _write(
            conversation,
            ANSWERED_KEY,
            {
                "question_id": question.question_id,
                "asked_text": question.asked_text,
                "answer_text": str(answer_text or "")[:_MAX_TEXT_CHARS],
                "at": dj_timezone.now().isoformat(),
            },
        )
        logger.info(
            "orchestrator.open_question.closed question=%s conversation=%s",
            question.question_id,
            getattr(conversation, "id", None),
        )
    except Exception:  # noqa: BLE001 — потерять запись хуже, чем потерять ход
        logger.exception("orchestrator.open_question.close_failed")
    return AnsweredQuestion(question=question, answer_text=str(answer_text or ""))


def render_answer_block(answered: AnsweredQuestion | None) -> str:
    """Абзац system-prompt на ход, которым человек ответил на вопрос бота.

    Факт плюс запреты. Три запрета — ровно три ошибки живого хода 12.09:
    переспросить то, на что ответили; вызвать инструмент, который уже
    отработал и отказал; пообещать проверку вместо следующего шага.
    """

    if answered is None:
        return ""
    asked = answered.question.asked_text.strip().replace("\n", " ")
    answer = answered.answer_text.strip()[:_MAX_TEXT_CHARS].replace("\n", " ")
    lines = ["На прошлом ходу ты задал вопрос, и эта реплика — ответ на него."]
    if asked:
        lines.append(f"Ты спросил: «{asked}»")
    lines.append(f"Человек ответил: «{answer}»")
    lines.append(
        "Прими ответ как факт и иди дальше по его запросу. "
        "Не задавай эти вопросы снова и не проси сказать иначе. "
        "Не обещай «проверить» или «поискать» — либо вызови нужный инструмент "
        "на этом же ходу, либо скажи, что можешь предложить прямо сейчас. "
        "Если человек сменил тему — иди за ним, а не за старым вопросом."
    )
    return "\n".join(lines)


__all__ = [
    "ANSWERED_KEY",
    "STATE_KEY",
    "STATE_TTL_SECONDS",
    "AnsweredQuestion",
    "OpenQuestion",
    "close_question",
    "open_question",
    "pending_question",
    "render_answer_block",
]
