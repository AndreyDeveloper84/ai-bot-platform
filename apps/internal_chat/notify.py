"""Deliver internal-chat messages to MAX (DRF-1061, block 3.3).

### The gap this closes

``apps.internal_chat`` is a complete two-way admin↔master thread store —
topics, statuses, assignment, sensitivity, SLA fields — with **no delivery
mechanism at all**. Its own module docstring says so: «Notification
dispatch (push / MAX DM / email) on new message — separate PR.» A posted
message becomes a database row, an audit entry and an analytics event, and
the counterparty learns of it only by opening the screen and looking.

On the pilot nobody opens that screen, so in practice the feature does not
exist: a master can write to the salon and the salon will never know.

This is the missing half. Same transport as everything else that works
here — a plain MAX message through
:func:`apps.handoff.notify.send_max_notification` — and the same identity
rule as the booking notice: **sent as the salon bot**, because this is
staff-to-staff correspondence and it must not arrive from the
customer-facing avatar.

### Who gets told

Direction decides the recipient:

* **master → admin**: the salon side, and only the salon's own manager
  address. Which KEY that address uses is decided once, in
  :mod:`apps.channels.max.addressing` (DRF-1559). This rung used to fall
  back to the configured operator channel — removed by the owner's
  decision of 2026-09-07, for the same reason the booking notice lost it:
  that list is global, it carries no tenant, so on the pilot every salon's
  staff correspondence resolved to one shared, hand-typed dialog. That is
  exactly the leak the admin→master direction already refuses below. A
  salon with no manager address configured simply has no MAX address, and
  that is a normal state.
* **admin → master**: that thread's master personally, via
  ``CatalogMaster.linked_bot_user.channel_user_id``. There is no fallback here on
  purpose: a message addressed to one master must not be broadcast to the
  salon's shared channel just because the link is missing. Silence plus a
  warning is the correct failure — the alternative leaks a private
  conversation to whoever reads the fallback chat.

### What the message contains

The thread's topic and a short excerpt, never the whole body. This is a
notification whose job is «go and read it», not a mirror of the thread —
threads carry complaints and offboarding discussions
(``is_sensitive``), and copying those into a shared salon chat would
defeat the sensitivity flag the model already maintains.

For a sensitive thread the excerpt is dropped entirely: subject only.

### Contract

* **After commit.** Registered through ``transaction.on_commit`` by the
  caller; a rolled-back message must never announce itself.
* **Best-effort, hard.** Nothing here may break sending a message. A
  failed notification must not cost the user their message — everything
  is caught and logged.
* **No client PII.** Internal chat is staff-to-staff; customer identity
  does not appear in it, and nothing here reads a customer record.
"""

from __future__ import annotations

import logging

from django.db import transaction

from apps.channels.max.addressing import MaxAddress, manager_address

logger = logging.getLogger(__name__)

#: Stream of the staff-facing bot, matching apps/booking/master_notify.py.
SALON_STREAM = "max_salon"

#: How much of the message body to quote. Long enough to tell an urgent
#: message from a routine one, short enough not to be the message itself.
EXCERPT_LEN = 120


def schedule_message_notification(*, message) -> None:
    """Queue delivery for after the surrounding transaction commits.

    The caller stays synchronous and unaware; if the transaction rolls
    back, nothing is sent.
    """

    transaction.on_commit(lambda: notify_internal_message(message=message))


def _excerpt(body: str, *, is_sensitive: bool) -> str:
    """A short quote, or nothing at all for a sensitive thread."""

    if is_sensitive:
        return ""
    text = " ".join((body or "").split())
    if len(text) <= EXCERPT_LEN:
        return text
    return text[: EXCERPT_LEN - 1].rstrip() + "…"


def build_notification_text(*, message) -> str:
    """Compose the notice. Topic + who wrote + optional excerpt."""

    thread = message.thread
    subject = (getattr(thread, "subject", "") or "").strip()
    topic = (getattr(thread, "topic", "") or "").strip()
    header = subject or topic or "Сообщение"

    who = "Мастер" if message.sender_role == "master" else "Администратор"
    lines = [f"💬 {who}: {header}"]

    excerpt = _excerpt(message.body, is_sensitive=bool(getattr(thread, "is_sensitive", False)))
    if excerpt:
        lines.append(excerpt)
    else:
        # Sensitive threads carry complaints and offboarding talk. Quoting
        # them into a shared salon chat would defeat the very flag the
        # model maintains to mark them.
        lines.append("Тема помечена как чувствительная — текст в кабинете.")

    lines.append("Ответить — в кабинете салона.")
    return "\n".join(lines)


def _salon_bot_for(tenant):
    """The salon's staff bot, or None. See apps/booking/master_notify.py."""

    try:
        from apps.channels.bot_registry import effective_registry, resolve_by_tenant_stream

        return resolve_by_tenant_stream(tenant.slug, SALON_STREAM, effective_registry())
    except Exception:  # noqa: BLE001 — identity must never break messaging
        logger.warning("internal_chat.notify.registry_unavailable tenant=%s", tenant.slug)
        return None


def _recipients_for(message) -> tuple[tuple[MaxAddress, ...], str]:
    """``(addresses, channel_label)`` for this message's direction.

    Каждый получатель несёт СВОЙ ключ адресации, и ветвление по ключу
    здесь не повторяется: с DRF-1559 выбор «человек, если задан, иначе
    диалог» живёт в :mod:`apps.channels.max.addressing`, а этот резолвер
    отвечает только на вопрос «кому».
    """

    thread = message.thread
    tenant = thread.tenant

    if message.sender_role == "master":
        # To the salon side, and only to this salon's own address. The
        # global operator channel was removed on 2026-09-07 (see the
        # module docstring): it names no tenant, so it would have shown
        # ten salons each other's staff correspondence.
        manager = manager_address(tenant)
        if manager:
            return (manager,), "manager"
        return (), "none"

    # To the master personally. No fallback on purpose: broadcasting a
    # message meant for one master to the salon's shared channel would
    # leak a private conversation to whoever reads that chat.
    master = getattr(thread, "master", None)
    linked = getattr(master, "linked_bot_user", None)
    # DRF-1558 — the person, not the dialog: this fan-out runs under the
    # salon bot's ``bot_scope`` and the master's stored ``chat_id`` names
    # their dialog with the CLIENT bot.
    user_id = (getattr(linked, "channel_user_id", "") or "").strip() if linked else ""
    return ((MaxAddress(user_id=user_id),), "master") if user_id else ((), "none")


def notify_internal_message(*, message) -> None:
    """Announce one internal-chat message in MAX. NEVER raises."""

    from apps.channels.bot_context import bot_scope
    from apps.handoff.notify import send_max_notification

    try:
        thread = message.thread
        tenant = thread.tenant
        recipients, channel = _recipients_for(message)
        recipient_count = len(recipients)

        if not recipient_count:
            if message.sender_role == "master":
                # Observable, but quiet. Since 2026-09-07 a salon with no
                # manager address has no MAX address at all, and that is
                # a normal state rather than a configuration defect — one
                # INFO line per pass, no warning. (The admin→master
                # direction below keeps its WARNING: an unlinked master
                # IS a defect.)
                logger.info(
                    "internal_chat.notify.no_salon_target tenant=%s thread=%s message=%s "
                    "— no manager address is configured, the salon copy is skipped",
                    tenant.slug,
                    thread.id,
                    message.id,
                )
                return
            # Loud, not silent: an undeliverable staff message is a
            # configuration defect, and silence is what made the whole
            # internal-chat feature invisible in the first place.
            logger.warning(
                "internal_chat.notify.no_recipients tenant=%s thread=%s message=%s "
                "sender_role=%s — nobody was told about this message",
                tenant.slug,
                thread.id,
                message.id,
                message.sender_role,
            )
            return

        text = build_notification_text(message=message)
        with bot_scope(_salon_bot_for(tenant)):
            failures = send_max_notification(text=text, addresses=recipients)

        if failures:
            logger.warning(
                "internal_chat.notify.partial_failure tenant=%s thread=%s channel=%s "
                "recipients=%d failures=%d",
                tenant.slug,
                thread.id,
                channel,
                recipient_count,
                failures,
            )
        else:
            logger.info(
                "internal_chat.notify.sent tenant=%s thread=%s channel=%s recipients=%d",
                tenant.slug,
                thread.id,
                channel,
                recipient_count,
            )
    except Exception:  # noqa: BLE001 — a failed notice must not cost the message
        logger.exception(
            "internal_chat.notify.failed message=%s",
            getattr(message, "id", None),
        )
