"""Welcome skill — bot entry point with inline-keyboard quick actions.

Replaces the bare-text ``/start`` reply that lived in the echo skill.
Greets the user and surfaces 7 quick-action buttons spanning both the
salon flow (booking + visits + profile) AND the Ayla wellness flow
(food diary + water + anketa + FAQ) — parity with the mysite welcome
that ran in prod since 2026-04.

Salon buttons (3):
  * 📅 Записаться — Mini App catalog screen
  * 📋 Мои визиты — Mini App visits screen
  * 👤 Профиль     — Mini App profile screen

Ayla wellness buttons (3):
  * 🍽 Дневник еды — prompts the user to send a food photo / description.
                     The food_scanner / food_clarify skill then takes the
                     next turn.
  * 💧 Вода         — prompts for a drink amount; water skill picks up
                     ("стакан", "250 мл") on the next turn.
  * 📊 Анкета       — payload is ``cb:anketa:start`` so the
                     nutrition_anketa skill matches directly and starts
                     the FSM without an intermediate welcome turn.

FAQ button (1):
  * ❓ Задать вопрос — pure-text callback prompting the user to ask. The
                       FAQ skill picks up the actual question on the next turn.

### Button-type ladder

The salon buttons need a Mini App route. Behaviour follows config:

* ``settings.MAX_BOT_WEB_APP`` set → ``open_app`` (Mini App opens INSIDE
  the MAX client; the route comes from the ``callback`` field which MAX
  forwards into the Mini App's ``initData``).
* ``settings.MAX_BOT_WEB_APP`` empty + ``settings.MAX_MINIAPP_URL`` set
  → ``link`` button (opens in the external browser).
* Both empty → only the wellness + FAQ callback buttons ship — zero-config
  fallback for tests + early dev.

The wellness + FAQ buttons are always ``callback`` type — they don't
need a Mini App route, just a follow-up bot turn.

### Why a dedicated skill and not an echo branch

Echo still owns the catch-all path. Welcome registers BEFORE echo so
``/start`` lands here first. The callback prefix ``cb:welcome:`` also
routes here so the welcome-keyboard taps get a helpful prompt rather
than verbatim echo of the callback payload.

### S1 auto-trigger (task #85, W2/Epsilon, 2026-05-26)

In addition to ``/start`` + ``cb:welcome:*``, welcome **also** triggers
on the **first message from any BotUser with ``welcomed_at IS NULL``**.
Per Tau's customer-onboarding-flow.md S1 step: customer who texts the
bot для первой раз (даже без явного ``/start``) должен получить welcome,
а не уйти в echo / generic Q&A fallback.

Idempotency: ``handle()`` sets ``bot_user.welcomed_at = timezone.now()``
on first delivery. ``matches()`` short-circuits the auto-trigger path
when ``welcomed_at`` is non-NULL — subsequent messages route к normal
dispatcher flow.

A «Начать» button advances к S2 (privacy consent) via callback
``cb:welcome:start_s2``. S2 implementation pending Tau's follow-up
PR for ``customer-onboarding-flow.md`` rebase; current handler echoes
a placeholder so the chain is wireable now and switchable later
without rebuilding S1.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from django.conf import settings
from django.utils import timezone

from apps.skills.base import SkillContext, SkillResult
from apps.skills.registry import register

logger = logging.getLogger(__name__)


WELCOME_TEXT = (
    "Здравствуйте! 👋\n\n"
    "Это бот массажного салона «Формула тела» в Пензе.\n"
    "Помогу записаться, расскажу об услугах и отвечу на частые вопросы.\n"
    "А ещё умею вести дневник еды и воды.\n\n"
    "Выберите раздел:"
)

# S1 → S2 transition placeholder. Real S2 (privacy consent prompt with
# 152-ФЗ legal text + Принять / Подробнее buttons) lands в Tau's
# customer-onboarding-flow.md follow-up PR. Current text — soft ack
# + return к main menu чтобы flow не упирался в dead-end.
START_S2_PLACEHOLDER_TEXT = (
    "Здорово! Сейчас покажу что я умею. Выбирайте раздел или напишите свой вопрос — отвечу."
)

ASK_PROMPT = (
    "Спросите о чём угодно — про услуги, цены, противопоказания, "
    "адрес или режим работы. Я постараюсь ответить."
)

FOOD_PROMPT = (
    "🍽 Пришлите фото блюда — распознаю и запишу в дневник. "
    "Или просто напишите название (например, «борщ 300г»)."
)

WATER_PROMPT = "💧 Сколько выпили? Напишите коротко — например, «стакан», «250 мл», «чашка кофе»."


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
        if text.startswith("cb:welcome:"):
            return True
        # S1 auto-trigger (task #85). Any text from an unwelcomed BotUser
        # routes here BEFORE other skills — first impression wins. After
        # ``handle()`` marks ``welcomed_at``, subsequent matches() calls
        # return False (this branch), and the normal dispatcher walks
        # other skills for the user's actual intent.
        if getattr(context.bot_user, "welcomed_at", None) is None:
            return True
        return False

    def handle(self, context: SkillContext) -> SkillResult:
        text = context.message_text.strip()
        if text == "cb:welcome:ask":
            # User tapped «❓ Задать вопрос» — prompt; FAQ skill picks up next turn.
            return SkillResult(
                reply_text=ASK_PROMPT,
                meta={"reply_kind": "welcome_ask_prompt"},
            )
        if text == "cb:welcome:food":
            return SkillResult(
                reply_text=FOOD_PROMPT,
                meta={"reply_kind": "welcome_food_prompt"},
            )
        if text == "cb:welcome:water":
            return SkillResult(
                reply_text=WATER_PROMPT,
                meta={"reply_kind": "welcome_water_prompt"},
            )
        if text == "cb:welcome:start_s2":
            # S1 → S2 transition. Placeholder text; final S2 (privacy
            # consent screen) lands в Tau's customer-onboarding-flow.md
            # follow-up PR. Until then re-show menu so user has a path
            # forward.
            return SkillResult(
                reply_text=START_S2_PLACEHOLDER_TEXT,
                action_type="welcome_menu",
                action_data={
                    "buttons": _welcome_buttons(),
                    "button_columns": 1,
                },
                meta={"reply_kind": "welcome_start_s2_placeholder"},
            )
        # /start OR S1 auto-trigger OR Mini-App-opening callback that we
        # don't need to respond to with a message. Re-show the menu so
        # they have a way back if the Mini App rejected the deeplink.
        #
        # S1 idempotency: stamp welcomed_at so subsequent inbound
        # messages bypass the auto-trigger branch in matches().
        bot_user = context.bot_user
        if getattr(bot_user, "welcomed_at", None) is None:
            try:
                bot_user.welcomed_at = timezone.now()
                bot_user.save(update_fields=["welcomed_at"])
            except Exception as exc:  # noqa: BLE001
                # Не блокируем welcome delivery если DB write fail —
                # худший случай: welcome re-fires на следующем msg.
                # ERROR log: оператор увидит pattern если это
                # систематически воспроизводится.
                logger.error(
                    "welcome.welcomed_at_save_failed bot_user_id=%s err=%s",
                    getattr(bot_user, "id", None),
                    exc,
                )
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
    """Build the welcome keyboard, picking salon-button type based on config.

    Order matters — emoji-prefixed labels keep the visual rhythm aligned
    with the legacy maxbot welcome (parity with prod since 2026-04).
    """
    web_app = getattr(settings, "MAX_BOT_WEB_APP", "")
    miniapp_url = getattr(settings, "MAX_MINIAPP_URL", "")

    salon_buttons: list[dict[str, str]] = []
    if web_app:
        # In-MAX Mini App — native UX. ``callback`` carries the route
        # payload that the Mini App reads from initData.start_param.
        #
        # MAX requires open_app button payload to match a restricted
        # regex (no `=`, `&`, etc — likely ``[A-Za-z0-9_:-]+``). Initial
        # ``route=catalog`` shape was rejected with HTTP 400
        # ``proto.payload``. Use a flat slug instead and let the Mini
        # App's parseStartRoute() resolve it via direct lookup.
        salon_buttons = [
            {"label": "📅 Записаться", "callback": "open_catalog", "web_app": web_app},
            {"label": "📋 Мои визиты", "callback": "open_visits", "web_app": web_app},
            {"label": "👤 Профиль", "callback": "open_profile", "web_app": web_app},
        ]
    elif miniapp_url:
        # External link fallback — opens in the user's browser.
        salon_buttons = [
            {"label": "📅 Записаться", "url": _join(miniapp_url, "catalog")},
            {"label": "📋 Мои визиты", "url": _join(miniapp_url, "visits")},
            {"label": "👤 Профиль", "url": _join(miniapp_url, "profile")},
        ]
    # Else: zero-config — no salon buttons (only wellness + FAQ ship).

    # Wellness + FAQ buttons are always callback-typed — they trigger a
    # follow-up bot turn rather than a Mini App route. Anketa jumps
    # straight into the nutrition_anketa FSM via its own callback prefix.
    wellness_buttons = [
        {"label": "🍽 Дневник еды", "callback": "cb:welcome:food"},
        {"label": "💧 Вода", "callback": "cb:welcome:water"},
        {"label": "📊 Анкета", "callback": "cb:anketa:start"},
        {"label": "❓ Задать вопрос", "callback": "cb:welcome:ask"},
    ]
    # S1 → S2 ack button (task #85). Sits под основными разделами —
    # «Начать» = ack «я готов(а)» → bot route к S2 privacy consent.
    # S2 = placeholder сейчас, реальный flow в Tau's PR.
    start_buttons = [
        {"label": "▶️ Начать", "callback": "cb:welcome:start_s2"},
    ]
    return salon_buttons + wellness_buttons + start_buttons


def _join(base: str, route: str) -> str:
    """Append a route segment to the Mini App base URL.

    Tolerates trailing slashes on the base and leading slashes on the
    route — both common in env-file copy-paste — so the producer doesn't
    have to think about it.
    """
    return f"{base.rstrip('/')}/{route.lstrip('/')}"
