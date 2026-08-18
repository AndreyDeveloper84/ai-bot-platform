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

* **master → admin**: the salon side. ``Tenant.manager_chat_id`` first,
  then the configured fallback channel — the same cascade the booking
  notice uses, deliberately, so a salon configures one destination rather
  than one per feature.
* **admin → master**: that thread's master personally, via
  ``CatalogMaster.linked_bot_user.chat_id``. There is no fallback here on
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


def _recipients_for(message) -> tuple[list[str], str]:
    """``(chat_ids, channel_label)`` for this message's direction."""

    from apps.handoff.notify import get_notify_chat_ids

    thread = message.thread
    tenant = thread.tenant

    if message.sender_role == "master":
        # To the salon side. Same cascade as the booking notice so a salon
        # configures one destination, not one per feature.
        manager_chat_id = (getattr(tenant, "manager_chat_id", "") or "").strip()
        if manager_chat_id:
            return [manager_chat_id], "manager"
        fallback = get_notify_chat_ids()
        return (list(fallback), "fallback") if fallback else ([], "none")

    # To the master personally. No fallback on purpose: broadcasting a
    # message meant for one master to the salon's shared channel would
    # leak a private conversation to whoever reads that chat.
    master = getattr(thread, "master", None)
    linked = getattr(master, "linked_bot_user", None)
    chat_id = (getattr(linked, "chat_id", "") or "").strip() if linked else ""
    return ([chat_id], "master") if chat_id else ([], "none")


def notify_internal_message(*, message) -> None:
    """Announce one internal-chat message in MAX. NEVER raises."""

    from apps.channels.bot_context import bot_scope
    from apps.handoff.notify import send_max_notification

    try:
        thread = message.thread
        tenant = thread.tenant
        chat_ids, channel = _recipients_for(message)

        if not chat_ids:
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
            failures = send_max_notification(text=text, chat_ids=chat_ids)

        if failures:
            logger.warning(
                "internal_chat.notify.partial_failure tenant=%s thread=%s channel=%s "
                "recipients=%d failures=%d",
                tenant.slug,
                thread.id,
                channel,
                len(chat_ids),
                failures,
            )
        else:
            logger.info(
                "internal_chat.notify.sent tenant=%s thread=%s channel=%s recipients=%d",
                tenant.slug,
                thread.id,
                channel,
                len(chat_ids),
            )
    except Exception:  # noqa: BLE001 — a failed notice must not cost the message
        logger.exception(
            "internal_chat.notify.failed message=%s",
            getattr(message, "id", None),
        )
