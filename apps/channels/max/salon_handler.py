"""The salon bot's conversation — staff onboarding and the action menu.

### Why this is not the client handler

The client bot is a conversation: an LLM concierge that interprets what a
customer means. This one is a control panel. Staff need actions, not
dialogue — and the owner failed to book in the client bot on 2026-08-14
precisely because he had to type a long service name exactly right
(DRF-1070). So there is **no LLM here and no skill dispatch**: buttons,
codes, and short deterministic replies.

It is also a security boundary. Whatever this handler can do, a customer
cannot reach — they are talking to a different bot with a different token.

### The whole flow

    someone writes to the salon bot
        ↓
    is this the opening of a master-invitation link?
        yes → the invitation, whatever role they hold
        no  ↓
    do they already hold a role?
        yes → menu for that role
        no  → does the message look like an invite code?
                yes → redeem it → welcome + menu
                no  → ask for the code
                (a link `?start=inv_XXXX` arrives as the text
                 «/start inv_XXXX», so it lands in the same branch)

No FSM, no "awaiting code" state. There is nothing to get stuck in, and a
person who reopens the bot a week later is in exactly the same position as
one who never left.

### Why the invitation is read above the role cascade (DRF-1424)

The person opening a master invitation may already be somebody here —
most obviously the owner, inviting himself as a master. Below the
cascade he would be handed the staff menu and the invitation would
vanish without an error. #1332 found exactly that shape one layer up:
`/onboarding/master` was mounted under the master surface, the role
cascade routed the owner elsewhere first, and the invitation
disappeared silently. Reading the payload first is the same fix applied
to the same chain.

### What IS recorded (DRF-1061 step 0)

A typed line from someone who holds a role — and the reply to it — are
appended to their :class:`~apps.conversations.models.StaffAssistantThread`.
Button taps are not: they are not speech, and the customer path already
paid for treating raw callback payloads as if they were (DRF-988).

That history is the foundation the assistant is built on in step 1. It
does NOT make this handler a dialogue yet: the reply is still the menu.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid

from apps.channels.bot_context import bot_scope
from apps.channels.max import outbound
from apps.channels.max.parser import CanonicalEvent, ParseError, parse_max_webhook
from apps.channels.max.staff_menu import (
    CB_APPROVE_PREFIX,
    CB_DAY,
    CB_REQUESTS,
    OPEN_APP_PAYLOAD,
    menu_attachments,
    menu_header,
)
from apps.events.services import emit
from apps.identity.services.identity_card import WHOAMI_COMMAND, build_card, render_for_person
from apps.identity.services.role_resolver import resolve_role
from apps.identity.services.staff_invites import (
    InviteError,
    MasterAlreadyLinked,
    PersonAlreadyMaster,
    InviteMasterMissing,
    InviteNotFound,
    InviteRateLimited,
    OwnerAlreadyExists,
    looks_like_code,
    redeem_staff_invite_by_identity,
)
from apps.tools.idempotency import AlreadyClaimed, with_idempotency

logger = logging.getLogger(__name__)

#: Deep-link payload prefix: `max://bot/<bot>?start=inv_AYLA7K3M`. The
#: parser folds that into the synthetic text «/start inv_AYLA7K3M».
DEEPLINK_PREFIX = "inv_"

#: Canonical UUID, anchored — the only tail a master invitation can have.
#:
#: The Mini App applies the identical rule to the same slug
#: (``_MASTER_INVITE_RE`` in ``apps/miniapp/src/lib/max-sdk.ts``), and for
#: the same reason: a start link is public, so whatever a stranger can
#: type after ``?start=`` arrives here. `master_invite_<uuid>?src=x` must
#: be refused as a token rather than echoed into an ``open_app`` button,
#: which MAX answers with HTTP 400 `proto.payload` — an error that lands
#: on the consumer, not on whoever crafted the link.
_INVITE_TOKEN_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

#: The stream this handler is registered on. Used to pick OUR registry
#: entry: a tenant may legitimately have more than one bot (a per-tenant
#: client bot and this one), and matching on tenant alone would return
#: whichever was declared first in MAX_BOTS — quite possibly the client
#: bot, whose token cannot even post to this chat.
SALON_STREAM = "max_salon"

ASK_FOR_CODE = (
    "Это рабочий бот салона.\n\n"
    "Пришлите код приглашения — его выдаёт администратор салона. "
    "Код выглядит так: AYLA-7K3M."
)

#: Payload кнопки «я работаю сам». Отдельный слаг, а не свободный текст:
#: свободный текст пришлось бы угадывать, а угаданное «да» — это
#: регистрация человека, который её не просил.
SOLO_REGISTER_CALLBACK = "cb:solo:register"

SOLO_OFFER = (
    "\n\nЕсли кода у вас нет и вы работаете сами, без салона, — можно завести свой кабинет."
)

SOLO_OFFER_BUTTON = "Я работаю сам"

# ─── Регистрация соло: имя → город → сводка → «Создать мой профиль» (DRF-1793, M1) ───
#
# Слово владельца 12.09 (PROMPT §12): кабинет не создаётся до явного
# подтверждения; имя из MAX — prefill, который можно исправить; город —
# из контролируемого списка, не свободный текст. Все шаги — callback-кнопки
# с префиксом ``cb:solo:``; единственный свободный ввод — имя, и его дверь
# принимает только когда у личности есть черновик на шаге «имя».
SOLO_NAME_KEEP_CALLBACK = "cb:solo:name:keep"
SOLO_NAME_EDIT_CALLBACK = "cb:solo:name:edit"
SOLO_CITY_CALLBACK_PREFIX = "cb:solo:city:"
SOLO_CONFIRM_CALLBACK = "cb:solo:confirm"
SOLO_CANCEL_CALLBACK = "cb:solo:cancel"

SOLO_ASK_NAME = (
    "Заведём ваш кабинет. Сначала — имя, которое увидят клиенты.\n\nНапишите его сообщением."
)
SOLO_ASK_NAME_WITH_PREFILL = (
    "Заведём ваш кабинет. Сначала — имя, которое увидят клиенты.\n\n"
    "Оставить «{name}» — нажмите кнопку, или напишите другое имя сообщением."
)
SOLO_NAME_KEEP_BUTTON = "Оставить «{name}»"
SOLO_NAME_REJECTED = {
    "empty": "Имя пустое. Напишите имя, которое увидят клиенты.",
    "too_short": "Слишком коротко. Напишите имя хотя бы из двух букв.",
    "too_long": "Слишком длинно — до 80 символов.",
    "no_letters": "В имени нужны буквы. Напишите имя, которое увидят клиенты.",
    "looks_like_a_command": "Это похоже на команду, а не на имя. Напишите имя сообщением.",
}
SOLO_ASK_CITY = "В каком городе вы принимаете клиентов? Выберите из списка."
SOLO_NO_CITIES = (
    "Пока не задан список городов, в которых работает Ayla, — регистрацию "
    "продолжить нельзя. Напишите в поддержку Ayla."
)
SOLO_CITY_UNKNOWN = "Такого города в списке нет. Выберите город кнопкой."
SOLO_SUMMARY = (
    "Проверьте:\n\n"
    "Имя: {name}\n"
    "Город: {city}\n\n"
    "Нажмите «Создать мой профиль» — и кабинет будет создан. "
    "До этого ничего не создаётся."
)
SOLO_CONFIRM_BUTTON = "Создать мой профиль"
SOLO_EDIT_NAME_BUTTON = "Изменить имя"
SOLO_CANCEL_BUTTON = "Отмена"
SOLO_CANCELLED = "Хорошо, ничего не создано. Если передумаете — нажмите «Я работаю сам»."
#: Кнопка без черновика (протух, отменён, или нажата не по порядку) —
#: не гадать, а начать заново.
SOLO_DRAFT_MISSING = (
    "Регистрация не начата или устарела. Нажмите «Я работаю сам», чтобы начать заново."
)

#: DRF-1766 — a person with a role in several salons is asked, not guessed for.
#: The tap comes back as ``cb:salon:choose:<tenant slug>``; the answer is kept
#: per identity for the length of a conversation, not written anywhere.
CB_SALON_CHOOSE_PREFIX = "cb:salon:choose:"
SALON_CHOICE_PROMPT = "У вас есть роль в нескольких салонах. В каком вы сейчас?"
SALON_CHOICE_TTL_SECONDS = 12 * 3600

#: §122: регистрация НЕ завершается как «готово». Текст говорит ровно то,
#: что произошло, и ровно то, чего ждать, — потому что произошло не всё.
#:
#: «Кабинет создан» без второй половины было бы той самой тишиной, ради
#: которой заводилось `setup_state`: человек ушёл бы считать себя
#: работающим и узнал бы обратное, не дождавшись ни одной записи.
SOLO_CREATED_PENDING = (
    "Кабинет создан: вы владелец и мастер в нём.\n\n"
    "Пока вас не видно клиентам — нужно связать кабинет с вашей учётной "
    "записью Ayla, это делает администратор. Напишите в поддержку салона, "
    "с которым работаете, или в поддержку Ayla.\n\n"
    "Записи к вам начнут приходить сразу после связывания."
)

#: Повторное нажатие — не ошибка и не повод молчать.
SOLO_ALREADY_REGISTERED = (
    "Кабинет у вас уже есть. Он ещё не связан с учётной записью Ayla — "
    "поэтому клиентам вас пока не видно."
)

SOLO_FAILED = (
    "Не получилось завести кабинет. Мы записали ошибку и разберёмся — "
    "напишите в поддержку, если ответа не будет сегодня."
)

CODE_NOT_ACCEPTED = (
    "Код не подошёл. Возможно, он уже использован или истёк — "
    "попросите администратора выдать новый."
)

# NB: the deeplink path goes through the very same limiter (same key,
# keyed on the person, not on how they entered the code), so this must not
# suggest a link as a way around the wait — it would send someone to tap a
# link that fails identically.
TOO_MANY_ATTEMPTS = (
    "Слишком много попыток ввода кода. Попробуйте через час — "
    "или попросите администратора выдать новый код."
)

MASTER_GONE = (
    "Приглашение указывает на карточку мастера, которой больше нет. Сообщите администратору салона."
)

# DRF-1647 / DRF-1650. Оба исхода до этого попадали в общую ветку
# `except InviteError` и получали `CODE_NOT_ACCEPTED` — то есть ложились в
# ту же кучу, что «неверный код», «истёк» и «чужой салон». Молчание там
# уже вылечено, но куча осталась, а лекарства у этих двоих разные: одному
# нужен СВОЙ код, другому никакой код не поможет.
#
# Довод про догадки сюда не переносится. `CODE_NOT_ACCEPTED` туманен
# намеренно: код из четырёх знаков угадываем, и подсказка «почти» была бы
# утечкой. Эти два отказа наступают ПОСЛЕ того, как код признан верным, —
# гадать уже нечего, и туман отнимает у человека единственное, что ему
# нужно знать: просить новый код или перестать пробовать.
#
# Чего в текстах намеренно НЕТ: обещания, что администратору что-то
# придёт. При обоих отказах ему не приходит ничего, только строка в лог.
# «Напишите администратору» — указание человеку, а не обещание
# уведомления; обещанное и не случившееся хуже неназванного.
#
# Тексты предложены исполнителем и владельцем не утверждены.
WRONG_RECIPIENT = (
    "Этот код не для вас: карточка мастера уже привязана к другому "
    "аккаунту. Попросите администратора салона выдать код именно на вас."
)

PERSON_ALREADY_MASTER = (
    "Ваш аккаунт уже привязан к другой карточке мастера. Новый код здесь "
    "не поможет — сначала нужно снять прежнюю привязку. Напишите об этом "
    "администратору салона."
)

OWNER_TAKEN = (
    "У салона уже есть владелец. Если владельца нужно сменить — "
    "это делается через поддержку, а не новым кодом."
)

# --- master invitation, opened by a start link (DRF-1424) -----------------
#
# Why these three refusals say what is wrong, while CODE_NOT_ACCEPTED above
# deliberately does not:
#
# A staff code is four characters and guessable, so telling someone which
# guess was close is a real leak — hence the hedge. An invite token is a
# UUIDv4: 122 bits, unguessable, and anyone reading one of these sentences
# is holding a link that was handed to them. Vagueness buys nothing there
# and costs the invited master the only thing they need to know — whether
# to ask for a new link or to stop trying.
#
# The one thing still deliberately collapsed is «not for this salon» into
# «not found»: distinguishing them would turn the bot into an oracle for
# whether a token is live somewhere else. That is the same collapse
# `validate_invite_token` already makes.

INVITE_WELCOME = (
    "Салон «{salon}» приглашает вас как мастера.\n\n"
    "Нажмите кнопку ниже — анкета откроется прямо здесь, в MAX.\n\n"
    "Приглашение действительно 7 дней."
)

INVITE_BUTTON_LABEL = "Принять приглашение"

INVITE_NOT_FOUND = (
    "Это приглашение не найдено. Возможно, ссылка скопирована не целиком "
    "или относится к другому салону — попросите администратора прислать её заново."
)

INVITE_EXPIRED = "Срок действия приглашения истёк. Попросите администратора салона выдать новое."

INVITE_ALREADY_USED = (
    "Это приглашение уже принято. Если мастер — вы, откройте кабинет из меню бота; "
    "если нет — попросите администратора выдать новое."
)

#: No Mini App name for this bot, so no button can be built. The invite
#: endpoint used to refuse the same way (`no_entry_configured`) before
#: §44.4 removed its message entirely; this rung is the one that
#: remains, and it answers the same way: say so rather than send a
#: message that looks like an invitation and does nothing. An https address is NOT offered as a
#: consolation — outside MAX it gets no `initData` and cannot work, and
#: #1332 removed exactly that promise after the owner followed it.
INVITE_NO_ENTRY = (
    "Приглашение получено, но бот пока не настроен на открытие анкеты. "
    "Сообщите администратору салона."
)

ROLE_GREETING = {
    "owner": "Готово, вы владелец салона «{salon}».",
    "admin": "Готово, вы администратор салона «{salon}».",
    "receptionist": "Готово, вы на ресепшене салона «{salon}».",
    "master": "Готово, вы мастер салона «{salon}».",
}

ALREADY_HAVE_ROLE = "У вас уже есть доступ к салону «{salon}»."

#: Fallback for a role added later without a greeting of its own. Says
#: "granted", not "already had" — inverting that would tell someone who
#: just gained access that nothing happened.
ROLE_GRANTED_GENERIC = "Готово, доступ к салону «{salon}» открыт."


def handle_salon_max_event(payload: dict, trace_id: str | uuid.UUID | None = None) -> None:
    """Process one MAX webhook addressed to the salon bot.

    Sibling of :func:`handle_max_event` / :func:`handle_global_max_event`.
    Called by ``SalonMaxHandler`` after the consumer has entered
    ``trace_id_scope`` and ``tenant_scope`` for the bot's tenant.

    Unsupported update types are tolerated and skipped, same contract as
    the other two handlers, so lifecycle updates do not retry-storm the PEL.
    """

    try:
        event = parse_max_webhook(payload)
    except ParseError as exc:
        logger.info(
            "channels.max.salon.skipped_unsupported update_type=%r reason=%s",
            (payload or {}).get("update_type") if isinstance(payload, dict) else None,
            exc,
        )
        return

    # Own idempotency namespace: a salon update and a client update must
    # never claim the same key, or one of them silently vanishes.
    callback_id = (event.raw or {}).get("callback_id", "") if isinstance(event.raw, dict) else ""
    if callback_id:
        idempotency_key = f"webhook:max_salon:callback:{callback_id}"
    elif event.channel_message_id:
        idempotency_key = f"webhook:max_salon:{event.channel_message_id}"
    else:
        # Never fall back to channel_user_id alone. The claim lives 24h, so
        # a single update missing both `mid` and `seq` would swallow every
        # later message from that person for a day — and their whole
        # onboarding is one message. Hash the content instead, which still
        # dedups a genuine redelivery but not a new message.
        digest = hashlib.sha256(
            f"{event.channel_user_id}:{event.timestamp}:{event.text}".encode()
        ).hexdigest()[:32]
        idempotency_key = f"webhook:max_salon:synthetic:{digest}"

    try:
        with with_idempotency(idempotency_key, ttl_seconds=86_400):
            _handle_salon_event_inner(event, trace_id)
    except AlreadyClaimed:
        logger.info(
            "channels.max.salon.dedup_short_circuit channel_message_id=%s",
            event.channel_message_id,
        )
        return


def _extract_code(text: str) -> str | None:
    """Pull an invite code out of whatever the person sent.

    Two shapes, one meaning:

    * ``/start inv_AYLA7K3M`` — they tapped a link, no typing involved;
    * ``AYLA-7K3M`` / ``ayla 7k3m`` / ``7K3M`` — they typed or pasted it.

    Returns ``None`` for ordinary chat, so «привет» is not counted as a
    failed attempt against the rate limit.
    """

    cleaned = (text or "").strip()
    if not cleaned:
        return None

    if cleaned.startswith("/start"):
        remainder = cleaned[len("/start") :].strip()
        if remainder.startswith(DEEPLINK_PREFIX):
            candidate = remainder[len(DEEPLINK_PREFIX) :]
            return candidate if looks_like_code(candidate) else None
        return None

    return cleaned if looks_like_code(cleaned) else None


def _invite_prefix() -> str:
    """The master-invitation slug, from the one place that declares it.

    Imported lazily, and that is not a style choice: ``views_invite``
    imports ``apps.channels.max.outbound``, so a module-scope import here
    would close the cycle. Every other cross-app reference in this file
    is lazy for related reasons.

    Not restated as a literal either. #1332 made this constant a contract
    between the bot and the Mini App's TypeScript, pinned by
    ``apps/admin_api/tests/test_invite_entry.py``; a copy here would be a
    third spelling free to drift out from under both.
    """

    from apps.admin_api.views_invite import MASTER_INVITE_PAYLOAD_PREFIX

    return MASTER_INVITE_PAYLOAD_PREFIX


def _extract_invite_token(text: str) -> str | None:
    """The master-invite token in a start-link opening, if there is one.

    MAX delivers ``?start=<payload>`` as the ``payload`` field of the
    ``bot_started`` event (verified on the pilot 30.08, stream
    ``ingress:max_salon``), and the parser folds it into the synthetic
    text «/start master_invite_<uuid>» — the same shape a typed
    «/start ...» would produce, so both are read here.

    Returns ``None`` for anything that is not exactly the prefix plus a
    canonical UUID: an ordinary «/start», an attribution deeplink
    (``ref_user_42``, the welcome skill's business), or a crafted tail.
    """

    cleaned = (text or "").strip()
    if not cleaned.startswith("/start"):
        return None
    remainder = cleaned[len("/start") :].strip()

    prefix = _invite_prefix()
    if not remainder.startswith(prefix):
        return None

    candidate = remainder[len(prefix) :]
    return candidate if _INVITE_TOKEN_RE.match(candidate) else None


def _handle_master_invite(event: CanonicalEvent, token: str, entry) -> None:
    """Answer an invitation link with the way into the invitation.

    ### What this does NOT do, and why that is the design

    It does not link the master, does not consume the token, and grants
    no role. Opening a link proves possession of the link and nothing
    else: ``bot_started`` carries ``user_id`` but no MAX username, while
    an invitation is addressed by ``max_handle`` — so there is nothing
    here to match the opener against the invitee, and pretending
    otherwise would mean binding a salon's master row to whoever was
    forwarded a message.

    So the bot delivers and stops. The binding happens where it can
    actually be checked: ``/onboarding/claim`` and ``/onboarding/accept``
    run inside a verified Mini App session and already refuse a forwarded
    link (``wrong_recipient``, 403, when the row is linked to somebody
    else). Leaving the token unspent is what keeps the rightful invitee
    able to accept after a stranger has opened the link.

    ### Ownership that CAN be decided here

    Which salon the token belongs to — the token says (DRF-1784, 12.09.2026).
    Until that day ``validate_invite_token`` was filtered by the tenant the
    consumer had entered from the bot's registry entry: «the salon bot is
    tenant-bound by construction». The owner reversed the premise (the bot
    serves every salon), so the token is resolved alone and the card names
    its salon; a UUIDv4 token never needed the tenant for security.

    The call is a locking read inside ``atomic`` (the function does
    ``select_for_update``), exactly as ``/onboarding/claim`` uses it. It
    mutates nothing, so re-opening the link is idempotent.

    ### Why there is no rate limit here, unlike the staff-code path

    ``redeem_staff_invite`` counts attempts because a staff code is four
    characters: guessing is a real strategy against it. A UUIDv4 is 122
    bits, so there is no guessing to slow down, and every message to this
    bot already costs the same DB round-trips before this branch is
    reached (identity resolution, role resolution) — the lookup adds no
    new amplification. What a limiter WOULD add is a way to lock out a
    master who tapped the link twice, which is the ordinary behaviour of
    someone who is not sure the first tap registered.
    """

    from django.db import transaction

    from apps.master_api.auth import (
        InvalidInviteToken,
        InviteAlreadyUsed,
        InviteExpired,
        InviteTokenError,
        validate_invite_token,
    )

    try:
        with transaction.atomic():
            master = validate_invite_token(token)
    except InviteExpired:
        _reply(event, INVITE_EXPIRED)
        return
    except InviteAlreadyUsed:
        _reply(event, INVITE_ALREADY_USED)
        return
    except InvalidInviteToken:
        _reply(event, INVITE_NOT_FOUND)
        return
    except InviteTokenError as exc:  # future slugs — never leak an exception text
        logger.warning("channels.max.salon.invite_rejected slug=%s", getattr(exc, "slug", "?"))
        _reply(event, INVITE_NOT_FOUND)
        return

    tenant = master.tenant
    web_app = getattr(entry, "web_app", "")
    if not web_app:
        # Nothing to build a button from, and no address worth offering:
        # a Mini App is entered through `initData`, which MAX hands only
        # to its own webview. Saying so beats sending an invitation that
        # cannot be opened — the failure this whole chain exists to stop.
        logger.error(
            "channels.max.salon.invite_no_web_app tenant=%s — invitation opened but "
            "no Mini App name for this bot (registry entry `web_app`, i.e. "
            "MAX_BOT_<SLUG>_WEB_APP); the invited master has no way in.",
            tenant.slug,
        )
        _reply(event, INVITE_NO_ENTRY)
        return

    salon = tenant.name or tenant.slug
    attachment = outbound.make_inline_keyboard_attachment(
        [
            {
                "label": INVITE_BUTTON_LABEL,
                "callback": f"{_invite_prefix()}{token}",
                "web_app": web_app,
            }
        ]
    )

    # Same shape as `invite_redeemed` below: the internal id, never the
    # raw MAX user id. Nothing about who was invited goes on the bus —
    # the token is a credential and the handle is the invitee's contact
    # detail, and neither is needed to count openings.
    emit(
        "channels.max.salon.invite_link_opened",
        payload={"channel": event.channel, "channel_user_id": str(event.channel_user_id)},
    )

    _reply(event, INVITE_WELCOME.format(salon=salon), attachments=[attachment])


def _handle_salon_event_inner(event: CanonicalEvent, trace_id: str | uuid.UUID | None) -> None:
    """Resolve who is speaking, then either onboard them or show the menu.

    The tenant comes FROM THE PERSON, not from the bot's registry entry
    (DRF-1783, срез 4a of DRF-1705; owner 12.09.2026: the salon bot does
    not belong to a salon). Order:

    1. **A working row** — the one ``BotUser`` of this MAX identity that
       carries a staff role or a live master card
       (:func:`resolve_working_bot_user`, DRF-1755). Its tenant is the
       tenant; the staff flow runs in ``tenant_scope`` of THAT row. A solo
       master therefore lands in her own workspace, and no ``customer``
       row is created for her in the salon the entry happens to name —
       until this slice that row was created first thing, for everyone.
    2. **No working row — the stranger path** (:func:`_serve_stranger`,
       DRF-1784, owner D2 → б): no ``BotUser`` is created until the person
       proves a path. A staff code decides the tenant by itself and the row
       is born there; «Я работаю сам» creates the solo workspace from the
       event alone; anything else is answered by identity, without a row.
       The tenant of the registry entry is not read on this path at all.
    """

    from apps.identity.services.bot_user_resolver import (
        SalonChoiceRequired,
        resolve_working_bot_user,
    )
    from apps.tenancy.context import tenant_scope

    # DRF-1766: a tap on «which salon» is the answer to the question below —
    # remember it for this identity, then resolve with it. A tap naming a
    # salon the person has no role in is not honoured (the resolver checks),
    # and the question is simply asked again.
    chosen = _remembered_salon_choice(event)
    if event.text.startswith(CB_SALON_CHOOSE_PREFIX):
        chosen = event.text[len(CB_SALON_CHOOSE_PREFIX) :].strip() or None
        _remember_salon_choice(event, chosen)

    try:
        working = resolve_working_bot_user(
            event.channel_user_id, surface="salon_bot", chosen_slug=chosen
        )
    except SalonChoiceRequired as exc:
        _ask_which_salon(event, exc.tenants)
        return
    if working is not None:
        with tenant_scope(working.tenant):
            _serve(event, trace_id, tenant=working.tenant, bot_user=working)
        return

    # No working row → the stranger path, WITHOUT a row (DRF-1784, D2 → б).
    # The tenant the consumer may still have entered from the registry entry
    # (``MAX_BOT_SALON_TENANT_SLUG``, until срез 4c) is deliberately not read
    # here: a stranger's tenant is decided by the code they type, or by
    # «Я работаю сам» — never by the salon the entry happens to name.
    _serve_stranger(event, trace_id)


def _salon_choice_key(event: CanonicalEvent) -> str:
    return f"salon_choice:{event.channel}:{event.channel_user_id}"


def _remembered_salon_choice(event: CanonicalEvent) -> str | None:
    """The salon this identity chose earlier in the conversation, or ``None``."""

    from django.core.cache import cache

    try:
        value = cache.get(_salon_choice_key(event))
    except Exception:  # noqa: BLE001 — a cache outage means «ask again», never a crash
        return None
    return str(value) if value else None


def _remember_salon_choice(event: CanonicalEvent, slug: str | None) -> None:
    from django.core.cache import cache

    try:
        if slug:
            cache.set(_salon_choice_key(event), slug, timeout=SALON_CHOICE_TTL_SECONDS)
        else:
            cache.delete(_salon_choice_key(event))
    except Exception:  # noqa: BLE001
        logger.warning(
            "channels.max.salon.choice_cache_unavailable channel_user_id=%s", event.channel_user_id
        )


def _ask_which_salon(event: CanonicalEvent, tenants) -> None:
    """One button per salon the person holds a role in, in the resolver's order (DRF-1766).

    Answered as the salon bot — the one on ``max_salon`` — because the person
    has not chosen a tenant yet and the bot does not belong to one.
    """

    from apps.channels.bot_registry import effective_registry, resolve_by_stream

    entry = resolve_by_stream(SALON_STREAM, effective_registry())
    if entry is None:
        logger.error(
            "channels.max.salon.no_salon_bot channel_user_id=%s — cannot ask which salon",
            event.channel_user_id,
        )
        return
    buttons = [
        {"label": (t.name or t.slug), "callback": f"{CB_SALON_CHOOSE_PREFIX}{t.slug}"}
        for t in tenants
    ]
    with bot_scope(entry):
        _reply(
            event,
            SALON_CHOICE_PROMPT,
            attachments=[outbound.make_inline_keyboard_attachment(buttons, columns=1)],
        )


def _serve(event: CanonicalEvent, trace_id: str | uuid.UUID | None, *, tenant, bot_user) -> None:
    """The salon bot's conversation for a person WITH a working row in ``tenant``.

    Invite link, whoami, buttons, talk, menu — the staff flow. ``bot_user``
    is the working row (DRF-1755): it carries a role in ``tenant`` by
    construction, so the «no role yet» branches live in
    :func:`_serve_stranger`, not here.
    """

    from apps.channels.bot_registry import effective_registry, resolve_by_slug

    entry = resolve_by_slug(_bot_slug_for(tenant), effective_registry())
    if entry is None:
        # Refuse to answer rather than answer as the wrong bot.
        #
        # `bot_scope(None)` is not neutral: outbound falls back to
        # settings.MAX_BOT_TOKEN, which is the CLIENT bot. A staff member
        # would get their salon reply from the customer-facing avatar —
        # invisible in logs, obvious and alarming to them. Silence is the
        # better failure, and the ERROR says exactly what to fix.
        logger.error(
            "channels.max.salon.no_registry_entry tenant=%s — refusing to reply; "
            "declare a bot with MAX_BOT_<SLUG>_TENANT_SLUG=%s",
            tenant.slug,
            tenant.slug,
        )
        return

    with bot_scope(entry):
        # Read the invitation BEFORE resolving roles. Someone opening a
        # master invitation may already hold a role here — the owner
        # inviting himself is the ordinary case — and below the cascade
        # he would get the staff menu while the invitation vanished
        # without an error. See the module docstring.
        invite_token = _extract_invite_token(event.text)
        if invite_token is not None:
            _handle_master_invite(event, invite_token, entry)
            return

        if event.text.strip() == WHOAMI_COMMAND:
            # Owner 11.09 §12.3 — the person's own card in THIS salon: role
            # here, master card here, dates; other salons as a number. Before
            # the role cascade so a person with no role yet (the ones §12 is
            # about) gets an answer instead of «введите код».
            _reply(
                event,
                render_for_person(
                    build_card(bot_user.channel, bot_user.channel_user_id),
                    tenant_slug=tenant.slug,
                ),
            )
            return

        role_ctx = resolve_role(bot_user)

        if role_ctx.primary_role == "customer":
            # The working row lost its role between resolution and here (a
            # revoke racing this message). Not an error to the person: they
            # are a stranger now, and the stranger path answers strangers.
            logger.info(
                "channels.max.salon.working_row_lost_its_role bot_user=%s tenant=%s",
                bot_user.id,
                tenant.slug,
            )
            _serve_stranger(event, trace_id)
            return

        # A button tap arrives as the callback payload in `text`.
        if event.text.startswith(CB_SALON_CHOOSE_PREFIX):
            # DRF-1766: the person just chose THIS salon — open with its menu.
            _send_menu(event, role_ctx, tenant, entry)
        elif _is_button_tap(event.text):
            _handle_button(event, role_ctx, bot_user, tenant, entry)
        else:
            _handle_talk(event, role_ctx, bot_user, tenant, entry)


def _serve_stranger(event: CanonicalEvent, trace_id: str | uuid.UUID | None) -> None:
    """The salon bot's conversation for a person with NO working row — and no row at all.

    Owner D2 (12.09.2026) → (б): a ``BotUser`` is not created for a stranger
    until they prove a path. Two paths prove it: a staff code (the tenant is
    the code's; the row is born there, inside the redemption) and «Я работаю
    сам» (``create_solo_provider`` builds the solo tenant and its row from
    the event alone). Everything else — the code prompt, ``/whoami``, an
    invitation link, a refused code, the attempt brake — is answered by
    identity ``(channel, channel_user_id)``, leaving nothing behind.

    The bot is the one on the ``max_salon`` stream (DRF-1726); the tenant of
    its registry entry is not consulted here.
    """

    from apps.channels.bot_registry import effective_registry, resolve_by_stream

    entry = resolve_by_stream(SALON_STREAM, effective_registry())
    if entry is None:
        logger.error(
            "channels.max.salon.no_salon_bot channel_user_id=%s — refusing to reply; "
            "declare a bot with MAX_BOT_<SLUG>_STREAM=%s",
            event.channel_user_id,
            SALON_STREAM,
        )
        return

    identity = _Identity.of(event)
    with bot_scope(entry):
        invite_token = _extract_invite_token(event.text)
        if invite_token is not None:
            _handle_master_invite(event, invite_token, entry)
            return

        if event.text.strip() == WHOAMI_COMMAND:
            # By identity, no salon to name «here»: the person has none yet.
            _reply(
                event,
                render_for_person(
                    build_card(identity.channel, identity.channel_user_id), tenant_slug=None
                ),
            )
            return

        # A stray button tap from someone who lost their access must not be
        # read as an invite code — it would burn a rate-limit attempt for a
        # message they did not type.
        if _is_button_tap(event.text):
            if _solo_registration_step(event, entry=entry):
                return
            _reply(event, ASK_FOR_CODE)
            return

        code = _extract_code(event.text)
        if code is None:
            # DRF-1793: свободный текст — это имя ТОЛЬКО когда у личности
            # есть живой черновик на шаге «имя». Код всегда старше: тот, кто
            # прислал AYLA-XXXX посреди регистрации, хотел войти по коду.
            if _solo_registration_takes_name(event):
                return
            _ask_for_code_with_solo_offer(event, entry=entry)
            return

        _redeem_and_greet(event, code, entry)


class _Identity:
    """The messenger identity of an event — what the stranger path knows about a person.

    Same attribute names as ``BotUser`` (``channel``, ``channel_user_id``,
    ``display_name``, ``chat_id``) so the identity-level helpers below —
    ``_already_has_a_solo_workspace``, ``_solo_identity_rejected`` — accept
    either a row or this, and the difference is not a second code path.
    """

    def __init__(self, channel: str, channel_user_id: str, display_name: str, chat_id: str) -> None:
        self.channel = channel
        self.channel_user_id = channel_user_id
        self.display_name = display_name
        self.chat_id = chat_id

    @classmethod
    def of(cls, event: CanonicalEvent) -> "_Identity":
        return cls(
            channel=event.channel,
            channel_user_id=event.channel_user_id,
            display_name=_sender_name(event),
            chat_id=str(event.chat_id or ""),
        )


def _has_a_master_card_anywhere(identity) -> bool:
    """Есть ли у этого человека карточка мастера хоть в одном тенанте — в том числе архивная.

    В путь незнакомца приходят ДВОЕ: человек, который здесь впервые, и
    мастер, чью карточку сняли. Второго `resolve_role` называет клиентом,
    потому что архивация пишет `is_active` и `archived_at` и **оставляет**
    `linked_bot_user` (DRF-1654). Предложить ему завести кабинет соло-мастера
    значило бы развести одного человека на два тенанта при живом следе в
    первом — и сделать это в тот момент, когда он пришёл разбираться, почему
    его сняли.

    До DRF-1784 предикат звался `_has_a_master_card_here` и смотрел одну
    строку — салонную, которую бот создавал первым сообщением. Строки
    больше нет; смотрим по личности: каждую строку человека — в её же
    тенанте, тенантным менеджером (сквозное чтение каталога живёт в
    `apps/marketplace`, MKT1). Без строк — `False` по построению.

    Считается только СНЯТАЯ карточка (`archived_at` или `is_active=False`).
    Живая карточка делает строку рабочей, и такой человек на путь
    незнакомца не попадает вовсе (срез 4a); в прямых вызовах предиката
    (тесты двери) живая соло-карточка — не «след», а кабинет, и ответ на
    неё — «кабинет уже есть», не «введите код».

    Замер пилота 11.09.2026: таких карточек **ноль** (архивных три, связи ни
    у одной). Проверка стережёт МЕХАНИЗМ, а не наблюдение.
    """
    from django.db.models import Q

    from apps.catalog.models import CatalogMaster
    from apps.identity.models import BotUser
    from apps.tenancy.context import tenant_scope

    rows = BotUser.all_tenants.filter(
        channel=identity.channel, channel_user_id=identity.channel_user_id
    ).select_related("tenant")
    for row in rows:
        with tenant_scope(row.tenant):
            retired = CatalogMaster.objects.filter(linked_bot_user=row).filter(
                Q(archived_at__isnull=False) | Q(is_active=False)
            )
            if retired.exists():
                return True
    return False


def _already_has_a_solo_workspace(bot_user) -> bool:
    """Заводил ли этот человек кабинет соло-мастера раньше.

    Ищется по слагу, который `create_solo_provider` выводит из той же
    пары `(channel, channel_user_id)` — то есть по тому же правилу, по
    которому кабинет создавался. Спрашивать про карточку мастера здесь
    бесполезно: кабинет заводит СВОЙ `BotUser` в своём тенанте, и по
    салонной строке человека он не находится (замерено: предикат
    `_has_a_master_card_here` на вернувшемся отвечает `False`).

    Без этой проверки человек, у которого кабинет уже есть, получал бы
    предложение завести его снова и узнавал бы правду только после
    нажатия. Не молчание, но и не ответ.
    """
    from apps.identity.services.solo_onboarding import _solo_tenant_slug
    from apps.tenancy.models import Tenant

    slug = _solo_tenant_slug(bot_user.channel, bot_user.channel_user_id)
    return Tenant.objects.filter(slug=slug).exists()


def _solo_identity_rejected(bot_user) -> bool:
    """Отклонил ли оператор связь личности этого соло-мастера (§6)."""
    from apps.identity.models import SoloIdentityLink

    return SoloIdentityLink.objects.filter(
        channel=bot_user.channel,
        channel_user_id=bot_user.channel_user_id,
        status=SoloIdentityLink.Status.REJECTED,
    ).exists()


def _open_cabinet_attachments(entry) -> list[dict] | None:
    """Дверь в кабинет соло-мастера — или ничего, если двери нет (DRF-1756).

    Та же кнопка и тот же источник адреса, что у меню сотрудников
    (``staff_menu._miniapp_button``): ``web_app`` записи бота, иначе
    ``miniapp_url``, иначе ``None``. Кнопка, которая не может сработать,
    хуже её отсутствия — поэтому без записи или без адреса вложений нет.

    Фриз §5: после создания рабочего пространства — «Открыть кабинет», и
    «реализация не завершена, если аккаунт есть, а мастер не может открыть
    обычное рабочее пространство». До этого среза оба текста соло-пути
    уходили без вложений, и из салонного бота в Mini App у соло-мастера не
    было ни одной кнопки; куда эта кнопка ведёт — решает резолвер
    (DRF-1755: к строке его собственного тенанта).
    """
    from apps.channels.max.outbound import make_inline_keyboard_attachment
    from apps.channels.max.staff_menu import _miniapp_button

    button = _miniapp_button(entry, "🏠 Открыть кабинет")
    if button is None:
        return None
    return [make_inline_keyboard_attachment([button], columns=1)]


def _ask_for_code_with_solo_offer(event: CanonicalEvent, *, entry=None) -> None:
    """Попросить код — и, если уместно, предложить кабинет соло-мастера.

    По личности события, без строки (DRF-1784). ``entry`` — запись салонного
    бота; нужна только вернувшемуся владельцу кабинета, чтобы вместе с
    ответом получить дверь в него.
    """

    identity = _Identity.of(event)
    if _has_a_master_card_anywhere(identity):
        _reply(event, ASK_FOR_CODE)
        return

    if _already_has_a_solo_workspace(identity):
        # Второе посещение. Предлагать завести то, что уже заведено, —
        # значит заставить человека нажать, чтобы узнать, что нажимать не
        # надо было.
        #
        # §6: если оператор ОТКЛОНИЛ связь — человек получает безопасное
        # сообщение с путём (поддержка), а не «кабинет уже есть»: второе
        # обещало бы кабинет, которого не будет. Дверь в него — тем более.
        if _solo_identity_rejected(identity):
            from apps.identity.services.solo_identity_link import REJECTED_RECOVERY_TEXT

            _reply(event, REJECTED_RECOVERY_TEXT)
            return
        # Вместо предложения — дверь (DRF-1756).
        _reply(event, SOLO_ALREADY_REGISTERED, attachments=_open_cabinet_attachments(entry))
        return

    from apps.channels.max import outbound

    attachment = outbound.make_inline_keyboard_attachment(
        [{"label": SOLO_OFFER_BUTTON, "callback": SOLO_REGISTER_CALLBACK}]
    )
    _reply(event, ASK_FOR_CODE + SOLO_OFFER, attachments=[attachment])


# ─── DRF-1793 (M1): диалог регистрации соло — имя → город → сводка → подтверждение ───


def _solo_registration_step(event: CanonicalEvent, *, entry=None) -> bool:
    """Обработать нажатие ``cb:solo:*``; ``True`` — нажатие было нашим и отвечено.

    Порядок шагов держит черновик (``SoloRegistrationDraft``), не память
    процесса: кнопка без черновика или не на своём шаге — «начните заново»,
    а не догадка. Кабинет создаётся ровно в одной ветке — подтверждении.
    """

    from apps.identity.services import solo_registration_draft as drafts

    text = event.text
    identity = _Identity.of(event)
    if text == SOLO_REGISTER_CALLBACK:
        _start_solo_registration(event, identity, entry=entry)
        return True
    if not text.startswith("cb:solo:"):
        return False

    draft = drafts.get_draft(channel=identity.channel, channel_user_id=identity.channel_user_id)
    if draft is None:
        _reply(event, SOLO_DRAFT_MISSING, attachments=_solo_offer_attachments())
        return True

    if text == SOLO_CANCEL_CALLBACK:
        drafts.discard_draft(draft)
        _reply(event, SOLO_CANCELLED, attachments=_solo_offer_attachments())
        return True
    if text == SOLO_NAME_KEEP_CALLBACK:
        if draft.step != drafts.SoloRegistrationDraft.Step.NAME or not draft.display_name:
            _reply(event, SOLO_DRAFT_MISSING, attachments=_solo_offer_attachments())
            return True
        drafts.accept_name(draft, draft.display_name)
        _ask_solo_city(event, draft)
        return True
    if text == SOLO_NAME_EDIT_CALLBACK:
        drafts.back_to_name(draft)
        _ask_solo_name(event, draft)
        return True
    if text.startswith(SOLO_CITY_CALLBACK_PREFIX):
        if draft.step not in (
            drafts.SoloRegistrationDraft.Step.CITY,
            drafts.SoloRegistrationDraft.Step.CONFIRM,
        ):
            _reply(event, SOLO_DRAFT_MISSING, attachments=_solo_offer_attachments())
            return True
        try:
            drafts.accept_city(draft, text[len(SOLO_CITY_CALLBACK_PREFIX) :])
        except drafts.CityRejected as exc:
            if exc.reason == "no_cities_configured":
                logger.error("channels.max.salon.solo_no_cities_configured")
                _reply(event, SOLO_NO_CITIES)
            else:
                _ask_solo_city(event, draft, preface=SOLO_CITY_UNKNOWN)
            return True
        _show_solo_summary(event, draft)
        return True
    if text == SOLO_CONFIRM_CALLBACK:
        if not drafts.is_confirmable(draft):
            _reply(event, SOLO_DRAFT_MISSING, attachments=_solo_offer_attachments())
            return True
        _register_solo_provider(
            event, entry=entry, display_name=draft.display_name, city=draft.city
        )
        drafts.discard_draft(draft)
        return True
    return False


def _solo_registration_takes_name(event: CanonicalEvent) -> bool:
    """Свободный текст незнакомца — это имя, если черновик ждёт имя. Иначе ``False``."""

    from apps.identity.services import solo_registration_draft as drafts

    identity = _Identity.of(event)
    draft = drafts.get_draft(channel=identity.channel, channel_user_id=identity.channel_user_id)
    if draft is None or draft.step != drafts.SoloRegistrationDraft.Step.NAME:
        return False
    try:
        drafts.accept_name(draft, event.text)
    except drafts.NameRejected as exc:
        _reply(event, SOLO_NAME_REJECTED[exc.reason], attachments=_name_keep_attachments(draft))
        return True
    _ask_solo_city(event, draft)
    return True


def _start_solo_registration(event: CanonicalEvent, identity: "_Identity", *, entry=None) -> None:
    """«Я работаю сам»: те же отказы, что у предложения, иначе — черновик и вопрос об имени."""

    from apps.identity.services import solo_registration_draft as drafts

    if _has_a_master_card_anywhere(identity):
        _reply(event, ASK_FOR_CODE)
        return
    if _already_has_a_solo_workspace(identity):
        if _solo_identity_rejected(identity):
            from apps.identity.services.solo_identity_link import REJECTED_RECOVERY_TEXT

            _reply(event, REJECTED_RECOVERY_TEXT)
            return
        _reply(event, SOLO_ALREADY_REGISTERED, attachments=_open_cabinet_attachments(entry))
        return

    draft = drafts.start_draft(
        channel=identity.channel,
        channel_user_id=identity.channel_user_id,
        chat_id=identity.chat_id,
        prefill_name=identity.display_name,
    )
    emit(
        "channels.max.salon.solo_registration_started",
        payload={"channel": identity.channel, "channel_user_id": str(identity.channel_user_id)},
    )
    _ask_solo_name(event, draft)


def _ask_solo_name(event: CanonicalEvent, draft) -> None:
    if draft.display_name:
        _reply(
            event,
            SOLO_ASK_NAME_WITH_PREFILL.format(name=draft.display_name),
            attachments=_name_keep_attachments(draft),
        )
        return
    _reply(event, SOLO_ASK_NAME, attachments=_cancel_attachments())


def _ask_solo_city(event: CanonicalEvent, draft, *, preface: str = "") -> None:
    from apps.channels.max import outbound
    from apps.identity.services import solo_registration_draft as drafts

    options = drafts.city_options()
    if not options:
        logger.error("channels.max.salon.solo_no_cities_configured")
        _reply(event, SOLO_NO_CITIES)
        return
    buttons = [
        {"label": option.name, "callback": f"{SOLO_CITY_CALLBACK_PREFIX}{option.code}"}
        for option in options
    ] + [{"label": SOLO_CANCEL_BUTTON, "callback": SOLO_CANCEL_CALLBACK}]
    text = f"{preface}\n\n{SOLO_ASK_CITY}" if preface else SOLO_ASK_CITY
    _reply(event, text, attachments=[outbound.make_inline_keyboard_attachment(buttons)])


def _show_solo_summary(event: CanonicalEvent, draft) -> None:
    from apps.channels.max import outbound

    buttons = [
        {"label": SOLO_CONFIRM_BUTTON, "callback": SOLO_CONFIRM_CALLBACK},
        {"label": SOLO_EDIT_NAME_BUTTON, "callback": SOLO_NAME_EDIT_CALLBACK},
        {"label": SOLO_CANCEL_BUTTON, "callback": SOLO_CANCEL_CALLBACK},
    ]
    _reply(
        event,
        SOLO_SUMMARY.format(name=draft.display_name, city=draft.city),
        attachments=[outbound.make_inline_keyboard_attachment(buttons)],
    )


def _name_keep_attachments(draft) -> list:
    from apps.channels.max import outbound

    buttons = []
    if draft.display_name:
        buttons.append(
            {
                "label": SOLO_NAME_KEEP_BUTTON.format(name=draft.display_name),
                "callback": SOLO_NAME_KEEP_CALLBACK,
            }
        )
    buttons.append({"label": SOLO_CANCEL_BUTTON, "callback": SOLO_CANCEL_CALLBACK})
    return [outbound.make_inline_keyboard_attachment(buttons)]


def _cancel_attachments() -> list:
    from apps.channels.max import outbound

    return [
        outbound.make_inline_keyboard_attachment(
            [{"label": SOLO_CANCEL_BUTTON, "callback": SOLO_CANCEL_CALLBACK}]
        )
    ]


def _solo_offer_attachments() -> list:
    from apps.channels.max import outbound

    return [
        outbound.make_inline_keyboard_attachment(
            [{"label": SOLO_OFFER_BUTTON, "callback": SOLO_REGISTER_CALLBACK}]
        )
    ]


def _register_solo_provider(
    event: CanonicalEvent, *, entry=None, display_name: str | None = None, city: str = ""
) -> None:
    """Завести кабинет соло-мастера и сказать правду о его состоянии.

    DRF-1793: зовётся из подтверждения сводки — ``display_name`` и ``city``
    приходят из черновика; без них (прямой вызов, прежние тесты) имя —
    из события, город пустой.

    Правду — то есть `setup_state`, а не факт создания. §122: регистрация
    не завершается как «готово», пока человека не видно клиентам, и
    единственный способ не соврать здесь — спросить у результата, а не у
    самого себя.

    ``entry`` — запись салонного бота: из неё берётся дверь «Открыть
    кабинет» (DRF-1756, фриз §5). Правда о состоянии и дверь не спорят:
    кабинет существует и в нём можно готовить профиль, даже пока клиентам
    мастера не видно.
    """
    from apps.identity.services.solo_onboarding import (
        SoloOnboardingError,
        SoloSetupState,
        create_solo_provider,
    )

    identity = _Identity.of(event)
    try:
        result = create_solo_provider(
            channel=identity.channel,
            channel_user_id=identity.channel_user_id,
            display_name=display_name if display_name is not None else identity.display_name,
            chat_id=identity.chat_id,
            city=city,
        )
    except SoloOnboardingError:
        logger.exception(
            "channels.max.salon.solo_register_failed channel_user_id=%s",
            identity.channel_user_id,
        )
        _reply(event, SOLO_FAILED)
        return

    # §148: пробуем связать АВТОМАТИЧЕСКИ, падаем в SETUP_PENDING,
    # оператор добивает. Попытка ничего не решает о готовности — её
    # по-прежнему считает `setup_state` по строке каталога, поэтому в
    # день, когда связывание начнёт получаться, здесь не изменится ни
    # строки: изменится ответ каталога, и состояние переедет само.
    #
    # На пилоте попытка будет отказывать всегда, и это правильный исход:
    # `resolve_external_user` заводит прокси лениво, а подставной ключ
    # писать запрещено. Причина приезжает машинным именем, а не «не
    # получилось», — см. `solo_link_attempt`.
    link_refusal = None
    if result.created:
        from apps.identity.services.solo_identity_link import open_link, record_attempt
        from apps.identity.services.solo_link_attempt import attempt_solo_link

        # §6 пакета 12.09: связь личности — состояние с провенансом и
        # аудит-пакетом, а не только столбец ключа. PENDING заводится ДО
        # попытки, чтобы оператор видел заявку даже когда попытка упала.
        # The SOLO row (DRF-1784): the link is about the workspace, and the
        # person has no other row — the salon one is not created any more.
        link = open_link(result.master, bot_user=result.bot_user, tenant=result.tenant)
        link_refusal = attempt_solo_link(result.master, result.bot_user)
        result.master.refresh_from_db(fields=["ayla_user_id"])
        record_attempt(link, refusal=link_refusal, ayla_user_id=result.master.ayla_user_id)

    emit(
        "channels.max.salon.solo_registered",
        payload={
            "bot_user_id": str(result.bot_user.id),
            "created": result.created,
            "setup_state": result.setup_state.value,
            "blocked_by": result.blocked_by,
            "link_refusal": link_refusal,
        },
    )
    logger.info(
        "channels.max.salon.solo_registered bot_user=%s tenant=%s created=%s "
        "setup_state=%s blocked_by=%s link_refusal=%s",
        result.bot_user.id,
        result.tenant.slug,
        result.created,
        result.setup_state.value,
        result.blocked_by,
        link_refusal,
    )

    if result.setup_state is SoloSetupState.READY:
        # Сегодня недостижимо — ключа взяться неоткуда, — но ветка есть,
        # чтобы в день, когда связывание заработает, человек не получил
        # текст про ожидание. Меню — по строке СОЛО-тенанта
        # (`result.bot_user`), а не по салонной `bot_user`: `resolve_role`
        # читает роли в тенанте своей строки, и салонная строка — customer.
        # До DRF-1756 здесь стояли `resolve_role(bot_user)` и `entry=None`
        # — меню клиента без двери.
        _send_menu(event, resolve_role(result.bot_user), result.tenant, entry)
        return

    _reply(
        event,
        SOLO_CREATED_PENDING if result.created else SOLO_ALREADY_REGISTERED,
        attachments=_open_cabinet_attachments(entry),
    )


def _is_button_tap(text: str) -> bool:
    """True when this inbound text is one of our own button payloads.

    Two grammars, not one, and the split is imposed by MAX rather than
    chosen: callback buttons carry ``cb:{domain}:{action}``, while an
    ``open_app`` payload may contain no colon at all
    (:data:`apps.channels.max.outbound.OPEN_APP_PAYLOAD_RE`). A tap must
    be recognised under either, or the Mini App button's payload reaches
    the conversation path as if it were something the person typed.
    """

    return text.startswith("cb:") or text == OPEN_APP_PAYLOAD


def _handle_button(event: CanonicalEvent, role_ctx, bot_user, tenant, entry) -> None:
    """Run the tapped action, then re-show the menu so the panel persists."""

    from apps.channels.max import staff_actions

    action = event.text
    is_admin_side = role_ctx.is_owner or role_ctx.is_admin or role_ctx.is_receptionist

    if action == CB_DAY:
        if is_admin_side:
            body = staff_actions.salon_day(tenant)
        else:
            master = _master_of(bot_user)
            body = (
                staff_actions.master_day(master)
                if master is not None
                else "Ваша карточка мастера не найдена."
            )
    elif action == CB_REQUESTS and is_admin_side:
        _reply(
            event,
            staff_actions.pending_requests(tenant),
            attachments=_requests_attachments(tenant, role_ctx, entry),
        )
        return
    elif action.startswith(CB_APPROVE_PREFIX) and is_admin_side:
        request_id = action[len(CB_APPROVE_PREFIX) :]
        outcome = staff_actions.approve_request(
            tenant=tenant, request_id=request_id, actor=bot_user
        )
        # Re-list after deciding: the queue the person is looking at just
        # changed, and showing the stale one invites a second tap on a
        # request that is already handled.
        _reply(
            event,
            f"{outcome}\n\n{staff_actions.pending_requests(tenant)}",
            attachments=_requests_attachments(tenant, role_ctx, entry),
        )
        return
    elif action == OPEN_APP_PAYLOAD:
        # The Mini App opens client-side; nothing to do server-side. This
        # branch is defensive: whether MAX echoes an `open_app` payload
        # back as a callback at all is not something this repo has
        # measured. If it does, the tap must land here and be silently
        # absorbed; if it does not, this costs nothing. What it must NOT
        # do is fall through to `_handle_talk` and hand the LLM the string
        # `staff_open_app` as if the person had typed it — which is
        # exactly what would happen without `_is_button_tap`, because the
        # payload cannot start with `cb:` (MAX forbids the colon).
        return
    else:
        # Unknown or not-permitted action: show the menu rather than an
        # error. A receptionist tapping an admin-only button from an old
        # message should see what they CAN do, not a refusal.
        _send_menu(event, role_ctx, tenant, entry)
        return

    _reply(event, body, attachments=menu_attachments(role_ctx, entry))


def _master_of(bot_user):
    """The catalog row this person is linked to, if any."""

    from apps.catalog.models import CatalogMaster

    # `.objects`, not `.all_tenants`: this runs inside the consumer's
    # tenant_scope, so the scoped manager is both available and stricter —
    # it makes reading another salon's catalog impossible here rather than
    # merely unintended.
    return (
        CatalogMaster.objects.filter(
            linked_bot_user=bot_user,
            archived_at__isnull=True,
        )
        .select_related("tenant")
        .first()
    )


def _requests_attachments(tenant, role_ctx, entry):
    """Menu keyboard plus one approve button per pending request."""

    from apps.channels.max import staff_actions
    from apps.channels.max.outbound import make_inline_keyboard_attachment
    from apps.channels.max.staff_menu import menu_buttons

    buttons = [
        {"label": label, "callback": f"{CB_APPROVE_PREFIX}{request_id}"}
        for request_id, label in staff_actions.pending_request_rows(tenant)
    ]
    buttons.extend(menu_buttons(role_ctx, entry))
    if not buttons:
        return None
    return [make_inline_keyboard_attachment(buttons, columns=1)]


def _handle_talk(event: CanonicalEvent, role_ctx, bot_user, tenant, entry) -> None:
    """Someone who holds a role typed a line rather than tapping.

    Step 0 still answers with the menu — the assistant that reads these
    lines lands in step 1. What changes here is that the exchange is
    written down: until now the salon bot kept no history at all, so there
    was nothing for an assistant to be built on.

    Only typed lines are recorded. Button taps are not replies, and the
    customer path already learned what happens when raw `cb:*` payloads
    reach a model as if they were speech (DRF-988: the callback text in
    history provoked hallucinated refusals).
    """

    thread = _open_thread(bot_user, role_ctx)
    inbound = _remember(thread, role="user", content=event.text)

    answer = _ask_assistant(bot_user, thread, event.text, exclude_id=inbound)
    if answer is None:
        # No assistant for this person (not a master yet, or the surface is
        # off). The menu is still a real answer — and it is the one this
        # handler gave for its whole life before now.
        body = menu_header(role_ctx, tenant)
        _reply(event, body, attachments=menu_attachments(role_ctx, entry))
        _remember(thread, role="assistant", content=body)
        return

    _reply(event, answer.text, attachments=menu_attachments(role_ctx, entry))
    _remember(
        thread,
        role="assistant",
        content=answer.text,
        tool_name=answer.tool_name,
        tokens_in=answer.tokens_in,
        tokens_out=answer.tokens_out,
        llm_provider=answer.llm_provider,
        llm_model=answer.llm_model,
        llm_cost_usd=answer.llm_cost_usd,
    )


def _open_thread(bot_user, role_ctx):
    """This person's working thread, or None if it cannot be opened.

    Never raises. A thread is a place to write history, not a
    precondition for answering — a staff member in the middle of a shift
    must get their menu even if the write fails.
    """

    from apps.conversations.staff_assistant import resolve_active_staff_thread

    try:
        return resolve_active_staff_thread(bot_user, role_at_open=role_ctx.primary_role)
    except Exception:  # noqa: BLE001 — history must never cost the reply
        logger.exception("channels.max.salon.thread_open_failed bot_user=%s", bot_user.id)
        return None


def _remember(thread, *, role: str, content: str, **telemetry):
    """Append one turn and return its id, swallowing failure as above."""

    if thread is None:
        return None

    from apps.conversations.staff_assistant import record_staff_message

    try:
        return record_staff_message(thread, role=role, content=content, **telemetry).id
    except Exception:  # noqa: BLE001
        logger.exception(
            "channels.max.salon.thread_write_failed thread=%s role=%s", thread.id, role
        )
        return None


def _ask_assistant(bot_user, thread, text: str, *, exclude_id=None):
    """The master's assistant, or None when there is nobody to answer as.

    Only masters have one in step 1: every tool reads one master's own
    schedule, and the admin-side equivalent needs a different set entirely.
    An admin typing a sentence keeps getting the menu, which is what they
    got yesterday — no promise is broken.

    Never raises. The assistant already degrades every failure to a
    sentence; this catch is for the paths it cannot know about, and its
    fallback is the menu.
    """

    from apps.conversations.staff_assistant import recent_staff_history
    from apps.master_api.services.assistant import answer_master_question

    master = _master_of(bot_user)
    if master is None:
        return None

    try:
        # The inbound line is already in the thread — exclude it, or the
        # model is handed the same question twice.
        history = recent_staff_history(thread, exclude_id=exclude_id) if thread is not None else []
        return answer_master_question(master=master, text=text, history=history)
    except Exception:  # noqa: BLE001
        logger.exception("channels.max.salon.assistant_failed bot_user=%s", bot_user.id)
        return None


def _send_menu(event: CanonicalEvent, role_ctx, tenant, entry) -> None:
    _reply(
        event,
        menu_header(role_ctx, tenant),
        attachments=menu_attachments(role_ctx, entry),
    )


def _sender_name(event: CanonicalEvent) -> str:
    """The person's channel-side name, from where MAX actually puts it.

    ``message.sender.name`` — not a top-level ``display_name``, which MAX
    never sends. Getting this wrong is silent: the BotUser is created
    nameless and the opportunistic blank-fill never fires again, so the
    salon sees an unnamed staff member forever.
    """

    raw = event.raw if isinstance(event.raw, dict) else {}
    sender = (raw.get("message") or {}).get("sender") or {}
    return (sender.get("name") or "").strip()


def _bot_slug_for(tenant) -> str:
    """Slug of the salon bot — the ONE serving ``max_salon``, whichever tenant.

    Until DRF-1705 this matched ``entry.tenant_slug == tenant.slug``: the
    salon bot was assumed to belong to a salon, and a master whose tenant is
    not the bot's (every solo master) got ``""`` → «refuse to answer rather
    than answer as the wrong bot» → silence. The bot does not belong to a
    salon (owner decision 12.09.2026); ``parse_registry`` guarantees there is
    at most one on the stream, so the pick is not arbitrary.

    ``tenant`` stays in the signature: the call site's log line names it,
    and the day a per-tenant staff bot returns this is where the choice
    would go back in.
    """
    from apps.channels.bot_registry import effective_registry, resolve_by_stream

    entry = resolve_by_stream(SALON_STREAM, effective_registry())
    return entry.slug if entry is not None else ""


def _redeem_and_greet(event: CanonicalEvent, code: str, entry) -> None:
    """Try the code and answer with the outcome, in the person's terms.

    The person has no row yet (DRF-1784): the code decides the salon, and
    the row is born there inside the redemption — see
    :func:`redeem_staff_invite_by_identity`.
    """

    identity = _Identity.of(event)
    try:
        result = redeem_staff_invite_by_identity(
            code=code,
            channel=identity.channel,
            channel_user_id=identity.channel_user_id,
            display_name=identity.display_name,
            chat_id=identity.chat_id,
        )
    except InviteRateLimited:
        _reply(event, TOO_MANY_ATTEMPTS)
        return
    except InviteNotFound:
        _reply(event, CODE_NOT_ACCEPTED)
        return
    except InviteMasterMissing:
        _reply(event, MASTER_GONE)
        return
    except OwnerAlreadyExists:
        _reply(event, OWNER_TAKEN)
        return
    # Обе ветки стоят ВЫШЕ общей намеренно: `MasterAlreadyLinked` и
    # `PersonAlreadyMaster` — подклассы `InviteError`, и снизу их уже
    # некому поймать. Порядок здесь не стиль, а условие того, что правка
    # вообще что-то меняет.
    except MasterAlreadyLinked:
        _reply(event, WRONG_RECIPIENT)
        return
    except PersonAlreadyMaster:
        _reply(event, PERSON_ALREADY_MASTER)
        return
    except InviteError as exc:  # future slugs — never leak an exception text
        logger.warning("channels.max.salon.redeem_failed slug=%s", getattr(exc, "slug", "?"))
        _reply(event, CODE_NOT_ACCEPTED)
        return

    bot_user = result.bot_user
    tenant = result.tenant
    assert bot_user is not None and tenant is not None  # by_identity fills both
    emit(
        "channels.max.salon.invite_redeemed",
        payload={
            "role": result.role,
            "already_had_role": result.already_had_role,
            "bot_user_id": str(bot_user.id),
        },
    )

    salon = tenant.name or tenant.slug
    if result.already_had_role:
        greeting = ALREADY_HAVE_ROLE.format(salon=salon)
    else:
        greeting = ROLE_GREETING.get(result.role, ROLE_GRANTED_GENERIC).format(salon=salon)

    # Re-resolve rather than infer from the invite: the person may hold
    # several roles, and the menu must reflect all of them.
    from apps.tenancy.context import tenant_scope

    with tenant_scope(tenant):
        role_ctx = resolve_role(bot_user)
        _reply(
            event,
            f"{greeting}\n\n{menu_header(role_ctx, tenant)}",
            attachments=menu_attachments(role_ctx, entry),
        )


def _reply(event: CanonicalEvent, text: str, attachments: list | None = None) -> None:
    """Send as the salon bot — identity comes from the surrounding scope."""

    outbound.send_message(chat_id=event.chat_id, text=text, attachments=attachments)


__all__ = ["handle_salon_max_event"]
