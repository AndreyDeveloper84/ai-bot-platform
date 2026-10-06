"""Текстовый ввод еды в DM — DRF-1837, решения владельца §109 и §136.

## Зачем

Замер 10.09 (``docs/MEASURE_FOOD_DIARY_RUNTIME_VS_DESIGN_2026-09-10.md``) и
карта 12.09 (``Ayla/docs/GAP_MAP_FOOD_DIARY_2026-09-12.md``): записать еду
нельзя было ни одним из трёх входов. Фото — за флагом
``FOOD_PHOTO_SCAN_ENABLED=False``; Mini App — ``guardProd``; текстом —
``DIARY_PROMPT`` «Через текст пока не умею». Бот и Mini App отсылали друг к
другу. Этот модуль — текстовый вход, и он же выход из кольца.

## Сценарий §109 (10.09.2026), дословно по шагам

1. человек описывает еду словами («борщ 300 г»);
2. Ayla оценивает состав и порцию — ``internal/food-estimate/`` каталога, та
   же ``NutritionLookup``, что у ручной записи, и **ни одной строки в базе**;
3. показывает «Я распознала так»;
4. предположения названы словами «примерно» / «оценка» — и порция без граммов,
   и калории/БЖУ по справочнику;
5. человек подтверждает (``✅ В дневник``) или исправляет граммы
   (``✏️ Поправить граммы``);
6. **только после подтверждения** — ``internal/food-log/``;
7. правка/удаление сохранённой записи — DRF-1838 (F4), не здесь.

## Происхождение (§136)

``text_estimated_confirmed`` — оценку подтвердили как есть;
``text_user_corrected`` — человек поправил граммы. Код едет в запись
(``entry_origin``) и не угадывается задним числом: он решается на карточке,
которую человек видел.

## Тип приёма

``meal_type="other"`` — «не указан». Угадывать завтрак по часам значило бы
записать за человека то, чего он не называл (§109); тот же код с мая шлёт
фото-путь, и каталог DRF-1837 его наконец принимает.

## Гейты

``NUTRITION_ENABLED`` и согласие на персональные данные — **по записи**
(``personal_records_consent_open``): тот же предикат, которым дневник
ЧИТАЕТСЯ. Писать в дневник, который потом нельзя прочитать, — хуже, чем не
писать. ``food_scanner_consent_at`` здесь не нужен: это согласие на передачу
фотографии распознавателю, а фотографии в текстовом пути нет.

## Состояние

``Conversation.skill_state["food_text"]`` — диалоговое, не память: исходная
фраза до тапа «📔 В дневник», последняя показанная оценка, ожидание граммов.
Свежесть — :data:`PENDING_TTL_SECONDS`, как у ``food_correction``: забытая
карточка не держит чужие реплики вечно.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from apps.integrations.ayla import (
    FoodLogResponse,
    FoodNotRecognizedError,
    MealEditConflictError,
    MealNotFoundError,
    MealRestoreExpiredError,
    NutritionAPIError,
    NutritionUncertainOutcomeError,
    NutritionUnavailableError,
    external_user_id_for,
    get_nutrition_client,
)
from apps.integrations.ayla.portion_provenance import (
    portion_numbers_are_named,
    portion_provenance_of,
)
from apps.orchestrator.ui.keyboards import (
    ENTRY_CALLBACK_RE,
    ENTRY_ID_RE,
    food_entry_keyboard,
    food_text_deleted_keyboard,
    food_text_estimate_keyboard,
    food_text_items_keyboard,
    food_text_logged_keyboard,
    food_text_unpriced_keyboard,
)
from apps.skills.base import SkillContext, SkillResult

logger = logging.getLogger(__name__)

STATE_KEY = "food_text"
PENDING_TTL_SECONDS = 600

#: Границы граммов — те, что каталог примет в запись: ``portion_multiplier``
#: 0.1…20 от базовых 100 г.
MIN_GRAMS = 10
MAX_GRAMS = 2000
BASELINE_G = 100.0

ORIGIN_ESTIMATED_CONFIRMED = "text_estimated_confirmed"
ORIGIN_USER_CORRECTED = "text_user_corrected"
#: DRF-2110 — две фото-половины §136 рядом с текстовыми: четыре значения в
#: одном месте. Пишутся ВСЕГДА (чат и прокси Mini App): до этого листа
#: фото-запись без поправки уходила без origin и хранилась как NULL —
#: «до §136 или не передано» — и была неотличима от старых строк.
PHOTO_ORIGIN_ESTIMATED_CONFIRMED = "photo_estimated_confirmed"
PHOTO_ORIGIN_USER_CORRECTED = "photo_user_corrected"
MEAL_TYPE_UNNAMED = "other"

CB_LOG = "cb:food:text_log"
CB_GRAMS = "cb:food:text_grams"
CB_REJECT = "cb:food:text_reject"
#: DRF-2768 — карточка без расчёта («Сохранить / Изменить / Отменить», решение
#: владельца 06.10) и «Изменить» под карточкой нескольких позиций. Свои
#: payload'ы, а не повтор ``text_log``/``text_reject``: подпись тапа в истории
#: берётся из клавиатуры по payload, и один payload с двумя подписями
#: (``В дневник`` / ``Сохранить``) оставил бы в истории одну из них.
CB_SAVE = "cb:food:text_save"
CB_EDIT = "cb:food:text_edit"
CB_CANCEL = "cb:food:text_cancel"
TEXT_CALLBACKS = frozenset({CB_LOG, CB_GRAMS, CB_REJECT, CB_SAVE, CB_EDIT, CB_CANCEL})

#: DRF-2768 — сколько позиций одной фразой принимает карточка. Больше — это
#: уже рассказ, и каждая позиция стоит отдельного запроса к каталогу.
MAX_POSITIONS = 6

#: DRF-1838 — тапы под СОХРАНЁННОЙ записью. ``id`` записи в payload:
#: запись переживает десятиминутное состояние разговора.
_ENTRY_CALLBACK = ENTRY_CALLBACK_RE
#: DRF-2108 — окно возврата НЕ константа бота: каталог отдаёт
#: ``restore_window_expires_at`` в ответе на удаление, минуты считаются от
#: него; без поля обещания нет — ни фразы, ни чипа «Вернуть».

# ─── тексты ───────────────────────────────────────────────────────────────

ASK_WHAT_TEXT = (
    "Напиши, что было и сколько граммов, — например «гречка 200 г». "
    "Посчитаю и покажу, прежде чем записать. Можно и фото."
)
NOT_FOUND_TEXT = (
    "Не нашла «{dish}» в справочнике блюд — ничего не записала. "
    "Напиши проще, например «омлет 150 г» или «борщ 300 г»."
)
GRAMS_PROMPT = "Сколько граммов было? Напиши число — пересчитаю."
GRAMS_UNREADABLE = f"Не поняла число. Напиши граммы цифрами — от {MIN_GRAMS} до {MAX_GRAMS}."
REJECTED_TEXT = "Поняла, не записываю."
#: DRF-2768 — числа не посчитались ни справочником, ни ИИ: запись без расчёта
#: предлагается, а не подставляется молча и не уходит модели бота (лёгший
#: прокси давал C01). Текст НЕЙТРАЛЬНЫЙ — решение владельца 06.10 (Q4): «Сейчас
#: не удалось…» только для настоящего временного сбоя, а каталог причину пока
#: не различает (ИИ выключен / нет согласия / сбой — всё ``None``).
#: Различающий текст — DRF-2823 (флаг ``kcal_ai_status`` от каталога).
UNPRICED_TEXT = "Калорийность не рассчитана. Записать без расчёта?"
#: Тот же вопрос в режиме «Без чисел»: о калориях человек слышать не выбрал.
UNPRICED_HIDDEN_TEXT = "Записать в дневник?"
EDIT_PROMPT = "Напиши, что было, ещё раз — посчитаю заново."
#: Часть позиций записалась, на следующей дневник не ответил. Ключи записей
#: не меняются, поэтому повторный тап дописывает остальное без дублей.
PARTIAL_TEXT = (
    "Записала не всё — дневник не ответил. Нажми «В дневник» ещё раз: уже записанное не задвоится."
)
INCOMPLETE_CARD_LINE = "Не всё посчитано — итог дня будет неполным."
NOT_LOGGED_TEXT = "Ничего не записала: дневник не принял ни одной позиции."
STALE_TEXT = "Эта оценка уже не действует — напиши, что было, ещё раз, и я посчитаю заново."
UNAVAILABLE_TEXT = "Дневник сейчас не отвечает — ничего не записала. Попробуй через минуту."
CONSENT_TEXT = (
    "Чтобы вести дневник, мне нужно согласие на обработку личных данных — "
    "без него я ничего не записываю."
)
#: DRF-2093 — согласие ДНЕВНИКА (реестр ``food_diary_processing``) отозвано или
#: не выдано. Один текст на воду, текст и фото в чате; без кнопки «Дать
#: согласие» (DRF-1968): та выдаёт PERSONAL_DATA, а согласие дневника из чата
#: выдать нечем — только экран Mini App. Тот же текст, что у сканера (F11).
#: DRF-2096 — под текстом кнопка ``open_app`` на экран согласия
#: (:func:`apps.skills.menu.marketplace.diary_consent_request_action_data`);
#: когда приложение не настроено — прежний текст без кнопки.
DIARY_CONSENT_REQUIRED_TEXT = (
    "Чтобы записать еду, нужно открыть Mini App и подтвердить согласие "
    "на обработку данных (152-ФЗ). После этого вернись — и пришли фото."
)
DIARY_CONSENT_REQUIRED_WITH_BUTTON_TEXT = (
    "Чтобы записать еду, нужно разрешить дневник питания в Mini App. "
    "Нажми кнопку ниже, подтверди — и вернись сюда: повтори, что было."
)


def diary_consent_required_result(reply_kind: str) -> SkillResult:
    """Отказ «нет согласия дневника» — один на воду, текст и фото (DRF-2096).

    Текст с приглашением — только когда есть кнопка; без приложения —
    прежний текст без обещания кнопки, которой нет.
    """
    from apps.skills.menu.marketplace import diary_consent_request_action_data

    action_data = diary_consent_request_action_data()
    return SkillResult(
        reply_text=DIARY_CONSENT_REQUIRED_WITH_BUTTON_TEXT
        if action_data
        else DIARY_CONSENT_REQUIRED_TEXT,
        action_data=action_data,
        meta={"reply_kind": reply_kind},
    )


NUTRITION_OFF_TEXT = "Дневник еды пока недоступен — функция готовится."
FIX_GRAMS_PROMPT = "Сколько граммов было на самом деле? Напиши число — пересчитаю запись."
FIXED_TEXT = "Исправила: {dish} — теперь {kcal} ккал."
# DRF-2761 — оценка калорий ИИ (решение владельца 02.10.2026, пересмотр §40).
# Пометка — слова владельца дословно; число с ней всегда «≈», чтобы оценку
# нельзя было принять за проверенное «N ккал» ни в одной из реплик.
AI_ESTIMATE_MARK = "Оценка ИИ"
#: DRF-2371 — та же правка, когда числа в записи нет: о числе молчим, а не
#: подставляем ноль. Текст, называющий сам пробел, ждёт слова владельца.
FIXED_WITHOUT_NUMBERS_TEXT = "Исправила: {dish}."
DELETED_TEXT = "Убрала запись из дневника."
DELETED_WITH_WINDOW_TEXT = "Убрала запись из дневника. Вернуть можно ещё {minutes}."
RESTORED_TEXT = "Вернула в дневник: {dish} — {kcal} ккал."
#: DRF-2371 — возврат записи без чисел: см. ``FIXED_WITHOUT_NUMBERS_TEXT``.
RESTORED_WITHOUT_NUMBERS_TEXT = "Вернула в дневник: {dish}."
RESTORE_EXPIRED_TEXT = "Уже не вернуть: окно возврата закрылось, запись удалена окончательно."
ENTRY_GONE_TEXT = "Этой записи уже нет в дневнике."
ENTRY_WATER_TEXT = "Эту запись ведёт учёт воды — её убирает отмена стакана."
EDIT_UNAVAILABLE_TEXT = "Дневник сейчас не отвечает — ничего не изменила. Попробуй через минуту."
UNCERTAIN_TEXT = (
    "Не знаю, дошло ли: дневник не ответил вовремя. Загляни в дневник, прежде чем повторять."
)
OTHER_QUESTION_TEXT = "Сначала закончим вопрос, который уже открыт, — потом исправлю граммы."

# ─── разбор фразы ─────────────────────────────────────────────────────────

_GRAMS_IN_TEXT = re.compile(
    r"(\d{1,4}(?:[.,]\d+)?)\s*(?:г|гр|грамм[а-я]*|g|мл|ml)(?![а-яёa-z])", re.IGNORECASE
)
#: DRF-2078 — голое число В КОНЦЕ фразы («борщ 250»): человек назвал порцию,
#: единицу опустил. Число в начале («2 яйца») — счёт, не граммы, и сюда не
#: попадает: якорь ``$`` требует, чтобы после числа не было слов.
_TRAILING_NUMBER = re.compile(r"(?<![\d.,])(\d{2,4})\s*\.?\s*$")
_GRAMS_ANSWER = re.compile(r"^\s*(\d{1,4})\s*(?:г|гр|грамм[а-я]*|g)?\s*\.?\s*$", re.IGNORECASE)
_FILLER = re.compile(
    r"^(?:(?:я|сегодня|вчера|утром|днём|днем|вечером|на\s+(?:завтрак|обед|ужин|перекус)"
    r"|съел|съела|поел|поела|ел|ела|скушал|скушала|был|была|были|было)\s+)+",
    re.IGNORECASE,
)
#: DRF-2765 — просьба записать («запиши в дневник», «добавь», «внеси») —
#: не название еды. Без этого «запиши в дневник борщ 300гр» уходило в
#: каталог целиком, справочник промахивался, и вместо проверенного борща
#: ложилась оценка ИИ, которая не входит в сумму дня. Глагол — целым словом:
#: «запеканка» и «добавка к супу» остаются блюдами.
_DIARY = r"(?:в|во)\s+(?:мой\s+)?(?:дневник|журнал)(?:\s+(?:питания|еды))?"
_ASK_TO_LOG = (
    r"(?:запиши|запишите|запишем|записать|добавь|добавьте|добавить|внеси|внесите|внести"
    r"|занеси|занесите|занести|отметь|отметьте|залогируй|зафиксируй)"
    r"(?:\s+(?:мне|пожалуйста))*"
)
_COMMAND_HEAD = re.compile(
    rf"^(?:(?:пожалуйста|давай)\s+)*{_ASK_TO_LOG}(?:\s+{_DIARY})?(?:\s+что)?(?:\s+|$)"
)
_COMMAND_TAIL = re.compile(rf"\s+(?:{_ASK_TO_LOG}(?:\s+{_DIARY})?|{_DIARY})$")
_NOISE = re.compile(r"[^\w\s-]", re.UNICODE)
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class ParsedFood:
    dish: str
    grams: float | None


def parse_food_text(text: str) -> ParsedFood | None:
    """«съел борщ 300г» → ``ParsedFood("борщ", 300.0)``; ``None`` — не разобрать.

    Граммы берутся, только если человек их НАЗВАЛ. Без граммов — ``None``, и
    оценка идёт на базовые 100 г с пометкой «это оценка» на карточке.
    Названные, но вне границ записи граммы — ``None`` целиком: пересчитать
    их молча в «ближайшее допустимое» значило бы записать не то, что сказано.

    Названными считаются (DRF-2078) и «борщ 250» — число в конце фразы без
    единицы. До этого такая фраза давала ``dish="борщ 250", grams=None``, и
    порция уезжала в 100 г по умолчанию, хотя человек её сказал. «мл» — тоже
    единица: для оценки по справочнику 1 мл принимается за 1 г; это
    допущение названо здесь, а не спрятано в числе.
    """
    raw = (text or "").strip()
    if not raw or raw.startswith("cb:"):
        return None
    grams: float | None = None
    match = _GRAMS_IN_TEXT.search(raw) or _TRAILING_NUMBER.search(raw)
    if match:
        grams = float(match.group(1).replace(",", "."))
        if not MIN_GRAMS <= grams <= MAX_GRAMS:
            return None
        raw = f"{raw[: match.start()]} {raw[match.end() :]}"
    cleaned = _WS.sub(" ", _NOISE.sub(" ", raw)).strip().lower()
    cleaned = _COMMAND_HEAD.sub("", cleaned)
    cleaned = _COMMAND_TAIL.sub("", cleaned)
    cleaned = _FILLER.sub("", cleaned + " ").strip()
    if not cleaned or len(cleaned) > 60:
        return None
    return ParsedFood(dish=cleaned, grams=grams)


#: DRF-2768 — разделители позиций: запятая, «;», перевод строки, «+». НЕ «и» и
#: НЕ «с»: «лепешка роти с творогом и сыром» — одно блюдо. Запятая между
#: цифрами («1,5 %», «200,5 г») — часть числа, не разделитель.
_POSITION_SPLIT = re.compile(r"\s*(?:[;\n+]|(?<!\d),|,(?!\d))\s*")


def parse_food_positions(text: str) -> list[ParsedFood] | None:
    """«лепешка роти с творогом и сыром, кофе» → две позиции; ``None`` — не еда.

    Каждая часть разбирается тем же :func:`parse_food_text` — со своими
    граммами и командными словами. Если хоть одна часть не разбирается —
    ``None`` целиком: половина фразы о еде не делает её списком блюд.
    """
    raw = (text or "").strip()
    if not raw or raw.startswith("cb:"):
        return None
    parts = [part for part in _POSITION_SPLIT.split(raw) if part.strip()]
    if not parts or len(parts) > MAX_POSITIONS:
        return None
    positions = [parse_food_text(part) for part in parts]
    if any(position is None for position in positions):
        return None
    return [position for position in positions if position is not None]


#: DRF-2768 (Q2 главного окна 06.10) — закрытый короткий список реплик, которые
#: не еда, хотя ``parse_food_text`` примет их за блюдо. Не детектор еды —
#: предохранитель от карточки «Записать без расчёта?» на «спасибо». Держать
#: маленьким: всё, чего здесь нет, получает карточку с «Отменить».
_NOT_FOOD_REPLIES = frozenset(
    {
        "спасибо",
        "спасибо большое",
        "благодарю",
        "ок",
        "окей",
        "хорошо",
        "понятно",
        "ясно",
        "нет",
        "не надо",
        "не помню",
        "не знаю",
        "забыл",
        "забыла",
        "ничего",
        "неважно",
        "отмена",
    }
)


def looks_like_not_food(text: str) -> bool:
    """Ответ — не описание еды: вопрос, реплика из закрытого списка или запись к мастеру."""
    stripped = (text or "").strip()
    if stripped.rstrip(" )!.…").endswith(("?", "？")):
        return True
    normalized = _WS.sub(" ", _NOISE.sub(" ", stripped)).strip().lower()
    if normalized in _NOT_FOOD_REPLIES:
        return True
    return "мастер" in normalized


# ─── состояние ────────────────────────────────────────────────────────────


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def restore_minutes_left(expires_at: str | None, *, now: datetime) -> int | None:
    """Сколько минут ещё можно вернуть запись — по ``restore_window_expires_at``.

    ``None`` — поля нет, дата не читается или окно уже закрылось: обещать
    нечего. Минуты — вверх: «ещё 15 минут» при 14:30 честнее, чем «14».
    """
    if not expires_at:
        return None
    try:
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    seconds = (expires - now).total_seconds()
    if seconds <= 0:
        return None
    return max(1, int(-(-seconds // 60)))


def _minutes_ru(n: int) -> str:
    mod10, mod100 = n % 10, n % 100
    if mod10 == 1 and mod100 != 11:
        word = "минуту"
    elif 2 <= mod10 <= 4 and not 12 <= mod100 <= 14:
        word = "минуты"
    else:
        word = "минут"
    return f"{n} {word}"


def _entry_fixable(log: FoodLogResponse) -> bool:
    """«Исправить граммы» — только у записи текстом (тот же предикат, что isTextEntry)."""
    origin = (log.raw or {}).get("entry_origin")
    return isinstance(origin, str) and origin.startswith("text_")


def _bucket(conversation: Any) -> dict[str, Any] | None:
    raw = getattr(conversation, "skill_state", None)
    if not isinstance(raw, dict):
        return None
    bucket = raw.get(STATE_KEY)
    if not isinstance(bucket, dict):
        return None
    stamped = bucket.get("at")
    if not isinstance(stamped, str):
        return None
    try:
        at = datetime.fromisoformat(stamped)
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - at > timedelta(seconds=PENDING_TTL_SECONDS):
        return None
    return bucket


def _write(conversation: Any, value: dict[str, Any] | None) -> None:
    """Best-effort запись под-ключа. Потеря состояния стоит одного переспроса."""
    try:
        from apps.conversations.models import Conversation

        if isinstance(conversation, Conversation):
            from apps.conversations.services import write_skill_state

            write_skill_state(conversation, STATE_KEY, value)
            return
    except Exception:  # noqa: BLE001 — degraded state beats a lost reply
        logger.debug(
            "food_text.state_write_skipped conversation=%s", getattr(conversation, "id", None)
        )
        return
    raw = getattr(conversation, "skill_state", None)
    if isinstance(raw, dict):
        if value is None:
            raw.pop(STATE_KEY, None)
        else:
            raw[STATE_KEY] = value


def has_pending_text_entry(conversation: Any) -> bool:
    """Ждёт ли бот ответа на свой вопрос — граммы или «что было»?

    Спрашивает глобальный диспетчер, решая, чья реплика: так же, как
    про открытую анкету и незакрытую поправку скана.
    """
    bucket = _bucket(conversation)
    return bool(
        bucket
        and (
            bucket.get("awaiting_grams")
            or bucket.get("expect_food")
            or bucket.get("awaiting_fix_grams")
        )
    )


def claims_text(conversation: Any, text: str) -> bool:
    """Эта реплика — ответ на открытый вопрос текстового ввода?"""
    bucket = _bucket(conversation)
    if not bucket:
        return False
    if bucket.get("awaiting_grams") or bucket.get("awaiting_fix_grams"):
        return bool(_GRAMS_ANSWER.match(text or ""))
    if bucket.get("expect_food"):
        # DRF-2768 — «спасибо» в ответ на «напиши, что было» не еда: без этой
        # строки оно получило бы карточку «Записать без расчёта?».
        return parse_food_text(text) is not None and not looks_like_not_food(text)
    return False


def remember_source(context: SkillContext, text: str) -> None:
    """Фраза до тапа «📔 В дневник»: тап несёт только payload, фраза — здесь."""
    _write(context.conversation, {"source": text, "at": _now_iso()})


def forget(context: SkillContext) -> None:
    _write(context.conversation, None)


def mark_after_scan(context: SkillContext) -> None:
    """Сканер сказал «не разобралась… просто напиши, что было» (DRF-2328).

    Отметка МЯГКАЯ — не ``expect_food``. :func:`has_pending_text_entry` и
    :func:`claims_text` её не видят, поэтому структурный путь реплику не
    забирает: ``parse_food_text`` принимает что угодно («спасибо» → блюдо
    «спасибо»), и взведённое ожидание увело бы вопрос человека в «Не
    нашла…». Отметку читает только ярлык в ``nutrition_global``, который
    отвечает лишь карточкой оценки, а всё остальное отпускает к модели.
    """
    _write(context.conversation, {"after_scan": True, "at": _now_iso()})


def take_after_scan(conversation: Any) -> bool:
    """Была ли свежая отметка :func:`mark_after_scan`; отметка расходуется.

    Одноразовая: второй набранный текст уже не ответ на вопрос сканера.
    """
    bucket = _bucket(conversation)
    if not bucket or not bucket.get("after_scan"):
        return False
    _write(conversation, None)
    return True


# ─── гейты ────────────────────────────────────────────────────────────────


def diary_entry_refusal(bot_user: Any) -> SkillResult | None:
    """Отказ записи в дневник ЭТОМУ человеку — экраном, или ``None``.

    Тот же предикат, что у всех писателей (``diary_write_refusal``,
    DRF-2093): флаг → PERSONAL_DATA → реестр согласия дневника; и те же
    три экрана отказа. Публичен ради кнопки «Записать еду» на возврате
    глобального бота (DRF-2120): ворота читаются ДО приглашения прислать
    еду, а не после него.
    """
    from apps.consent.diary_gate import (
        CONSENT_REQUIRED,
        FOOD_DIARY_CONSENT_REQUIRED,
        NUTRITION_DISABLED,
        diary_write_refusal,
    )

    reason = diary_write_refusal(bot_user)
    if reason == NUTRITION_DISABLED:
        return SkillResult(
            reply_text=NUTRITION_OFF_TEXT, meta={"reply_kind": "food_text_nutrition_off"}
        )
    if reason == CONSENT_REQUIRED:
        from apps.skills.welcome.skill import consent_offer_action_data

        return SkillResult(
            reply_text=CONSENT_TEXT,
            action_data=consent_offer_action_data("text"),
            meta={"reply_kind": "food_text_consent_required"},
        )
    if reason == FOOD_DIARY_CONSENT_REQUIRED:
        return diary_consent_required_result("food_text_diary_consent_required")
    return None


def _gate(context: SkillContext) -> SkillResult | None:
    """Ворота записи текстом. Правка и возврат записи идут через них же
    (они пишут в дневник); удаление — нет."""
    return diary_entry_refusal(context.bot_user)


# ─── шаги ─────────────────────────────────────────────────────────────────


def on_diary_tap(context: SkillContext) -> SkillResult:
    """«📔 В дневник» под «Это про еду?»: оценить исходную фразу — или спросить её."""
    bucket = _bucket(context.conversation)
    source = bucket.get("source") if bucket else None
    # DRF-2287: фраза-вопрос («а торт в справочнике есть?») — не описание
    # еды. Оценить её значило бы искать в справочнике весь вопрос и ответить
    # «Не нашла «а есть вообще торт…»». Спрашиваем, что было, — как без фразы.
    is_question = isinstance(source, str) and source.rstrip(" )!.…").endswith(("?", "？"))
    positions = (
        parse_food_positions(source) if isinstance(source, str) and not is_question else None
    )
    if positions is None:
        _write(context.conversation, {"expect_food": True, "at": _now_iso()})
        return SkillResult(reply_text=ASK_WHAT_TEXT, meta={"reply_kind": "food_text_ask"})
    return show_positions(context, positions)


def on_text(context: SkillContext, text: str) -> SkillResult:
    """Реплика, которую :func:`claims_text` признал ответом."""
    bucket = _bucket(context.conversation) or {}
    if bucket.get("awaiting_fix_grams"):
        return _on_fix_grams_answer(context, bucket, text)
    if bucket.get("awaiting_grams"):
        return _on_grams_answer(context, bucket, text)
    positions = parse_food_positions(text)
    if positions is None:
        return SkillResult(reply_text=ASK_WHAT_TEXT, meta={"reply_kind": "food_text_ask"})
    return show_positions(context, positions)


def show_positions(context: SkillContext, positions: list[ParsedFood]) -> SkillResult:
    """Одна позиция — прежняя карточка; несколько — карточка всех позиций (DRF-2768)."""
    if len(positions) == 1:
        return show_estimate(context, positions[0].dish, positions[0].grams, corrected=False)
    return show_items(context, positions)


def on_callback(context: SkillContext, text: str) -> SkillResult:
    if text in (CB_REJECT, CB_CANCEL):
        forget(context)
        return SkillResult(reply_text=REJECTED_TEXT, meta={"reply_kind": "food_text_rejected"})
    if text == CB_EDIT:
        # «Изменить» — написать заново: блюдо и граммы правятся новой фразой.
        _write(context.conversation, {"expect_food": True, "at": _now_iso()})
        return SkillResult(
            reply_text=EDIT_PROMPT,
            action_data={"buttons": _after_entry_buttons()},
            meta={"reply_kind": "food_text_edit"},
        )
    bucket = _bucket(context.conversation)
    has_card = bool(bucket and bucket.get("token") and (bucket.get("dish") or bucket.get("items")))
    if not bucket or not has_card:
        forget(context)
        return SkillResult(reply_text=STALE_TEXT, meta={"reply_kind": "food_text_stale"})
    if bucket.get("items"):
        if text in (CB_LOG, CB_SAVE):
            return _log_items(context, bucket)
        # У карточки нескольких позиций граммов не правят — пишут заново.
        _write(context.conversation, {"expect_food": True, "at": _now_iso()})
        return SkillResult(
            reply_text=EDIT_PROMPT,
            action_data={"buttons": _after_entry_buttons()},
            meta={"reply_kind": "food_text_edit"},
        )
    if text == CB_GRAMS:
        _write(context.conversation, {**bucket, "awaiting_grams": True, "at": _now_iso()})
        return SkillResult(reply_text=GRAMS_PROMPT, meta={"reply_kind": "food_text_grams_prompt"})
    return _log(context, bucket)


def show_estimate(
    context: SkillContext, dish: str, grams: float | None, *, corrected: bool
) -> SkillResult:
    """Шаги 2–4 §109: оценка без записи и карточка «Я распознала так»."""
    refused = _gate(context)
    if refused is not None:
        return refused
    external_id = external_user_id_for(context.bot_user)
    try:
        estimate = asyncio.run(
            get_nutrition_client().estimate_dish(
                external_user_id=external_id, dish_name=dish, portion_g=grams
            )
        )
    except FoodNotRecognizedError:
        forget(context)
        return SkillResult(
            reply_text=NOT_FOUND_TEXT.format(dish=dish),
            meta={"reply_kind": "food_text_not_found"},
        )
    except NutritionUnavailableError:
        logger.warning("food_text.estimate.unavailable user=%s", external_id)
        return SkillResult(
            reply_text=UNAVAILABLE_TEXT, meta={"reply_kind": "food_text_unavailable"}
        )
    except NutritionAPIError:
        logger.exception("food_text.estimate.error user=%s", external_id)
        return SkillResult(
            reply_text=UNAVAILABLE_TEXT, meta={"reply_kind": "food_text_unavailable"}
        )

    _write(
        context.conversation,
        {
            "dish": estimate.matched_dish,
            "portion_g": estimate.portion_g,
            "portion_estimated": estimate.portion_estimated,
            "corrected": corrected,
            "token": uuid.uuid4().hex,
            "at": _now_iso(),
        },
    )
    if _unpriced(estimate):
        # DRF-2768, путь 2: ни справочник, ни ИИ числа не дали. Запись без
        # расчёта предлагается словами владельца — не молча и не через модель.
        return SkillResult(
            reply_text=render_unpriced_card(
                [
                    (
                        estimate.matched_dish,
                        None if estimate.portion_estimated else estimate.portion_g,
                    )
                ],
                hide_numbers=_numbers_hidden(context),
            ),
            action_type="food_text_unpriced_card",
            action_data={"buttons": food_text_unpriced_keyboard(), "dish": estimate.matched_dish},
            meta={"reply_kind": UNPRICED_CARD_KIND, "kcal_known": False},
        )
    return SkillResult(
        reply_text=render_estimate_card(estimate, hide_numbers=_numbers_hidden(context)),
        action_type="food_text_estimate_card",
        action_data={
            "buttons": food_text_estimate_keyboard(),
            "dish": estimate.matched_dish,
            "portion_g": estimate.portion_g,
            "portion_estimated": estimate.portion_estimated,
        },
        # DRF-2328: число дал справочник (``seed_ru``/USDA). Каталог с DRF-2371
        # не отказывает ни на какое имя — «спасибо» тоже получает оценку, с
        # пустыми числами или числом ИИ (``kcal_ai_estimate``), так что
        # «карточка пришла» ещё не значит «это еда».
        meta={"reply_kind": "food_text_estimate_card", "kcal_known": estimate.kcal is not None},
    )


def _numbers_hidden(context: SkillContext) -> bool:
    """«Без чисел» (DRF-2766): человек сам выбрал не видеть калории, БЖУ и цели.

    Решение владельца 04.10 — режим отображения на всех экранах, включая чат.
    Записи дневника он не трогает: запись ложится с числами, а реплика о ней
    звучит без чисел — теми же шаблонами, что и запись, у которой числа нет.
    """
    from apps.nutrition_proactive.prefs import numbers_hidden_for

    return numbers_hidden_for(context.bot_user)


def ai_kcal_phrase(kcal: float) -> str:
    """«≈ 320 ккал · Оценка ИИ» — единственная форма, в которой оценка звучит."""
    return f"≈ {int(round(kcal))} ккал · {AI_ESTIMATE_MARK}"


def _ai_calories_of(log: Any) -> float | None:
    """Оценка ИИ у записи — только когда проверенного числа нет.

    Проверенное бьёт оценку и здесь: если каталог отдал ``calories``, оценка
    не показывается, даже если поле с ней почему-то пришло.
    """
    if getattr(log, "calories", None) is not None:
        return None
    value = getattr(log, "ai_calories", None)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def render_estimate_card(estimate: Any, *, hide_numbers: bool = False) -> str:
    """«Я распознала так» — каждое предположение названо предположением (§109 шаг 4).

    ``hide_numbers`` — выбор человека «Без чисел» (DRF-2766): ни калорий, ни
    БЖУ, ни оценки ИИ. Блюдо и порция остаются — без них нечего подтверждать.
    """
    grams = int(round(estimate.portion_g))
    lines = [f"Я распознала так: {estimate.matched_dish}."]
    if estimate.portion_estimated:
        lines.append(f"Порция — примерно {grams} г, это оценка: граммов в сообщении не было.")
    else:
        lines.append(f"Порция — {grams} г, по твоим словам.")
    # DRF-2371 — числа может не быть вовсе (блюда нет в справочнике). Тогда
    # строки макросов нет: «Примерно 0 ккал» утверждало бы посчитанное.
    # Текст, называющий сам пробел, ждёт слова владельца (OWNER_QUESTIONS);
    # до ответа карточка о числах молчит, а дорога рядом — назвать граммы.
    # DRF-2371 — см. `portion_provenance`: число называем только тогда,
    # когда вес кто-то назвал; подставленное за названное не выдаём.
    provenance = portion_provenance_of((getattr(estimate, "raw", None) or {}).get("portion_source"))
    show = not hide_numbers
    if show and estimate.kcal is not None and portion_numbers_are_named(provenance):
        macros = [f"Примерно {int(round(estimate.kcal))} ккал"]
        for label, value in (
            ("Б", estimate.protein_g),
            ("Ж", estimate.fat_g),
            ("У", estimate.carbs_g),
        ):
            if value is not None:
                macros.append(f"{label} {int(round(value))}")
        lines.append(" · ".join(macros) + " — оценка по справочнику блюд.")
    elif show and estimate.kcal is None and getattr(estimate, "kcal_ai_estimate", None) is not None:
        # DRF-2761 — справочник блюда не знает, калории оценил ИИ. Довод
        # владельца: вес при промахе мы и так показываем «примерно», значит
        # и калории честнее показать примерными, чем прятать, — поэтому
        # оценка звучит и тогда, когда граммов человек не называл. Только
        # калории: БЖУ ИИ не оценивает. Проверенное число этой ветки не
        # достигает (``kcal is None``).
        lines.append(ai_kcal_phrase(estimate.kcal_ai_estimate) + ".")
    lines.append("Записать в дневник?")
    return "\n".join(lines)


# ─── DRF-2768: несколько позиций одной фразой и запись без расчёта ────────

ESTIMATE_CARD_KIND = "food_text_estimate_card"
ITEMS_CARD_KIND = "food_text_items_card"
UNPRICED_CARD_KIND = "food_text_unpriced_card"
#: Все виды карточки подтверждения: ответ после фото (DRF-2328) отвечается
#: любой из них — запись всё равно только по тапу.
CARD_KINDS = frozenset({ESTIMATE_CARD_KIND, ITEMS_CARD_KIND, UNPRICED_CARD_KIND})

SOURCE_DICTIONARY = "dictionary"
SOURCE_AI = "ai"


def _unpriced(estimate: Any) -> bool:
    """Ни справочник, ни ИИ числа не дали — путь 2 решения владельца."""
    return estimate.kcal is None and getattr(estimate, "kcal_ai_estimate", None) is None


def render_unpriced_card(
    entries: list[tuple[str, float | None]], *, hide_numbers: bool = False
) -> str:
    """«Калорийность не рассчитана. Записать без расчёта?» + что запишется.

    Исходная фраза без чисел: человек видит, ЧТО ляжет в дневник, и что ляжет
    без калорий (не нулём — итог дня пометится неполным). В режиме «Без чисел»
    о калориях не говорим вовсе — только что запишется. Граммы, которые
    человек назвал, показываются: они уйдут в запись.
    """
    named = ", ".join(
        f"«{dish}» ({int(round(grams))} г)" if grams is not None else f"«{dish}»"
        for dish, grams in entries
    )
    question = UNPRICED_HIDDEN_TEXT if hide_numbers else UNPRICED_TEXT
    return f"{question}\nЗапишу как есть: {named}."


def _item_of(position: ParsedFood, estimate: Any | None) -> dict[str, Any]:
    """Позиция карточки и её число — с происхождением, или без числа.

    Число справочника — только когда вес назван (тот же
    ``portion_numbers_are_named``, что у карточки одного блюда); оценка ИИ —
    когда справочник блюда не знает. Справочное число и оценка ИИ для одной
    порции не складываются: проверенное бьёт оценку.
    """
    if estimate is None:
        grams = position.grams if position.grams is not None else BASELINE_G
        return {
            "dish": position.dish,
            "portion_g": grams,
            "portion_estimated": position.grams is None,
            "kcal": None,
            "source": None,
        }
    item: dict[str, Any] = {
        "dish": estimate.matched_dish,
        "portion_g": estimate.portion_g,
        "portion_estimated": estimate.portion_estimated,
        "kcal": None,
        "source": None,
    }
    provenance = portion_provenance_of((getattr(estimate, "raw", None) or {}).get("portion_source"))
    if estimate.kcal is not None and portion_numbers_are_named(provenance):
        item.update(kcal=float(estimate.kcal), source=SOURCE_DICTIONARY)
    elif estimate.kcal is None and getattr(estimate, "kcal_ai_estimate", None) is not None:
        item.update(kcal=float(estimate.kcal_ai_estimate), source=SOURCE_AI)
    return item


def render_items_card(items: list[dict[str, Any]], *, hide_numbers: bool = False) -> str:
    """Карточка нескольких позиций: у каждого числа — откуда оно (handoff §5, путь 1)."""
    lines = ["Я распознала так:"]
    for item in items:
        grams = int(round(float(item["portion_g"])))
        portion = f"примерно {grams} г (оценка)" if item["portion_estimated"] else f"{grams} г"
        line = f"• {item['dish']} — {portion}"
        if not hide_numbers:
            if item["source"] == SOURCE_DICTIONARY:
                line += f" — примерно {int(round(item['kcal']))} ккал по справочнику"
            elif item["source"] == SOURCE_AI:
                line += f" — {ai_kcal_phrase(item['kcal'])}"
            else:
                line += " — без расчёта"
        lines.append(line)
    if not hide_numbers and any(item["source"] is None for item in items):
        lines.append(INCOMPLETE_CARD_LINE)
    lines.append("Записать в дневник?")
    return "\n".join(lines)


def show_items(context: SkillContext, positions: list[ParsedFood]) -> SkillResult:
    """Оценка каждой позиции без записи и ОДНА карточка подтверждения на все."""
    refused = _gate(context)
    if refused is not None:
        return refused
    external_id = external_user_id_for(context.bot_user)
    client = get_nutrition_client()
    items: list[dict[str, Any]] = []
    for position in positions:
        try:
            estimate = asyncio.run(
                client.estimate_dish(
                    external_user_id=external_id,
                    dish_name=position.dish,
                    portion_g=position.grams,
                )
            )
        except FoodNotRecognizedError:
            estimate = None
        except (NutritionUnavailableError, NutritionAPIError):
            logger.warning("food_text.items.estimate_failed user=%s", external_id)
            return SkillResult(
                reply_text=UNAVAILABLE_TEXT,
                action_data={"buttons": _after_entry_buttons()},
                meta={"reply_kind": "food_text_unavailable"},
            )
        items.append(_item_of(position, estimate))

    _write(
        context.conversation,
        {"items": items, "token": uuid.uuid4().hex, "at": _now_iso()},
    )
    dishes = [str(item["dish"]) for item in items]
    if all(item["source"] is None for item in items):
        return SkillResult(
            reply_text=render_unpriced_card(
                [
                    (str(item["dish"]), None if item["portion_estimated"] else item["portion_g"])
                    for item in items
                ],
                hide_numbers=_numbers_hidden(context),
            ),
            action_type="food_text_unpriced_card",
            action_data={"buttons": food_text_unpriced_keyboard(), "dishes": dishes},
            meta={"reply_kind": UNPRICED_CARD_KIND, "kcal_known": False},
        )
    return SkillResult(
        reply_text=render_items_card(items, hide_numbers=_numbers_hidden(context)),
        action_type="food_text_items_card",
        action_data={"buttons": food_text_items_keyboard(), "dishes": dishes},
        meta={
            "reply_kind": ITEMS_CARD_KIND,
            "kcal_known": any(item["source"] == SOURCE_DICTIONARY for item in items),
        },
    )


def _log_items(context: SkillContext, bucket: dict[str, Any]) -> SkillResult:
    """Запись всех позиций карточки — по тапу, каждая со своим ключом идемпотентности.

    Ключ ``food-text:{человек}:{токен карточки}:{номер}`` не меняется между
    тапами: повторный тап после частичного сбоя дописывает остальное, а уже
    записанное каталог узнаёт по ключу и не задваивает.
    """
    refused = _gate(context)
    if refused is not None:
        return refused
    external_id = external_user_id_for(context.bot_user)
    client = get_nutrition_client()
    logged: list[Any] = []
    for index, item in enumerate(bucket["items"]):
        try:
            log = asyncio.run(
                client.log_meal(
                    external_user_id=external_id,
                    dish_name=str(item["dish"]),
                    meal_type=MEAL_TYPE_UNNAMED,
                    portion_multiplier=round(float(item["portion_g"]) / BASELINE_G, 3),
                    idempotency_key=f"food-text:{external_id}:{bucket['token']}:{index}",
                    entry_origin=ORIGIN_ESTIMATED_CONFIRMED,
                )
            )
        except FoodNotRecognizedError:
            logger.info("food_text.items.not_recognized user=%s index=%d", external_id, index)
            continue
        except (NutritionUnavailableError, NutritionAPIError):
            logger.warning("food_text.items.log_failed user=%s index=%d", external_id, index)
            # Повтор — та же карточка: «В дневник» дописывает остальное без дублей.
            return SkillResult(
                reply_text=PARTIAL_TEXT,
                action_data={"buttons": food_text_items_keyboard()},
                meta={"reply_kind": "food_text_partial"},
            )
        logged.append(log)

    forget(context)
    hide = _numbers_hidden(context)
    parts = []
    for log in logged:
        ai_calories = _ai_calories_of(log)
        if hide:
            parts.append(log.dish_name)
        elif ai_calories is not None:
            parts.append(f"{log.dish_name} — {ai_kcal_phrase(ai_calories)}")
        elif log.calories is None:
            parts.append(log.dish_name)
        else:
            parts.append(f"{log.dish_name} — {int(round(log.calories))} ккал")
    return SkillResult(
        reply_text="Записала в дневник: " + "; ".join(parts) + "." if parts else NOT_LOGGED_TEXT,
        claims_done=bool(parts),
        claims_done_evidence="ayla.meals.log:log_id" if parts else "",
        action_type="food_logged" if parts else "",
        action_data={
            "log_ids": [log.log_id for log in logged],
            "buttons": _after_entry_buttons(),
        },
        meta={"reply_kind": "food_text_items_logged" if parts else "food_text_not_found"},
    )


def _on_grams_answer(context: SkillContext, bucket: dict[str, Any], text: str) -> SkillResult:
    match = _GRAMS_ANSWER.match(text or "")
    grams = int(match.group(1)) if match else 0
    if not MIN_GRAMS <= grams <= MAX_GRAMS:
        return SkillResult(
            reply_text=GRAMS_UNREADABLE, meta={"reply_kind": "food_text_grams_unreadable"}
        )
    return show_estimate(context, str(bucket.get("dish") or ""), float(grams), corrected=True)


def _log(context: SkillContext, bucket: dict[str, Any]) -> SkillResult:
    """Шаг 6 §109: запись — только по тапу «✅ В дневник» под показанной оценкой."""
    refused = _gate(context)
    if refused is not None:
        return refused
    external_id = external_user_id_for(context.bot_user)
    origin = ORIGIN_USER_CORRECTED if bucket.get("corrected") else ORIGIN_ESTIMATED_CONFIRMED
    try:
        log = asyncio.run(
            get_nutrition_client().log_meal(
                external_user_id=external_id,
                dish_name=str(bucket["dish"]),
                meal_type=MEAL_TYPE_UNNAMED,
                portion_multiplier=round(float(bucket["portion_g"]) / BASELINE_G, 3),
                idempotency_key=f"food-text:{external_id}:{bucket['token']}",
                entry_origin=origin,
            )
        )
    except FoodNotRecognizedError:
        forget(context)
        return SkillResult(
            reply_text=NOT_FOUND_TEXT.format(dish=bucket.get("dish") or ""),
            meta={"reply_kind": "food_text_not_found"},
        )
    except NutritionUnavailableError:
        logger.warning("food_text.log.unavailable user=%s", external_id)
        return SkillResult(
            reply_text=UNAVAILABLE_TEXT, meta={"reply_kind": "food_text_unavailable"}
        )
    except NutritionAPIError:
        logger.exception("food_text.log.error user=%s", external_id)
        return SkillResult(
            reply_text=UNAVAILABLE_TEXT, meta={"reply_kind": "food_text_unavailable"}
        )

    forget(context)
    action_data: dict[str, Any] = {
        "log_id": log.log_id,
        "dish_name": log.dish_name,
        "calories": log.calories,
        "entry_origin": origin,
    }
    ai_calories = _ai_calories_of(log)
    if ai_calories is not None:
        # Своим ключом: читатель ``calories`` оценку за проверенное не примет.
        action_data["ai_calories"] = ai_calories
    entry_chips: list[dict[str, str]] = []
    if log.log_id and ENTRY_ID_RE.match(log.log_id):
        # DRF-1838 — §109 шаг 7: сохранённую запись можно исправить или удалить.
        entry_chips = food_text_logged_keyboard(log.log_id)
    # DRF-2267 (CD §72): и следующий шаг — «Мой дневник», «Меню».
    action_data["buttons"] = [*entry_chips, *_after_entry_buttons()]
    return SkillResult(
        reply_text=(
            # DRF-2766 — «Без чисел»: о записи говорим, число не называем.
            f"Записала в дневник: {log.dish_name}."
            if _numbers_hidden(context)
            # DRF-2371 — запись легла, числа нет: говорим о записи, а число
            # не выдумываем (ноль читался бы как посчитанный).
            else f"Записала в дневник: {log.dish_name} — {ai_kcal_phrase(ai_calories)}."
            if ai_calories is not None
            else f"Записала в дневник: {log.dish_name}."
            if log.calories is None
            else f"Записала в дневник: {log.dish_name} — {int(round(log.calories))} ккал."
        ),
        claims_done=True,
        claims_done_evidence="ayla.meals.log:log_id",
        action_type="food_logged",
        action_data=action_data,
        meta={
            "reply_kind": "food_text_logged",
        },
    )


# ─── сохранённая запись: исправить / удалить / вернуть (DRF-1838) ─────────


def is_entry_callback(text: str) -> bool:
    """Тап под сохранённой записью (``cb:food:entry_{fix,del,undo}:<id>``)?"""
    return bool(_ENTRY_CALLBACK.match((text or "").strip()))


def _nutrition_on() -> bool:
    from django.conf import settings

    return bool(getattr(settings, "NUTRITION_ENABLED", False))


def _other_question_open(conversation: Any) -> bool:
    """Открыт ли вопрос, который заберёт число раньше этого скилла?

    Диспетчер питания пробует скиллы по порядку: поправка скана и анкета
    стоят раньше ``food_clarify``. Ответ «250» ушёл бы им, а запись дневника
    осталась бы прежней — поэтому «Исправить граммы» ждёт, пока они закроются.
    """
    from apps.orchestrator import nutrition_global

    return bool(
        nutrition_global._food_correction_pending(conversation)  # noqa: SLF001 — одна правда на весь диспетчер
        or nutrition_global._anketa_fsm_active(conversation)  # noqa: SLF001
    )


def on_entry_callback(context: SkillContext, text: str) -> SkillResult:
    """§109 шаг 7 — правка, удаление и возврат записи по тапу под ней.

    Удаление не требует открытого согласия: убрать свою запись человек
    вправе всегда, это не новая обработка. Правка и возврат пишут в дневник —
    те же ворота, что у записи (:func:`_gate`).
    """
    match = _ENTRY_CALLBACK.match((text or "").strip())
    assert match is not None  # routed only after is_entry_callback
    action, log_id = match.group(1), match.group(2)
    if action == "del":
        if not _nutrition_on():
            return SkillResult(
                reply_text=NUTRITION_OFF_TEXT, meta={"reply_kind": "food_text_nutrition_off"}
            )
        return _delete_entry(context, log_id)
    refused = _gate(context)
    if refused is not None:
        return refused
    if action == "fix":
        if _other_question_open(context.conversation):
            return SkillResult(
                reply_text=OTHER_QUESTION_TEXT, meta={"reply_kind": "food_entry_fix_blocked"}
            )
        _write(
            context.conversation,
            {"awaiting_fix_grams": True, "log_id": log_id, "at": _now_iso()},
        )
        return SkillResult(
            reply_text=FIX_GRAMS_PROMPT, meta={"reply_kind": "food_entry_fix_prompt"}
        )
    return _restore_entry(context, log_id)


def _entry_refusal(exc: Exception, *, external_id: str, step: str) -> SkillResult:
    # DRF-2267 (CD §72) — отказ про УЖЕ существующую запись: «Мой дневник»
    # показывает, что в дневнике на самом деле, и «Меню».
    way_on = {"buttons": _after_entry_buttons(), "button_columns": 1}
    if isinstance(exc, MealRestoreExpiredError):
        return SkillResult(
            reply_text=RESTORE_EXPIRED_TEXT,
            action_data=way_on,
            meta={"reply_kind": "food_entry_restore_expired"},
        )
    if isinstance(exc, MealNotFoundError):
        return SkillResult(
            reply_text=ENTRY_GONE_TEXT, action_data=way_on, meta={"reply_kind": "food_entry_gone"}
        )
    if isinstance(exc, MealEditConflictError):
        return SkillResult(
            reply_text=ENTRY_WATER_TEXT, action_data=way_on, meta={"reply_kind": "food_entry_water"}
        )
    if isinstance(exc, NutritionUncertainOutcomeError):
        logger.warning("food_entry.%s.uncertain user=%s", step, external_id)
        return SkillResult(
            reply_text=UNCERTAIN_TEXT,
            action_data=way_on,
            meta={"reply_kind": "food_entry_uncertain"},
        )
    if isinstance(exc, NutritionUnavailableError):
        logger.warning("food_entry.%s.unavailable user=%s", step, external_id)
    else:
        logger.exception("food_entry.%s.error user=%s", step, external_id)
    return SkillResult(
        reply_text=EDIT_UNAVAILABLE_TEXT,
        action_data=way_on,
        meta={"reply_kind": "food_entry_unavailable"},
    )


def _delete_entry(context: SkillContext, log_id: str) -> SkillResult:
    external_id = external_user_id_for(context.bot_user)
    try:
        deletion = asyncio.run(
            get_nutrition_client().delete_meal(external_user_id=external_id, log_id=log_id)
        )
    except NutritionAPIError as exc:
        return _entry_refusal(exc, external_id=external_id, step="delete")
    minutes = restore_minutes_left(deletion.restore_window_expires_at, now=_now_utc())
    if minutes is None:
        # Окна с провода нет — не обещаем ни фразой, ни чипом (fail-closed).
        return SkillResult(
            reply_text=DELETED_TEXT,
            claims_done=True,
            claims_done_evidence="ayla.meals.delete:2xx",
            action_type="food_entry_deleted",
            action_data={"log_id": log_id, "buttons": _after_delete_buttons()},
            meta={
                "reply_kind": "food_entry_deleted",
            },
        )
    return SkillResult(
        reply_text=DELETED_WITH_WINDOW_TEXT.format(minutes=_minutes_ru(minutes)),
        claims_done=True,
        claims_done_evidence="ayla.meals.delete:restore_window",
        action_type="food_entry_deleted",
        action_data={
            "log_id": log_id,
            "buttons": [*food_text_deleted_keyboard(log_id), *_after_delete_buttons()],
        },
        meta={
            "reply_kind": "food_entry_deleted",
        },
    )


def _after_entry_buttons() -> list[dict[str, str]]:
    """DRF-2267 (CD §72) — под записанным: «Мой дневник» (где дойдёт) и «Меню»."""
    from apps.orchestrator.next_steps import after_entry_buttons

    return after_entry_buttons()


def _after_delete_buttons() -> list[dict[str, str]]:
    """DRF-2267 (CD §72) — после удаления: записать заново и «Меню»."""
    from apps.orchestrator.next_steps import log_food_button, menu_button

    return [log_food_button(), menu_button()]


def _restore_entry(context: SkillContext, log_id: str) -> SkillResult:
    external_id = external_user_id_for(context.bot_user)
    try:
        log = asyncio.run(
            get_nutrition_client().restore_meal(external_user_id=external_id, log_id=log_id)
        )
    except NutritionAPIError as exc:
        return _entry_refusal(exc, external_id=external_id, step="restore")
    return SkillResult(
        reply_text=(
            RESTORED_WITHOUT_NUMBERS_TEXT.format(dish=log.dish_name)
            if _numbers_hidden(context)
            else f"Вернула в дневник: {log.dish_name} — {ai_kcal_phrase(ai_calories)}."
            if (ai_calories := _ai_calories_of(log)) is not None
            else RESTORED_WITHOUT_NUMBERS_TEXT.format(dish=log.dish_name)
            if log.calories is None
            else RESTORED_TEXT.format(dish=log.dish_name, kcal=int(round(log.calories)))
        ),
        claims_done=True,
        claims_done_evidence="ayla.meals.restore:log_id",
        action_type="food_entry_restored",
        action_data={
            "log_id": log_id,
            "buttons": [
                *food_entry_keyboard(log_id, fixable=_entry_fixable(log)),
                *_after_entry_buttons(),
            ],
        },
        meta={
            "reply_kind": "food_entry_restored",
        },
    )


def _on_fix_grams_answer(context: SkillContext, bucket: dict[str, Any], text: str) -> SkillResult:
    match = _GRAMS_ANSWER.match(text or "")
    grams = int(match.group(1)) if match else 0
    if not MIN_GRAMS <= grams <= MAX_GRAMS:
        return SkillResult(
            reply_text=GRAMS_UNREADABLE, meta={"reply_kind": "food_text_grams_unreadable"}
        )
    refused = _gate(context)
    if refused is not None:
        # Отказ снимает ожидание: иначе каждое число десять минут получало бы отказ.
        forget(context)
        return refused
    log_id = str(bucket.get("log_id") or "")
    external_id = external_user_id_for(context.bot_user)
    try:
        log = asyncio.run(
            get_nutrition_client().update_meal(
                external_user_id=external_id,
                log_id=log_id,
                portion_multiplier=round(grams / BASELINE_G, 3),
            )
        )
    except NutritionAPIError as exc:
        forget(context)
        return _entry_refusal(exc, external_id=external_id, step="update")
    forget(context)
    return SkillResult(
        reply_text=(
            FIXED_WITHOUT_NUMBERS_TEXT.format(dish=log.dish_name)
            if _numbers_hidden(context)
            else f"Исправила: {log.dish_name} — теперь {ai_kcal_phrase(ai_calories)}."
            if (ai_calories := _ai_calories_of(log)) is not None
            else FIXED_WITHOUT_NUMBERS_TEXT.format(dish=log.dish_name)
            if log.calories is None
            else FIXED_TEXT.format(dish=log.dish_name, kcal=int(round(log.calories)))
        ),
        claims_done=True,
        claims_done_evidence="ayla.meals.update:log_id",
        action_type="food_entry_updated",
        action_data={
            "log_id": log_id,
            "buttons": [
                *food_entry_keyboard(log_id, fixable=_entry_fixable(log)),
                *_after_entry_buttons(),
            ],
        },
        meta={
            "reply_kind": "food_entry_updated",
        },
    )
