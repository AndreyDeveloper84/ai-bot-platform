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
from apps.identity.services.role_resolver import resolve_role
from apps.identity.services.staff_invites import (
    InviteError,
    InviteMasterMissing,
    InviteNotFound,
    InviteRateLimited,
    OwnerAlreadyExists,
    looks_like_code,
    redeem_staff_invite,
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

#: No Mini App name for this bot, so no button can be built. Same class of
#: failure as `no_entry_configured` in `views_invite._dispatch_max_dm`, and
#: answered the same way: say so rather than send a message that looks like
#: an invitation and does nothing. An https address is NOT offered as a
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


def _handle_master_invite(event: CanonicalEvent, token: str, bot_user, tenant, entry) -> None:
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

    Which salon the token belongs to. ``validate_invite_token`` filters
    by tenant, and the tenant is already known — the salon bot is
    tenant-bound by construction and the consumer entered its scope. So
    a token issued by another salon is «not found» here, in the same
    deliberately collapsed way the Mini App reports it.

    The call is a locking read inside ``atomic`` (the function does
    ``select_for_update``), exactly as ``/onboarding/claim`` uses it. It
    mutates nothing, so re-opening the link is idempotent.
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
            validate_invite_token(token, tenant)
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
        payload={"bot_user_id": str(bot_user.id)},
    )

    _reply(event, INVITE_WELCOME.format(salon=salon), attachments=[attachment])


def _handle_salon_event_inner(event: CanonicalEvent, trace_id: str | uuid.UUID | None) -> None:
    """Resolve who is speaking, then either onboard them or show the menu."""

    from apps.channels.bot_registry import effective_registry, resolve_by_slug
    from apps.identity.services.resolver import resolve_or_create_bot_user
    from apps.tenancy.context import current_tenant

    tenant = current_tenant()
    if tenant is None:
        # The consumer enters tenant_scope from the registry entry; without
        # it we cannot tell which salon this is, and guessing would attach
        # a person to the wrong one.
        logger.error("channels.max.salon.no_tenant_scope channel_user_id=%s", event.channel_user_id)
        return

    bot_user = resolve_or_create_bot_user(
        channel=event.channel,
        channel_user_id=event.channel_user_id,
        display_name=_sender_name(event),
        chat_id=event.chat_id,
    )

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
            _handle_master_invite(event, invite_token, bot_user, tenant, entry)
            return

        role_ctx = resolve_role(bot_user)

        if role_ctx.primary_role != "customer":
            # A button tap arrives as the callback payload in `text`.
            if _is_button_tap(event.text):
                _handle_button(event, role_ctx, bot_user, tenant, entry)
            else:
                _handle_talk(event, role_ctx, bot_user, tenant, entry)
            return

        # No role yet. A stray button tap from someone who lost their access
        # must not be read as an invite code — it would burn a rate-limit
        # attempt for a message they did not type.
        if _is_button_tap(event.text):
            _reply(event, ASK_FOR_CODE)
            return

        code = _extract_code(event.text)
        if code is None:
            _reply(event, ASK_FOR_CODE)
            return

        _redeem_and_greet(event, bot_user, code, tenant, entry)


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
    """Find which registry entry serves this tenant.

    Matched on BOTH tenant and stream. Tenant alone is not enough: nothing
    in the registry forbids a salon from also having a per-tenant client bot
    (`stream=max`), and picking that one would send staff replies from the
    customer-facing token — which, since MAX chat_ids are per-bot, most
    likely 4xxs, leaves the entry unacked in the PEL, and the person gets
    nothing at all.
    """

    from apps.channels.bot_registry import effective_registry

    for entry in effective_registry():
        if entry.tenant_slug == tenant.slug and entry.stream == SALON_STREAM:
            return entry.slug
    return ""


def _redeem_and_greet(event: CanonicalEvent, bot_user, code: str, tenant, entry) -> None:
    """Try the code and answer with the outcome, in the person's terms."""

    try:
        result = redeem_staff_invite(code=code, bot_user=bot_user, tenant=tenant)
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
    except InviteError as exc:  # future slugs — never leak an exception text
        logger.warning("channels.max.salon.redeem_failed slug=%s", getattr(exc, "slug", "?"))
        _reply(event, CODE_NOT_ACCEPTED)
        return

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
