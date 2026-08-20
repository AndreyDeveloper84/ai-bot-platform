"""Welcome skill — bot entry point with inline-keyboard quick actions.

Replaces the bare-text ``/start`` reply that lived in the echo skill.
Greets the user and surfaces quick-action buttons spanning both the
salon flow (booking + visits + profile) AND the Ayla wellness flow
(food diary + water + FAQ).

Salon buttons (3):
  * 📅 Записаться  — bot-native booking entry (``cb:menu:book``, DRF-963;
                     was a Mini App catalog route)
  * 📋 Мои записи  — bot-native booking lookup (``cb:menu:my_bookings``,
                     DRF-963; was a Mini App visits route)
  * 👤 Профиль     — Mini App profile screen (config-gated)

Ayla wellness buttons (2):
  * 🍽 Дневник еды — prompts the user to send a food photo / description.
                     The food_scanner / food_clarify skill then takes the
                     next turn.
  * 💧 Вода         — prompts for a drink amount; water skill picks up
                     ("стакан", "250 мл") on the next turn.

### DRF-1199 — no «Анкета» button on First Contact

BOT-001 forbids a standalone questionnaire entry point in four independent
places: §13.3 «a standalone "Анкета" object or button», non-goal #2, AC-5.1,
and the CDP anti-pattern «Questionnaire as entry price». The button is gone
from BOTH welcome surfaces (the S1 keyboard and the S5 first-action grid).
The anketa itself is untouched — ``NutritionAnketaSkill`` still owns
``/anketa`` and ``cb:anketa:start``; what is removed is its promotion to an
entry price.

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

### S2 privacy consent (152-ФЗ) — task #85 part 2, 2026-05-26

«Начать» button (callback ``cb:welcome:start_s2``) routes к real S2
privacy consent prompt (Tau's customer-onboarding-flow.md §5):

* ``cb:welcome:consent_yes`` — DIRECT path («Да, продолжим» из S2).
  Funnels к ``_render_consent_granted(show_s3=True)`` — stamps
  consent_at idempotently + renders S3 + S5 combined bubble.
* ``cb:welcome:consent_yes_via_s2a`` — S2a path («Понятно, продолжим»
  из S2a fold). Same handler but ``show_s3=False`` per Tau §6
  conditional rule (user already saw scope disclosure → S3
  repositioning would feel repetitive).
* ``cb:welcome:consent_details`` — S2a expanded fold disclosing scope.
* ``cb:welcome:consent_refuse`` — State 3 graceful exit. No keyboard.

### S3 positioning + S5 first-action grid — task #85 part 3, 2026-05-26

After consent, customer lands на S5 (Tau §8 Variant A: Grid 2×2 +
exit valve) — KEY MOMENT, выбор первого experience с Ayla. Five
buttons: 4 Mini-App primary actions + «Просто посмотреть» exit valve
(DRF-1199 removed the sixth, «Начать анкету»). Combined-bubble S3+S5
preserves «no user action between bubbles» intent without
multi-message infrastructure.

### S1 multi-tenant variant — task #85 part 4, 2026-05-26

When customer arrives via referral / QR / IG share-link, MAX delivers
``bot_started`` event с deeplink payload. Parser folds payload into
``/start <payload>`` synthetic text (apps/channels/max/parser.py).
Welcome skill detects ``ref_*`` / ``qr_*_*`` / ``ig_post_*`` prefixes
и renders Tau §4 multi-tenant variant («Помогу с записью в {salon}»)
instead of standard WELCOME_TEXT. Baseline ``/start`` (no payload) +
unparseable payloads fall through to standard text — strictly additive,
no regression risk.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from django.conf import settings
from django.utils import timezone

from apps.skills.base import SkillContext, SkillResult
from apps.skills.menu.matching import (
    CALLBACK_MENU_BOOK,
    CALLBACK_MENU_HELP,
    CALLBACK_MENU_MY_BOOKINGS,
    pilot_ux_enabled,
)
from apps.skills.registry import register
from apps.tenancy.context import current_tenant

logger = logging.getLogger(__name__)


# S1 greeting for a New User.
#
# DRF-1203 — копия состояние-первая, а не сервис-первая. Было: сначала
# идентификация бизнеса («Это бот массажного салона …»), потом перечень
# услуг, состояние человека — третьим предложением после «А ещё», и
# закрывающее «Выберите раздел:».
#
# Канон:
# * CDP-01 «Goal Before Mechanism» — разговор «never requires the user to
#   understand Ayla's services, categories, screens or internal structure
#   in order to make progress»; «Ayla is not a service catalogue with a
#   chat wrapper».
# * BOT-001 §8.1 — «The greeting SHOULD introduce Ayla's role briefly»,
#   «MUST NOT deliver a long feature explanation or guided tour».
# * BOT-001 §6.1 п.3 — «The greeting SHOULD offer help or invite the user
#   to state their goal in free text».
# * CDP §5 анти-паттерн #1 «Navigation before intent» — «Выберите
#   раздел:» превращало приветствие в меню.
#
# Канон задаёт смысл, но не дословный текст (§8.2: «Quick Action copy
# MUST NOT be canonicalized in this specification»), поэтому формулировка
# написана по смыслу. Салон назван — но как контекст в конце, а не как
# заголовок: на пилоте бот действительно принадлежит одному салону, и
# скрывать это было бы нечестно.
WELCOME_TEXT = (
    "Здравствуйте! 👋\n\n"
    "Я Ayla. Скажите своими словами, что сейчас нужно вам — отдохнуть, "
    "разобраться с самочувствием или записаться к мастеру. Я пойму и помогу.\n\n"
    "Рядом со мной — массажный салон «Формула тела» в Пензе."
)

# S1 multi-tenant variant (Tau §4) — rendered when bot_started arrives
# с deeplink payload ``ref_<user_id>`` / ``qr_<salon_id>_<placement>``
# / ``ig_post_<id>``. Frames Ayla с salon context (third-party reference
# per tenant-as-provider-model), so user knows этот entry point came
# from somewhere specific — referral / QR / IG link.
#
# ``{salon_name}`` placeholder filled from ``bot_user.tenant.name`` at
# render time. Pilot = single salon «Формула тела»; multi-tenant pattern
# generalises к post-pilot when bot serves multiple tenants.
S1_MULTITENANT_TEXT_TEMPLATE = (
    "Привет, я Ayla. Помогу с записью в {salon_name} "
    "и с уходом за собой каждый день — еда, вода, отдых.\n\n"
    "Начнём?"
)

# Recognised start_param prefixes per Tau §4. Tuple для order-independent
# membership test + future-proofing если добавятся patterns.
_S1_MULTITENANT_PREFIXES = ("ref_", "qr_", "ig_post_")

# S2 privacy consent (152-ФЗ) — Tau's customer-onboarding-flow.md §5
# verbatim. Brand-Guardian-approved (9.5/10), gender-neutral
# «Продолжим?» (не misgender ~10-20% male users в pilot cohort).
S2_CONSENT_TEXT = (
    "Прежде чем начать — короткое слово.\n"
    "Я буду помнить о тебе только то, что поможет рекомендовать точнее. "
    "Хранится безопасно. Удалить можно в любой момент.\n\n"
    "Продолжим?"
)

# S2a expanded fold (Tau §5). Active-voice «Запоминаю» (Brand Guardian
# fix; was passive). Surface scope explicitly so user знает что
# именно accumulates → 152-ФЗ informed-consent doctrine.
S2A_DETAILS_TEXT = (
    "Запоминаю: твои сообщения мне, выбранные цели, питание и вода "
    "если решишь логировать, записи к мастерам. Не делюсь с салонами "
    "без твоего разрешения. Подробнее в Профиле → «Данные обо мне» "
    "когда зайдёшь."
)

# State 3 (Tau §11) — refused consent graceful exit. Brand-Guardian
# rated «six words, dignity preserved, door open». No keyboard ships —
# conversation ends here; user может вернуться писать когда захочет.
S2_REFUSED_TEXT = "Поняла. Когда захочешь — пиши, я тут."

# S3 positioning text (Tau §6 verbatim). Brand-Guardian-approved
# «anti-positioning без being defensive». Conditional: rendered ONLY
# on direct S1→S2→S3 path. Skipped когда user уже видел S1 «Узнать
# подробнее» fold ИЛИ S2a expanded — repositioning would feel
# repetitive. «Без оценок» — psychological anchor repeats core promise.
S3_POSITIONING_TEXT = (
    "Если коротко — я не календарь и не ещё одна программа правильного "
    "питания. Я помогу разобраться с собой каждый день — еда, вода, "
    "ближайшая запись, самочувствие. Без оценок."
)

# S5 prompt (Tau §8). KEY MOMENT — customer выбирает первый experience
# с Ayla.
#
# DRF-1199: раньше здесь была пара строк — приглашение выбрать кнопку плюс
# отдельная рамка «Или расскажи о себе — 5 шагов» под кнопку анкеты. Анкета
# как входная точка запрещена (BOT-001 §13.3, non-goal #2, AC-5.1), рамка
# ушла вместе с кнопкой. Вместо неё — приглашение сказать своими словами:
# BOT-001 §6.1 «The greeting SHOULD offer help or invite the user to state
# their goal in free text», §11 «Free-text input MUST be the primary
# interaction mode». Текст читается и без клавиатуры — в zero-config
# конфигурации кнопок на этом экране не остаётся вовсе, и это допустимо:
# AC-4.1 «Quick Actions are optional».
S5_PROMPT_TEXT = "С чего хочешь начать? Напиши своими словами, что сейчас нужно, — я пойму."

# ── DRF-1202 — ровно три состояния приветствия ─────────────────────────
#
# BOT-001 P8: «MVP First Contact MUST use exactly three greeting states:
# New User, Returning User, User with an Active Task.» AC-3.1 требует,
# чтобы эти три состояния были проверяемы, AC-3.2 запрещает сегментацию
# сверх них («No behavioral cohort, RFM, VIP, favorite-service or churn
# segmentation appears in First Contact»).
#
# Нарушение было односторонним: варианта для вернувшегося пользователя
# не существовало вовсе. `/start` от уже поздоровавшегося человека
# отдавал ту же копию для новичка, а любой другой его текст навык просто
# не брал. Реализованным было одно состояние из трёх.
#
# Ниже — три состояния как явная, читаемая величина; резолвер один,
# и добавить четвёртое незаметно уже нельзя.
GREETING_STATE_NEW = "new_user"
GREETING_STATE_RETURNING = "returning_user"
GREETING_STATE_ACTIVE_TASK = "active_task"

# Returning User (BOT-001 §9.1): «The greeting SHOULD acknowledge the
# return»; «MUST NOT assume or create a new Task automatically». §9.3:
# «A Returning User MUST be able to start a new intent at any time via
# free text. The system MUST NOT force continuation of a previous topic
# that is no longer active.» — поэтому здесь ни намёка на прошлую тему.
RETURNING_TEXT = "С возвращением! 👋\n\nС чем помочь сегодня? Скажите своими словами — я пойму."

# User with an Active Task (BOT-001 §10.1): «Ayla MAY offer to continue
# the Active Task»; «MUST NOT force continuation»; «MUST allow the user
# to start a new intent via free text at any time». Формулировка по
# смыслу — §18.4 даёт пример поведения, но не канонический текст.
ACTIVE_TASK_TEXT = (
    "С возвращением! 👋\n\n"
    "Мы кое-что не закончили — продолжим? "
    "Или скажите своими словами, если сейчас нужно другое."
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
        # ``/start`` baseline + ``/start <deeplink_payload>`` variant
        # (multi-tenant referral / QR / IG entry, Tau §4). Parser folds
        # ``bot_started.payload`` into text — see _parse_bot_started в
        # apps/channels/max/parser.py.
        if text == "/start" or text.startswith("/start "):
            return True
        if text.startswith("cb:welcome:"):
            return True
        # S1 auto-trigger (task #85). Text from an unwelcomed BotUser
        # routes here — but NOT before other skills.
        #
        # DRF-1205 — фактическая правка комментария. Раньше здесь стояло
        # «Any text from an unwelcomed BotUser routes here BEFORE other
        # skills — first impression wins». Это неверно и всегда было
        # неверно: ``registry.dispatch`` идёт по реестру сверху вниз и
        # берёт ПЕРВЫЙ сматчившийся навык, а welcome зарегистрирован
        # четырнадцатым — booking (12) и остальные опрашиваются раньше
        # (apps/skills/apps.py). Поэтому «хочу записаться на маникюр»
        # уходит в booking, и welcome об этом ходе даже не спрашивают.
        #
        # Это и есть BOT-001 P1 «Intent Before Ceremony» — но соблюдается
        # он здесь ПОБОЧНО, порядком регистрации, а не выраженным
        # правилом. Комментарий описывал ровно нарушающее поведение как
        # действующее; исполнитель, который «починит» код под такой
        # комментарий, соответствие уничтожит.
        #
        # После того как ``handle()`` проставит ``welcomed_at``,
        # последующие matches() возвращают False (ветка ниже).
        if getattr(context.bot_user, "welcomed_at", None) is None:
            # Regression guard (#85): never hijack a user who is ALREADY
            # inside a flow — a resolved/in-progress human handoff or any
            # prior conversation. The current conversation's own messages
            # are deliberately excluded: the channel records the inbound
            # text BEFORE dispatch, so counting it would disable the
            # auto-welcome for genuinely first-contact users too.
            if _flow_already_established(context):
                return False
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
            # S1 → S2 privacy consent prompt (Tau §5 verbatim). Three
            # buttons: «Да, продолжим» (consent), «Узнать что хранится»
            # (S2a fold), «Не сейчас» (refuse → goodbye).
            return SkillResult(
                reply_text=S2_CONSENT_TEXT,
                action_type="welcome_consent_prompt",
                action_data={
                    "buttons": _s2_consent_buttons(),
                    "button_columns": 1,
                },
                meta={"reply_kind": "welcome_s2_consent_prompt"},
            )
        if text == "cb:welcome:consent_details":
            # S2a expanded fold (Tau §5). Two buttons: «Понятно,
            # продолжим» (=consent_yes — same outcome as «Да,
            # продолжим»), «Не сейчас» (=consent_refuse).
            return SkillResult(
                reply_text=S2A_DETAILS_TEXT,
                action_type="welcome_consent_details",
                action_data={
                    "buttons": _s2a_details_buttons(),
                    "button_columns": 1,
                },
                meta={"reply_kind": "welcome_s2a_details"},
            )
        if text == "cb:welcome:consent_yes":
            # Direct S1 → S2 → S3 → S5 path. SHOW S3 positioning.
            return self._render_consent_granted(context, show_s3=True)
        if text == "cb:welcome:consent_yes_via_s2a":
            # User came through S2a expanded fold — already disclosed
            # «что именно запоминаю». Tau §6 conditional rule: SKIP S3
            # — repositioning would feel repetitive. Route straight к
            # S5 first-action grid.
            return self._render_consent_granted(context, show_s3=False)
        if text == "cb:welcome:consent_refuse":
            # State 3 (Tau §11). Graceful exit — no keyboard, no menu.
            # consent_at remains NULL → если user пишет снова, welcome
            # auto-trigger ne fires (welcomed_at is set), но downstream
            # gates можно использовать consent_at IS NULL для re-prompt.
            #
            # Audit log: 152-ФЗ refusal IS a regulator-relevant event
            # (pre-pilot fix Y3 из CR на PR #776). INFO level, не ERROR
            # — это user-initiated normal flow, не failure.
            logger.info(
                "welcome.consent_refused bot_user_id=%s channel=%s",
                getattr(context.bot_user, "id", None),
                getattr(context.bot_user, "channel", None),
            )
            return SkillResult(
                reply_text=S2_REFUSED_TEXT,
                meta={"reply_kind": "welcome_consent_refused"},
            )
        # /start OR S1 auto-trigger OR Mini-App-opening callback that we
        # don't need to respond to with a message. Re-show the menu so
        # they have a way back if the Mini App rejected the deeplink.
        #
        # DRF-1202 — состояние приветствия определяется ДО того, как
        # ``welcomed_at`` будет проставлен: сразу после штампа тот же
        # пользователь выглядит вернувшимся.
        greeting_state = _greeting_state(context)
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
        # DRF-1202 — Returning User (§9) и User with an Active Task (§10).
        # Оба доступны только через явный жест: `/start` или колбэк
        # welcome-клавиатуры. Автотриггер по произвольному тексту
        # по-прежнему только для New User (``welcomed_at IS NULL``) —
        # расширять его на вернувшихся значило бы ставить церемонию перед
        # намерением (P1) на каждом ходу.
        if greeting_state == GREETING_STATE_ACTIVE_TASK:
            return SkillResult(
                reply_text=ACTIVE_TASK_TEXT,
                action_type="welcome_menu",
                action_data={
                    "buttons": _welcome_buttons(),
                    "button_columns": 1,
                },
                meta={"reply_kind": "welcome_active_task"},
            )
        if greeting_state == GREETING_STATE_RETURNING:
            return SkillResult(
                reply_text=RETURNING_TEXT,
                action_type="welcome_menu",
                action_data={
                    "buttons": _welcome_buttons(),
                    "button_columns": 1,
                },
                meta={"reply_kind": "welcome_returning"},
            )
        # S1 multi-tenant variant detection (Tau §4). Folded deeplink
        # payload sits в text suffix after «/start ». Recognised prefixes
        # = ref_ / qr_ / ig_post_. На match → render multi-tenant text
        # с salon name из current tenant. Unparseable / empty → standard
        # WELCOME_TEXT, no behavior change.
        #
        # Это НЕ четвёртое состояние: канон прямо велит учитывать
        # entry context внутри состояния. P3 — «Greeting behavior MUST
        # adapt to entry context: user state, channel and available
        # context»; §5 перечисляет «a deep link or trigger that carries
        # context» как способ начать First Contact; §17 шаг 1 —
        # «Determine entry context: channel, user state, trigger/deep
        # link». Сегментация, запрещённая AC-3.2, — поведенческая
        # (cohort / RFM / VIP / favorite-service / churn), а не источник
        # перехода.
        start_param = _extract_start_param(text)
        if start_param and start_param.startswith(_S1_MULTITENANT_PREFIXES):
            salon_name = _resolve_salon_name(bot_user)
            return SkillResult(
                reply_text=S1_MULTITENANT_TEXT_TEMPLATE.format(salon_name=salon_name),
                action_type="welcome_menu",
                action_data={
                    "buttons": _welcome_buttons(),
                    "button_columns": 1,
                },
                meta={
                    "reply_kind": "welcome_s1_multitenant",
                    "start_param": start_param,
                },
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

    def _render_consent_granted(self, context: SkillContext, *, show_s3: bool) -> SkillResult:
        """Stamp consent_at + render S5 first-action grid.

        Both consent_yes callbacks (direct + via_s2a) funnel here for
        single-source idempotent consent stamping. ``show_s3`` toggles
        S3 positioning prepend per Tau §6 conditional rule.

        Combined-bubble interpretation: Tau §6 specs «sequential — S5
        message arrives next without user action», but the SkillResult
        contract is one outgoing reply per turn. Combined-bubble (S3
        text prepended к S5 prompt) preserves user-facing intent («no
        user action between S3 and S5») without requiring multi-message
        infrastructure. Strict two-bubble может revisit post-pilot.
        """
        bot_user = context.bot_user
        # #1074 — on the GLOBAL (tenant-less) path we do NOT stamp consent_at here.
        # global_onboarding calls ``consent.services.record_global_consent`` right
        # after this render, and that stamps consent_at ATOMICALLY with the
        # ConsentRecord (proof-of-consent) — so on the global path consent_at can
        # never be set without the record. On the per-tenant path
        # (``current_tenant()`` set) we stamp as before.
        if current_tenant() is not None and getattr(bot_user, "consent_at", None) is None:
            try:
                bot_user.consent_at = timezone.now()
                bot_user.save(update_fields=["consent_at"])
            except Exception as exc:  # noqa: BLE001
                # Mirror welcomed_at pattern: log + continue. Worst case
                # — consent re-asked on next entry to S2; not data-loss
                # since user IS giving consent right now.
                logger.error(
                    "welcome.consent_at_save_failed bot_user_id=%s err=%s",
                    getattr(bot_user, "id", None),
                    exc,
                )
        body_parts: list[str] = []
        if show_s3:
            body_parts.append(S3_POSITIONING_TEXT)
        body_parts.append(S5_PROMPT_TEXT)
        return SkillResult(
            reply_text="\n\n".join(body_parts),
            action_type="welcome_s5_first_action",
            action_data={
                "buttons": _s5_first_action_buttons(),
                # Grid 2×2 per Tau §8 Variant A — primary 4 buttons +
                # «Просто посмотреть», всё в 2-col layout.
                "button_columns": 2,
            },
            meta={
                "reply_kind": "welcome_s5_first_action",
                "s3_shown": show_s3,
            },
        )


def _extract_start_param(text: str) -> str:
    """Pull deeplink payload out of folded ``/start <payload>`` text.

    Returns empty string на baseline ``/start`` (no payload), the raw
    stripped suffix otherwise. Caller decides if suffix matches
    recognised patterns (Tau §4 multi-tenant prefixes) vs noise.
    """
    if not text.startswith("/start "):
        return ""
    return text[len("/start ") :].strip()


def _resolve_salon_name(bot_user) -> str:
    """Return salon (tenant) display name for S1 multi-tenant render.

    Pilot = single tenant «Формула тела», so this typically returns that
    name from ``bot_user.tenant.name``. Generalises post-pilot when bot
    serves multiple tenants — salon resolution then depends на the
    ``qr_<salon_id>_<placement>`` slot semantics (W1 follow-up scope).

    Defensive fallback «нашем салоне» — covers edge cases когда tenant
    attribute missing / DB error / unresolved cross-tenant invisible
    relationship at first-contact. Better to render generic phrasing
    than 500 or expose internal slug.
    """
    try:
        tenant = getattr(bot_user, "tenant", None)
        if tenant is not None:
            name = getattr(tenant, "name", "") or ""
            if name:
                return name
    except Exception as exc:  # noqa: BLE001
        # Don't silent-fail на programming bugs (TypeError, broken
        # tenant FK, DB error). WARNING level — operator must see the
        # fallback was triggered, иначе regression hides за «нашем
        # салоне». CR #810 follow-up #4.
        logger.warning(
            "welcome.salon_name_resolve_failed bot_user_id=%s err=%s",
            getattr(bot_user, "id", None),
            exc,
        )
    return "нашем салоне"


def _welcome_buttons() -> list[dict[str, str]]:
    """Build the welcome keyboard, picking salon-button type based on config.

    Order matters — emoji-prefixed labels keep the visual rhythm aligned
    with the legacy maxbot welcome (parity with prod since 2026-04).

    ### DRF-963 (Wave 1, variant A) — booking actions are bot-native

    «📅 Записаться» and «📋 Мои записи» used to be Mini App routes, so on
    a deployment without ``MAX_BOT_WEB_APP`` / ``MAX_MINIAPP_URL`` the
    welcome shipped NO booking entry at all, and even with a Mini App the
    chat itself offered no way in — the pilot complaint DRF-963 exists to
    fix. They are now ``cb:menu:*`` callbacks handled by
    :class:`apps.skills.menu.skill.MenuSkill`, which translates each tap
    into the canonical phrase the booking skill already claims. They ship
    unconditionally, config or not.

    The Mini App is NOT lost: «👤 Профиль» keeps the config-gated ladder,
    and the S5 first-action grid still opens the catalog directly
    (``open_catalog`` in :func:`_s5_first_action_buttons`).
    """
    web_app = getattr(settings, "MAX_BOT_WEB_APP", "")
    miniapp_url = getattr(settings, "MAX_MINIAPP_URL", "")

    if not pilot_ux_enabled():
        # DRF-963 rolled back without a deploy — restore the pre-change
        # keyboard exactly: Mini-App salon trio, no «Помощь». Emitting
        # cb:menu:* here while MenuSkill stands down would ship dead
        # buttons, which is worse than the bug we were fixing.
        return (
            _legacy_salon_buttons(web_app, miniapp_url)
            + _wellness_buttons(include_help=False)
            + _start_buttons()
        )

    # Bot-native booking actions — always present, always work in chat.
    salon_buttons: list[dict[str, str]] = [
        {"label": "📅 Записаться", "callback": CALLBACK_MENU_BOOK},
        {"label": "📋 Мои записи", "callback": CALLBACK_MENU_MY_BOOKINGS},
    ]
    if web_app:
        # In-MAX Mini App — native UX. ``callback`` carries the route
        # payload that the Mini App reads from initData.start_param.
        #
        # MAX requires open_app button payload to match a restricted
        # regex (no `=`, `&`, etc — likely ``[A-Za-z0-9_:-]+``). Initial
        # ``route=catalog`` shape was rejected with HTTP 400
        # ``proto.payload``. Use a flat slug instead and let the Mini
        # App's parseStartRoute() resolve it via direct lookup.
        salon_buttons.append(
            {"label": "👤 Профиль", "callback": "open_profile", "web_app": web_app},
        )
    elif miniapp_url:
        # External link fallback — opens in the user's browser.
        salon_buttons.append({"label": "👤 Профиль", "url": _join(miniapp_url, "profile")})
    # Else: zero-config — no Mini App button; the bot-native pair still ships.

    return salon_buttons + _wellness_buttons(include_help=True) + _start_buttons()


def _legacy_salon_buttons(web_app: str, miniapp_url: str) -> list[dict[str, str]]:
    """Pre-DRF-963 Mini-App salon trio, used only when the flag is OFF."""
    if web_app:
        return [
            {"label": "📅 Записаться", "callback": "open_catalog", "web_app": web_app},
            {"label": "📋 Мои визиты", "callback": "open_visits", "web_app": web_app},
            {"label": "👤 Профиль", "callback": "open_profile", "web_app": web_app},
        ]
    if miniapp_url:
        return [
            {"label": "📅 Записаться", "url": _join(miniapp_url, "catalog")},
            {"label": "📋 Мои визиты", "url": _join(miniapp_url, "visits")},
            {"label": "👤 Профиль", "url": _join(miniapp_url, "profile")},
        ]
    return []


def _wellness_buttons(*, include_help: bool) -> list[dict[str, str]]:
    """Wellness + FAQ row — always callback-typed.

    These trigger a follow-up bot turn rather than a Mini App route.
    «❓ Помощь» (DRF-963) lists what the bot can do; «❓ Задать вопрос» stays
    the FAQ entry — complementary, not duplicates.

    DRF-1199: «📊 Анкета» used to sit here and jump straight into the
    nutrition_anketa FSM. BOT-001 §13.3 forbids a standalone «Анкета» button
    on First Contact, so it was removed; ``/anketa`` still starts the same
    flow when the user asks for it.
    """
    buttons = [
        {"label": "🍽 Дневник еды", "callback": "cb:welcome:food"},
        {"label": "💧 Вода", "callback": "cb:welcome:water"},
        {"label": "❓ Задать вопрос", "callback": "cb:welcome:ask"},
    ]
    if include_help:
        buttons.append({"label": "❓ Помощь", "callback": CALLBACK_MENU_HELP})
    return buttons


def _start_buttons() -> list[dict[str, str]]:
    """S1 → S2 ack button (task #85).

    Sits под основными разделами — «Начать» = ack «я готов(а)» → bot route
    к S2 privacy consent.
    """
    return [{"label": "▶️ Начать", "callback": "cb:welcome:start_s2"}]


def _s2_consent_buttons() -> list[dict[str, str]]:
    """S2 privacy consent keyboard (Tau §5).

    Three options:
      * «Да, продолжим» → ``cb:welcome:consent_yes`` (stamps consent_at).
      * «Узнать что хранится» → ``cb:welcome:consent_details`` (S2a fold).
      * «Не сейчас» → ``cb:welcome:consent_refuse`` (goodbye, no menu).

    Single-column layout matches the rest of the welcome flow (legacy
    maxbot established 1-col convention since 2026-04) — easier на
    mobile, less visual noise vs. 3-col grid.
    """
    return [
        {"label": "Да, продолжим", "callback": "cb:welcome:consent_yes"},
        {"label": "Узнать что хранится", "callback": "cb:welcome:consent_details"},
        {"label": "Не сейчас", "callback": "cb:welcome:consent_refuse"},
    ]


def _s2a_details_buttons() -> list[dict[str, str]]:
    """S2a expanded keyboard (Tau §5).

    Two options:
      * «Понятно, продолжим» → ``cb:welcome:consent_yes_via_s2a``.
        Distinct callback from S2 direct «Да, продолжим» — funnels к
        same idempotent consent-stamp helper, но flag's S3 SKIP per
        Tau §6 (user already disclosed enough в S2a fold).
      * «Не сейчас» → ``cb:welcome:consent_refuse``.
    """
    return [
        {"label": "Понятно, продолжим", "callback": "cb:welcome:consent_yes_via_s2a"},
        {"label": "Не сейчас", "callback": "cb:welcome:consent_refuse"},
    ]


def _s5_first_action_buttons() -> list[dict[str, str]]:
    """S5 first-action grid (Tau §8 Variant A — Grid 2×2 + exit valve).

    Five buttons total, all routed к Mini App start_params. DRF-1199 removed
    the sixth («📝 Начать анкету») — BOT-001 §13.3 forbids a standalone
    «Анкета» button on First Contact.

    Mini App ladder (mirrors ``_welcome_buttons()``):
      * ``MAX_BOT_WEB_APP`` set → ``open_app`` buttons with flat-slug
        callback payloads (no `=`, `&` — MAX HTTP 400 on those).
      * ``MAX_MINIAPP_URL`` only → ``link`` buttons.
      * Neither → drop Mini-App-dependent buttons; «Просто посмотреть»
        drops too (Dashboard exists only as Mini App). Zero-config mode =
        no buttons at all, only the free-text invitation in
        :data:`S5_PROMPT_TEXT` — allowed by AC-4.1 («Quick Actions are
        optional») now that the copy no longer promises a keyboard.

    Routing per Tau §8 (resolved Mini App side — DRF-1167 fix):
      * ``open_food_scan`` → /customer/food-scanner/capture
      * ``open_water_add_250`` → /customer/wellness
      * ``open_goal_select`` → /customer/goal-select
      * ``open_catalog`` → /catalog
      * ``open_home`` → /customer/main

    Source of truth for slug→path resolution is the consumer-side
    ``_ROUTE_MAP`` in ``apps/miniapp/src/lib/max-sdk.ts`` (CONTRACT MIRROR):
    adding a button here requires adding the matching route there in the
    same PR, otherwise the menu ships a dead deeplink. The ``link``
    fallback below points at the same paths as the resolved slugs.
    """
    web_app = getattr(settings, "MAX_BOT_WEB_APP", "")
    miniapp_url = getattr(settings, "MAX_MINIAPP_URL", "")

    primary_actions: list[dict[str, str]] = []
    just_browse: list[dict[str, str]] = []
    if web_app:
        primary_actions = [
            {
                "label": "📸 Сфотографировать еду",
                "callback": "open_food_scan",
                "web_app": web_app,
            },
            {
                "label": "💧 + стакан воды",
                "callback": "open_water_add_250",
                "web_app": web_app,
            },
            {"label": "🎯 Выбрать цель", "callback": "open_goal_select", "web_app": web_app},
            {"label": "📅 Найти услугу", "callback": "open_catalog", "web_app": web_app},
        ]
        just_browse = [
            {"label": "Просто посмотреть", "callback": "open_home", "web_app": web_app},
        ]
    elif miniapp_url:
        # Link fallback — same targets as the slugs resolved in
        # apps/miniapp/src/lib/max-sdk.ts::_ROUTE_MAP (DRF-1167 fix:
        # previously pointed at /food_scan, /goal_select etc., none of
        # which exist as Mini App routes).
        primary_actions = [
            {
                "label": "📸 Сфотографировать еду",
                "url": _join(miniapp_url, "customer/food-scanner/capture"),
            },
            {"label": "💧 + стакан воды", "url": _join(miniapp_url, "customer/wellness")},
            {"label": "🎯 Выбрать цель", "url": _join(miniapp_url, "customer/goal-select")},
            {"label": "📅 Найти услугу", "url": _join(miniapp_url, "catalog")},
        ]
        just_browse = [
            {"label": "Просто посмотреть", "url": _join(miniapp_url, "customer/main")},
        ]
    # Else: zero-config — no Mini-App buttons at all.
    # NOTE: Pilot deployment assumes MAX_BOT_WEB_APP IS configured (production-
    # validated MAX stack). Zero-config branch = degraded dev / test mode;
    # S5 surface shows the positioning text plus the free-text invitation and
    # no keyboard. Acceptable for pilot, но если zero-config попадёт в prod
    # это значит config drift — alert на operator side, не silent UX
    # regression.

    return primary_actions + just_browse


def _join(base: str, route: str) -> str:
    """Append a route segment to the Mini App base URL.

    Tolerates trailing slashes on the base and leading slashes on the
    route — both common in env-file copy-paste — so the producer doesn't
    have to think about it.
    """
    return f"{base.rstrip('/')}/{route.lstrip('/')}"


def _greeting_state(context: SkillContext) -> str:
    """Which of the three canonical greeting states this turn is in.

    BOT-001 P8 / AC-3.1 — ровно три: New User, Returning User, User with
    an Active Task. Порядок проверок повторяет §17 (шаги 4–6): сначала
    «есть ли прошлое взаимодействие», потом «есть ли незавершённая
    работа».

    * **New User** — §8: «no prior recognized interaction with Ayla in the
      current pilot context». В коде это ``welcomed_at IS NULL``.
    * **User with an Active Task** — §10: «an ongoing Task from a previous
      interaction». §10.3 намеренно оставляет обнаружение рантайму («This
      specification does not define: Task storage; Task retrieval
      logic…»), поэтому берём собственную запись рантайма о незавершённой
      работе — ``Conversation.skill_state``, куда многошаговые навыки
      (booking-continuation, nutrition_anketa) кладут свой FSM. Это НЕ
      ``AdminTask``: тот — тикет оператору, вещь другого рода.
    * **Returning User** — §9: прошлое взаимодействие есть, активной
      задачи нет.
    """
    if getattr(context.bot_user, "welcomed_at", None) is None:
        return GREETING_STATE_NEW
    skill_state = getattr(context.conversation, "skill_state", None)
    if isinstance(skill_state, dict) and any(v for v in skill_state.values()):
        return GREETING_STATE_ACTIVE_TASK
    return GREETING_STATE_RETURNING


# DRF-1207 — how many rows the CURRENT conversation may hold and still count
# as first contact. The channel records the inbound text BEFORE dispatch
# (apps/channels/max/handler.py::record_message), so a genuine first-contact
# turn sees exactly one row: the message being dispatched right now.
_FIRST_CONTACT_MESSAGE_ROWS = 1


def _flow_already_established(context: SkillContext) -> bool:
    """True when the user already has a live/established flow the
    auto-welcome must not interrupt.

    Two markers, both checked cross-tenant (the global path runs
    tenant-less):

    * messages already exchanged with this bot_user — in any other
      conversation, or in the current one beyond the single inbound row
      the channel recorded a moment ago (DRF-1207);
    * any AdminTask ever created for this bot_user — a handoff, open or
      long since resolved, proves there WAS a prior interaction, so this
      turn is not a first contact (see the DRF-1206 note below).

    ### DRF-1207 — why the current conversation is no longer skipped wholesale

    The previous guard excluded EVERY row of the current conversation, on the
    reasoning that a first-contact user legitimately owns one message here.
    That is true of the first turn only. If turn 1 was claimed by another
    skill (booking matches «хочу записаться» at registry position 12, welcome
    sits at 14), ``welcomed_at`` is never stamped — and on turn 2 the guard
    still saw an "empty" conversation, so the full greeting surfaced in the
    middle of a live dialogue. From the outside that reads as «бот забыл, что
    мы уже общаемся» — CDP anti-pattern «Amnesia» and a greeting ceremony
    delaying an intent already in progress (BOT-001 P1).

    Counting instead of excluding keeps the original intent (turn 1 must still
    auto-welcome) and closes turn 2+: by then the conversation holds the first
    inbound, the reply that was sent, and the new inbound.
    """
    from apps.conversations.models import Message
    from apps.handoff.models import AdminTask

    current_pk = getattr(context.conversation, "pk", None)
    messages = Message.all_tenants.filter(conversation__bot_user=context.bot_user)
    # Test contexts may carry a MagicMock conversation — split current from
    # other only when the pk is a real key value. int included for test
    # contexts with a plain-int pk; the ORM accepts it identically to a
    # UUID/str at the lookup layer.
    import uuid as _uuid

    if isinstance(current_pk, (int, _uuid.UUID)):
        if messages.exclude(conversation_id=current_pk).exists():  # type: ignore[misc]
            return True
        current_rows = messages.filter(conversation_id=current_pk).count()  # type: ignore[misc]
        if current_rows > _FIRST_CONTACT_MESSAGE_ROWS:
            return True
    elif messages.exists():
        return True

    # DRF-1206 — DELIBERATELY unconditioned by status. Do not "fix" this
    # into ``status__in=(OPEN, IN_PROGRESS)``; that was tried and reverted.
    #
    # The audit (AUDIT_DIALOGUE_MODEL.md §2.6 п.10) reads this line as the
    # detector for the BOT-001 §10 greeting state «User with an Active Task»
    # and calls it too wide. It is not that detector. That state is resolved
    # by :func:`_greeting_state` from ``Conversation.skill_state`` (DRF-1202).
    # This predicate answers a different question — «is this turn genuinely a
    # first contact?» — and an AdminTask that ever existed is proof that it is
    # not: the user already talked to us and to an operator. Under §8 a New
    # User is someone with «no prior recognized interaction», so declining the
    # auto-welcome here is what the canon asks for, not a deviation from it.
    #
    # Narrowing it to active statuses breaks
    # ``apps/skills/human_handoff/tests/test_skill.py::TestDispatcherGuard
    # ::test_resume_after_resolve``: the follow-up after a RESOLVED handoff
    # comes back as a full new-user greeting instead of resuming — the same
    # «bot forgot we were talking» failure DRF-1207 exists to remove.
    #
    # The canon-level complaint behind DRF-1206 — «приветствие не показывается
    # такому пользователю никогда» — is real but lives elsewhere, and DRF-1202
    # closes it: such a user is a Returning User (§9) and now has a greeting of
    # their own.
    return AdminTask.all_tenants.filter(conversation__bot_user=context.bot_user).exists()
