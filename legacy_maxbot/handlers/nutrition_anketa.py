"""TIER-A анкета — FSM handlers (Phase 3.1 Part 1).

Точка входа: `on_start_anketa` в nutrition_entry.py (после клика
PAYLOAD_NUTRITION_START_ANKETA). Дальше — chain handlers по state'ам:

    awaiting_consent → awaiting_gender → awaiting_age → awaiting_height →
    awaiting_weight → awaiting_goal → (awaiting_pace | awaiting_gain_clarify) →
    [awaiting_bmi_ladder] → complete

Каждый шаг шлёт PATCH в Ayla `POST /profile/` с complete=false; последний —
с complete=true. Idempotency-Key = uuid5(external_user_id, step_name).

См. `docs/plans/maxbot-phase3-nutrition-design.md` v2 §4 + Ayla spec §1.
"""
from __future__ import annotations

import logging
import uuid

from asgiref.sync import sync_to_async
from maxapi import F, Router
from maxapi.context.context import MemoryContext
from maxapi.types import MessageCallback, MessageCreated

from maxbot import keyboards
from maxbot.services.ayla_user_proxy import external_user_id_for
from maxbot.services.nutrition_client import (
    NutritionAPIError,
    NutritionUnavailableError,
    get_nutrition_client,
)
from maxbot.states import NutritionAnketaStates


logger = logging.getLogger("maxbot.handlers.nutrition_anketa")
router = Router()


CONSENT_TEXT = (
    "📝 Перед тем как начнём — короткий дисклеймер.\n\n"
    "Я попрошу 5 параметров (пол, возраст, рост, вес, цель), чтобы "
    "посчитать твою норму ккал и БЖУ. Эти данные хранятся в зашифрованном "
    "виде, используются только внутри сервиса (152-ФЗ).\n\n"
    "Любой шаг можно пропустить — тогда применю средние значения."
)

GENDER_TEXT = (
    "● ○ ○ ○ ○\n\n"
    "Какой у тебя пол?\n\n"
    "Это нужно для расчёта BMR (базового обмена) — у Ж и М разные "
    "коэффициенты. Можно пропустить — тогда возьму средние значения."
)


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_ANKETA_CONSENT_OK)
async def on_consent_ok(callback: MessageCallback, context: MemoryContext) -> None:
    """Согласие → переход на awaiting_gender."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    await context.set_state(NutritionAnketaStates.awaiting_gender)
    await callback.bot.send_message(
        chat_id=chat_id,
        text=GENDER_TEXT,
        attachments=[keyboards.anketa_gender_keyboard()],
    )


@router.message_callback(
    F.callback.payload == keyboards.PAYLOAD_ANKETA_CONSENT_DECLINE,
)
async def on_consent_decline(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    """Отказ от анкеты → state очищен. Юзер может вернуться позже из меню."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    await context.clear()
    await callback.bot.send_message(
        chat_id=chat_id,
        text=(
            "Поняла, без проблем. Когда соберёшься настроить — заходи в "
            "🍎 Дневник питания через главное меню.\n\n"
            "Сейчас можешь просто прислать фото блюда — посчитаю калории "
            "по средним значениям."
        ),
    )


# ─── helpers ───────────────────────────────────────────────────────────────


def _client():
    """Indirection чтобы тесты могли monkeypatch'ить."""
    return get_nutrition_client()


async def _resolve_bot_user(callback_or_event):
    """Получить BotUser по sender max_user_id с lazy-create.

    Принимает либо MessageCallback (имеет .callback.user.user_id), либо
    MessageCreated (имеет .message.sender.user_id).
    """
    from maxbot.personalization import get_or_create_bot_user
    if hasattr(callback_or_event, "callback"):
        sender_id = callback_or_event.callback.user.user_id  # MessageCallback
    else:
        sender_id = callback_or_event.message.sender.user_id  # MessageCreated
    # get_or_create_bot_user is already @sync_to_async — call directly.
    # It returns (BotUser, created) — unpack to bot_user only.
    bot_user, _ = await get_or_create_bot_user(sender_id)
    return bot_user


def _idempotency_key(external_user_id: str, step: str) -> str:
    """UUID5 — стабилен между ретраями того же шага.

    NOTE (deferred): этот ключ ПОКА не передаётся в `upsert_profile` —
    у `NutritionClient.upsert_profile()` нет kwarg `idempotency_key`.
    Helper зарезервирован под расширение клиента (Phase 3.2): добавить
    `idempotency_key: str | None = None` в client method, который
    приклеит его как HTTP header `Idempotency-Key: <uuid>` (Ayla spec §1.2).
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{external_user_id}:anketa:{step}"))


async def _upsert(
    callback_or_event,
    *,
    step: str,
    body: dict,
    advance_to,
    context: MemoryContext,
    next_text: str,
    next_keyboard,
    chat_id: int,
) -> None:
    """Общий шаг анкеты: POST в Ayla → если успех, advance state + render
    next screen. На транзиентной ошибке — show retry hint, state не меняем.
    """
    bot_user = await _resolve_bot_user(callback_or_event)
    extid = external_user_id_for(bot_user)

    try:
        await _client().upsert_profile(
            external_user_id=extid,
            data={**body, "complete": False},
        )
    except NutritionUnavailableError:
        await callback_or_event.bot.send_message(
            chat_id=chat_id,
            text=(
                "Хм, не могу сохранить шаг — попробуй ещё раз через минуту "
                "или нажми «Назад» в меню. Извини 🙏"
            ),
        )
        return
    except NutritionAPIError as exc:
        logger.warning("anketa.upsert_failed step=%s err=%s", step, exc)
        await callback_or_event.bot.send_message(
            chat_id=chat_id,
            text="Не получилось — давай попробуем заново через меню.",
        )
        await context.clear()
        return
    except ValueError as exc:
        # NutritionClient ctor бросает ValueError при пустом AYLA_BASE_URL
        # / NUTRITION_SERVICE_TOKEN. На проде этого быть не должно (env
        # задан), но если кто-то забыл настроить — не падаем
        # HandlerException, показываем мягкое сообщение и логируем для
        # ops. Прод-инцидент 2026-05-04: на staging .env не было vars
        # → юзер увидел красный handler-crash вместо «не получилось».
        logger.error("anketa.upsert_misconfigured step=%s err=%s", step, exc)
        await callback_or_event.bot.send_message(
            chat_id=chat_id,
            text="Сервис временно недоступен — попробуй чуть позже 🙏",
        )
        return

    await context.set_state(advance_to)
    await callback_or_event.bot.send_message(
        chat_id=chat_id,
        text=next_text,
        attachments=[next_keyboard()] if next_keyboard else None,
    )


# ─── gender ────────────────────────────────────────────────────────────────


AGE_TEXT = (
    "● ● ○ ○ ○\n\n"
    "Сколько тебе лет? Напиши число (например, 35) или пропусти."
)


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_ANKETA_GENDER_FEMALE)
async def on_gender_female(callback: MessageCallback, context: MemoryContext) -> None:
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return
    await _upsert(
        callback,
        step="gender",
        body={"gender": "female"},
        advance_to=NutritionAnketaStates.awaiting_age,
        context=context,
        next_text=AGE_TEXT,
        next_keyboard=keyboards.anketa_skip_keyboard,
        chat_id=chat_id,
    )


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_ANKETA_GENDER_MALE)
async def on_gender_male(callback: MessageCallback, context: MemoryContext) -> None:
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return
    await _upsert(
        callback,
        step="gender",
        body={"gender": "male"},
        advance_to=NutritionAnketaStates.awaiting_age,
        context=context,
        next_text=AGE_TEXT,
        next_keyboard=keyboards.anketa_skip_keyboard,
        chat_id=chat_id,
    )


# ─── age (text-input) ──────────────────────────────────────────────────────


HEIGHT_TEXT = (
    "● ● ● ○ ○\n\n"
    "Какой у тебя рост в сантиметрах? Напиши число (например, 165) "
    "или пропусти."
)


async def _treat_text_step_as_skip(
    msg, ctx, chat_id, *, field, advance_to, next_text, next_kb,
) -> None:
    """Helper: free-text парсер вернул REFUSED → шлём как skip."""
    await _upsert(
        msg,
        step=field,
        body={"_skipped_fields": [field]},
        advance_to=advance_to,
        context=ctx,
        next_text=next_text,
        next_keyboard=next_kb,
        chat_id=chat_id,
    )


@router.message_created(NutritionAnketaStates.awaiting_age)
async def on_age_text(event: MessageCreated, context: MemoryContext) -> None:
    """Юзер пишет возраст — пытаемся parse_age."""
    from maxbot.ai_parsers import parse_age, REFUSED

    text = (event.message.body.text or "").strip()
    chat_id = event.message.recipient.chat_id

    parsed = await parse_age(text)

    if parsed == REFUSED:
        await _treat_text_step_as_skip(
            event, context, chat_id,
            field="age",
            advance_to=NutritionAnketaStates.awaiting_height,
            next_text=HEIGHT_TEXT,
            next_kb=keyboards.anketa_skip_keyboard,
        )
        return

    if parsed is None:
        await event.bot.send_message(
            chat_id=chat_id,
            text=(
                "Не поняла возраст — напиши число от 16 до 99 (например, 35) "
                "или нажми «⏭ Пропустить»."
            ),
            attachments=[keyboards.anketa_skip_keyboard()],
        )
        return

    await _upsert(
        event,
        step="age",
        body={"age": parsed},
        advance_to=NutritionAnketaStates.awaiting_height,
        context=context,
        next_text=HEIGHT_TEXT,
        next_keyboard=keyboards.anketa_skip_keyboard,
        chat_id=chat_id,
    )


# ─── height ────────────────────────────────────────────────────────────────


WEIGHT_TEXT = (
    "● ● ● ● ○\n\n"
    "Сколько весишь в кг? Можно точно (70) или диапазоном (65-75). "
    "Или пропусти."
)


@router.message_created(NutritionAnketaStates.awaiting_height)
async def on_height_text(event: MessageCreated, context: MemoryContext) -> None:
    from maxbot.ai_parsers import parse_height, REFUSED

    text = (event.message.body.text or "").strip()
    chat_id = event.message.recipient.chat_id

    parsed = await parse_height(text)

    if parsed == REFUSED:
        await _treat_text_step_as_skip(
            event, context, chat_id,
            field="height",
            advance_to=NutritionAnketaStates.awaiting_weight,
            next_text=WEIGHT_TEXT,
            next_kb=keyboards.anketa_skip_keyboard,
        )
        return

    if parsed is None:
        await event.bot.send_message(
            chat_id=chat_id,
            text="Не поняла рост — напиши число в см (например, 165) или пропусти.",
            attachments=[keyboards.anketa_skip_keyboard()],
        )
        return

    await _upsert(
        event,
        step="height",
        body={"height_cm": parsed},
        advance_to=NutritionAnketaStates.awaiting_weight,
        context=context,
        next_text=WEIGHT_TEXT,
        next_keyboard=keyboards.anketa_skip_keyboard,
        chat_id=chat_id,
    )


# ─── weight ────────────────────────────────────────────────────────────────


GOAL_TEXT = (
    "● ● ● ● ●\n\n"
    "Какая цель?"
)


@router.message_created(NutritionAnketaStates.awaiting_weight)
async def on_weight_text(event: MessageCreated, context: MemoryContext) -> None:
    """parse_weight возвращает dict {value: int|None, range: tuple|None, exact: bool}
    или 'REFUSED' или None."""
    from maxbot.ai_parsers import parse_weight, REFUSED

    text = (event.message.body.text or "").strip()
    chat_id = event.message.recipient.chat_id

    parsed = await parse_weight(text)

    if parsed == REFUSED:
        await _treat_text_step_as_skip(
            event, context, chat_id,
            field="weight",
            advance_to=NutritionAnketaStates.awaiting_goal,
            next_text=GOAL_TEXT,
            next_kb=keyboards.anketa_goal_keyboard,
        )
        return

    if parsed is None:
        await event.bot.send_message(
            chat_id=chat_id,
            text="Не поняла вес — напиши число (70) или диапазон (65-75) или пропусти.",
            attachments=[keyboards.anketa_skip_keyboard()],
        )
        return

    body: dict = {}
    if parsed.get("exact"):
        body["weight_kg"] = parsed["value"]
    else:
        rng = parsed.get("range")
        if rng is not None:
            body["weight_range"] = f"{rng[0]}-{rng[1]}"
        else:
            # approx без диапазона — сохраняем как weight_kg
            body["weight_kg"] = parsed["value"]

    await _upsert(
        event,
        step="weight",
        body=body,
        advance_to=NutritionAnketaStates.awaiting_goal,
        context=context,
        next_text=GOAL_TEXT,
        next_keyboard=keyboards.anketa_goal_keyboard,
        chat_id=chat_id,
    )


# ─── goal step ─────────────────────────────────────────────────────────────


PACE_TEXT = (
    "Темп похудения — какой выбираешь?\n\n"
    "🐢 Спокойный (-10% к норме) — комфортно, медленно.\n"
    "⚖️ Средний (-15%) — баланс между скоростью и комфортом."
)

GAIN_CLARIFY_TEXT = (
    "Что важнее — набрать массу или подтянуть фигуру?"
)

BMI_LADDER_TEXT = (
    "У тебя сейчас вес ниже нормы (BMI < 18.5). Дефицит может быть "
    "опасен — давай решим вместе:"
)


async def _fetch_profile_for_bmi(bot_user) -> tuple[int, int] | None:
    """Получить (weight_kg, height_cm) из текущего Ayla профиля для BMI check.

    Возвращает None если хоть одно поле отсутствует — тогда ladder не
    триггерим (нет данных для расчёта).
    """
    extid = external_user_id_for(bot_user)
    profile = await _client().get_profile(external_user_id=extid)
    if profile is None:
        return None
    if profile.weight_kg <= 0 or profile.height_cm <= 0:
        return None
    return (profile.weight_kg, profile.height_cm)


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_ANKETA_GOAL_MAINTAIN)
async def on_goal_maintain(callback: MessageCallback, context: MemoryContext) -> None:
    """maintain — finalize сразу, нет pace/gain_clarify/BMI ladder."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return
    await _finalize_anketa(callback, context, chat_id, goal="maintain")


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_ANKETA_GOAL_LOSE)
async def on_goal_lose(callback: MessageCallback, context: MemoryContext) -> None:
    """lose — проверяем BMI: если <18.5 → ladder, иначе → pace."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    bot_user = await _resolve_bot_user(callback)
    profile_data = await _fetch_profile_for_bmi(bot_user)

    if profile_data is not None:
        from maxbot.nutrition_calc import calc_bmi
        weight_kg, height_cm = profile_data
        try:
            bmi = calc_bmi(weight_kg=weight_kg, height_cm=height_cm)
        except ValueError as exc:
            # _fetch_profile_for_bmi уже валидирует weight/height > 0,
            # поэтому сюда дойдём только при race-condition (Ayla side
            # rounded value до 0). Не падаем — логируем и не триггерим
            # ladder (assume normal BMI).
            logger.warning(
                "anketa.goal_lose.calc_bmi_value_error weight_kg=%r height_cm=%r err=%r",
                weight_kg, height_cm, exc,
            )
            bmi = 25.0  # fallback nominal — не триггерит ladder
        if bmi < 18.5:
            await context.set_state(NutritionAnketaStates.awaiting_bmi_ladder)
            await callback.bot.send_message(
                chat_id=chat_id,
                text=BMI_LADDER_TEXT,
                attachments=[keyboards.anketa_bmi_ladder_keyboard()],
            )
            return

    # BMI normal или нет данных — переход на pace
    await context.set_state(NutritionAnketaStates.awaiting_pace)
    await callback.bot.send_message(
        chat_id=chat_id,
        text=PACE_TEXT,
        attachments=[keyboards.anketa_pace_keyboard()],
    )


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_ANKETA_GOAL_GAIN)
async def on_goal_gain(callback: MessageCallback, context: MemoryContext) -> None:
    """gain — уточняем mass vs tone.

    Намеренно НЕ персистим goal=gain здесь — финальный выбор (gain или tone)
    делается в gain_clarify шаге (Task 10) и тогда же шлём complete=True
    upsert. Если юзер отключится на awaiting_gain_clarify — потеряет
    только choice цели, прошлые шаги (gender/age/height/weight) уже
    сохранены через свои _upsert вызовы.
    """
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return
    await context.set_state(NutritionAnketaStates.awaiting_gain_clarify)
    await callback.bot.send_message(
        chat_id=chat_id,
        text=GAIN_CLARIFY_TEXT,
        attachments=[keyboards.anketa_gain_clarify_keyboard()],
    )


# ─── pace handlers ─────────────────────────────────────────────────────────


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_ANKETA_PACE_GENTLE)
async def on_pace_gentle(callback: MessageCallback, context: MemoryContext) -> None:
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return
    await _finalize_anketa(callback, context, chat_id, goal="lose", pace="gentle")


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_ANKETA_PACE_MODERATE)
async def on_pace_moderate(callback: MessageCallback, context: MemoryContext) -> None:
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return
    await _finalize_anketa(callback, context, chat_id, goal="lose", pace="moderate")


# ─── gain clarify ──────────────────────────────────────────────────────────


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_ANKETA_GAIN_MASS)
async def on_gain_mass(callback: MessageCallback, context: MemoryContext) -> None:
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return
    await _finalize_anketa(callback, context, chat_id, goal="gain")


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_ANKETA_GAIN_TONE)
async def on_gain_tone(callback: MessageCallback, context: MemoryContext) -> None:
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return
    await _finalize_anketa(callback, context, chat_id, goal="tone")


# ─── finalize anketa (full impl, Task 10) ──────────────────────────────────


async def _finalize_anketa(
    callback_or_event,
    context: MemoryContext,
    chat_id: int,
    *,
    goal: str,
    pace: str | None = None,
) -> None:
    """Финальный POST с complete=true → render финальный экран.

    Mark BotUser.nutrition_onboarded_at, чтобы entry-screen в следующий
    раз сразу вёл в дневник, а не предлагал анкету заново.
    """
    bot_user = await _resolve_bot_user(callback_or_event)
    extid = external_user_id_for(bot_user)

    body: dict = {"goal": goal, "complete": True}
    if pace is not None:
        body["pace"] = pace

    try:
        profile = await _client().upsert_profile(
            external_user_id=extid,
            data=body,
        )
    except (NutritionUnavailableError, NutritionAPIError):
        # Финальный шаг — оба класса ошибок трактуем одинаково: fatal,
        # без recovery (вернуться в анкету посередине нельзя — все шаги
        # уже отправлены, complete=True не прошёл). Клиент должен
        # начать заново через меню. Это намеренная divergence от _upsert,
        # где на Unavailable сохраняем state для retry — здесь некуда
        # ретраить (state уже awaiting_pace/gain_clarify, его нельзя
        # «дозавершить» новым кликом по той же кнопке).
        await callback_or_event.bot.send_message(
            chat_id=chat_id,
            text="Не получилось сохранить — попробуй открыть дневник заново.",
        )
        await context.clear()
        return

    await _mark_onboarded(bot_user)

    await context.set_state(NutritionAnketaStates.complete)
    text = _format_complete_text(profile)
    await callback_or_event.bot.send_message(
        chat_id=chat_id,
        text=text,
        attachments=[keyboards.anketa_complete_keyboard()],
    )


def _format_complete_text(profile) -> str:
    """Финальный экран TIER-A с опциональным блоком «Учла важное».

    Блок рендерится только если Ayla вернула overrides_applied (см.
    `maxbot-phase3-ayla-spec.md` §1.2). В TIER-A overrides могут быть
    только bmr_floor (BMI<18.5+lose без override → ladder, но если юзер
    нажал [Всё равно худеть] и Ayla подняла pace до gentle = bmr_floor) —
    pregnancy/breastfeeding собирается в TIER-B, не здесь.
    """
    water_ml = profile.water_ml or 0
    base = (
        "Готово ✓\n\n"
        f"🎯 Норма: {profile.daily_kcal} ккал\n"
        f"   Б {profile.protein_g} / Ж {profile.fat_g} / У {profile.carbs_g}\n"
        f"   💧 {water_ml} мл воды"
    )

    overrides = (profile.raw or {}).get("overrides_applied") or []
    if not overrides:
        return base

    lines = ["", "Учла важное:"]
    for ov in overrides:
        reason = ov.get("reason", "")
        if reason == "pregnancy":
            lines.append("• Беременность → дефицит небезопасен, цель «держать вес»")
        elif reason == "breastfeeding":
            lines.append("• Грудное вскармливание → +400 ккал, +25 г белка")
        elif reason == "eating_disorder":
            lines.append("• Учитываю особенности — без цифр калорий в советах")
        elif reason == "bmr_floor":
            lines.append(
                "• Подняла норму — она была ниже того, что нужно "
                "организму чтобы дышать и думать"
            )
        elif reason == "low_bmi":
            lines.append("• BMI ниже нормы — рекомендую обсудить с врачом")
        # неизвестные reasons silent skip

    return base + "\n" + "\n".join(lines)


async def _mark_onboarded(bot_user) -> None:
    """Записать nutrition_onboarded_at = now на BotUser."""
    from django.utils import timezone

    @sync_to_async
    def _save():
        bot_user.nutrition_onboarded_at = timezone.now()
        bot_user.save(update_fields=["nutrition_onboarded_at"])

    await _save()


# ─── BMI ladder ────────────────────────────────────────────────────────────


DOCTOR_REFERRAL_TEXT = (
    "Низкий BMI часто связан с гормонами или дефицитами — лучше "
    "разобраться с врачом, чем гадать.\n\n"
    "Запишись к терапевту в поликлинике или эндокринологу — они "
    "проверят анализы и подскажут план. Я буду здесь, когда вернёшься."
)


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_ANKETA_BMI_DOCTOR)
async def on_bmi_doctor(callback: MessageCallback, context: MemoryContext) -> None:
    """[Хочу к врачу] — НЕ кросс-промо в салон (Design Doc §4.4 honest doctor referral)."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return
    await context.clear()
    await callback.bot.send_message(
        chat_id=chat_id,
        text=DOCTOR_REFERRAL_TEXT,
    )


@router.message_callback(
    F.callback.payload == keyboards.PAYLOAD_ANKETA_BMI_SWITCH_MAINTAIN,
)
async def on_bmi_switch_maintain(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    """[Поменять на «держать»] — finalize как maintain."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return
    await _finalize_anketa(callback, context, chat_id, goal="maintain")


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_ANKETA_BMI_OVERRIDE)
async def on_bmi_override(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    """[Всё равно худеть] — overrides помечаем флагом, advance to pace."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    bot_user = await _resolve_bot_user(callback)
    extid = external_user_id_for(bot_user)

    try:
        await _client().upsert_profile(
            external_user_id=extid,
            data={
                "health_flags": {"bmi_warning_overridden": True},
                "complete": False,
            },
        )
    except (NutritionUnavailableError, NutritionAPIError):
        await callback.bot.send_message(
            chat_id=chat_id,
            text="Не получилось сохранить — попробуй ещё раз.",
        )
        return

    await context.set_state(NutritionAnketaStates.awaiting_pace)
    await callback.bot.send_message(
        chat_id=chat_id,
        text=PACE_TEXT,
        attachments=[keyboards.anketa_pace_keyboard()],
    )


# ─── first meal CTA ────────────────────────────────────────────────────────


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_NUTRITION_FIRST_MEAL)
async def on_first_meal(callback: MessageCallback, context: MemoryContext) -> None:
    """[📸 Сфоткать первый приём] — exit FSM, hint про фото.

    Сам food scanner работает через handlers/food_scanner.py — нам тут
    нужно только закрыть FSM (чтобы юзер мог свободно слать фото) и
    дать инструкцию.
    """
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    await context.clear()
    await callback.bot.send_message(
        chat_id=chat_id,
        text=(
            "📸 Пришли фото блюда — распознаю и посчитаю калории.\n\n"
            "Можешь добавить подпись («половина порции», «у мамы в гостях») — "
            "учту в расчёте."
        ),
    )


# ─── universal Skip handler — диспетчер по текущему state ──────────────────


_SKIP_FIELD_BY_STATE = {
    str(NutritionAnketaStates.awaiting_gender): (
        "gender",
        NutritionAnketaStates.awaiting_age,
        AGE_TEXT,
        keyboards.anketa_skip_keyboard,
    ),
    str(NutritionAnketaStates.awaiting_age): (
        "age",
        NutritionAnketaStates.awaiting_height,
        HEIGHT_TEXT,
        keyboards.anketa_skip_keyboard,
    ),
    str(NutritionAnketaStates.awaiting_height): (
        "height",
        NutritionAnketaStates.awaiting_weight,
        WEIGHT_TEXT,
        keyboards.anketa_skip_keyboard,
    ),
    str(NutritionAnketaStates.awaiting_weight): (
        "weight",
        NutritionAnketaStates.awaiting_goal,
        GOAL_TEXT,
        keyboards.anketa_goal_keyboard,
    ),
}


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_ANKETA_SKIP)
async def on_skip(callback: MessageCallback, context: MemoryContext) -> None:
    """Универсальный Skip: маппит current state → field name + next state."""
    state = await context.get_state()
    if str(state) not in _SKIP_FIELD_BY_STATE:
        # Skip-кнопка не должна появляться вне состояний из мапа.
        # Если попали — это либо UI-баг, либо забыли добавить state в
        # _SKIP_FIELD_BY_STATE при добавлении нового шага.
        logger.warning(
            "anketa.skip_unknown_state state=%r — _SKIP_FIELD_BY_STATE not updated?",
            state,
        )
        return
    field, advance_to, next_text, next_kb = _SKIP_FIELD_BY_STATE[str(state)]

    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    await _upsert(
        callback,
        step=field,
        body={"_skipped_fields": [field]},
        advance_to=advance_to,
        context=context,
        next_text=next_text,
        next_keyboard=next_kb,
        chat_id=chat_id,
    )
