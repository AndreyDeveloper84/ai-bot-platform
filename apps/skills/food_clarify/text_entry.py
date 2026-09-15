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
from apps.orchestrator.ui.keyboards import (
    ENTRY_CALLBACK_RE,
    ENTRY_ID_RE,
    food_text_deleted_keyboard,
    food_text_estimate_keyboard,
    food_text_logged_keyboard,
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
MEAL_TYPE_UNNAMED = "other"

CB_LOG = "cb:food:text_log"
CB_GRAMS = "cb:food:text_grams"
CB_REJECT = "cb:food:text_reject"
TEXT_CALLBACKS = frozenset({CB_LOG, CB_GRAMS, CB_REJECT})

#: DRF-1838 — тапы под СОХРАНЁННОЙ записью. ``id`` записи в payload:
#: запись переживает десятиминутное состояние разговора.
_ENTRY_CALLBACK = ENTRY_CALLBACK_RE
#: Окно восстановления каталога (``food_log_edit_service.RESTORE_WINDOW_MINUTES``).
RESTORE_WINDOW_MINUTES = 15

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
STALE_TEXT = "Эта оценка уже не действует — напиши, что было, ещё раз, и я посчитаю заново."
UNAVAILABLE_TEXT = "Дневник сейчас не отвечает — ничего не записала. Попробуй через минуту."
CONSENT_TEXT = (
    "Чтобы вести дневник, мне нужно согласие на обработку личных данных — "
    "без него я ничего не записываю."
)
NUTRITION_OFF_TEXT = "Дневник еды пока недоступен — функция готовится."
FIX_GRAMS_PROMPT = "Сколько граммов было на самом деле? Напиши число — пересчитаю запись."
FIXED_TEXT = "Исправила: {dish} — теперь {kcal} ккал."
DELETED_TEXT = f"Убрала запись из дневника. Вернуть можно в течение {RESTORE_WINDOW_MINUTES} минут."
RESTORED_TEXT = "Вернула в дневник: {dish} — {kcal} ккал."
RESTORE_EXPIRED_TEXT = (
    f"Уже не вернуть: прошло больше {RESTORE_WINDOW_MINUTES} минут, запись удалена окончательно."
)
ENTRY_GONE_TEXT = "Этой записи уже нет в дневнике."
ENTRY_WATER_TEXT = "Эту запись ведёт учёт воды — её убирает отмена стакана."
EDIT_UNAVAILABLE_TEXT = "Дневник сейчас не отвечает — ничего не изменила. Попробуй через минуту."
UNCERTAIN_TEXT = (
    "Не знаю, дошло ли: дневник не ответил вовремя. Загляни в дневник, прежде чем повторять."
)
OTHER_QUESTION_TEXT = "Сначала закончим вопрос, который уже открыт, — потом исправлю граммы."

# ─── разбор фразы ─────────────────────────────────────────────────────────

_GRAMS_IN_TEXT = re.compile(
    r"(\d{1,4}(?:[.,]\d+)?)\s*(?:г|гр|грамм[а-я]*|g)(?![а-яёa-z])", re.IGNORECASE
)
_GRAMS_ANSWER = re.compile(r"^\s*(\d{1,4})\s*(?:г|гр|грамм[а-я]*|g)?\s*\.?\s*$", re.IGNORECASE)
_FILLER = re.compile(
    r"^(?:(?:я|сегодня|вчера|утром|днём|днем|вечером|на\s+(?:завтрак|обед|ужин|перекус)"
    r"|съел|съела|поел|поела|ел|ела|скушал|скушала|был|была|были|было)\s+)+",
    re.IGNORECASE,
)
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
    """
    raw = (text or "").strip()
    if not raw or raw.startswith("cb:"):
        return None
    grams: float | None = None
    match = _GRAMS_IN_TEXT.search(raw)
    if match:
        grams = float(match.group(1).replace(",", "."))
        if not MIN_GRAMS <= grams <= MAX_GRAMS:
            return None
        raw = f"{raw[: match.start()]} {raw[match.end() :]}"
    cleaned = _WS.sub(" ", _NOISE.sub(" ", raw)).strip().lower()
    cleaned = _FILLER.sub("", cleaned + " ").strip()
    if not cleaned or len(cleaned) > 60:
        return None
    return ParsedFood(dish=cleaned, grams=grams)


# ─── состояние ────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        return parse_food_text(text) is not None
    return False


def remember_source(context: SkillContext, text: str) -> None:
    """Фраза до тапа «📔 В дневник»: тап несёт только payload, фраза — здесь."""
    _write(context.conversation, {"source": text, "at": _now_iso()})


def forget(context: SkillContext) -> None:
    _write(context.conversation, None)


# ─── гейты ────────────────────────────────────────────────────────────────


def _consent_open(bot_user: Any) -> bool:
    from apps.orchestrator.personal_surface import personal_records_consent_open

    return personal_records_consent_open(bot_user)


def _gate(context: SkillContext) -> SkillResult | None:
    from django.conf import settings

    if not getattr(settings, "NUTRITION_ENABLED", False):
        return SkillResult(
            reply_text=NUTRITION_OFF_TEXT, meta={"reply_kind": "food_text_nutrition_off"}
        )
    if not _consent_open(context.bot_user):
        return SkillResult(
            reply_text=CONSENT_TEXT, meta={"reply_kind": "food_text_consent_required"}
        )
    return None


# ─── шаги ─────────────────────────────────────────────────────────────────


def on_diary_tap(context: SkillContext) -> SkillResult:
    """«📔 В дневник» под «Это про еду?»: оценить исходную фразу — или спросить её."""
    bucket = _bucket(context.conversation)
    source = bucket.get("source") if bucket else None
    parsed = parse_food_text(source) if isinstance(source, str) else None
    if parsed is None:
        _write(context.conversation, {"expect_food": True, "at": _now_iso()})
        return SkillResult(reply_text=ASK_WHAT_TEXT, meta={"reply_kind": "food_text_ask"})
    return show_estimate(context, parsed.dish, parsed.grams, corrected=False)


def on_text(context: SkillContext, text: str) -> SkillResult:
    """Реплика, которую :func:`claims_text` признал ответом."""
    bucket = _bucket(context.conversation) or {}
    if bucket.get("awaiting_fix_grams"):
        return _on_fix_grams_answer(context, bucket, text)
    if bucket.get("awaiting_grams"):
        return _on_grams_answer(context, bucket, text)
    parsed = parse_food_text(text)
    if parsed is None:
        return SkillResult(reply_text=ASK_WHAT_TEXT, meta={"reply_kind": "food_text_ask"})
    return show_estimate(context, parsed.dish, parsed.grams, corrected=False)


def on_callback(context: SkillContext, text: str) -> SkillResult:
    if text == CB_REJECT:
        forget(context)
        return SkillResult(reply_text=REJECTED_TEXT, meta={"reply_kind": "food_text_rejected"})
    bucket = _bucket(context.conversation)
    if not bucket or not bucket.get("dish") or not bucket.get("token"):
        forget(context)
        return SkillResult(reply_text=STALE_TEXT, meta={"reply_kind": "food_text_stale"})
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
    return SkillResult(
        reply_text=render_estimate_card(estimate),
        action_type="food_text_estimate_card",
        action_data={
            "buttons": food_text_estimate_keyboard(),
            "dish": estimate.matched_dish,
            "portion_g": estimate.portion_g,
            "portion_estimated": estimate.portion_estimated,
        },
        meta={"reply_kind": "food_text_estimate_card"},
    )


def render_estimate_card(estimate: Any) -> str:
    """«Я распознала так» — каждое предположение названо предположением (§109 шаг 4)."""
    grams = int(round(estimate.portion_g))
    lines = [f"Я распознала так: {estimate.matched_dish}."]
    if estimate.portion_estimated:
        lines.append(f"Порция — примерно {grams} г, это оценка: граммов в сообщении не было.")
    else:
        lines.append(f"Порция — {grams} г, по твоим словам.")
    macros = [f"Примерно {int(round(estimate.kcal))} ккал"]
    for label, value in (("Б", estimate.protein_g), ("Ж", estimate.fat_g), ("У", estimate.carbs_g)):
        if value is not None:
            macros.append(f"{label} {int(round(value))}")
    lines.append(" · ".join(macros) + " — оценка по справочнику блюд.")
    lines.append("Записать в дневник?")
    return "\n".join(lines)


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
    if log.log_id and ENTRY_ID_RE.match(log.log_id):
        # DRF-1838 — §109 шаг 7: сохранённую запись можно исправить или удалить.
        action_data["buttons"] = food_text_logged_keyboard(log.log_id)
    return SkillResult(
        reply_text=f"Записала в дневник: {log.dish_name} — {int(round(log.calories))} ккал.",
        action_type="food_logged",
        action_data=action_data,
        meta={"reply_kind": "food_text_logged"},
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
    if isinstance(exc, MealRestoreExpiredError):
        return SkillResult(
            reply_text=RESTORE_EXPIRED_TEXT, meta={"reply_kind": "food_entry_restore_expired"}
        )
    if isinstance(exc, MealNotFoundError):
        return SkillResult(reply_text=ENTRY_GONE_TEXT, meta={"reply_kind": "food_entry_gone"})
    if isinstance(exc, MealEditConflictError):
        return SkillResult(reply_text=ENTRY_WATER_TEXT, meta={"reply_kind": "food_entry_water"})
    if isinstance(exc, NutritionUncertainOutcomeError):
        logger.warning("food_entry.%s.uncertain user=%s", step, external_id)
        return SkillResult(reply_text=UNCERTAIN_TEXT, meta={"reply_kind": "food_entry_uncertain"})
    if isinstance(exc, NutritionUnavailableError):
        logger.warning("food_entry.%s.unavailable user=%s", step, external_id)
    else:
        logger.exception("food_entry.%s.error user=%s", step, external_id)
    return SkillResult(
        reply_text=EDIT_UNAVAILABLE_TEXT, meta={"reply_kind": "food_entry_unavailable"}
    )


def _delete_entry(context: SkillContext, log_id: str) -> SkillResult:
    external_id = external_user_id_for(context.bot_user)
    try:
        asyncio.run(get_nutrition_client().delete_meal(external_user_id=external_id, log_id=log_id))
    except NutritionAPIError as exc:
        return _entry_refusal(exc, external_id=external_id, step="delete")
    return SkillResult(
        reply_text=DELETED_TEXT,
        action_type="food_entry_deleted",
        action_data={"log_id": log_id, "buttons": food_text_deleted_keyboard(log_id)},
        meta={"reply_kind": "food_entry_deleted"},
    )


def _restore_entry(context: SkillContext, log_id: str) -> SkillResult:
    external_id = external_user_id_for(context.bot_user)
    try:
        log = asyncio.run(
            get_nutrition_client().restore_meal(external_user_id=external_id, log_id=log_id)
        )
    except NutritionAPIError as exc:
        return _entry_refusal(exc, external_id=external_id, step="restore")
    return SkillResult(
        reply_text=RESTORED_TEXT.format(dish=log.dish_name, kcal=int(round(log.calories))),
        action_type="food_entry_restored",
        action_data={"log_id": log_id, "buttons": food_text_logged_keyboard(log_id)},
        meta={"reply_kind": "food_entry_restored"},
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
        reply_text=FIXED_TEXT.format(dish=log.dish_name, kcal=int(round(log.calories))),
        action_type="food_entry_updated",
        action_data={"log_id": log_id, "buttons": food_text_logged_keyboard(log_id)},
        meta={"reply_kind": "food_entry_updated"},
    )
