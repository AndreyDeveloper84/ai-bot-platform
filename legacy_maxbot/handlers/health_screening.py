"""TIER-B health screening (Phase 3.2A) — lazy on-demand FSM handlers.

Запускается из 2 точек:
  - free-text trigger в ai_assistant (`detect_health_signal` hit)
  - explicit opt-in (callback из settings menu или free-text «настрой советы»)

После complete: Ayla `POST /profile/` пересчитывает нормы с override
(pregnancy/breastfeeding/eating_disorder ladder) → бот рендерит «Учла важное».

См. Design Doc v2 §4.5 + §4.6.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from asgiref.sync import sync_to_async
from maxapi import F, Router
from maxapi.context.context import MemoryContext
from maxapi.types import MessageCallback, MessageCreated

from maxbot import keyboards
from maxbot.ai_parsers import parse_allergies
from maxbot.health_overrides_render import render_overrides_applied
from maxbot.personalization import get_or_create_bot_user
from maxbot.services.ayla_user_proxy import external_user_id_for
from maxbot.services.nutrition_client import (
    NutritionAPIError,
    NutritionUnavailableError,
    get_nutrition_client,
)
from maxbot.states import NutritionAnketaStates


logger = logging.getLogger("maxbot.handlers.health_screening")
router = Router()


CONSENT_TEXT = (
    "🤖 Хочу подсказать аккуратно — но сначала "
    "пара уточнений (15 секунд), чтобы совет был безопасным."
)

PREGNANCY_TEXT = "Сейчас беременна?"
BREASTFEEDING_TEXT = "Кормишь грудью?"
DIABETES_TEXT = "Есть диабет или преддиабет?"
CHRONIC_TEXT = (
    "Хронические состояния — выбери всё, что относится. "
    "Когда закончишь, нажми «✓ Готово»."
)
ALLERGIES_TEXT = "Аллергии или непереносимость продуктов?"
ALLERGIES_FREE_TEXT_PROMPT = "Какие? Напиши через запятую."
MEDS_TEXT = "Принимаешь регулярные лекарства? (какие — не спрашиваю)"
MENOPAUSE_TEXT = "Есть проявления менопаузы или предклимакс?"


# ─── Helpers ───────────────────────────────────────────────────────────────


def _persist_health_flag(bot_user, key: str, value) -> None:
    """Sync ORM helper. Updates BotUser.health_flags JSON, saves only that field.

    Module-level (not nested) for monkeypatch in tests.
    """
    flags = dict(bot_user.health_flags or {})
    flags[key] = value
    bot_user.health_flags = flags
    bot_user.save(update_fields=["health_flags"])


def _utc_now_iso() -> str:
    return datetime.now(ZoneInfo("UTC")).isoformat()


# ─── Public entry ──────────────────────────────────────────────────────────


async def start_health_screening(
    *, bot, chat_id: int, context: MemoryContext,
) -> None:
    """Public entry — invoked from ai_assistant pre-hook OR explicit-opt-in handler."""
    await context.set_state(NutritionAnketaStates.awaiting_health_consent)
    await bot.send_message(
        chat_id=chat_id,
        text=CONSENT_TEXT,
        attachments=[keyboards.tier_b_consent_keyboard()],
    )


# ─── Screen 0 — consent ────────────────────────────────────────────────────


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_TIER_B_CONSENT_OK)
async def on_health_consent_ok(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None or callback.callback.user is None:
        return
    user_id = callback.callback.user.user_id
    full_name = callback.callback.user.full_name
    bot_user, _ = await get_or_create_bot_user(user_id, full_name)
    await sync_to_async(_persist_health_flag)(
        bot_user, "health_consent_acked_at", _utc_now_iso(),
    )
    await context.set_state(NutritionAnketaStates.awaiting_pregnancy)
    await callback.bot.send_message(
        chat_id=chat_id,
        text=PREGNANCY_TEXT,
        attachments=[keyboards.tier_b_yes_no_keyboard()],
    )


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_TIER_B_CONSENT_DECLINE)
async def on_health_consent_decline(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None or callback.callback.user is None:
        return
    user_id = callback.callback.user.user_id
    full_name = callback.callback.user.full_name
    bot_user, _ = await get_or_create_bot_user(user_id, full_name)
    await sync_to_async(_persist_health_flag)(
        bot_user, "health_consent_declined_at", _utc_now_iso(),
    )
    await context.clear()
    await callback.bot.send_message(
        chat_id=chat_id,
        text=(
            "Хорошо, поняла. Буду отвечать без персональных рекомендаций — "
            "общая инфа всегда доступна. Если передумаешь, напиши «настрой советы»."
        ),
    )


# ─── Helper: read state-targeted callback ──────────────────────────────────


async def _user_and_chat(callback: MessageCallback):
    """Common boilerplate. Returns (chat_id, bot_user) or (None, None)."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None or callback.callback.user is None:
        return None, None
    bot_user, _ = await get_or_create_bot_user(
        callback.callback.user.user_id, callback.callback.user.full_name,
    )
    return chat_id, bot_user


# ─── Screen 1 — pregnancy ──────────────────────────────────────────────────


@router.message_callback(
    NutritionAnketaStates.awaiting_pregnancy,
    F.callback.payload.in_({keyboards.PAYLOAD_TIER_B_YES, keyboards.PAYLOAD_TIER_B_NO}),
)
async def on_pregnancy_answer(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    chat_id, bot_user = await _user_and_chat(callback)
    if chat_id is None:
        return
    is_pregnant = callback.callback.payload == keyboards.PAYLOAD_TIER_B_YES
    await sync_to_async(_persist_health_flag)(bot_user, "pregnant", is_pregnant)
    await context.set_state(NutritionAnketaStates.awaiting_breastfeeding)
    await callback.bot.send_message(
        chat_id=chat_id,
        text=BREASTFEEDING_TEXT,
        attachments=[keyboards.tier_b_yes_no_skip_keyboard()],
    )


# ─── Screen 1b — breastfeeding ─────────────────────────────────────────────


@router.message_callback(
    NutritionAnketaStates.awaiting_breastfeeding,
    F.callback.payload.in_({
        keyboards.PAYLOAD_TIER_B_YES,
        keyboards.PAYLOAD_TIER_B_NO,
        keyboards.PAYLOAD_TIER_B_SKIP,
    }),
)
async def on_breastfeeding_answer(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    chat_id, bot_user = await _user_and_chat(callback)
    if chat_id is None:
        return
    payload = callback.callback.payload
    if payload == keyboards.PAYLOAD_TIER_B_SKIP:
        await sync_to_async(_persist_health_flag)(
            bot_user, "breastfeeding_skipped", True,
        )
    else:
        is_bf = payload == keyboards.PAYLOAD_TIER_B_YES
        await sync_to_async(_persist_health_flag)(bot_user, "breastfeeding", is_bf)
    await context.set_state(NutritionAnketaStates.awaiting_diabetes)
    await callback.bot.send_message(
        chat_id=chat_id,
        text=DIABETES_TEXT,
        attachments=[keyboards.tier_b_diabetes_keyboard()],
    )


_DIABETES_PAYLOAD_TO_TYPE = {
    keyboards.PAYLOAD_TIER_B_DIABETES_NO: "no",
    keyboards.PAYLOAD_TIER_B_DIABETES_T1: "t1",
    keyboards.PAYLOAD_TIER_B_DIABETES_T2: "t2",
    keyboards.PAYLOAD_TIER_B_DIABETES_PRE: "pre",
}


# ─── Screen 2 — diabetes ──────────────────────────────────────────────────


@router.message_callback(
    NutritionAnketaStates.awaiting_diabetes,
    F.callback.payload.in_(set(_DIABETES_PAYLOAD_TO_TYPE.keys())),
)
async def on_diabetes_answer(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    chat_id, bot_user = await _user_and_chat(callback)
    if chat_id is None:
        return
    diabetes_type = _DIABETES_PAYLOAD_TO_TYPE[callback.callback.payload]
    await sync_to_async(_persist_health_flag)(bot_user, "diabetes_type", diabetes_type)
    await context.set_state(NutritionAnketaStates.awaiting_chronic)
    await context.update_data(chronic_selected=[])
    await callback.bot.send_message(
        chat_id=chat_id,
        text=CHRONIC_TEXT,
        attachments=[keyboards.tier_b_chronic_keyboard(selected=set())],
    )


# ─── Screen 2b — chronic multi-select ─────────────────────────────────────


@router.message_callback(
    NutritionAnketaStates.awaiting_chronic,
    F.callback.payload.startswith("cb:tier_b:chronic:toggle:"),
)
async def on_chronic_toggle(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    """Toggle slug в state-stored selection. Edit message с новой keyboard."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return
    payload = callback.callback.payload or ""
    parts = payload.split(":")
    if len(parts) != 5:
        return
    slug = parts[4]
    data = await context.get_data() or {}
    selected = list(data.get("chronic_selected") or [])
    if slug in selected:
        selected.remove(slug)
    else:
        selected.append(slug)
    await context.update_data(chronic_selected=selected)
    # Edit existing message with rebuilt keyboard for visual feedback.
    try:
        await callback.bot.edit_message(
            chat_id=chat_id,
            message_id=callback.message.body.mid if callback.message and callback.message.body else None,
            attachments=[keyboards.tier_b_chronic_keyboard(selected=set(selected))],
        )
    except Exception as exc:  # noqa: BLE001
        # Fallback: send new message с обновлённой keyboard
        logger.warning("chronic_toggle.edit_failed err=%s", exc)
        await callback.bot.send_message(
            chat_id=chat_id,
            text=CHRONIC_TEXT,
            attachments=[keyboards.tier_b_chronic_keyboard(selected=set(selected))],
        )


@router.message_callback(
    NutritionAnketaStates.awaiting_chronic,
    F.callback.payload == keyboards.PAYLOAD_TIER_B_CHRONIC_DONE,
)
async def on_chronic_done(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    chat_id, bot_user = await _user_and_chat(callback)
    if chat_id is None:
        return
    data = await context.get_data() or {}
    selected = list(data.get("chronic_selected") or [])
    await sync_to_async(_persist_health_flag)(bot_user, "chronic", selected)
    await context.set_state(NutritionAnketaStates.awaiting_allergies)
    await callback.bot.send_message(
        chat_id=chat_id,
        text=ALLERGIES_TEXT,
        attachments=[keyboards.tier_b_allergies_choice_keyboard()],
    )


@router.message_callback(
    NutritionAnketaStates.awaiting_chronic,
    F.callback.payload == keyboards.PAYLOAD_TIER_B_CHRONIC_NONE,
)
async def on_chronic_none(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    chat_id, bot_user = await _user_and_chat(callback)
    if chat_id is None:
        return
    await sync_to_async(_persist_health_flag)(bot_user, "chronic", [])
    await context.set_state(NutritionAnketaStates.awaiting_allergies)
    await callback.bot.send_message(
        chat_id=chat_id,
        text=ALLERGIES_TEXT,
        attachments=[keyboards.tier_b_allergies_choice_keyboard()],
    )


# ─── Helper: read TIER-A profile age + gender from BotUser ─────────────────


async def _fetch_age_and_gender(bot_user) -> tuple[int | None, str | None]:
    """Read age/gender from cached health_flags or fetch from Ayla profile.

    Module-level for monkeypatching. Returns (None, None) if unavailable.
    For Phase 3.2A — read from cached `health_flags` (T01 of TIER-A populates).
    """
    flags = bot_user.health_flags or {}
    age = flags.get("age")
    gender = flags.get("gender")
    try:
        age_int = int(age) if age else None
    except (TypeError, ValueError):
        age_int = None
    return age_int, str(gender) if gender else None


# ─── Screen 3 — allergies choice ──────────────────────────────────────────


@router.message_callback(
    NutritionAnketaStates.awaiting_allergies,
    F.callback.payload.in_({
        keyboards.PAYLOAD_TIER_B_ALLERGIES_NONE,
        keyboards.PAYLOAD_TIER_B_ALLERGIES_TEXT,
        keyboards.PAYLOAD_TIER_B_ALLERGIES_VAGUE,
    }),
)
async def on_allergies_choice(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    chat_id, bot_user = await _user_and_chat(callback)
    if chat_id is None:
        return
    payload = callback.callback.payload
    if payload == keyboards.PAYLOAD_TIER_B_ALLERGIES_NONE:
        await sync_to_async(_persist_health_flag)(bot_user, "allergies", [])
        await context.set_state(NutritionAnketaStates.awaiting_meds)
        await callback.bot.send_message(
            chat_id=chat_id,
            text=MEDS_TEXT,
            attachments=[keyboards.tier_b_meds_keyboard()],
        )
    elif payload == keyboards.PAYLOAD_TIER_B_ALLERGIES_TEXT:
        await context.set_state(NutritionAnketaStates.awaiting_allergies_text)
        await callback.bot.send_message(chat_id=chat_id, text=ALLERGIES_FREE_TEXT_PROMPT)
    else:  # VAGUE
        await sync_to_async(_persist_health_flag)(bot_user, "allergies_vague", True)
        await context.set_state(NutritionAnketaStates.awaiting_meds)
        await callback.bot.send_message(
            chat_id=chat_id,
            text=MEDS_TEXT,
            attachments=[keyboards.tier_b_meds_keyboard()],
        )


@router.message_created(NutritionAnketaStates.awaiting_allergies_text)
async def on_allergies_text_input(
    event: MessageCreated, context: MemoryContext,
) -> None:
    """Free-text «лактоза, орехи» → parse_allergies → save."""
    if event.message.sender is None:
        return
    chat_id = event.message.recipient.chat_id
    text = (event.message.body.text or "").strip() if event.message.body else ""
    if not text:
        return
    bot_user, _ = await get_or_create_bot_user(
        event.message.sender.user_id, event.message.sender.full_name,
    )
    slugs = await parse_allergies(text)
    await sync_to_async(_persist_health_flag)(bot_user, "allergies", slugs)
    await context.set_state(NutritionAnketaStates.awaiting_meds)
    await event.bot.send_message(
        chat_id=chat_id,
        text=MEDS_TEXT,
        attachments=[keyboards.tier_b_meds_keyboard()],
    )


# ─── Screen 3b — meds ─────────────────────────────────────────────────────


@router.message_callback(
    NutritionAnketaStates.awaiting_meds,
    F.callback.payload.in_({
        keyboards.PAYLOAD_TIER_B_YES,
        keyboards.PAYLOAD_TIER_B_NO,
        keyboards.PAYLOAD_TIER_B_SKIP,
    }),
)
async def on_meds_answer(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    chat_id, bot_user = await _user_and_chat(callback)
    if chat_id is None:
        return
    payload = callback.callback.payload
    if payload == keyboards.PAYLOAD_TIER_B_SKIP:
        await sync_to_async(_persist_health_flag)(bot_user, "meds_skipped", True)
    else:
        await sync_to_async(_persist_health_flag)(
            bot_user, "meds", payload == keyboards.PAYLOAD_TIER_B_YES,
        )

    age, gender = await _fetch_age_and_gender(bot_user)
    if (age is not None and age >= 45) and gender == "female":
        await context.set_state(NutritionAnketaStates.awaiting_menopause)
        await callback.bot.send_message(
            chat_id=chat_id,
            text=MENOPAUSE_TEXT,
            attachments=[keyboards.tier_b_menopause_keyboard()],
        )
    else:
        # Skip menopause — go directly to complete (handler in T07)
        await _complete_tier_b(
            callback.bot, chat_id=chat_id, bot_user=bot_user, context=context,
        )


_MENOPAUSE_PAYLOAD_TO_VALUE = {
    keyboards.PAYLOAD_TIER_B_MENOPAUSE_NO: "no",
    keyboards.PAYLOAD_TIER_B_MENOPAUSE_YES: "yes",
    keyboards.PAYLOAD_TIER_B_MENOPAUSE_UNSURE: "unsure",
}


# ─── Screen 4 — menopause ─────────────────────────────────────────────────


@router.message_callback(
    NutritionAnketaStates.awaiting_menopause,
    F.callback.payload.in_(
        set(_MENOPAUSE_PAYLOAD_TO_VALUE.keys()) | {keyboards.PAYLOAD_TIER_B_SKIP}
    ),
)
async def on_menopause_answer(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    chat_id, bot_user = await _user_and_chat(callback)
    if chat_id is None:
        return
    payload = callback.callback.payload
    if payload == keyboards.PAYLOAD_TIER_B_SKIP:
        await sync_to_async(_persist_health_flag)(bot_user, "menopause_skipped", True)
    else:
        value = _MENOPAUSE_PAYLOAD_TO_VALUE[payload]
        await sync_to_async(_persist_health_flag)(bot_user, "menopause", value)
    await _complete_tier_b(
        callback.bot, chat_id=chat_id, bot_user=bot_user, context=context,
    )


# ─── Complete TIER-B — Ayla upsert + render «Учла важное» ─────────────────


_HEALTH_FLAG_KEYS_TO_AYLA = {
    "pregnant", "breastfeeding", "diabetes_type", "chronic",
    "allergies", "allergies_vague", "meds", "menopause",
}


def _build_ayla_payload(health_flags: dict) -> dict:
    """Pick relevant keys from local health_flags → Ayla upsert payload."""
    return {
        k: v for k, v in (health_flags or {}).items()
        if k in _HEALTH_FLAG_KEYS_TO_AYLA
    }


async def _complete_tier_b(
    bot, *, chat_id: int, bot_user, context: MemoryContext,
) -> None:
    """POST /profile/ → render «Учла важное» → clear state."""
    ext_id = external_user_id_for(bot_user)
    payload = _build_ayla_payload(bot_user.health_flags or {})

    try:
        client = get_nutrition_client()
        profile = await client.upsert_profile(external_user_id=ext_id, data=payload)
    except (NutritionUnavailableError, NutritionAPIError) as exc:
        logger.warning(
            "tier_b.upsert_failed user=%s err=%s",
            bot_user.max_user_id, exc,
        )
        await context.clear()
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "Сохранила. Советы появятся чуть позже — сейчас сервис "
                "питания недоступен."
            ),
        )
        return

    overrides_text = render_overrides_applied(profile)
    if overrides_text:
        text = (
            "Готово ✓ Обновила советы под тебя.\n\n"
            f"🎯 Норма: {profile.daily_kcal} ккал\n"
            f"   Б {profile.protein_g} / Ж {profile.fat_g} / У {profile.carbs_g}\n"
            f"   💧 {profile.water_ml} мл воды\n\n"
            f"{overrides_text}"
        )
    else:
        text = "Готово ✓ Учту при советах."

    await context.clear()
    await bot.send_message(chat_id=chat_id, text=text)
