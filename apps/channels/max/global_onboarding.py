"""Global (tenant-less) MAX onboarding — welcome + 152-ФЗ consent (#1046).

The nationwide marketplace path (``_handle_global_max_event_inner``) previously
dropped a brand-new user straight into discovery — no greeting, no consent
capture. That was both a UX miss and a 152-ФЗ gap (the conversation is persisted,
and long-term memory will be, without a recorded consent). This module adds a
**Variant A «soft gate»** (founder verdict 2026-07-02): we greet + offer consent,
but we do NOT block discovery / one-off booking on it. Only long-term memory (G2)
and proactive messaging are gated — and that enforcement lives in the memory
writer (S1.7 / #1054, `apps/identity`), NOT here. This module is presentation +
consent capture only.

### Design — reuse WelcomeSkill directly

The per-tenant welcome/consent state machine already exists in
:class:`apps.skills.welcome.skill.WelcomeSkill` (``/start`` → menu → S2 consent →
stamp ``consent_at`` → S5). We reuse it **directly**, NOT through the per-tenant
skill dispatcher — the global path must stay tenant-less and cannot pull
per-tenant skills. Its :class:`SkillResult` is wrapped into a
:class:`DiscoveryReply` whose ``action_data`` intentionally mirrors
``SkillResult.action_data``, so ``handler._build_attachments`` renders the
keyboard identically with zero rendering changes.

Two text surfaces are swapped for marketplace framing, because WelcomeSkill
hardcodes the pilot salon «Формула тела» + a wellness first-action grid
(«сфотографировать еду / вода / цель»), which is the wrong entry for a nationwide
discovery bot:

* the initial welcome → :data:`GLOBAL_WELCOME_TEXT` + a single «Начать» button
  that routes into the shared S2 consent flow;
* the S5 first-action prompt → :data:`GLOBAL_S5_TEXT` + the C01 Quick Actions
  (:mod:`apps.channels.max.quick_actions`), with the wellness grid dropped.

Обе копии переписаны в DRF-1348 (решение владельца 24.08): need/outcome-first,
без города и каталожной подачи. Экран C01 несёт три goal-like чипа и один
вторичный вход; тап по любому из них — обычное сообщение человека, а не
команда (см. модуль ``quick_actions``).

The S2 consent texts themselves are marketplace-neutral («Я буду помнить о тебе
только то, что поможет рекомендовать точнее…») and pass through unchanged.

### Tenant-safety

Everything here runs at ``current_tenant() is None`` (asserted in
:func:`run_onboarding_turn`). Consent is journaled server-side via
:func:`apps.consent.services.record_global_consent` (sentinel-scoped
``ConsentRecord`` + audit) without ever entering a tenant scope — the server
proof-of-consent the regulator needs, extending the food-scanner journal (#956).
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from apps.channels.max.quick_actions import render_first_contact
from apps.orchestrator.discovery import DiscoveryReply
from apps.tenancy.context import current_tenant

logger = logging.getLogger(__name__)

# Any ``cb:discover:*`` callback (today: the ``cb:discover:book:{tenant}:{master}``
# booking handoff, #1020) is NEVER onboarding — it must reach the booking flow
# even for a user whose ``welcomed_at`` is still NULL. Every global BotUser that
# predates this feature has ``welcomed_at IS NULL`` (the global path never stamped
# it), so without this guard the first post-flag-flip booking tap of an existing
# user would be swallowed by the welcome greeting.
#
# DRF-1304 adds ``cb:catalog:*`` (the salon / service chips) for exactly the same
# reason: those chips lead INTO the booking chain, and the greeting must not
# swallow a tap on a card the bot itself just drew.
_PASSTHROUGH_CALLBACK_PREFIXES = ("cb:discover:", "cb:catalog:")


# ---------------------------------------------------------------------------
# DRF-990, продолжение — чем тап приветствия был В ИСТОРИИ
# ---------------------------------------------------------------------------
#
# Маршрутизация выше по этому файлу — про ТЕКУЩИЙ ход: ``needs_onboarding``
# и ``WelcomeSkill.matches``/``handle`` разбирают именно payload, и он обязан
# доехать до них нетронутым. Историю же диалога консьерж читает на БУДУЩИХ
# ходах, и там строка «cb:welcome:consent_yes» с ролью ``user`` выглядит как
# то, что человек написал ему словами.
#
# Семейство шире анкеты: анкету открывают не все, приветствие проходит каждый
# новый пользователь пилота, поэтому сырой payload попадал в историю ВСЕМ.
#
# Выбран перевод в ФРАЗУ, а не пропуск, и довод структурный:
#
#   * вход в приветствие человек НАБИРАЕТ — «/start» или любую свободную
#     фразу, — и этот ход в историю попадает всегда. Дальше идут только
#     тапы. Пропуск оставил бы запись, где «привет» есть, а два решения,
#     которые человек принял («Да, продолжим» / «Не сейчас»), отсутствуют, —
#     ровно та асимметрия, из-за которой фразу дали и анкете (DRF-990);
#   * ответ бота на отказ («ничего запоминать не буду») записывается всегда.
#     Без реплики человека он читается как решение, принятое ботом самим;
#   * каждая кнопка этого экрана — человеческая фраза от первого лица, а не
#     id карточки. Поэтому подсемейства не выделяются и второй таблицы, по
#     которой пришлось бы решать «фраза или молчание», не заводится: метка
#     нажатой кнопки И ЕСТЬ то, чем тап был как реплика.
#
# Побочно: число строк в диалоге не меняется, а его СЧИТАЮТ два стража
# первого контакта (``_conversation_already_under_way`` ниже и
# ``WelcomeSkill._flow_already_established``, оба с порогом
# ``_FIRST_CONTACT_MESSAGE_ROWS``). Пропуск тапов сдвинул бы их порог молча.
#
# Разбирается ФОРМА, а не префикс: человек может НАБРАТЬ «cb:welcome: …»
# руками, и подменять ему его собственные слова нельзя (правило C01,
# ``apps/channels/tests/test_first_contact_c01.py``).

#: Строгая форма payload'а приветствия: один сегмент из ``[a-z0-9_]``.
#: Покрывает всё, что выкладывают строители клавиатур
#: :func:`apps.skills.welcome.skill.welcome_tap_labels`.
_WELCOME_CALLBACK_RE = re.compile(r"^cb:welcome:[a-z0-9_]+$")


@dataclass(frozen=True)
class WelcomeTap:
    """Разбор тапа приветствия глазами ИСТОРИИ диалога.

    ``history_text`` — фраза, которой этот тап является как реплика, или
    ``None``, если подставить нечего (снятая кнопка) и в историю не должно
    попасть ничего.
    """

    history_text: str | None


def resolve_welcome_tap(text: str) -> WelcomeTap | None:
    """Разобрать тап приветствия; ``None`` — «это не тап приветствия».

    ``None`` означает «обычное сообщение»: вызывающий не трогает ни текст
    хода, ни персистенс. Это важнее, чем кажется, — функция стоит перед
    записью в историю, и ошибка в сторону «это тап» стёрла бы человеку его
    собственную реплику либо подменила бы её.

    Нераспознанный, но правильной формы payload (кнопка, которую сняли) —
    это ``WelcomeTap(None)``: сырой ``cb:`` в истории — ровно тот дефект,
    который здесь чинится, а выдумать за человека фразу нечем.
    """
    stripped = (text or "").strip()
    if not _WELCOME_CALLBACK_RE.match(stripped):
        return None

    # Ленивый импорт, и ПОСЛЕ проверки формы: модуль ``WelcomeSkill`` при
    # загрузке дёргает @register (тянет apps.skills.registry) — держим это вне
    # пути загрузки, как и ``run_onboarding_turn`` ниже, и вне пути обычного
    # текстового хода, который сюда приходит на каждой реплике.
    from apps.skills.welcome.skill import welcome_tap_labels

    return WelcomeTap(history_text=welcome_tap_labels().get(stripped))


# Need/outcome-first welcome (DRF-1348, решение владельца 24.08 дословно:
# «GLOBAL welcome copy обязательно переписать: need/outcome-first, без города
# и каталожной подачи»).
#
# Было — «Помогу подобрать мастера по всей стране и записаться — маникюр,
# массаж, стрижка и не только». Это каталог: человеку предлагают выбрать
# позицию из списка раньше, чем спросили, что его беспокоит. Утверждённый
# макет C01 (v1.0 APPROVED) даёт другую первую реплику, и она здесь дословно.
#
# WelcomeSkill.WELCOME_TEXT по-прежнему не подходит, но по другой причине —
# он называет салон «Формула тела» и wellness-меню.
GLOBAL_WELCOME_TEXT = (
    "Привет! Я Ayla 👋\n\n"
    "Расскажи, чего тебе хочется или что сейчас беспокоит.\n"
    "Я помогу разобраться и предложу подходящий следующий шаг.\n\n"
    "Начнём?"
)

# Экран C01 — то, что человек видит сразу после согласия.
#
# Было — «Напиши услугу и город, например "маникюр в Пензе"». Города на
# первом экране больше нет: он спрашивается тогда, когда нужен для поиска
# исполнителя, а не как обязательное поле анкеты (решение владельца 24.08).
# Wellness-грид сюда по-прежнему не возвращается (см. :86 ниже по истории
# файла): человек маркетплейса не должен попадать в дневник еды.
#
# Приветствие здесь не повторяется: «Привет! Я Ayla 👋» человек прочитал
# ходом раньше, на экране согласия. На макете эти два экрана — один пузырь,
# потому что макет шага согласия не содержит (известное расхождение,
# зафиксировано в теле DRF-1348); в пилоте между ними стоит 152-ФЗ.
#
# Строка «Можно написать своими словами или выбрать пример:» и сами чипы
# добавляются рендером (``quick_actions.render_first_contact``), а не текстом
# этой константы — чтобы состояние **No Quick Actions** убирало подсказку
# вместе с чипами, а не оставляло обещание примеров без примеров.
GLOBAL_S5_TEXT = (
    "Расскажи, чего тебе хочется или что сейчас беспокоит.\n"
    "Я помогу разобраться и предложу подходящий следующий шаг."
)

# Single «Начать» button on the marketplace welcome — routes into the SHARED S2
# consent flow (WelcomeSkill handles ``cb:welcome:start_s2``). We drop the
# salon/wellness buttons WelcomeSkill would otherwise attach.
_START_BUTTON: list[dict[str, str]] = [{"label": "▶️ Начать", "callback": "cb:welcome:start_s2"}]

# reply_kind values (WelcomeSkill.meta["reply_kind"]) whose TEXT we replace with a
# marketplace surface. Everything else (S2 consent prompt, S2a details, refusal,
# ask/food/water prompts) passes through verbatim.
_WELCOME_KINDS = frozenset(
    {
        "welcome",
        "welcome_s1_multitenant",
    }
)

# «Возврат к диалогу» (макет C01, ДОПОЛНИТЕЛЬНЫЕ СОСТОЯНИЯ).
#
# DRF-1202 добавил в WelcomeSkill два состояния возврата — Returning User и
# User with an Active Task, — и их тексты («С возвращением! 👋 С чем помочь
# сегодня?» / «Мы кое-что не закончили — продолжим?») ровно то, что макет
# называет возвратом к диалогу.
#
# До сих пор этот путь их **выбрасывал**: они лежали в ``_WELCOME_KINDS``, и
# вернувшийся человек — уже поздоровавшийся, уже давший согласие — получал
# полное первое приветствие с кнопкой «▶️ Начать», ведущей в согласие,
# которое у него уже есть. Единственное состояние возврата на пилоте было
# неотличимо от первого контакта.
#
# Теперь текст WelcomeSkill проходит как есть (он маркетплейс-нейтрален), а
# клавиатура — та же, что на C01: возврат на верхний уровень, макет для него
# чипы предписывает («КОГДА ПОКАЗЫВАТЬ: всегда при начале нового диалога или
# при возврате на верхний уровень»).
#
# **Пока согласия нет — поведение прежнее.** «▶️ Начать» на этом пути
# единственный вход в 152-ФЗ; заменить его чипами у несогласившегося значило
# бы отобрать у него согласие вместе с кнопкой.
_RETURN_KINDS = frozenset(
    {
        "welcome_returning",
        "welcome_active_task",
    }
)
_S5_KIND = "welcome_s5_first_action"

# Source slug stamped on the server consent journal row for the global welcome.
_CONSENT_SOURCE = "global_onboarding:welcome_s2"

# Disclosure-version snapshot stamped on the consent journal (152-ФЗ informed
# consent: the row must prove WHICH text the user accepted, not just that they
# tapped «Да»). Tracks the WelcomeSkill S2 consent copy (S2_CONSENT_TEXT /
# S2A_DETAILS_TEXT); bump this slug whenever that copy changes materially.
CONSENT_DOCUMENT_VERSION = "welcome-s2-v1"


# DRF-1207 (второй путь) — сколько строк может держать ТЕКУЩИЙ разговор и
# всё ещё считаться первым контактом. `_handle_global_max_event_inner`
# записывает входящее (`record_global_message`) ДО ветвления ответа, ровно
# как per-tenant handler делает это до диспетчеризации, поэтому у настоящего
# первого контакта здесь одна строка. Правило то же, что в
# `apps/skills/welcome/skill.py::_flow_already_established`; продублировано
# значением, а не импортом приватного имени через границу пакета.
_FIRST_CONTACT_MESSAGE_ROWS = 1


def _conversation_already_under_way(conversation: Any) -> bool:
    """True когда разговор содержит больше одного сообщения.

    DRF-1207, второй путь. Глобальный путь не проходит через
    `WelcomeSkill.matches`, а значит и через его guard
    `_flow_already_established` — он зовёт `handle()` напрямую
    (`run_onboarding_turn`). Свой guard тут отсутствовал вовсе: признаком
    первого контакта считался только `welcomed_at IS NULL`.

    Последствие ровно то же, что на основном пути: если первый ход забрала
    другая ветка (safety, хендоф, карточки визитов, personal booking lookup
    — или, после DRF-1205, понятное намерение), `welcomed_at` не
    проставляется, и следующее не-намеренное сообщение получает полное
    приветствие посреди идущего разговора.

    Best-effort: сбой чтения не должен рушить ход — деградируем к «разговор
    не начат», то есть к прежнему поведению.
    """
    conversation_id = getattr(conversation, "id", None)
    if conversation_id is None:
        return False
    try:
        from apps.conversations.models import Message

        rows = Message.all_tenants.filter(conversation_id=conversation_id).count()
    except Exception:  # noqa: BLE001 — guard must never break the turn
        logger.exception(
            "global_onboarding.conversation_probe_failed conversation=%s",
            conversation_id,
        )
        return False
    return rows > _FIRST_CONTACT_MESSAGE_ROWS


def needs_onboarding(bot_user: Any, text: str, conversation: Any = None) -> bool:
    """Decide whether this global turn should run onboarding instead of discovery.

    True when any of (per #1046):

    * ``/start`` or ``/start <deeplink_payload>`` — explicit entry / deep link;
    * a ``cb:welcome:*`` callback tap — the user is mid-consent-flow (S2 prompt,
      consent yes/no, details fold);
    * first contact — the BotUser has ``welcomed_at IS NULL`` (never greeted),
      the message carries no clear actionable intent (DRF-1205), and the
      conversation is not already under way (DRF-1207, see
      :func:`_conversation_already_under_way`).

    False otherwise — an already-welcomed user's plain message (or a
    ``cb:discover:book:*`` handoff tap) flows straight to discovery. This is what
    makes the gate «soft»: after a greeting (or a consent refusal) the user can
    keep searching without re-entering onboarding.

    ### DRF-1205 — Intent Before Ceremony on the global path

    BOT-001 P1: «If the user's first message contains a clear actionable intent,
    Ayla MUST progress that intent immediately. Greeting or scripted introduction
    MUST NOT delay useful action.» §17 spells out the same decision as two rows:
    step 2 (first message contains clear actionable intent → progress it, skip
    the scripted greeting) vs step 3 (greeting only → contextual greeting).
    CDP-02 repeats it verbatim for every capability.

    The per-tenant path satisfies this by accident — the registry walks booking
    before welcome. This path had no such accident: it looked at ``/start``, two
    callback prefixes and ``welcomed_at``, never at what the user actually said,
    so «хочу массаж в Пензе» as a first message was swallowed by the greeting.

    The intent signal is the one this path ALREADY uses for exactly this class of
    turn — ``looks_like_booking_request`` (DRF-1102, handler branch 2.7). Reusing
    it keeps one definition of «booking-shaped turn» instead of inventing a
    second. It is deliberately narrow: an unrecognised first message still gets
    the greeting, which is the canon-correct outcome for a greeting-driven entry.

    Consent is not lost. Variant A is a soft gate by design (module docstring):
    consent is enforced by the memory writer, not by this greeting, and a user
    who opens with an intent still meets the greeting + consent offer on their
    next non-intent turn.

    A ``cb:discover:*`` callback (the booking handoff, #1020) or a ``cb:catalog:*``
    one (the salon / service chips, DRF-1304) is explicitly NOT onboarding even
    when ``welcomed_at IS NULL`` — a tap on a card the bot itself drew must reach
    what the card promised, never the welcome greeting. This matters at flag flip: every
    pre-existing global BotUser has ``welcomed_at IS NULL``, so without this guard
    their first booking tap after enabling the flag would be swallowed.
    """
    stripped = (text or "").strip()
    # A tap on a card the bot drew wins over onboarding, unconditionally.
    if stripped.startswith(_PASSTHROUGH_CALLBACK_PREFIXES):
        return False
    if stripped == "/start" or stripped.startswith("/start "):
        return True
    if stripped.startswith("cb:welcome:"):
        return True
    if getattr(bot_user, "welcomed_at", None) is not None:
        return False
    # DRF-1205 — намерение обходит церемонию (BOT-001 P1 / CDP-02).
    from apps.skills.menu.matching import looks_like_booking_request

    if looks_like_booking_request(stripped):
        return False
    # DRF-1207 (второй путь) — приветствие не просыпается посреди разговора.
    if conversation is not None and _conversation_already_under_way(conversation):
        return False
    return True


def run_onboarding_turn(
    conversation: Any,
    bot_user: Any,
    text: str,
    trace_id: str | uuid.UUID | None = None,
) -> DiscoveryReply:
    """Run one onboarding turn via WelcomeSkill and wrap it as a DiscoveryReply.

    Reuses :class:`WelcomeSkill` directly (see module docstring), swaps the two
    marketplace text surfaces, and — when this turn is the one that newly stamps
    ``consent_at`` — writes the server consent journal (#956 extension).

    Invariant: runs at ``current_tenant() is None`` (the global path never enters
    a tenant scope). This is load-bearing for the #1074 consent-atomicity fix:
    WelcomeSkill skips its own consent_at stamp exactly when ``current_tenant()``
    is None, deferring to ``record_global_consent``'s atomic write. If a tenant
    scope ever leaked in here, WelcomeSkill would stamp consent_at separately
    (non-atomic) and reopen the split-transaction bug — so we FAIL LOUD with a
    raise (not an ``assert``, which ``python -O`` would strip).
    """
    if current_tenant() is not None:
        raise RuntimeError(
            "global onboarding must run at current_tenant() is None; a leaked "
            "tenant scope would break the #1074 consent-atomicity invariant."
        )

    # Lazy import — WelcomeSkill's module runs @register at import time (pulls
    # apps.skills.registry). Keep it off this module's load path, mirroring the
    # handler's lazy skill import.
    from apps.skills.base import SkillContext
    from apps.skills.welcome.skill import WelcomeSkill

    ctx = SkillContext(
        conversation=conversation,
        bot_user=bot_user,
        message_text=text,
        trace_id=str(trace_id) if trace_id else "",
    )
    result = WelcomeSkill().handle(ctx)

    # Capture consent on the grant turn. WelcomeSkill renders the S5 first-action
    # surface only on the consent-grant callbacks (both consent_yes and
    # consent_yes_via_s2a funnel through ``_render_consent_granted``), so the S5
    # reply_kind is the reliable «consent granted this turn» signal. On this global
    # path WelcomeSkill does NOT stamp consent_at itself (current_tenant() is None
    # → its guard skips); record_global_consent stamps consent_at ATOMICALLY with
    # the ConsentRecord (#1074). Idempotent (get_or_create) — re-tapping «Да» never
    # duplicates and a repeat tap reconciles a consent_at a prior failure dropped.
    if _is_consent_grant_turn(result):
        _record_consent_journal(bot_user)

    return _to_discovery_reply(result, bot_user)


def _is_consent_grant_turn(result: Any) -> bool:
    """True when this WelcomeSkill turn is the one that grants consent (S5 render)."""
    return (getattr(result, "meta", None) or {}).get("reply_kind", "") == _S5_KIND


def _consent_captured(bot_user: Any) -> bool:
    """True когда 152-ФЗ согласие у этого пользователя есть СЕЙЧАС.

    Читается журнал, а не столбец ``consent_at``. Столбец —
    денормализованная отметка, которую ``apps.consent.services.withdraw``
    никогда не снимает (DRF-1314), то есть он отвечает «когда-либо давал»,
    а вопрос этого экрана другой: **нужен ли человеку вход в согласие
    сегодня**. Отозвавшему нужен — и «▶️ Начать» обязан вернуться на место.

    ``consent_blocker`` (то, на что указывает страж столбца) здесь тоже не
    подходит по смыслу: он отвечает «можно ли писать первым» и учитывает
    ``proactive_messages_opt_out``. Человек, отказавшийся от проактивных
    сообщений, согласие 152-ФЗ не отзывал, и показывать ему экран согласия
    заново было бы неверно. Нужен ровно активный грант PERSONAL_DATA —
    тот самый, который записывает :func:`_record_consent_journal` ходом
    выше по этому же файлу.

    Best-effort: чтение — запрос к базе, а сбой не должен рушить ход.
    Деградация — в сторону «согласия нет», то есть в прежнее поведение с
    кнопкой «▶️ Начать»: лишний раз предложить согласие безопаснее, чем
    молча решить, что оно есть.
    """
    try:
        from apps.consent.models import ConsentRecord
        from apps.consent.services import has_global_consent

        return has_global_consent(bot_user, ConsentRecord.ConsentType.PERSONAL_DATA.value)
    except Exception:  # noqa: BLE001 — consent probe must never break the turn
        logger.exception(
            "global_onboarding.consent_probe_failed bot_user=%s",
            getattr(bot_user, "id", None),
        )
        return False


def _consent_entry_reply() -> DiscoveryReply:
    """Приветствие с единственным входом в 152-ФЗ («▶️ Начать»)."""
    return DiscoveryReply(
        text=GLOBAL_WELCOME_TEXT,
        action_data={"buttons": _START_BUTTON, "button_columns": 1},
    )


def _to_discovery_reply(result: Any, bot_user: Any = None) -> DiscoveryReply:
    """Wrap a WelcomeSkill :class:`SkillResult` into a :class:`DiscoveryReply`.

    Swaps the marketplace text surfaces; otherwise passes ``reply_text`` +
    ``action_data`` through unchanged so ``_build_attachments`` renders the same
    keyboard it would on the per-tenant path.

    ### DRF-1348 — экран C01 наконец несёт кнопки

    До 24.08 ветка S5 возвращала ``DiscoveryReply(text=…)`` **без**
    ``action_data``, поэтому ``_build_attachments`` не рисовал ничего:
    замысел был сбросить wellness-грид WelcomeSkill, но вместе с гридом
    ушли все кнопки, а маркетплейсных на их место не поставили. Владелец
    прошёл от ``/start`` и не увидел ни одного чипа ровно поэтому.

    Wellness-грид не возвращается. Вместо него — Quick Actions макета C01:
    три goal-like чипа и вторичный вход, четыре кнопки при потолке в пять
    (BOT-001 AC-4.2 / DRF-1200).
    """
    reply_kind = (getattr(result, "meta", None) or {}).get("reply_kind", "")

    if reply_kind in _WELCOME_KINDS:
        return _consent_entry_reply()

    if reply_kind in _RETURN_KINDS:
        # «Возврат к диалогу». Без согласия — прежнее поведение: «Начать»
        # здесь единственный вход в 152-ФЗ (см. :data:`_RETURN_KINDS`).
        if not _consent_captured(bot_user):
            return _consent_entry_reply()
        text, action_data = render_first_contact(result.reply_text)
        return DiscoveryReply(text=text, action_data=action_data)

    if reply_kind == _S5_KIND:
        text, action_data = render_first_contact(GLOBAL_S5_TEXT)
        return DiscoveryReply(text=text, action_data=action_data)

    # S2 consent prompt / S2a details / refusal / ask-food-water prompts →
    # verbatim (their texts are already marketplace-neutral).
    return DiscoveryReply(text=result.reply_text, action_data=result.action_data)


def _record_consent_journal(bot_user: Any) -> None:
    """Capture consent server-side, ATOMICALLY (best-effort, loud on failure).

    ``record_global_consent`` writes the proof-of-consent ConsentRecord AND stamps
    ``bot_user.consent_at`` in one transaction (#1074), so on this global path
    consent_at is set iff the record exists — a transient failure rolls back both,
    never leaving consent_at set without proof. The call is wrapped best-effort so
    a failure doesn't break the user-facing reply; we log LOUD (``exception``)
    because a failure means NO consent was captured this turn (the S5 CTA still
    renders, but the user isn't marked consented — safe: nothing is persisted for
    them). Idempotent, so a subsequent consent tap re-attempts cleanly.

    ### Two types, ONE tap (DRF-1311)

    The S2 text the user accepts is, verbatim, a memory disclosure —
    «Я буду помнить о тебе только то, что поможет рекомендовать точнее.
    Хранится безопасно. Удалить можно в любой момент.», expanded by S2a
    («Запоминаю: твои сообщения мне, выбранные цели, питание и вода…»).
    MEMORY_FOUNDATION_DESIGN §8 q.2 decided accordingly: *«в пилоте
    активируем personal_data + memory_green»*. Only ``personal_data`` was
    ever written, so :func:`apps.consent.services.has_memory_consent`
    returned False for EVERY user and the read side
    (:mod:`apps.identity.services.personal_context`) was permanently
    ``BLOCKED_CONSENT`` — writes landed, nothing could be read back, and the
    Ayla declared-prefs PATCH bridge was blocked with them (live pilot,
    2026-08-23: ``memory_bridge.patch_blocked`` + five ``personal_context.
    gate_closed``). Both types are recorded here, under the SAME
    ``document_version`` — this records the scope the user was actually
    shown, it does not widen it.

    The two calls are deliberately NOT one transaction: ``memory_green``
    failing must not roll back the ``personal_data`` proof-of-consent. The
    withdraw side already treats them as a set (``withdraw_personal_data``
    cascades personal_data → memory_*, §8.4); this is the missing grant half.
    """
    from apps.consent.models import ConsentRecord
    from apps.consent.services import record_global_consent

    for consent_type in (
        ConsentRecord.ConsentType.PERSONAL_DATA.value,
        ConsentRecord.ConsentType.MEMORY_GREEN.value,
    ):
        try:
            record_global_consent(
                bot_user,
                consent_type=consent_type,
                source=_CONSENT_SOURCE,
                document_version=CONSENT_DOCUMENT_VERSION,
            )
        except Exception:  # noqa: BLE001 — journal failure must not break the reply
            logger.exception(
                "global_onboarding.consent_journal_failed bot_user_id=%s type=%s",
                getattr(bot_user, "id", None),
                consent_type,
            )
