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
import uuid

from apps.channels.bot_context import bot_scope
from apps.channels.max import outbound
from apps.channels.max.parser import CanonicalEvent, ParseError, parse_max_webhook
from apps.channels.max.staff_menu import (
    CB_APPROVE_PREFIX,
    CB_DAY,
    CB_OPEN_APP,
    CB_REQUESTS,
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
        role_ctx = resolve_role(bot_user)

        if role_ctx.primary_role != "customer":
            # A button tap arrives as the callback payload in `text`.
            if event.text.startswith("cb:"):
                _handle_button(event, role_ctx, bot_user, tenant, entry)
            else:
                _handle_talk(event, role_ctx, bot_user, tenant, entry)
            return

        # No role yet. A stray button tap from someone who lost their access
        # must not be read as an invite code — it would burn a rate-limit
        # attempt for a message they did not type.
        if event.text.startswith("cb:"):
            _reply(event, ASK_FOR_CODE)
            return

        code = _extract_code(event.text)
        if code is None:
            _reply(event, ASK_FOR_CODE)
            return

        _redeem_and_greet(event, bot_user, code, tenant, entry)


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
    elif action == CB_OPEN_APP:
        # The Mini App opens client-side; nothing to do server-side. MAX
        # still delivers the callback, and answering nothing would look
        # like the bot ignored the tap.
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
    _remember(thread, role="user", content=event.text)

    body = menu_header(role_ctx, tenant)
    _reply(event, body, attachments=menu_attachments(role_ctx, entry))

    _remember(thread, role="assistant", content=body)


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


def _remember(thread, *, role: str, content: str) -> None:
    """Append one turn, swallowing failure for the same reason as above."""

    if thread is None:
        return

    from apps.conversations.staff_assistant import record_staff_message

    try:
        record_staff_message(thread, role=role, content=content)
    except Exception:  # noqa: BLE001
        logger.exception(
            "channels.max.salon.thread_write_failed thread=%s role=%s", thread.id, role
        )


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
