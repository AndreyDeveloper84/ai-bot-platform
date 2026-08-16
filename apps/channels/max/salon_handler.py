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
"""

from __future__ import annotations

import hashlib
import logging
import uuid

from apps.channels.bot_context import bot_scope
from apps.channels.max import outbound
from apps.channels.max.parser import CanonicalEvent, ParseError, parse_max_webhook
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
            _reply(event, _menu_text(role_ctx, tenant))
            return

        code = _extract_code(event.text)
        if code is None:
            _reply(event, ASK_FOR_CODE)
            return

        _redeem_and_greet(event, bot_user, code, tenant)


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


def _redeem_and_greet(event: CanonicalEvent, bot_user, code: str, tenant) -> None:
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
    _reply(event, f"{greeting}\n\n{_menu_text(role_ctx, tenant)}")


def _menu_text(role_ctx, tenant) -> str:
    """Placeholder menu body.

    The button menu itself (``cb:staff:*`` callbacks, ``open_app`` into the
    admin Mini App) is the next commit; this keeps the handler honest in
    the meantime — a staff member who writes in gets an answer that names
    their role rather than silence.
    """

    salon = tenant.name or tenant.slug
    if role_ctx.is_master and not (role_ctx.is_owner or role_ctx.is_admin):
        return f"Салон «{salon}». Ваш кабинет мастера открыт в приложении."
    return f"Салон «{salon}». Кабинет салона открыт в приложении."


def _reply(event: CanonicalEvent, text: str) -> None:
    """Send as the salon bot — identity comes from the surrounding scope."""

    outbound.send_message(chat_id=event.chat_id, text=text)


__all__ = ["handle_salon_max_event"]
