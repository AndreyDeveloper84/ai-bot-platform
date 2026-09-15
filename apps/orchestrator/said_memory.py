"""Память сказанного, срез 1 — город и когда удобно приходить (бриф окна «Мозг», п.4).

### Зачем

Первый сценарий владельца (12.09): «хочу расслабиться вечером / ноет спина» →
массаж, Пенза → мастер → запись. Всё, что человек уже сказал о себе, бот
забывал к следующему разговору и спрашивал снова. Замер на пилоте 13.09:
``MemoryEntry`` — одна строка на всю базу; писатель
(:func:`apps.orchestrator.memory.personal_context.record_explicit_green_facts`)
узнаёт диету, бюджет, районы и время только по якорям «мне удобно…», а города и
«хочу вечером» не знает вовсе.

Здесь — два факта, каждый со своим доказательством «это сказал человек»:

* **город** — пишется, только когда модель искала мастеров в городе
  (``show_masters`` в трассе хода, DRF-1385) И этот город назван словами самого
  человека (его реплики, не ассистента) И это город, где у нас есть мастера
  (тот же набор, что распознаёт discovery). Город, придуманный моделью, не
  пишется;
* **когда удобно приходить** — «после работы» / «вечером» / «в выходные» в
  реплике-желании («хочу…», «записаться…», «расслабиться…»), закрытым словарём.

### Чего здесь нет — намеренно

* **Смысла здоровья.** Спецкатегория (DRF-1729), согласия на неё нет. Фраза,
  где есть симптом («ноет спина», беременность, лекарства), не даёт ни одного
  факта — даже соседнее «вечером» из той же клаузы. Ответ человека на вопросы
  скрининга («1. Спина, 2. После работы») не читается вовсе: «после работы»
  там — когда болит, а не когда удобно прийти. Значения фактов берутся из
  закрытого словаря или из списка городов, никогда — из текста человека.
* **Мастера.** Решение владельца 24.08 (``personal_fields.NEVER_CROSSES``):
  любимый мастер — отношение с одним салоном и не должен переезжать в другой;
  а зелёные строки сегодня читаются по ``user_id`` без тенанта
  (``POLICY_DEBT``). Новый переезжающий ключ про мастера — второй долг того же
  рода; «записаться к Инне?» ждёт тенантного чтения.

### Происхождение, согласие, стирание

``source='explicit'`` + ``content.origin='conversation'`` + ``said_at`` — «сказано
в разговоре <дата>»; словарь ``origin`` общий с анкетой каталога
(``ORIGIN_ANKETA``). Гейт — тот же, что у существующего писателя: действующее
PERSONAL_DATA и канонический ``ayla_user_id``; новых согласий не нужно (главное
окно, 14.09, по переписи согласий). Стирание — существующими путями: «забудь
всё» и удаление аккаунта гасят все зелёные строки человека по ``user_id``, а
«забудь город» знает домен (``memory_commands._KEY_KEYWORDS``).

### Как читается

:func:`render_said_block` — абзац system-prompt консьержа: факт с датой и
правило «не спрашивай заново, предложи подтвердить». В общий блок памяти
(ai-core) и в ``memory_surface`` эти ключи не идут — у них своя инструкция, и
«город — Пенза» в двух местах промпта читался бы как два факта.

### Подтверждение одним тапом (DRF-1878)

Модель не пишет вопрос-подтверждение словами: она вызывает инструмент
``confirm_said_fact(key)``, а вопрос и кнопки рисует бот
(:func:`confirm_offer`, :func:`confirm_keyboard`) — «Ищем в городе Пенза, как
обычно?» · «Да, Пенза» / «Другой город», callback ``cb:said:<key>:yes|other``.
Кнопки и метки для истории строит один и тот же строитель, так что
переименованная кнопка уходит в историю уже новым именем.

* **«Да»** — факт переписывается свежей строкой (новый ``said_at``, прежняя
  строка — superseded) и ход идёт дальше текстом метки: консьерж закрывает
  открытый вопрос ``said.<key>`` этой репликой, как любой другой ответ.
* **«Другое»** — бот спрашивает сам («В каком городе ищем?»), без модели, и
  открывает вопрос ``said.<key>``; новый сказанный город вытесняет прежний
  существующим писателем.

В истории тап — **фраза**, а не молчание: «Да, Пенза» — высказывание человека
о себе, по образцу тапов еды (``nutrition_global.resolve_food_tap``: «✅ В
дневник» ложится меткой). Сырой payload в историю не попадает никогда (класс
DRF-990); кнопка, за которой факта уже нет (стёрт), — устаревшая: в историю
не идёт ничего.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

KEY_CITY = "city"
KEY_VISIT_CONTEXT = "visit_context"
SAID_KEYS = frozenset({KEY_CITY, KEY_VISIT_CONTEXT})

#: Словарь ``origin`` — общий с анкетой каталога (``ORIGIN_ANKETA = "anketa"``).
ORIGIN_CONVERSATION = "conversation"

_WRITE_PURPOSE = "conversation:said_fact"

#: Сколько последних реплик человека проверяется на «город назван им самим».
_SAID_USER_TURNS = 12

#: Когда удобно приходить — закрытый словарь. Порядок — приоритет: «после работы
#: вечером» — это «после работы», более узкое.
_VISIT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("after_work", re.compile(r"после\s+(?:работ|офис)\w*", re.IGNORECASE)),
    ("weekend", re.compile(r"\bвыходн\w*", re.IGNORECASE)),
    ("evening", re.compile(r"\bвечер\w*", re.IGNORECASE)),
)
VISIT_CONTEXT_LABELS = {
    "after_work": "после работы",
    "weekend": "в выходные",
    "evening": "вечером",
}

#: Реплика-желание: без неё «вечером» — не про визит («вечером болит»).
#: «хоч\w*» ловит и опечатку живого хода 12.09 («я хочв расслабиться»).
_DESIRE_RE = re.compile(
    r"\bхоч\w*|\bхотел\w*|\bхочется\b|\bпланиру\w*|\bзапис\w*|\bрасслаб\w*"
    r"|\bсходить\b|\bприйти\b|\bприду\b|\bзайти\b",
    re.IGNORECASE,
)

#: Клауза — по знакам и по союзам: «расслабиться вечером и у меня ноет спина»
#: — две клаузы, и симптом второй не отдаёт свою соседку.
_CLAUSE_SPLIT_RE = re.compile(r"[,.!?;:\n]+|\s+(?:и|но|а|потому\s+что)\s+", re.IGNORECASE)

#: Стемы смысла здоровья поверх классификатора скрининга (он знает боль, но не
#: беременность и лекарства). Лишнее срабатывание здесь — потерянный факт;
#: пропуск — записанная спецкатегория. Выбрана первая ошибка.
_HEALTH_STEMS = (
    "бол",
    "ноет",
    "ноют",
    "тянет",
    "немеет",
    "онемел",
    "беремен",
    "лекарств",
    "таблет",
    "давлен",
    "диабет",
    "операц",
    "травм",
    "тошн",
    "головокруж",
    "аллерг",
    "врач",
    "диагноз",
)

_WORD_RE = re.compile(r"[А-Яа-яЁёA-Za-z-]+")


@dataclass(frozen=True)
class SaidFact:
    key: str
    value: str
    said_at: datetime | None


# --------------------------------------------------------------------------- #
# Извлечение                                                                  #
# --------------------------------------------------------------------------- #
def _has_health_meaning(clause: str) -> bool:
    from apps.skills.health_screening.classifier import PainSignal, classify

    low = clause.lower()
    if any(stem in low for stem in _HEALTH_STEMS):
        return True
    return classify(clause) != PainSignal.NONE


def visit_context_from_text(text: str) -> str | None:
    """«когда удобно приходить» из реплики-желания, или None.

    Клауза с любым смыслом здоровья отбрасывается целиком. Два разных контекста
    в разных клаузах одной реплики — неоднозначно, None.
    """

    found: list[str] = []
    for clause in _CLAUSE_SPLIT_RE.split(text or ""):
        clause = clause.strip()
        if not clause or _has_health_meaning(clause):
            continue
        if _DESIRE_RE.search(clause) is None:
            continue
        matched = [value for value, pattern in _VISIT_RULES if pattern.search(clause)]
        # «после работы вечером» — одно и то же, узкое побеждает; «вечером или
        # в выходные» — два разных, неоднозначно.
        if "after_work" in matched and "evening" in matched:
            matched.remove("evening")
        found.extend(matched)
    distinct = list(dict.fromkeys(found))
    return distinct[0] if len(distinct) == 1 else None


def _person_named_cities(conversation: Any, message_text: str) -> list[str]:
    """Города (в хранимом написании), которые назвал САМ человек.

    Читает реплики роли ``user`` — не ассистента: город, который бот сам
    перечислил, человеком не сказан. Сравнение — тем же распознавателем, что у
    discovery, по живому набору городов с мастерами.
    """

    from apps.marketplace.discovery import _split_known_cities

    parts = [message_text or ""]
    try:
        from apps.conversations.models import Message

        rows = (
            Message.all_tenants.filter(conversation=conversation, role="user")
            .order_by("-created_at")
            .values_list("content", flat=True)[:_SAID_USER_TURNS]
        )
        parts.extend(row for row in rows if row)
    except Exception:  # noqa: BLE001 — без истории остаётся «только этот ход»
        logger.warning("orchestrator.said_memory.history_failed", exc_info=True)
    tokens = [w.casefold() for part in parts for w in _WORD_RE.findall(part)]
    _service_tokens, named = _split_known_cities(tokens)
    return named


def _searched_cities(tool_trace: Any) -> list[str]:
    cities: list[str] = []
    for entry in tool_trace or ():
        if not isinstance(entry, dict) or entry.get("tool") != "show_masters":
            continue
        if str(entry.get("result") or "").startswith("declined"):
            continue
        arguments = entry.get("arguments")
        city = arguments.get("city") if isinstance(arguments, dict) else None
        if isinstance(city, str) and city.strip():
            cities.append(city.strip())
    return cities


def _answered_screening_this_turn(conversation: Any, message_text: str) -> bool:
    """Была ли эта реплика ответом на вопросы скрининга (DRF-1779)."""

    from apps.orchestrator.open_question import ANSWERED_KEY

    try:
        from apps.conversations.models import Conversation

        state = (
            Conversation.all_tenants.filter(pk=conversation.pk)
            .values_list("skill_state", flat=True)
            .first()
        )
    except Exception:  # noqa: BLE001 — не знаем → считаем ответом: не пишем
        return True
    row = state.get(ANSWERED_KEY) if isinstance(state, dict) else None
    if not isinstance(row, dict):
        return False
    return (
        str(row.get("question_id") or "").startswith("health_screening")
        and str(row.get("answer_text") or "") == (message_text or "")[:400]
    )


# --------------------------------------------------------------------------- #
# Запись                                                                      #
# --------------------------------------------------------------------------- #
def _write_said_fact(
    bot_user: Any, *, kind: str, content: dict[str, str], refresh: bool = False
) -> bool:
    """Одна зелёная строка «сказано в разговоре». Гейты — как у M-B2.

    ``content`` приходит литералом ``{"key": …}`` от вызывающего — так ключ
    видит ``tools/lint/personal_field_guard.py`` (регистр полей и кардинальностей).

    ``refresh=True`` (DRF-1878, человек подтвердил) — то же значение пишется
    свежей строкой, а прежняя строка с этим ключом уходит в superseded: у
    факта новый ``said_at``, а живая строка по ключу по-прежнему одна.
    """

    from django.utils import timezone

    from apps.consent.memory import can_store_green_memory
    from apps.identity.models import MemoryEntry
    from apps.identity.services.ayla_link import ensure_ayla_link
    from apps.identity.services.memory_key_policy import read_current_view
    from apps.identity.services.memory_reader import (
        get_or_create_personal_context,
        read_green_entries,
    )
    from apps.identity.services.memory_writer import supersede_entries, write_entry

    key, value = content["key"], content["value"]
    if not can_store_green_memory(bot_user):
        return False
    user_id = ensure_ayla_link(bot_user, trigger="memory_write")
    if user_id is None:
        return False
    for fact in read_current_view(user_id).green_facts:
        existing = fact.content if isinstance(fact.content, dict) else {}
        if not refresh and existing.get("key") == key and existing.get("value") == value:
            return False
    upc = get_or_create_personal_context(user_id)
    if upc.soft_deleted_at is not None or upc.forget_all_requested_at is not None:
        return False
    live_rows = read_green_entries(user_id)
    entry = write_entry(
        user_id=user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        kind=kind,
        content={
            **content,
            "origin": ORIGIN_CONVERSATION,
            "said_at": timezone.now().isoformat(),
            "display": _display(key, value),
        },
        request_id=uuid.uuid4(),
        purpose=_WRITE_PURPOSE,
        consent_at=None,
    )
    if entry is None:
        return False
    displaced = [
        row
        for row in live_rows
        if row.id != entry.id
        and isinstance(row.content, dict)
        and row.content.get("key") == key
        and (refresh or row.content.get("value") != value)
    ]
    if displaced:
        supersede_entries(replaced_by=entry, entries=displaced)
    return True


def _display(key: str, value: str) -> str:
    """Фраза для «покажи, что знаешь» (после «Помню, что ты …»)."""

    if key == KEY_CITY:
        return f"ищешь мастеров в городе {value}"
    return f"хочешь приходить {VISIT_CONTEXT_LABELS.get(value, value)}"


def record_said_facts(
    bot_user: Any,
    conversation: Any,
    message_text: str,
    *,
    tool_trace: Any = None,
) -> int:
    """Записать сказанное на этом ходу. Возвращает число записанных фактов.

    Вызывается ПОСЛЕ отправки ответа; не бросает — память не стоит хода.
    """

    written = 0
    try:
        if conversation is None or _answered_screening_this_turn(conversation, message_text):
            return 0
        searched = _searched_cities(tool_trace)
        if searched:
            named = {c.casefold(): c for c in _person_named_cities(conversation, message_text)}
            city = next(
                (named[c.casefold()] for c in reversed(searched) if c.casefold() in named), None
            )
            if city and _write_said_fact(
                bot_user, kind="preference", content={"key": "city", "value": city}
            ):
                written += 1
        visit = visit_context_from_text(message_text)
        if visit and _write_said_fact(
            bot_user, kind="lifestyle", content={"key": "visit_context", "value": visit}
        ):
            written += 1
    except Exception:  # noqa: BLE001 — память не стоит хода
        logger.exception("orchestrator.said_memory.write_failed")
        return written
    if written:
        logger.info(
            "orchestrator.said_memory.written bot_user=%s count=%d",
            getattr(bot_user, "id", None),
            written,
        )
    return written


# --------------------------------------------------------------------------- #
# Чтение                                                                      #
# --------------------------------------------------------------------------- #
def said_facts(bot_user: Any) -> list[SaidFact]:
    """Текущие факты «сказано в разговоре» — под теми же гейтами, что запись."""

    try:
        from apps.consent.memory import can_store_green_memory
        from apps.identity.services.memory_key_policy import read_current_view
        from apps.orchestrator.memory_block import concierge_memory_enabled

        if not concierge_memory_enabled() or not can_store_green_memory(bot_user):
            return []
        user_id = getattr(bot_user, "ayla_user_id", None)
        if not user_id:
            return []
        view = read_current_view(user_id)
    except Exception:  # noqa: BLE001
        logger.exception("orchestrator.said_memory.read_failed")
        return []
    out: list[SaidFact] = []
    for fact in view.green_facts:
        content = fact.content if isinstance(fact.content, dict) else {}
        key, value = content.get("key"), content.get("value")
        if key not in SAID_KEYS or content.get("origin") != ORIGIN_CONVERSATION:
            continue
        if not isinstance(value, str) or not value:
            continue
        try:
            said_at = datetime.fromisoformat(str(content.get("said_at")))
        except (TypeError, ValueError):
            said_at = None
        out.append(SaidFact(key=key, value=value, said_at=said_at))
    return out


def render_said_block(bot_user: Any) -> str:
    """Абзац system-prompt: что человек уже сказал о себе и как этим пользоваться."""

    facts = said_facts(bot_user)
    if not facts:
        return ""
    lines = ["Человек сам говорил в прошлых разговорах (это его слова, не догадки):"]
    for fact in sorted(facts, key=lambda f: f.key):
        when = f" ({fact.said_at:%d.%m})" if fact.said_at else ""
        if fact.key == KEY_CITY:
            lines.append(f"- город — {fact.value}{when} [key={KEY_CITY}]")
        else:
            label = VISIT_CONTEXT_LABELS.get(fact.value, fact.value)
            lines.append(f"- когда удобно приходить — {label}{when} [key={KEY_VISIT_CONTEXT}]")
    lines.append(
        "Не спрашивай это заново. Если относится к запросу — не спрашивай текстом, "
        f"вызови {CONFIRM_SAID_FACT_TOOL} с этим key: вопрос и кнопки нарисует бот. "
        "Назовёт другое — иди за новым и не спорь. Не выдавай это за сегодняшний факт."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Подтверждение одним тапом (DRF-1878)                                        #
# --------------------------------------------------------------------------- #
CONFIRM_SAID_FACT_TOOL = "confirm_said_fact"

#: Плоская спецификация — как у ``ASK_CLARIFICATION_TOOL_SPEC``: провайдер сам
#: заворачивает её в ``{"type": "function", ...}``.
CONFIRM_SAID_FACT_TOOL_SPEC: dict[str, Any] = {
    "name": CONFIRM_SAID_FACT_TOOL,
    "description": (
        "Offer the person to confirm with one tap something THEY said in an "
        "earlier conversation (the city to search in, or when they like to come). "
        "The bot renders the question and the buttons itself — call this instead "
        "of asking about the known fact in plain text."
    ),
    "parameters": {
        "type": "object",
        "properties": {"key": {"type": "string", "enum": [KEY_CITY, KEY_VISIT_CONTEXT]}},
        "required": ["key"],
    },
}

SAID_CALLBACK_PREFIX = "cb:said:"
#: Строгая форма: «cb:said: Пенза», набранное руками, тапом не является.
_SAID_CALLBACK_RE = re.compile(r"^cb:said:(city|visit_context):(yes|other)$")

VERDICT_YES = "yes"
VERDICT_OTHER = "other"

OTHER_LABELS = {KEY_CITY: "Другой город", KEY_VISIT_CONTEXT: "Другое время"}
#: Вопрос после «Другое» — задаёт бот, не модель.
OTHER_QUESTIONS = {
    KEY_CITY: "В каком городе ищем?",
    KEY_VISIT_CONTEXT: "Когда тебе удобно приходить?",
}
STALE_TEXT = "Эта кнопка уже неактуальна. Напиши, что ищем, — продолжу."


def said_question_id(key: str) -> str:
    """Id открытого вопроса (DRF-1779) — код, без смысла здоровья."""

    return f"said.{key}"


@dataclass(frozen=True)
class ConfirmOffer:
    key: str
    question: str
    #: ``(label, callback)`` — порядок кнопок на экране.
    buttons: tuple[tuple[str, str], ...]


def _offer(fact: SaidFact) -> ConfirmOffer:
    if fact.key == KEY_CITY:
        question = f"Ищем в городе {fact.value}, как обычно?"
        yes_label = f"Да, {fact.value}"
    else:
        label = VISIT_CONTEXT_LABELS.get(fact.value, fact.value)
        question = f"Как обычно — {label}?"
        yes_label = f"Да, {label}"
    return ConfirmOffer(
        key=fact.key,
        question=question,
        buttons=(
            (yes_label, f"{SAID_CALLBACK_PREFIX}{fact.key}:{VERDICT_YES}"),
            (OTHER_LABELS[fact.key], f"{SAID_CALLBACK_PREFIX}{fact.key}:{VERDICT_OTHER}"),
        ),
    )


def confirm_offer(bot_user: Any, key: str) -> ConfirmOffer | None:
    """Вопрос и кнопки для сказанного факта — или None, если факта нет."""

    fact = next((f for f in said_facts(bot_user) if f.key == key), None)
    return _offer(fact) if fact is not None else None


def confirm_keyboard(offer: ConfirmOffer) -> dict[str, Any]:
    """``action_data`` ответа: одна строка кнопок, как у ``_render_ask_clarification``."""

    return {
        "attachments": [
            {
                "type": "inline_keyboard",
                "payload": {
                    "buttons": [
                        {"label": label, "callback": callback} for label, callback in offer.buttons
                    ]
                },
            }
        ]
    }


def said_tap_labels(bot_user: Any) -> dict[str, str]:
    """``{callback: метка}`` по текущим фактам — тем же строителем, что кнопки."""

    return {
        callback: label for fact in said_facts(bot_user) for label, callback in _offer(fact).buttons
    }


@dataclass(frozen=True)
class SaidTap:
    key: str
    verdict: str
    #: Метка кнопки — чем тап был как реплика; None — кнопка устарела (факта нет).
    history_text: str | None


def resolve_said_tap(text: str, bot_user: Any) -> SaidTap | None:
    """Разобрать тап подтверждения; ``None`` — «это не тап ``cb:said:``»."""

    stripped = (text or "").strip()
    match = _SAID_CALLBACK_RE.match(stripped)
    if match is None:
        return None
    return SaidTap(
        key=match.group(1),
        verdict=match.group(2),
        history_text=said_tap_labels(bot_user).get(stripped),
    )


def confirm_said_fact(bot_user: Any, key: str) -> bool:
    """Человек подтвердил сказанный факт: переписать его свежей строкой. Не бросает."""

    try:
        fact = next((f for f in said_facts(bot_user) if f.key == key), None)
        if fact is None:
            return False
        if key == KEY_CITY:
            return _write_said_fact(
                bot_user,
                kind="preference",
                content={"key": "city", "value": fact.value},
                refresh=True,
            )
        return _write_said_fact(
            bot_user,
            kind="lifestyle",
            content={"key": "visit_context", "value": fact.value},
            refresh=True,
        )
    except Exception:  # noqa: BLE001 — память не стоит хода
        logger.exception("orchestrator.said_memory.confirm_failed key=%s", key)
        return False


__all__ = [
    "CONFIRM_SAID_FACT_TOOL",
    "CONFIRM_SAID_FACT_TOOL_SPEC",
    "OTHER_LABELS",
    "OTHER_QUESTIONS",
    "SAID_CALLBACK_PREFIX",
    "STALE_TEXT",
    "ConfirmOffer",
    "SaidTap",
    "confirm_keyboard",
    "confirm_offer",
    "confirm_said_fact",
    "resolve_said_tap",
    "said_question_id",
    "said_tap_labels",
    "KEY_CITY",
    "KEY_VISIT_CONTEXT",
    "ORIGIN_CONVERSATION",
    "SAID_KEYS",
    "VISIT_CONTEXT_LABELS",
    "SaidFact",
    "record_said_facts",
    "render_said_block",
    "said_facts",
    "visit_context_from_text",
]
