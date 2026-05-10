"""Food scanner + diary handler for the MAX bot (DRF-246, DRF-247).

Triggers:
1. User sends a photo → scan flow (DRF-246).
2. 152-FZ consent buttons (accept / decline) (DRF-246).
3. Meal-type buttons on a scan card (DRF-247) — write to diary.
4. /дневник text command (DRF-247) — show daily summary.

Errors surface as plain-text fallbacks — the bot never silently fails.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone
from maxapi import F, Router
from maxapi.context.context import MemoryContext
from maxapi.enums.attachment import AttachmentType
from maxapi.types import MessageCallback, MessageCreated

from maxbot import ai_ui, keyboards
from maxbot.evening_inline import should_trigger_evening_inline
from maxbot.menu_state import send_with_main_menu
from maxbot.nutrition_settings_helpers import set_setting
from maxbot.personalization import get_or_create_bot_user
from maxbot.services.ayla_user_proxy import external_user_id_for
from maxbot.services.nutrition_client import (
    FoodNotRecognizedError,
    NutritionAPIError,
    NutritionUnavailableError,
    get_nutrition_client,
)
from django.conf import settings as django_settings
from maxbot.states import NutritionAnketaStates


logger = logging.getLogger("maxbot.food_scanner")
router = Router()

PHOTO_DOWNLOAD_TIMEOUT_S = 8.0
MAX_PHOTO_BYTES = 10 * 1024 * 1024  # Ayla serializer caps at 10 MiB.


def _now_msk() -> datetime:
    """Current datetime в Europe/Moscow. Module-level for monkeypatch in tests."""
    return datetime.now(ZoneInfo("Europe/Moscow"))


# ─── Photo upload trigger ──────────────────────────────────────────────────


@router.message_created(F.message.body.attachments)
async def on_photo_message(event: MessageCreated, context: MemoryContext) -> None:
    """Fires only when message has at least one IMAGE attachment.

    F-filter `F.message.body.attachments` (truthy = non-empty list)
    отсекает text-only сообщения на dispatcher-уровне. Иначе maxapi
    считает это handler-ом сматчившимся успешно и НЕ прокидывает событие
    дальше — ai_assistant.on_free_text не получает шанс.

    Внутри handler'а ещё проверяем что есть именно photo (не video / file).
    """
    if event.message.sender is None:
        return  # системные

    body = event.message.body
    if body is None:
        return

    photo_url = _first_photo_url(body)
    if photo_url is None:
        return

    chat_id = event.message.recipient.chat_id

    # NUTRITION_ENABLED gate — feature flag (Part 1 hotfix). До деплоя
    # Ayla DRF-300..303 endpoints — фото не идёт в Ayla. Юзер видит
    # «Скоро будет» (тот же UX что в nutrition_entry::COMING_SOON_TEXT).
    if not getattr(django_settings, "NUTRITION_ENABLED", False):
        await event.bot.send_message(
            chat_id=chat_id,
            text=(
                "🍎 Дневник питания\n\n"
                "Скоро будет — фича в разработке. Когда подключим, "
                "посчитаю калории по фото блюда."
            ),
        )
        return

    # FSM-aware skip — если юзер сейчас в анкете, не запускаем scan
    # (Design Doc v2 §5.5). Подсказываем продолжить FSM.
    state = await context.get_state()
    if state is not None and str(state).startswith("NutritionAnketaStates"):
        await event.bot.send_message(
            chat_id=chat_id,
            text=(
                "Сейчас отвечаю на вопросы анкеты — "
                "пришли число / нажми кнопку, чтобы продолжить."
            ),
        )
        return

    sender = event.message.sender
    bot_user, _ = await get_or_create_bot_user(sender.user_id, sender.full_name)

    # Consent gate
    if bot_user.food_scanner_consent_at is None:
        text, atts = ai_ui.render_food_consent_request()
        await event.bot.send_message(chat_id=chat_id, text=text, attachments=atts)
        return

    # Download photo bytes from MAX CDN
    try:
        image_bytes = await _download_photo(photo_url)
    except _PhotoTooLargeError:
        await send_with_main_menu(
            bot=event.bot, chat_id=chat_id,
            text="Фото слишком большое. Пришлите фото поменьше (до 10 МБ).",
            bot_user=bot_user,
        )
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("food_scanner.download_failed url=%s err=%s",
                       photo_url[:60], type(exc).__name__)
        await send_with_main_menu(
            bot=event.bot, chat_id=chat_id,
            text="Не получилось загрузить фото. Попробуйте ещё раз.",
            bot_user=bot_user,
        )
        return

    external_id = external_user_id_for(bot_user)
    client = get_nutrition_client()

    # Edit-message loading pattern (Design Doc §5.1) — сразу шлём
    # «👀 Распознаю...», потом edit'им на финал. Юзер видит мгновенный
    # ответ что бот работает, не "висит".
    loading_msg = await event.bot.send_message(
        chat_id=chat_id,
        text=ai_ui.render_loading_card(),
    )
    loading_msg_id = getattr(loading_msg, "message_id", None)

    async def _replace(text: str, attachments=None) -> None:
        """Edit loading-card на финал. Если message_id потерялся
        (старая API), fallback на новый send_message."""
        if loading_msg_id is not None:
            try:
                await event.bot.edit_message(
                    message_id=loading_msg_id, text=text,
                    attachments=attachments,
                )
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("food_scanner.edit_failed err=%s", exc)
        # Fallback — новое сообщение
        await event.bot.send_message(
            chat_id=chat_id, text=text, attachments=attachments,
        )

    try:
        scan = await client.scan_photo(
            external_user_id=external_id,
            image_bytes=image_bytes,
        )
    except FoodNotRecognizedError:
        await _replace(
            "Не получилось распознать блюдо на фото. Попробуй фото получше.",
        )
        return
    except NutritionUnavailableError as exc:
        logger.warning("food_scanner.unavailable user=%s reason=%s",
                       bot_user.max_user_id, exc)
        await _replace("Сканер еды временно недоступен. Попробуй через минуту.")
        return
    except NutritionAPIError as exc:
        logger.exception("food_scanner.api_error user=%s err=%s",
                         bot_user.max_user_id, exc)
        await _replace("Что-то пошло не так со сканером. Попробуй позже.")
        return

    text, attachments = ai_ui.render_food_scan_v2(scan.raw)
    await _replace(text, attachments=attachments if attachments else None)


# ─── Consent callbacks ─────────────────────────────────────────────────────


@router.message_callback(F.callback.payload == "cb:nutrition:consent:agree")
async def on_consent_agree(callback: MessageCallback, context: MemoryContext) -> None:
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None or callback.callback.user is None:
        return
    user_id = callback.callback.user.user_id
    full_name = callback.callback.user.full_name
    bot_user, _ = await get_or_create_bot_user(user_id, full_name)

    await sync_to_async(_set_consent)(bot_user)
    await callback.bot.send_message(
        chat_id=chat_id,
        text="Спасибо! Теперь шлите фото еды — я распознаю блюдо и посчитаю КБЖУ.",
    )


@router.message_callback(F.callback.payload == "cb:nutrition:consent:decline")
async def on_consent_decline(callback: MessageCallback, context: MemoryContext) -> None:
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return
    await callback.bot.send_message(
        chat_id=chat_id,
        text="Понятно. Сканер еды доступен, когда передумаете — просто пришлите фото снова.",
    )


# ─── DRF-247: log meal callback + /дневник command ─────────────────────────


@router.message_callback(F.callback.payload.startswith("cb:nutrition:log:"))
async def on_log_meal(callback: MessageCallback, context: MemoryContext) -> None:
    """Click `[Завтрак|Обед|Ужин|Перекус]` on a scan card → write FoodLog.

    Payload: `cb:nutrition:log:{scan_id}:{meal_type}`. Idempotency-key
    derived from (scan_id, meal_type) so re-clicks during a flaky network
    do not double-log.
    """
    payload = callback.callback.payload or ""
    parts = payload.split(":")
    if len(parts) != 5:
        return
    _, _, _, scan_id, meal_type = parts
    if meal_type not in ("breakfast", "lunch", "dinner", "snack"):
        return

    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None or callback.callback.user is None:
        return
    user_id = callback.callback.user.user_id
    full_name = callback.callback.user.full_name
    bot_user, _ = await get_or_create_bot_user(user_id, full_name)

    external_id = external_user_id_for(bot_user)
    # Deterministic idempotency key — re-clicks within a session map to
    # the same UUID so FoodLogService.create returns the prior row.
    idem = str(uuid.uuid5(uuid.NAMESPACE_OID, f"{external_id}:{scan_id}:{meal_type}"))

    client = get_nutrition_client()
    try:
        log = await client.log_meal(
            external_user_id=external_id,
            scan_id=scan_id,
            meal_type=meal_type,
            idempotency_key=idem,
        )
    except FoodNotRecognizedError:
        await callback.bot.send_message(
            chat_id=chat_id,
            text="Это блюдо не удалось распознать на скане — добавь вручную через /дневник.",
        )
        return
    except NutritionUnavailableError:
        await callback.bot.send_message(
            chat_id=chat_id,
            text="Сервис временно недоступен. Попробуй через минуту.",
        )
        return
    except NutritionAPIError as exc:
        logger.exception("food_scanner.log.api_error user=%s err=%s",
                         bot_user.max_user_id, exc)
        await callback.bot.send_message(
            chat_id=chat_id,
            text="Не получилось записать. Попробуй ещё раз.",
        )
        return

    meal_label = {
        "breakfast": "завтрак", "lunch": "обед",
        "dinner": "ужин", "snack": "перекус",
    }[meal_type]
    text, attachments = ai_ui.render_food_logged_with_footer(
        meal_label=meal_label, dish_name=log.dish_name, kcal=int(log.calories),
    )
    await callback.bot.send_message(
        chat_id=chat_id, text=text, attachments=attachments,
    )

    await _maybe_send_evening_inline(
        callback.bot, chat_id=chat_id, bot_user=bot_user,
        client=client, external_id=external_id,
    )


async def _maybe_send_evening_inline(
    bot, *, chat_id: int, bot_user, client, external_id: str,
) -> None:
    """Phase 3.1 Part 2D.3 T05: post-log evening daily-report trigger.

    Best-effort: any failure (no summary, no chat_id, etc.) silently logs
    and returns — does not affect the primary log_meal acknowledgement.
    """
    try:
        now_local_msk = _now_msk()
        try:
            user_tz = ZoneInfo(bot_user.timezone or "Europe/Moscow")
        except Exception:  # noqa: BLE001
            user_tz = ZoneInfo("Europe/Moscow")
        now_local = now_local_msk.astimezone(user_tz)
        today_iso = now_local.date().isoformat()

        # Cheap pre-check using cached fields BEFORE fetching summary
        settings_dict = bot_user.nutrition_settings or {}
        if settings_dict.get("daily_report_time") == "off":
            return
        if settings_dict.get("evening_inline_shown_at") == today_iso:
            return
        if now_local.hour < 18 or now_local.hour >= 22:
            return

        # Fetch fresh summary (with AI comment) for entries count + render
        try:
            summary = await client.daily_summary(
                external_user_id=external_id, with_comment=True,
            )
        except (NutritionUnavailableError, NutritionAPIError) as exc:
            logger.warning(
                "evening_inline.summary_failed user=%s err=%s",
                bot_user.max_user_id, exc,
            )
            return

        if not should_trigger_evening_inline(
            bot_user, summary=summary,
            now_local=now_local, today_local_date=today_iso,
        ):
            return

        try:
            water_today = await client.get_water_today(external_user_id=external_id)
        except (NutritionUnavailableError, NutritionAPIError):
            water_today = None

        eating_disorder = bool(
            (bot_user.health_flags or {}).get("eating_disorder", False)
        )
        from maxbot import keyboards
        text = ai_ui.render_daily_full_report(
            summary, water_today, eating_disorder=eating_disorder,
        )
        await bot.send_message(
            chat_id=chat_id, text=text,
            attachments=[keyboards.daily_report_footer_keyboard()],
        )
        await sync_to_async(set_setting)(
            bot_user, "evening_inline_shown_at", today_iso,
        )
    except Exception:  # noqa: BLE001
        # B-19 (DRF-298): catch-all best-effort → logger.exception для
        # traceback в Sentry. Inner typed-exception блоки выше используют
        # logger.warning (NutritionUnavailable/API error без stack нужен).
        # Never break log_meal flow.
        logger.exception(
            "evening_inline.unexpected user=%s",
            getattr(bot_user, "max_user_id", "?"),
        )

    # DRF-274 (B-4): cross-domain insight surfacing — best-effort.
    # Skip if user has eating_disorder flag set OR per-user gate is closed.
    # Errors NEVER propagate up the log_meal flow.
    # B-1 review fix: health_flags is a model field on BotUser, not nested in context.
    # DRF-288 (B9): visibility gate — global CROSS_DOMAIN_ENABLED OR internal list.
    if (
        not (bot_user.health_flags or {}).get("eating_disorder")
        and _cross_domain_visible_for(bot_user)
    ):
        try:
            await _maybe_send_cross_domain_card(
                bot=callback.bot, chat_id=chat_id, bot_user=bot_user,
            )
        except Exception:  # noqa: BLE001 — best-effort, never break log flow
            logger.warning(
                "cross_domain.hook_failed_silently user=%s",
                bot_user.max_user_id,
                exc_info=True,
            )


def _cross_domain_visible_for(bot_user) -> bool:
    """Per-user gate (DRF-288 / B9). Global CROSS_DOMAIN_ENABLED overrides.

    Returns True if user should see Track E cross-domain insight cards.
    - Global flag on → everyone visible (preserves B-4 behavior).
    - Global flag off → only max_user_ids in CROSS_DOMAIN_INTERNAL_ACCOUNTS.
    - bot_user is None → False (safe default).
    """
    if getattr(settings, "CROSS_DOMAIN_ENABLED", False):
        return True
    if bot_user is None:
        return False
    internal = getattr(settings, "CROSS_DOMAIN_INTERNAL_ACCOUNTS", [])
    return bot_user.max_user_id in internal


async def _maybe_send_cross_domain_card(*, bot, chat_id: int, bot_user) -> None:
    """Fetch + render + send a cross-domain insight card. Best-effort.

    Caller MUST wrap this in try/except — see `on_log_meal`. Internally we
    also swallow API/network errors so the food log flow stays unaffected
    even if this helper is invoked elsewhere.

    Logs are PII-safe: shown_id / rule_slug only, never insight_text.
    """
    # B-1 review fix: health_flags is a real model field on BotUser
    # (services_app/models.py:1135), NOT nested in `context`. Use the field directly.
    # Eating-disorder gate must come BEFORE the per-user gate so that
    # internal-list accounts with eating_disorder=True still get nothing.
    if (bot_user.health_flags or {}).get("eating_disorder"):
        return
    # DRF-288 (B9): per-user gate — global flag on OR max_user_id in internal list.
    if not _cross_domain_visible_for(bot_user):
        return

    # Lazy import — handlers/cross_domain.py imports nutrition_client which
    # imports django.conf — top-level import would create a cycle on boot.
    from maxbot.handlers.cross_domain import render_cross_domain_card

    client = get_nutrition_client()
    try:
        insight = await client.get_cross_domain_insights(
            external_user_id=external_user_id_for(bot_user),
        )
    except (NutritionUnavailableError, NutritionAPIError):
        # Swallow — flag-gated feature, never break log flow.
        return

    if insight is None:
        return

    text, attachments = render_cross_domain_card(insight)
    await bot.send_message(chat_id=chat_id, text=text, attachments=attachments)
    logger.info(
        "cross_domain.card_sent user=%s shown_id=%s rule=%s",
        bot_user.max_user_id, insight.shown_id, insight.rule_slug,
    )

    # Fire-and-forget telemetry: mark as seen.
    try:
        await client.post_cross_domain_seen(
            external_user_id=external_user_id_for(bot_user),
            shown_id=insight.shown_id,
        )
    except (NutritionUnavailableError, NutritionAPIError):
        return


@router.message_created(
    F.message.body.text.lower().in_(
        ("/день", "/дневник", "/diary", "день", "дневник"),
    ),
)
async def on_diary_command(event: MessageCreated, context: MemoryContext) -> None:
    """`/дневник` → hybrid daily report (Part 2C: summary + water + render)."""
    if event.message.sender is None:
        return
    chat_id = event.message.recipient.chat_id
    sender = event.message.sender
    bot_user, _ = await get_or_create_bot_user(sender.user_id, sender.full_name)

    extid = external_user_id_for(bot_user)
    client = get_nutrition_client()

    try:
        summary = await client.daily_summary(
            external_user_id=extid, with_comment=True,
        )
    except NutritionUnavailableError:
        await send_with_main_menu(
            bot=event.bot, chat_id=chat_id,
            text="Дневник временно недоступен. Попробуй через минуту.",
            bot_user=bot_user,
        )
        return
    except NutritionAPIError as exc:
        logger.exception("food_scanner.summary.api_error user=%s err=%s",
                         bot_user.max_user_id, exc)
        await send_with_main_menu(
            bot=event.bot, chat_id=chat_id,
            text="Не получилось загрузить дневник.",
            bot_user=bot_user,
        )
        return

    water_today = None
    try:
        water_today = await client.get_water_today(external_user_id=extid)
    except (NutritionUnavailableError, NutritionAPIError):
        logger.info("food_scanner.diary water unavailable for user=%s",
                    bot_user.max_user_id)

    eating_disorder = bool(
        (bot_user.health_flags or {}).get("eating_disorder", False)
    )
    text = ai_ui.render_daily_full_report(
        summary, water_today, eating_disorder=eating_disorder,
    )
    await event.bot.send_message(
        chat_id=chat_id,
        text=text,
        attachments=[keyboards.daily_report_footer_keyboard()],
    )


# ─── Helpers ───────────────────────────────────────────────────────────────


class _PhotoTooLargeError(Exception):
    pass


def _first_photo_url(body) -> str | None:
    """Return the URL of the first IMAGE attachment, or None."""
    attachments = getattr(body, "attachments", None) or []
    for att in attachments:
        if getattr(att, "type", None) != AttachmentType.IMAGE:
            continue
        payload = getattr(att, "payload", None)
        url = getattr(payload, "url", None) if payload else None
        if url:
            return url
    return None


async def _download_photo(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=PHOTO_DOWNLOAD_TIMEOUT_S) as http:
        async with http.stream("GET", url) as resp:
            resp.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > MAX_PHOTO_BYTES:
                    raise _PhotoTooLargeError(f"photo > {MAX_PHOTO_BYTES} bytes")
                chunks.append(chunk)
            return b"".join(chunks)


def _set_consent(bot_user) -> None:
    bot_user.food_scanner_consent_at = timezone.now()
    bot_user.save(update_fields=["food_scanner_consent_at"])
