"""Welcome skill — bot entry point with inline-keyboard quick actions.

Replaces the bare-text ``/start`` reply that lived in the echo skill.
Greets the user and surfaces 4 quick-action buttons:

  * 📅 Записаться — Mini App catalog screen
  * 📋 Мои визиты — Mini App visits screen
  * 👤 Профиль — Mini App profile screen
  * ❓ Задать вопрос — pure-text callback prompting the user to ask

The first three open the platform's customer Mini App. When
``settings.MAX_BOT_WEB_APP`` (bot username) is set the buttons use MAX's
native ``open_app`` type so the Mini App opens INSIDE the MAX client.
When unset (and ``settings.MAX_MINIAPP_URL`` is configured), the buttons
fall back to ``link`` type — opens in the external browser but still
deep-links to the right screen. When neither is configured, the welcome
keyboard collapses to a single «❓ Задать вопрос» callback button —
zero-config fallback for tests + early dev.

### Why a dedicated skill and not an echo branch

Echo still owns the catch-all path. Welcome registers BEFORE echo so
``/start`` lands here first. The callback prefix ``cb:welcome:`` also
routes here so the «❓ Задать вопрос» tap gets a helpful prompt rather
than a verbatim echo of the callback payload.
"""

from __future__ import annotations

from typing import ClassVar

from django.conf import settings

from apps.skills.base import SkillContext, SkillResult
from apps.skills.registry import register


WELCOME_TEXT = (
    "Здравствуйте! 👋\n\n"
    "Это бот массажного салона «Формула тела» в Пензе.\n"
    "Помогу записаться, расскажу об услугах и отвечу на частые вопросы.\n\n"
    "Выберите раздел:"
)

ASK_PROMPT = (
    "Спросите о чём угодно — про услуги, цены, противопоказания, "
    "адрес или режим работы. Я постараюсь ответить."
)


@register
class WelcomeSkill:
    """Greet on /start and route welcome-keyboard taps.

    Registered BEFORE echo so ``/start`` lands here. Echo retains its
    catch-all role for every other text.
    """

    name: ClassVar[str] = "welcome"

    def matches(self, context: SkillContext) -> bool:
        text = context.message_text.strip()
        if text == "/start":
            return True
        return text.startswith("cb:welcome:")

    def handle(self, context: SkillContext) -> SkillResult:
        text = context.message_text.strip()
        if text == "cb:welcome:ask":
            # User tapped the «❓ Задать вопрос» button — just prompt them.
            # FAQ skill picks up the actual question on the next turn.
            return SkillResult(
                reply_text=ASK_PROMPT,
                meta={"reply_kind": "welcome_ask_prompt"},
            )
        # Either /start OR a Mini-App-opening callback that we don't need
        # to respond to with a message (the user already left for the Mini
        # App). Re-show the menu so they have a way back if the Mini App
        # rejected the deeplink.
        return SkillResult(
            reply_text=WELCOME_TEXT,
            action_type="welcome_menu",
            action_data={
                "buttons": _welcome_buttons(),
                "button_columns": 1,
            },
            meta={"reply_kind": "welcome"},
        )


def _welcome_buttons() -> list[dict[str, str]]:
    """Build the welcome keyboard, picking button type based on config.

    Order matters — emoji-prefixed labels keep the visual rhythm aligned
    with the legacy maxbot welcome (parity with prod since 2026-04).
    """
    web_app = getattr(settings, "MAX_BOT_WEB_APP", "")
    miniapp_url = getattr(settings, "MAX_MINIAPP_URL", "")

    nav_buttons: list[dict[str, str]] = []
    if web_app:
        # In-MAX Mini App — native UX. ``callback`` carries the route
        # payload that the Mini App reads from initData.
        nav_buttons = [
            {"label": "📅 Записаться", "callback": "route=catalog", "web_app": web_app},
            {"label": "📋 Мои визиты", "callback": "route=visits", "web_app": web_app},
            {"label": "👤 Профиль", "callback": "route=profile", "web_app": web_app},
        ]
    elif miniapp_url:
        # External link fallback — opens in the user's browser.
        nav_buttons = [
            {"label": "📅 Записаться", "url": _join(miniapp_url, "catalog")},
            {"label": "📋 Мои визиты", "url": _join(miniapp_url, "visits")},
            {"label": "👤 Профиль", "url": _join(miniapp_url, "profile")},
        ]
    # Else: zero-config — only the «❓ Задать вопрос» callback ships.

    nav_buttons.append({"label": "❓ Задать вопрос", "callback": "cb:welcome:ask"})
    return nav_buttons


def _join(base: str, route: str) -> str:
    """Append a route segment to the Mini App base URL.

    Tolerates trailing slashes on the base and leading slashes on the
    route — both common in env-file copy-paste — so the producer doesn't
    have to think about it.
    """
    return f"{base.rstrip('/')}/{route.lstrip('/')}"
