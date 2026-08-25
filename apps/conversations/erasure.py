"""Dialogue anonymisation — the third obligation of «удалить всё» (DRF-1369).

Owner ruling ``OD_MEMORY.md`` §4, verbatim:

    «удалить всё» = удалить память/профиль и **обезличить переписку с
    гарантией недоступности для prompt pipeline»

Three obligations. The first two landed (DRF-1366 … DRF-1370). This module is
the third — and the word that decides its shape is **гарантия**.

# Why «filter it out at every reader» was not enough

The obvious build is a read-side filter: teach the prompt assemblers to skip an
erased person's turns. The contour had already run that experiment and the
result was on disk before this ticket opened. ``short_term.clear`` carried the
docstring «Used by the 152-ФЗ delete-my-data workflow» and had **no caller
anywhere in ``apps/``** — the intention was written down, in the right module,
by someone who meant it, and the guarantee was still absent. A window of raw
sentences survived «забудь всё» with a 24-hour TTL as its only lifetime, and
sutki are sutki while «удалить» is heard as «сейчас».

So anonymisation here does not mark the text and hope. It **moves** it:

    Message.content        →  ""            (blanked in place)
    Message.rendered_text  →  ""            (blanked in place)
    the redacted body      →  ArchivedMessage

Every reader of the dialogue reads ``Message``. The ones this ticket found, the
ones it did not, and the one written next month in a package nobody thought to
grep — all of them get an empty string, by construction, with no filter to
forget. That is what «гарантия, а не намерение» buys and a per-reader filter
does not: the property survives the arrival of a **new** reader.

Two stores cannot be moved because they are not rows, so they are deleted:

    short_term (Redis LIST)      →  short_term.clear()
    pii_tokenizer rev-map (Redis)→  pii_tokenizer.clear_conversation()

The second one is easy to miss and holds the worst of it: the tokeniser's
reverse map is literally ``rev:<PHONE_a8f2c1d4_1>`` → the person's real phone
number, kept so the LLM's reply can be de-tokenised. Clearing the message
window and leaving that behind would empty the sentence and keep the number.

The remaining prompt-bound reader that touches ``Message`` at all —
``master_api.services.ai_drafts._recent_history`` — additionally honours the
cutoff, so the master's draft prompt does not even receive the blanked rows.
That is belt-and-braces, not the mechanism; the mechanism is that the text is
not in the column.

The standing proof that a *future* reader cannot quietly reopen the route is
the registry guard in ``apps/conversations/dialogue_readers.py`` and
``apps/conversations/tests/test_dialogue_reader_registry.py``.

# «Обезличить», precisely

* The **words stay.** The owner's reason is explicit: «это единственная запись
  того, что бот на самом деле сказал человеку, и она нужна при разборе
  инцидента и спора о брони». An anonymisation that deletes them is erasure
  under another name, which the ruling rejects — so this module ships with a
  test asserting the archive is still *readable*, not only that the prompt is
  empty.
* The **direct identifiers do not.** Bodies go through
  :class:`apps.replay.redactor.Redactor` (pinned ``regex_v1``: phone, e-mail,
  card, OTP, tokened URL) on the way into the archive.
* The **person key is kept.** ``ArchivedMessage`` still hangs off the
  conversation, which still hangs off the ``BotUser`` shell. A dispute about a
  booking is unresolvable without knowing whose booking it was. This is
  pseudonymisation of the content, not severance of the row — named here
  rather than implied, because claiming more than was done is the failure this
  ticket exists to repair.

# A cutoff, not a flag

``Conversation.anonymized_through`` is a timestamp. «Забудь всё» is not the end
of the dialogue — the person keeps talking to the bot, and the turns they take
*after* the request are theirs again. Anonymising the thread as a whole would
mean the hourly ``forget_all_sweep`` blanked their live conversation every hour
forever. The cutoff is taken from ``UPC.forget_all_requested_at`` (the moment
the person asked), so re-running is a no-op by construction — the same
idempotence-by-construction the sweep already relies on.

# Retention — the term, named

``OD_MEMORY.md`` §4: «Срок хранения обезличенного назвать явно. „Бессрочно" —
отсутствие решения.»

The canon does not contain one. ``docs/152fz/REESTR_PDN_DRAFT.md`` records
``Conversation``/``Message`` as «Retention НЕ НАЙДЕН» and lists the storage
term for dialogue text among the questions still open with legal counsel
(block D, q. 19). So the term here is **derived**, not invented, and the
derivation is the audit trail:

    AUDIT_LOG_RETENTION_DAYS    = 90   (config/settings/base.py)
    PAYMENT_EVENT_RETENTION_DAYS= 90   (idem)

An incident review reconstructs a turn from the ``AuditLog`` row and the
dialogue row **together**. A dialogue body that outlives its audit row is
unpaired evidence — it can be read but not placed — and one that dies first
leaves the audit row pointing at nothing. So the anonymised body is kept for
exactly as long as the forensic tier it is reviewed alongside: **90 days**,
:data:`ANONYMIZED_DIALOGUE_RETENTION_DAYS`, enforced by
``apps.conversations.tasks.purge_expired_archived_messages``.

Enforced, not merely declared — a term nothing sweeps is one more docstring
promise, which is precisely the defect DRF-1370 had to repair three docstrings
at a time.

**Open with the owner** (registered in ``docs/OPEN_DECISIONS.md``): 90 days is
derived from the audit tier, not ruled. If a booking dispute window in
consumer law is longer, the number moves — one setting, one line.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit.services import write_audit
from apps.conversations.models import AiDraft, ArchivedMessage, Conversation, Message

logger = logging.getLogger(__name__)


#: How long an anonymised dialogue body is kept. See the module docstring for
#: the derivation and for the question standing with the owner. Overridable via
#: ``settings.ANONYMIZED_DIALOGUE_RETENTION_DAYS`` for staging.
ANONYMIZED_DIALOGUE_RETENTION_DAYS = 90


def retention_days() -> int:
    return int(
        getattr(
            settings,
            "ANONYMIZED_DIALOGUE_RETENTION_DAYS",
            ANONYMIZED_DIALOGUE_RETENTION_DAYS,
        )
    )


@dataclass(frozen=True)
class AnonymizeResult:
    """What one run actually moved. Counts and ids, never values."""

    conversations: int = 0
    messages_archived: int = 0
    drafts_cleared: int = 0
    windows_cleared: int = 0
    conversation_ids: tuple[uuid.UUID, ...] = field(default_factory=tuple)

    @property
    def changed(self) -> bool:
        return bool(self.conversations)


def _redact(text: str) -> str:
    """Strip direct identifiers, keep the sentence.

    Imported lazily: ``apps.replay`` pulls settings-bound machinery that the
    conversations app has no other reason to load at import time.
    """

    from apps.replay.redactor import Redactor

    return Redactor().redact_text(text or "")


def _redact_value(value: object) -> object:
    """Recursive redaction of a JSON payload (``action_data`` / ``tool_call``)."""

    from apps.replay.redactor import Redactor

    if value is None:
        return None
    return Redactor().redact_value(value)


def _clear_redis_stores(conversation_id: uuid.UUID) -> None:
    """Drop both per-conversation Redis stores of raw dialogue PII.

    Runs BEFORE the database transaction on purpose. Redis is not in the
    transaction, so a failure here must leave the cutoff unmoved — otherwise
    the sweep sees an already-anonymised thread on its next pass and never
    retries the half that failed.
    """

    from apps.llm import pii_tokenizer
    from apps.orchestrator.memory import short_term

    # The window itself — the raw sentence the fact was extracted from. This
    # is the production caller the docstring of `short_term.clear` promised
    # and the repository did not have (DRF-1369).
    short_term.clear(conversation_id)
    # The tokeniser reverse map: `rev:<PHONE_…>` → the real phone number,
    # kept so the model's reply can be de-tokenised. Emptying the sentence
    # and leaving this behind would keep the number.
    pii_tokenizer.clear_conversation(conversation_id)


def shell_ids_for_person(
    *,
    bot_user: object | None = None,
    ayla_user_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    """Every ``BotUser`` shell of one human, for the anonymiser's subject set.

    The dialogue is cross-tenant for the same reason memory is: the pilot
    resolves one shell under the ``global_bot`` sentinel tenant and another
    under ``MAX_BOT_TENANT_SLUG``, and both hold conversations. Anonymising
    only the requesting shell would report an erasure and leave the person's
    words readable on the row the bot actually talks to.

    Narrower than :func:`apps.identity.services.privacy._person_shell_ids` on
    purpose: that one also fans out over ``(channel, channel_user_id)`` and
    has to fail closed on an identity conflict, because it drives a
    *destructive* cascade over other people's rows. This one is called by the
    two erasure verbs that are already keyed on ``ayla_user_id`` — the memory
    key — so the key is the subject and there is no conflict to resolve.
    """

    from apps.identity.models import BotUser

    ids: set[uuid.UUID] = set()
    if bot_user is not None and getattr(bot_user, "id", None) is not None:
        ids.add(bot_user.id)
        ayla_user_id = ayla_user_id or getattr(bot_user, "ayla_user_id", None)
    if ayla_user_id is not None:
        ids.update(
            BotUser.all_tenants.filter(ayla_user_id=ayla_user_id).values_list("id", flat=True)
        )
    return sorted(ids)


def anonymize_dialogue(
    bot_user_ids: Iterable[uuid.UUID],
    *,
    through: datetime,
    reason: str,
) -> AnonymizeResult:
    """Anonymise every dialogue of ``bot_user_ids`` up to ``through``.

    Idempotent and re-runnable: a thread whose cutoff already reaches
    ``through`` is skipped, and a message whose body is already archived is
    skipped by the ``OneToOne`` reverse filter rather than by the caller's
    good behaviour.

    Args:
      bot_user_ids: every shell of the person (memory is cross-tenant, and so
        is the dialogue — the same human holds a ``BotUser`` per tenant).
      through: the cutoff. Messages created at or before this instant are
        anonymised; later turns are the person's own again. For «забудь всё»
        pass ``UPC.forget_all_requested_at`` — the moment they asked, which
        makes re-running free. For the account delete, «now».
      reason: :class:`ArchivedMessage.Reason` value.

    Returns:
      :class:`AnonymizeResult` — counts, never values.
    """

    ids = [i for i in bot_user_ids if i is not None]
    if not ids:
        return AnonymizeResult()

    conversations = list(
        # ``created_at__lte`` matters: a thread STARTED after the request
        # instant cannot hold a pre-cutoff turn, so anonymising it would move
        # a cutoff and wipe a live Redis window for nothing. Without this the
        # hourly forget-all sweep would blank the first window of every new
        # conversation a forgotten person opens.
        Conversation.all_tenants.filter(bot_user_id__in=ids, created_at__lte=through).only(
            "id", "tenant_id", "anonymized_through"
        )
    )
    touched: list[uuid.UUID] = []
    archived_total = 0
    drafts_total = 0
    windows_total = 0
    keep_until = timezone.now() + timedelta(days=retention_days())

    for conv in conversations:
        already = conv.anonymized_through
        if already is not None and already >= through:
            # The cutoff already covers this window. Nothing to bury, and —
            # importantly — no Redis clear: the hourly sweep must not keep
            # wiping the live window of someone who asked once and kept
            # talking.
            continue

        _clear_redis_stores(conv.id)
        windows_total += 1

        with transaction.atomic():
            doomed = list(
                Message.all_tenants.filter(
                    conversation_id=conv.id,
                    created_at__lte=through,
                    archived_body__isnull=True,
                ).order_by("created_at")
            )
            if doomed:
                ArchivedMessage.all_tenants.bulk_create(
                    [
                        ArchivedMessage(
                            tenant_id=m.tenant_id,
                            conversation_id=conv.id,
                            message_id=m.id,
                            role=m.role,
                            body=_redact(m.content),
                            rendered_body=_redact(m.rendered_text),
                            action_type=m.action_type or "",
                            action_data=_redact_value(m.action_data),
                            tool_call=_redact_value(m.tool_call),
                            original_created_at=m.created_at,
                            retention_until=keep_until,
                            reason=reason,
                        )
                        for m in doomed
                    ]
                )
                Message.all_tenants.filter(id__in=[m.id for m in doomed]).update(
                    content="",
                    rendered_text="",
                    # action_data is not metadata. The clarification block
                    # holds the question the person was asked and the options
                    # they were offered, and `handler._last_clarification_offer`
                    # reads it back to rebuild a pending multi-select — a
                    # prompt path. Blanking the two text columns and leaving
                    # this one behind kept the words reachable; the registry
                    # guard found it, which is what the registry is for.
                    action_data=None,
                    # Same reason: the model's tool arguments quote the
                    # person's phrasing.
                    tool_call=None,
                )
                archived_total += len(doomed)

            # AiDraft.content quotes the customer verbatim — it is the master's
            # unsent reply built from these very turns. Layer 1 clears it at
            # terminal status (`master_api.services.ai_drafts`); an ACTIVE
            # draft would otherwise carry the erased person's words into the
            # master's compose box after the erasure.
            drafts_total += (
                AiDraft.all_tenants.filter(conversation_id=conv.id)
                .exclude(content="")
                .update(content="")
            )

            Conversation.all_tenants.filter(id=conv.id).update(
                anonymized_through=through, anonymized_reason=reason
            )
        touched.append(conv.id)

    result = AnonymizeResult(
        conversations=len(touched),
        messages_archived=archived_total,
        drafts_cleared=drafts_total,
        windows_cleared=windows_total,
        conversation_ids=tuple(touched),
    )

    if result.changed:
        write_audit(
            "conversation.anonymized",
            target="Conversation",
            target_id=touched[0],
            payload={
                "reason": reason,
                "through": through.isoformat(),
                "conversations": result.conversations,
                "messages_archived": result.messages_archived,
                "drafts_cleared": result.drafts_cleared,
                # Ids, never bodies (C5 §6.2) — the audit row must not carry
                # the text this function just went to the trouble of moving.
                "conversation_ids": [str(c) for c in touched],
                "retention_until": keep_until.isoformat(),
            },
        )
        logger.info(
            "conversations.erasure.anonymized reason=%s conversations=%d messages=%d drafts=%d",
            reason,
            result.conversations,
            result.messages_archived,
            result.drafts_cleared,
        )
    return result


def is_anonymized(conversation: Conversation) -> bool:
    """Has any part of this thread been anonymised?"""

    return getattr(conversation, "anonymized_through", None) is not None


def read_anonymized_dialogue(
    conversation: Conversation,
    *,
    purpose: str,
) -> list[ArchivedMessage]:
    """The sole sanctioned read of the archive — incident review only.

    The owner kept the dialogue for a reason, and a store that cannot be read
    would be erasure wearing anonymisation's name. So this exists, it returns
    the real (redacted) words, and it is audited — same posture as
    :class:`apps.identity.services.red_zone_reader.RedZoneReader`: mandatory
    ``purpose``, one row per read.

    NOT a prompt path and never to become one. Anything that assembles an LLM
    prompt reads ``Message``; the registry guard in
    ``apps/conversations/dialogue_readers.py`` fails if a new caller of this
    function appears without classifying itself.
    """

    if not purpose or not purpose.strip():
        raise ValueError("read_anonymized_dialogue requires a non-empty purpose")

    rows = list(
        ArchivedMessage.all_tenants.filter(conversation_id=conversation.id).order_by(
            "original_created_at"
        )
    )
    write_audit(
        "conversation.archive_read",
        target="Conversation",
        target_id=conversation.id,
        payload={"purpose": purpose.strip(), "rows": len(rows)},
    )
    return rows
