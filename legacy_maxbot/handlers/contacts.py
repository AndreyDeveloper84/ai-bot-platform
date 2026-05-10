"""Handler «Контакты» — формат адреса/телефона/часов из SiteSettings.

Кнопки:
- Скопировать телефон (ClipboardButton — встроенная кнопка MAX)
- Открыть на карте (LinkButton, только если yandex_maps_link или google_maps_link)
- Назад в меню

Note: LinkButton требует http/https URL — tel: не поддерживается, поэтому для
телефона используем ClipboardButton (копирует в буфер; пользователь вставит в
дайлер).
"""
from __future__ import annotations

from asgiref.sync import sync_to_async
from maxapi import F, Router
from maxapi.context.context import MemoryContext
from maxapi.types import CallbackButton, ClipboardButton, LinkButton, MessageCallback
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from maxbot import keyboards
from services_app.models import SiteSettings


router = Router()


@sync_to_async
def _read_site_settings() -> SiteSettings | None:
    return SiteSettings.objects.first()


def _format_text(s: SiteSettings | None) -> str:
    if s is None:
        return (
            "Контакты:\n\n"
            "📍 г. Пенза, ул. Пушкина, 45\n"
            "🕐 9:00 — 21:00, без выходных\n"
            "🌐 https://formulatela58.ru"
        )
    lines = ["Контакты:\n"]
    if s.address:
        lines.append(f"📍 {s.address}")
    if s.contact_phone:
        lines.append(f"☎️ {s.contact_phone}")
    if s.working_hours:
        lines.append(f"🕐 {s.working_hours}")
    lines.append("🌐 https://formulatela58.ru")
    return "\n".join(lines)


def _build_keyboard(s: SiteSettings | None):
    builder = InlineKeyboardBuilder()
    # Skype/copy phone — ClipboardButton (LinkButton не принимает tel:)
    if s and s.contact_phone:
        builder.row(
            ClipboardButton(text="📋 Скопировать телефон", payload=s.contact_phone),
        )
    # Map: yandex предпочтительнее (РФ), fallback на google
    map_url = (s.yandex_maps_link or s.google_maps_link) if s else None
    if map_url:
        builder.row(LinkButton(text="🗺 Открыть на карте", url=map_url))
    builder.row(
        CallbackButton(text="← Назад в меню", payload=keyboards.PAYLOAD_BACK),
    )
    return builder.as_markup()


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_MENU_CONTACTS)
async def on_show_contacts(callback: MessageCallback, context: MemoryContext) -> None:
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return
    settings = await _read_site_settings()
    await callback.bot.send_message(
        chat_id=chat_id,
        text=_format_text(settings),
        attachments=[_build_keyboard(settings)],
    )
