"""Cross-domain insights handler (DRF-274 / B-4).

After a user logs a meal in the food scanner, the bot may surface a
cross-domain insight from Ayla (e.g. "vitamin D deficit → argan oil massage").
This handler renders the card, handles dismiss/convert/seen callbacks, and
redirects to the booking flow when the user clicks "Записаться".

The hook itself lives in `food_scanner.py::_maybe_send_cross_domain_card`,
which is best-effort — failures here MUST NOT crash the food log flow. The
callback handlers below DO log failures (those clicks are user intent).

Wire format (cb:cross:*):
- cb:cross:convert:{shown_id} — open booking flow with pre-selected category
- cb:cross:dismiss:{shown_id} — POST /dismiss/, soft-confirm to user
- cb:cross:seen:{shown_id}    — fire-and-forget telemetry

PII safety: insight_text / rationale_text are personalized and MUST NOT be
logged at INFO level. Use shown_id / rule_slug for diagnostics instead.
"""
from __future__ import annotations

import logging

from asgiref.sync import sync_to_async
from maxapi import F, Router
from maxapi.context.context import MemoryContext
from maxapi.enums.intent import Intent
from maxapi.types import CallbackButton, MessageCallback
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from maxbot.personalization import get_or_create_bot_user
from maxbot.services.ayla_user_proxy import external_user_id_for
from maxbot.services.nutrition_client import (
    CrossDomainInsight,
    NutritionAPIError,
    NutritionUnavailableError,
    get_nutrition_client,
)


logger = logging.getLogger("maxbot.cross_domain")
router = Router()


# ─── Render ────────────────────────────────────────────────────────────────


def render_cross_domain_card(insight: CrossDomainInsight) -> tuple[str, list]:
    """Render an insight card → (text, attachments).

    Disclaimer goes on a separate italic line so it's visually distinct
    (Markdown italics: `_…_`). Two-button row: convert (positive) + dismiss.
    """
    lines = [insight.insight_text]
    if insight.rationale_text:
        lines.append(insight.rationale_text)
    if insight.disclaimer_text:
        # Italic markdown for disclaimer to soften the recommendation.
        lines.append(f"_{insight.disclaimer_text}_")
    text = "\n\n".join(lines)

    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(
            text="📅 Записаться",
            payload=f"cb:cross:convert:{insight.shown_id}",
            intent=Intent.POSITIVE,
        ),
        CallbackButton(
            text="Не сейчас",
            payload=f"cb:cross:dismiss:{insight.shown_id}",
        ),
    )
    return text, [builder.as_markup()]


# ─── Helpers ───────────────────────────────────────────────────────────────


def _shown_id_from_payload(payload: str, prefix: str) -> str | None:
    if not payload or not payload.startswith(prefix):
        return None
    rest = payload[len(prefix):]
    return rest or None


@sync_to_async
def _category_for_slug(slug: str):
    """Lazy import — services_app is a Django app."""
    from services_app.models import ServiceCategory
    return (
        ServiceCategory.objects.active()
        .filter(slug=slug)
        .first()
    )


async def _send_booking_redirect(
    *,
    bot,
    chat_id: int,
    bot_user,
    service_category_slug: str,
) -> None:
    """Open booking flow with pre-selected service category.

    If the category exists, show its services list (mirrors the second
    step of `handlers/services.py::on_show_services`). Otherwise fall
    back to the top-level category list so the user can still record
    a request.
    """
    from maxbot import keyboards
    # Lazy ORM imports.
    from services_app.models import Service

    category = await _category_for_slug(service_category_slug)
    if category is None:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "Открываю запись. Выберите категорию услуги — я подскажу "
                "дальше."
            ),
            attachments=[keyboards.back_to_menu_keyboard()],
        )
        return

    services = await sync_to_async(list)(
        Service.objects.active()
        .filter(category_id=category.id)
        .order_by("name")
    )
    if not services:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"В категории «{category.name}» пока нет активных услуг. "
                "Менеджер вернётся с вариантами."
            ),
            attachments=[keyboards.back_to_menu_keyboard()],
        )
        return

    await bot.send_message(
        chat_id=chat_id,
        text=f"Выбрала для вас категорию «{category.name}». Что записать?",
        attachments=[keyboards.services_keyboard(services)],
    )


# ─── Callback handlers ─────────────────────────────────────────────────────


@router.message_callback(F.callback.payload.startswith("cb:cross:dismiss:"))
async def on_dismiss(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    """User clicked «Не сейчас» — POST /dismiss/ + soft-confirm message."""
    payload = callback.callback.payload or ""
    shown_id = _shown_id_from_payload(payload, "cb:cross:dismiss:")
    if not shown_id:
        return

    chat_id = (
        callback.message.recipient.chat_id if callback.message else None
    )
    if chat_id is None or callback.callback.user is None:
        return
    user = callback.callback.user
    bot_user, _ = await get_or_create_bot_user(user.user_id, user.full_name)

    client = get_nutrition_client()
    try:
        await client.post_cross_domain_dismiss(
            external_user_id=external_user_id_for(bot_user),
            shown_id=shown_id,
        )
    except (NutritionUnavailableError, NutritionAPIError) as exc:
        logger.warning(
            "cross_domain.dismiss.failed shown_id=%s err=%s",
            shown_id, type(exc).__name__,
        )

    await callback.bot.send_message(
        chat_id=chat_id,
        text="Поняла, не буду беспокоить. Если передумаете — напишите.",
    )


@router.message_callback(F.callback.payload.startswith("cb:cross:convert:"))
async def on_convert(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    """User clicked «Записаться» — POST /convert/ + open booking flow.

    We don't know the appointment_id at click time (booking flow hasn't
    completed yet). Pattern: redirect into booking flow with the insight's
    pre-selected `service_category_slug`. The convert ping is fire-and-
    forget telemetry tied to the click itself.
    """
    payload = callback.callback.payload or ""
    shown_id = _shown_id_from_payload(payload, "cb:cross:convert:")
    if not shown_id:
        return

    chat_id = (
        callback.message.recipient.chat_id if callback.message else None
    )
    if chat_id is None or callback.callback.user is None:
        return
    user = callback.callback.user
    bot_user, _ = await get_or_create_bot_user(user.user_id, user.full_name)

    # Look up the original insight to recover service_category_slug.
    # Stored on the assistant message that rendered the card; for the MVP we
    # call the engine again — the same shown_id is still active until
    # dismissed. If the lookup fails we still redirect to a generic flow.
    client = get_nutrition_client()
    service_category_slug = ""
    try:
        insight = await client.get_cross_domain_insights(
            external_user_id=external_user_id_for(bot_user),
        )
        if insight and insight.shown_id == shown_id:
            service_category_slug = insight.service_category_slug
    except (NutritionUnavailableError, NutritionAPIError) as exc:
        logger.warning(
            "cross_domain.convert.lookup_failed shown_id=%s err=%s",
            shown_id, type(exc).__name__,
        )

    # Fire-and-forget convert ping. We don't have an appointment_id yet —
    # send a placeholder; Ayla treats the click itself as conversion intent
    # and may stamp the appointment_id later via a separate signal.
    try:
        await client.post_cross_domain_convert(
            external_user_id=external_user_id_for(bot_user),
            shown_id=shown_id,
            appointment_id="",
        )
    except (NutritionUnavailableError, NutritionAPIError) as exc:
        logger.warning(
            "cross_domain.convert.post_failed shown_id=%s err=%s",
            shown_id, type(exc).__name__,
        )

    await _send_booking_redirect(
        bot=callback.bot,
        chat_id=chat_id,
        bot_user=bot_user,
        service_category_slug=service_category_slug,
    )


@router.message_callback(F.callback.payload.startswith("cb:cross:seen:"))
async def on_seen(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    """Fire-and-forget telemetry. Kept for completeness even if callers
    most often POST /seen/ directly after rendering the card."""
    payload = callback.callback.payload or ""
    shown_id = _shown_id_from_payload(payload, "cb:cross:seen:")
    if not shown_id:
        return

    if callback.callback.user is None:
        return
    user = callback.callback.user
    bot_user, _ = await get_or_create_bot_user(user.user_id, user.full_name)

    client = get_nutrition_client()
    try:
        await client.post_cross_domain_seen(
            external_user_id=external_user_id_for(bot_user),
            shown_id=shown_id,
        )
    except (NutritionUnavailableError, NutritionAPIError) as exc:
        logger.warning(
            "cross_domain.seen.failed shown_id=%s err=%s",
            shown_id, type(exc).__name__,
        )
