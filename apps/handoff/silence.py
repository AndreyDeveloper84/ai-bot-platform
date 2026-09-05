"""Telling the person why the bot stopped answering — and when it is back (DRF-1486).

# What happened on 04.09.2026

The client wrote to a SALON bot and was told «Передаю менеджеру — ответят
в течение 30 минут.» An `AdminTask` was filed at 12:15 UTC against the
salon's dialog. The bot that then went silent was the GLOBAL one — a
different bot, in a different chat, that had said nothing about any
operator. It stayed silent for 1 hour 24 minutes, over five incoming
messages, and the person had no way to connect the two events.

The mute itself is correct and stays (DRF-1015): a human operator and a
bot must not answer the same person at once, and the mute travels with
the person across their dialogs on purpose. What was missing is the
sentence that makes the silence readable.

# The contract

* **On a state transition, never on a message.** The notice is sent when
  the mute engages for this dialog, and the release notice when it lifts.
  Five inbound messages produce one notice, not five —
  ``HandoffSilenceNotice.silence_notified_at`` is what remembers.
* **Two wordings, one true fact each.** When the handoff was announced in
  THIS dialog the person already knows a human is coming and only needs
  to know why the bot stopped; when the mute travelled here from another
  bot, the wording has to say exactly that, because nothing else will.
* **No identifiers, ever.** No task id, no tenant, no queue name, no
  salon name, no operator name. The person needs the state of their
  conversation, not a view of our schema (and cross-tenant naming is
  exactly what the admin banner warns about).
* **Best-effort, hard.** A failed notice must never break inbound
  processing, roll back a handoff, or leave the release path half-done.
  Everything is caught and logged — the mute working is more important
  than the explanation of it. Delivery and transcript are contained
  separately so a log line never blames the wrong half.

### Surfaces

MAX, both client paths: the global (marketplace) bot and the per-tenant
salon bot. The salon one matters most — the 04.09 incident STARTED there.
Telegram (``apps.channels.telegram.handler``) has the same silent branch
and is deliberately not wired: it carries no pilot traffic, and a second
surface would double the outbound-identity question with nothing to test
it against.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.utils import timezone

from apps.handoff.models import HandoffSilenceNotice

if TYPE_CHECKING:  # pragma: no cover — typing only
    from apps.conversations.models import Conversation
    from apps.handoff.models import AdminTask
    from apps.identity.models import BotUser

logger = logging.getLogger(__name__)

#: The mute engaged in the dialog that ALSO delivered the handoff
#: confirmation. The person knows a human is coming; what they do not know
#: is that the bot has deliberately stepped back, and for how long.
SILENCE_ANNOUNCED_HERE_TEXT = (
    "Ваш вопрос уже у сотрудника — он с ним разбирается.\n"
    "Пока сотрудник на связи, я не отвечаю: иначе вы получите от нас "
    "два разных ответа на один вопрос.\n"
    "Как только он закончит, я напишу вам и снова смогу помочь."
)

#: The mute engaged in a dialog that never mentioned an operator — the
#: person asked for a human somewhere else, and THIS bot went quiet. That
#: connection is invisible from where they are sitting, so it is the first
#: thing the message says.
SILENCE_TRANSFERRED_TEXT = (
    "Здесь я пока не отвечаю: вы просили связать вас с сотрудником "
    "в другом нашем чате, и он уже занимается вашим вопросом.\n"
    "Мы не отвечаем одновременно, чтобы вы не получили два разных ответа.\n"
    "Как только сотрудник закончит, я напишу вам сюда и снова смогу помочь."
)

#: The mute lifted. Until DRF-1486 the bot simply started answering again
#: on the next message the person happened to send — which, after an hour
#: of silence, they had every reason not to send.
SILENCE_RELEASED_TEXT = (
    "Сотрудник завершил разговор — я снова на связи.\n"
    "Если вопрос ещё открыт, напишите его здесь, я помогу."
)

#: Tags the assistant turns in the transcript so an operator reading the
#: dialog later can tell the chrome from the conversation.
SILENCE_ACTION_TYPE = "handoff_silence_notice"
RELEASE_ACTION_TYPE = "handoff_silence_released"


def mark_handoff_announced(*, conversation: "Conversation", chat_id: str) -> None:
    """Record that THIS dialog delivered the handoff confirmation.

    Called by the escalation path right after the «передаю менеджеру» line
    goes out. It does not send anything: it decides which of the two
    wordings the person reads when the mute actually bites, on their next
    message. Without it, a handoff asked for in the global chat but filed
    against a salon's queue would be described to the person as having
    come from somewhere else — true of the task's tenant, false of their
    experience.
    """

    try:
        notice = _open_notice(conversation)
        if notice is None:
            HandoffSilenceNotice.objects.create(
                conversation=conversation,
                bot_user=conversation.bot_user,
                chat_id=str(chat_id or ""),
                announced_here=True,
            )
            return
        if not notice.announced_here:
            notice.announced_here = True
            notice.chat_id = notice.chat_id or str(chat_id or "")
            notice.save(update_fields=["announced_here", "chat_id"])
    except Exception:  # noqa: BLE001 — bookkeeping must never break a handoff
        logger.exception(
            "handoff.silence.mark_announced_failed conversation=%s",
            getattr(conversation, "id", None),
        )


def notify_silence(
    *,
    conversation: "Conversation",
    bot_user: "BotUser",
    chat_id: str,
    trace_id: object = None,
) -> bool:
    """Tell the person once that the bot is muted. Returns True when sent.

    Idempotent per mute episode: the first muted turn sends the notice and
    stamps ``silence_notified_at``; every later turn in the same episode
    finds the stamp and returns False. The episode ends when
    :func:`release_notices_for` clears the row, so a second handoff later
    gets its own single notice.
    """

    try:
        notice = _open_notice(conversation)
        if notice is None:
            notice = HandoffSilenceNotice.objects.create(
                conversation=conversation,
                bot_user=bot_user,
                chat_id=str(chat_id or ""),
                announced_here=False,
            )
        if notice.silence_notified_at is not None:
            return False
        text = SILENCE_ANNOUNCED_HERE_TEXT if notice.announced_here else SILENCE_TRANSFERRED_TEXT
        target_chat = str(chat_id or "") or notice.chat_id
        if not target_chat:
            logger.warning(
                "handoff.silence.no_chat_id conversation=%s",
                conversation.id,
            )
            return False
        # Claim the right to speak BEFORE speaking. The stamp is the whole
        # of «once per episode», and a read-modify-write cannot carry that
        # weight: the consumer is single-threaded, but a web worker closing
        # a task runs `release_notices_for` against these same rows. Whoever
        # wins this conditional UPDATE owns the notice; everyone else finds
        # zero rows and says nothing.
        #
        # Claiming first also picks the safer failure. Send-then-stamp
        # repeats the message when the stamp fails; stamp-then-send loses it
        # when the send fails. For an explanation of silence, saying it twice
        # is worse than saying it once and missing it — the person already
        # has an unanswered dialog in front of them.
        claimed = HandoffSilenceNotice.objects.filter(
            pk=notice.pk, silence_notified_at__isnull=True
        ).update(silence_notified_at=timezone.now(), chat_id=target_chat)
        if not claimed:
            return False
        _send(chat_id=target_chat, text=text)
        _record(
            conversation=conversation,
            text=text,
            action_type=SILENCE_ACTION_TYPE,
            trace_id=trace_id,
        )
        logger.info(
            "handoff.silence.notified conversation=%s announced_here=%s",
            conversation.id,
            notice.announced_here,
        )
        return True
    except Exception:  # noqa: BLE001 — the mute matters more than the notice
        logger.exception(
            "handoff.silence.notify_failed conversation=%s",
            getattr(conversation, "id", None),
        )
        return False


def release_notices_for(task: "AdminTask") -> int:
    """Close every open notice this task's closure actually released.

    Called after :func:`apps.handoff.services.release_conversation_to_bot`
    on both close paths. Walks the open notices belonging to the same
    channel identity — the mute radius is the person, not the dialog
    (DRF-1015) — and for each one re-asks the mute question. A notice
    whose dialog is STILL muted (another task open on this person) is left
    alone: telling them the bot is back while it is not would be worse
    than the silence.

    A notice that never sent a silence message is closed without sending a
    release one: there is nothing to release the person from.

    The episode is closed BEFORE the message goes out, and the send is
    contained separately. That ordering is load-bearing: a chat that is
    unreachable (blocked bot, 4xx, network) used to leave the row open
    forever, and an open row is what ``notify_silence`` reads as «already
    explained». One transient send failure would have switched this whole
    mechanism off for that dialog permanently, silently, and for every
    future episode — a worse outcome than the missed sentence it was
    protecting.

    Returns the number of release messages actually sent.
    """

    sent = 0
    try:
        bot_user = task.bot_user
        open_notices = list(
            HandoffSilenceNotice.objects.filter(
                released_at__isnull=True,
                bot_user__channel=bot_user.channel,
                bot_user__channel_user_id=bot_user.channel_user_id,
            ).select_related("conversation", "bot_user")
        )
    except Exception:  # noqa: BLE001 — never break the close path
        logger.exception("handoff.silence.release_lookup_failed task=%s", getattr(task, "id", None))
        return 0

    for notice in open_notices:
        try:
            if _still_muted(notice):
                logger.info(
                    "handoff.silence.release_deferred conversation=%s reason=another_open_task",
                    notice.conversation_id,
                )
                continue
            # Same conditional-UPDATE claim as `notify_silence`, for the
            # same reason: two operators closing this person's two tasks at
            # once would otherwise both pass the mute check and both say
            # «сотрудник завершил разговор».
            claimed = HandoffSilenceNotice.objects.filter(
                pk=notice.pk, released_at__isnull=True
            ).update(released_at=timezone.now())
            if not claimed:
                continue
            logger.info(
                "handoff.silence.released conversation=%s notified=%s",
                notice.conversation_id,
                notice.silence_notified_at is not None,
            )
            if notice.silence_notified_at is not None and notice.chat_id:
                # Delivery and transcript are contained SEPARATELY, so the log
                # names what actually broke. Folded together, a transcript
                # failure was reported as `release_send_failed` — sending the
                # next person to debug the network the message had just
                # travelled over successfully.
                try:
                    _send(chat_id=notice.chat_id, text=SILENCE_RELEASED_TEXT)
                    sent += 1
                except Exception:  # noqa: BLE001 — the episode is closed either way
                    logger.exception(
                        "handoff.silence.release_send_failed conversation=%s",
                        notice.conversation_id,
                    )
                try:
                    _record(
                        conversation=notice.conversation,
                        text=SILENCE_RELEASED_TEXT,
                        action_type=RELEASE_ACTION_TYPE,
                        trace_id=None,
                    )
                except Exception:  # noqa: BLE001 — the client already read it
                    logger.exception(
                        "handoff.silence.release_record_failed conversation=%s",
                        notice.conversation_id,
                    )
        except Exception:  # noqa: BLE001 — one dialog must not block the rest
            logger.exception(
                "handoff.silence.release_failed conversation=%s",
                notice.conversation_id,
            )
    return sent


# --------------------------------------------------------------------------- #
# Internals                                                                    #
# --------------------------------------------------------------------------- #
def _open_notice(conversation: "Conversation") -> HandoffSilenceNotice | None:
    """The one un-released notice for this dialog, if the episode is open."""

    return HandoffSilenceNotice.objects.filter(
        conversation_id=conversation.id, released_at__isnull=True
    ).first()


def _still_muted(notice: HandoffSilenceNotice) -> bool:
    """Re-ask DRF-1015's own question — imported, never re-implemented."""

    from apps.orchestrator.handoff import global_handoff_muted

    return global_handoff_muted(
        conversation=notice.conversation,
        channel=notice.bot_user.channel,
        channel_user_id=notice.bot_user.channel_user_id,
    )


def _send(*, chat_id: str, text: str) -> None:
    """Outbound for a notice. Short timeout — this is chrome, not an answer.

    Sender identity: neither the global nor the per-tenant client path
    enters a ``bot_scope`` (only ``salon_handler`` does), so both the
    silence notice — sent from inside the inbound turn — and the release
    notice — sent later from the admin or the sweep — resolve to the same
    ``settings.MAX_BOT_TOKEN``. The two messages therefore arrive from the
    same avatar as every other reply in that dialog. Should a client path
    ever acquire a ``bot_scope``, this call has to carry the bot forward
    explicitly instead: the release runs outside the turn, and the
    fallback would then answer as the wrong bot.
    """

    from apps.channels.max.outbound import send_message

    send_message(chat_id=chat_id, text=text, timeout=5.0)


def _record(*, conversation: "Conversation", text: str, action_type: str, trace_id: object) -> None:
    """Persist the notice as an assistant turn — transcript, not memory.

    It goes into ``Message`` so the operator opening the dialog can read
    what the bot said to this person, and it deliberately does NOT go into
    short-term memory: the concierge grounds its next reply on what the
    person and the bot discussed, and «я сейчас молчу» is not part of that
    conversation.

    What is recorded is the INTENT, not a delivery receipt — the row is
    written even when the send that precedes it failed. That is the house
    convention (``handler.py`` records the assistant turn before
    ``send_message`` for exactly this reason: a failed send must not lose
    the record of what was meant), and the alternative is worse — a dialog
    whose transcript silently omits turns is unreadable as evidence. The
    delivery outcome lives in the log line next to it.
    """

    from apps.conversations.services import record_global_message, record_message
    from apps.identity.services.global_tenant import get_global_bot_tenant
    from apps.tenancy.context import tenant_scope

    sentinel = get_global_bot_tenant()
    if conversation.tenant_id == sentinel.id:
        record_global_message(
            conversation,
            role="assistant",
            content=text,
            rendered_text=text,
            action_type=action_type,
            trace_id=trace_id,  # type: ignore[arg-type]
        )
    else:
        # The scope is taken from the conversation, never from the caller —
        # the same rule ``AdminTaskAdmin.save_model`` follows, and for the
        # same reason: the release notice runs from ``transaction.on_commit``,
        # which fires AFTER the admin's ``with tenant_scope(...)`` has exited.
        # ``record_message`` raises without a tenant in scope, so without this
        # the salon dialog's release line reached the client and never reached
        # the transcript — and the failure surfaced as
        # ``release_send_failed``, blaming a network that had worked.
        with tenant_scope(conversation.tenant):
            record_message(
                conversation,
                role="assistant",
                content=text,
                rendered_text=text,
                action_type=action_type,
                trace_id=trace_id,  # type: ignore[arg-type]
            )
