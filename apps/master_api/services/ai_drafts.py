"""Master M6 AI drafts service layer (Bundle B / item 4 backend).

Three operations driving the §M6 «Предложенный ответ» card buttons:

* :func:`generate_draft_for_conversation` — produce a new LLM draft for
  this master to act on. Replaces any prior ACTIVE draft on the same
  conversation. Idempotent within a 60s window for the same trigger
  message (cost guard).
* :func:`send_draft_as_master` — master tapped «Отправить от себя».
  Creates a master-attributed assistant :class:`Message` and marks the
  draft :attr:`AiDraft.Status.SENT_AS_MASTER`. Honours the
  ``override_content`` path for the «Отредактировать» button.
* :func:`release_draft_to_ai` — master tapped «Пусть помощник ответит».
  Creates a plain assistant :class:`Message` (no master attribution)
  and marks the draft :attr:`AiDraft.Status.RELEASED_TO_AI`.

### Spec quote (master-mobile §M6 lines 662-671)

    «✨ Предложенный ответ ... [Отправить от себя] [Отредактировать]
    [Пусть помощник ответит]»

### Spec quote (master-mobile §M6 lines 706-712)

    «When master taps «Отправить от себя» on a draft, the message
    renders to the customer as «Помощник: …». Same single assistant
    identity. Master's authorship is recorded in attribution metadata
    (``actor_type=master``, ``composed_by=master_id``)»

### LLM integration entry point

We do **not** call ``apps.orchestrator.pipeline.turn()`` — that's the
inbound-webhook pipeline and runs intent classification, safety pre/post
checks, skill dispatch, channel outbound, etc. The master-draft flow
needs a *single LLM completion* with the conversation history as
context; not a full turn.

We call ``apps.llm.router.get_router().get_provider(tenant, skill='master_draft')``
followed by ``provider.complete(messages, model=...)`` — same pattern
the FAQ skill uses (``apps/skills/faq/skill.py:170``). Wrapped in
``asyncio.run`` to bridge from the sync DRF view.

### Cost tracking

The cost is computed via :func:`apps.llm.pricing.compute_cost` from the
returned ``CompletionResult.prompt_tokens`` + ``completion_tokens`` and
stored on :attr:`AiDraft.llm_cost_usd`. Unknown models silently fall
back to 0 (audit row carries the model so an unknown-model gap shows
up in observability).

### Idempotency window + double-paid LLM guard (PR #535 follow-up)

A second ``generate_draft_for_conversation`` call within 60 seconds
returns the existing ACTIVE draft when:
  * the most recent customer message hasn't changed since generation
  * AND the existing row is still :attr:`AiDraft.Status.ACTIVE`

**Concurrency hardening (Blocker #1):** the idempotency check, the
per-master cost guard, AND the LLM call all run INSIDE a
``select_for_update`` lock on the :class:`Conversation` row. Without
the lock two concurrent generate requests (rapid double-tap, network
retry) both saw «no ACTIVE» outside the lock, both called the LLM,
and the partial unique constraint caught at INSERT — second 500'd as
IntegrityError. Holding the lock during the LLM call (1-3s typical)
is fine: concurrent generates per master per conversation are rare,
and serialising them is exactly what prevents the double-bill.

### Cost cap order (Blocker #3)

The per-master cumulative cost guard
(:func:`apps.master_api.services.ai_draft_limits.check_cost_cap`) fires
BEFORE the LLM call (inside the conversation lock). Pre-fix the only
cost accounting happened AFTER the call — useless for blocking. The
tenant-level cap in :mod:`apps.llm.cost_tracker` still runs INSIDE the
provider, but per-master adds a finer-grained guard on top.

### Staleness check at send time (Blocker #4)

When the master taps «Отправить от себя» or «Пусть помощник ответит»,
we recompute the latest customer message id and compare against
:attr:`AiDraft.trigger_message_id`. If they differ → 409
``draft_stale``. Spec rationale: customer wrote «можно записаться?» →
master generated a booking answer → while master was reading, customer
wrote «отмените запись» → master must NOT ship the stale booking
answer in response to a cancellation. Frontend force-regenerates.

We chose Option A (send-time comparison) over Option B (signal-based
REPLACED transition on new USER messages): Option B couples the
conversations app to a signal-bus across the master_api boundary and
forces every USER message write to also write to AiDraft. Option A
is one extra SELECT inside the existing lock — cleaner separation,
no cross-app signal contract.

### PII retention (Blocker #5 — Layer 1)

When the draft transitions to ANY terminal status (SENT_AS_MASTER,
RELEASED_TO_AI, REPLACED, DISMISSED), the :attr:`AiDraft.content`
column is cleared to the empty string in the SAME transaction as the
status flip. Drafts quote customer PII («Анна, ваша запись на 14:00
…») and must not accumulate at-rest beyond the moment of action.
The tokens / cost / model metadata STAYS for finance reconciliation;
Layer 2 (the daily Celery Beat sweep) eventually deletes the row
including the metadata at 30 days.

### IntegrityError defence-in-depth (Blocker #6)

With Blocker #1's lock the partial unique constraint
(``ai_draft_one_active_per_conversation``) is unreachable on the
happy path. Defence-in-depth: the INSERT is still wrapped in
try/except :class:`django.db.IntegrityError` so a future lock-bypass
bug doesn't cascade to a 500 — instead we SELECT the current ACTIVE
and return it.

### Out of scope (per Bundle B brief)

* Auto-trigger on inbound customer message — needs Celery + rate
  limiting; deferred.
* Pre-send persona check engine — Phase 1+; spec scope-out.
* Dismiss endpoint — reserved status value only.
* HUMAN_LOCKED conversations — refused at the generate gate with
  ``conversation_locked`` slug.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone as dj_timezone

from apps.audit.services import write_audit
from apps.catalog.models import CatalogMaster
from apps.conversations.models import AiDraft, Conversation, Message
from apps.conversations.services import record_message
from apps.events.services import emit
from apps.events.vocabulary import (
    MASTER_AI_DRAFT_AUTO_GENERATED,
    MASTER_AI_DRAFT_GENERATED,
    MASTER_DRAFT_RELEASED_TO_AI,
    MASTER_DRAFT_SENT_AS_SELF,
)
from apps.llm.pricing import UnknownModelError, compute_cost
from apps.llm.protocol import (
    CompletionResult,
    LLMError,
    LLMProviderUnavailable,
)
from apps.llm.router import get_router
from apps.master_api.services.ai_draft_limits import check_cost_cap
from apps.master_api.services.conversation_detail import (
    ConversationDetailError,
    _verify_master_involved,
)

logger = logging.getLogger(__name__)


# --- constants ------------------------------------------------------------

MAX_OVERRIDE_LENGTH = 2000
"""Mirror of :data:`apps.master_api.services.conversation_detail.MAX_COMPOSE_LENGTH`.

The «Отредактировать» path lets the master swap the LLM text with their
own typing. We cap it to the same 2000 chars as a plain compose so a
fat-finger paste can't land a multi-MB row in the audit log.
"""

MAX_HISTORY_MESSAGES = 20
"""Number of recent messages threaded into the LLM prompt as context.

Spec scoping decision: 20 covers «yesterday's exchange» without bloating
prompt tokens on a long-running conversation. Older context is summarised
implicitly by being absent — the model focuses on the freshest turn.
"""

IDEMPOTENCY_WINDOW = timedelta(seconds=60)
"""Re-tap window for ``generate_draft_for_conversation``.

Within this window AND with no new customer message in between, we
return the existing ACTIVE draft instead of calling the LLM again.
A second generate after a new customer message ALWAYS regenerates —
the prompt context has changed.
"""

SKILL_NAME = "master_draft"
"""Router skill slug used for per-skill provider resolution.

Operators can set ``settings.SKILL_LLM_PROVIDER['master_draft'] = 'anthropic'``
to canary master drafts onto a cheaper model without flipping the
org-wide default.
"""

# Statuses that count as «terminal» for the Blocker #5 content-clear path.
# ACTIVE is the only non-terminal state per the AiDraft.Status lifecycle.
_TERMINAL_STATUSES = frozenset(
    {
        AiDraft.Status.SENT_AS_MASTER,
        AiDraft.Status.RELEASED_TO_AI,
        AiDraft.Status.REPLACED,
        AiDraft.Status.DISMISSED,
    }
)


# --- error class ----------------------------------------------------------


class DraftActionError(ConversationDetailError):
    """Validation / authz failure for the AI draft endpoints.

    Inherits :class:`ConversationDetailError` so the existing view
    layer's ``except ConversationDetailError`` catches it uniformly —
    we get HTTP status mapping + slug-string serialisation for free.
    """


# --- response shapes ------------------------------------------------------


@dataclass(frozen=True)
class DraftResponse:
    """JSON shape for the generate endpoint."""

    draft_id: str
    content: str
    created_at: str
    llm_provider: str
    llm_model: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "content": self.content,
            "created_at": self.created_at,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
        }


@dataclass(frozen=True)
class DraftMessageResponse:
    """JSON shape for the send-as-me / release-to-ai endpoints."""

    message_id: str
    content: str
    sent_at: str
    composed_by_master: bool
    was_edited: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "content": self.content,
            "sent_at": self.sent_at,
            "composed_by_master": self.composed_by_master,
            "was_edited": self.was_edited,
        }


# --- helpers --------------------------------------------------------------


def _resolve_draft(
    master: CatalogMaster,
    conversation: Conversation,
    draft_id: uuid.UUID | str,
) -> AiDraft:
    """Fetch the draft for ``(master, conversation)`` or raise 404.

    Tenant + conversation + master triangulation is the security
    perimeter: a draft for a different master MUST NOT load even if
    the caller knows the UUID. We use ``all_tenants`` + an explicit
    ``tenant_id=master.tenant_id`` filter so the lookup is the same
    code path regardless of whether the caller wrapped us in a
    ``tenant_scope`` (the master_api views currently do not).
    """

    try:
        draft_uuid = uuid.UUID(str(draft_id))
    except (TypeError, ValueError) as exc:
        raise DraftActionError("not_found", "draft id is not a valid UUID", status=404) from exc

    draft = AiDraft.all_tenants.filter(
        id=draft_uuid,
        tenant_id=master.tenant_id,
        conversation_id=conversation.id,
        master_id=master.id,
    ).first()
    if draft is None:
        raise DraftActionError("not_found", "draft not found", status=404)
    return draft


def _latest_customer_message(conversation: Conversation) -> Message | None:
    """The most recent USER message on the conversation, if any.

    Used both for the idempotency window (compare against the existing
    draft's :attr:`AiDraft.trigger_message`) and for the LLM prompt
    context (it's the message the draft is responding to).
    """

    return (
        Message.all_tenants.filter(
            conversation_id=conversation.id,
            role=Message.Role.USER,
        )
        .order_by("-created_at")
        .first()
    )


def _latest_customer_message_id(conversation: Conversation) -> uuid.UUID | None:
    """Same as :func:`_latest_customer_message` but returns only the id.

    Used by the Blocker #4 staleness check at send/release time — we
    only need the id to compare against ``draft.trigger_message_id``,
    not the full row.
    """

    msg = _latest_customer_message(conversation)
    return msg.id if msg is not None else None


def _recent_history(conversation: Conversation, limit: int = MAX_HISTORY_MESSAGES) -> list[Message]:
    """Last ``limit`` messages in chronological order.

    Pulled desc + reversed so the index ``(conversation, created_at)``
    is honoured AND the slice cap matches the SQL fetch (no extra rows
    served and discarded).
    """

    rows = list(
        Message.all_tenants.filter(conversation_id=conversation.id)
        .exclude(role=Message.Role.SYSTEM)
        .order_by("-created_at")[:limit]
    )
    rows.reverse()
    return rows


def _build_prompt_messages(
    *,
    master: CatalogMaster,
    history: list[Message],
) -> list[dict[str, Any]]:
    """Assemble the chat-message array sent to the LLM provider.

    Phase 0 prompt — deliberately minimal. We use:
      * a system message defining the assistant identity (single «Помощник»
        voice per assistant-persona policy + the master's specialisation
        as context)
      * the recent history mapped role-for-role (USER → ``user``,
        ASSISTANT → ``assistant``, TOOL → ``tool``)

    Brand-voice / tenant tone is a Phase 1+ enhancement once the
    BrandVoiceConfig contract lands; for now the system prompt is
    fixed Russian-language matching §M6's «Помощник» voice.
    """

    specialization = (master.specialization or "").strip()
    role_hint = f" Мастер специализируется на: {specialization}." if specialization else ""
    system_prompt = (
        "Ты — «Помощник», единый голос ассистента салона. "
        "Отвечай клиенту коротко (1-3 предложения), вежливо и по делу. "
        "Никогда не подписывайся именем мастера или администратора. "
        "Если не уверен в ответе — мягко предложи уточнить у мастера." + role_hint
    )

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for m in history:
        # Tool / system rows are filtered out in _recent_history; map
        # ASSISTANT and USER 1:1. Empty content → skip (no information
        # value in the prompt; also avoids tripping provider validation
        # on empty turns).
        body = (m.rendered_text or m.content or "").strip()
        if not body:
            continue
        if m.role == Message.Role.USER:
            messages.append({"role": "user", "content": body})
        elif m.role == Message.Role.ASSISTANT:
            messages.append({"role": "assistant", "content": body})
    return messages


def _safe_compute_cost(model: str, result: CompletionResult) -> Decimal:
    """Wrap :func:`compute_cost` so an unknown model never breaks the draft.

    A missing pricing entry is a deploy-time misconfig — surfaced via
    the audit row + WARN log. The draft still ships with cost=0 so the
    master's flow isn't blocked by a pricing-table omission.
    """

    if not model:
        return Decimal(0)
    try:
        return compute_cost(
            model,
            input_tokens=result.prompt_tokens,
            output_tokens=result.completion_tokens,
        )
    except UnknownModelError:
        logger.warning(
            "ai_drafts.compute_cost.unknown_model model=%s — "
            "draft cost stored as 0; add the row to apps.llm.pricing.MODEL_PRICES",
            model,
        )
        return Decimal(0)


def _clear_draft_content_inplace(draft_pk: Any, *, new_status: str, now: Any) -> None:
    """Blocker #5 Layer 1 — clear content + flip status atomically.

    Single UPDATE that nulls the customer-PII-quoting content AND
    sets the terminal status in one SQL statement. Called from inside
    the per-draft lock in the three terminal paths (send / release /
    regenerate-replaces).

    ``content`` is ``nullable=False, default=""`` on the model — we set
    it to the empty string, not NULL, so downstream serialisation
    queries don't need NULL-handling branches.

    Note: tokens / cost / model metadata stay intact. The 30-day Celery
    Beat sweep eventually deletes the whole row for finance windows
    that have closed.

    Note (Blocker #5 design): NO separate audit row for «content
    cleared» — the status flip already produced one upstream and another
    would be noise.
    """

    AiDraft.all_tenants.filter(pk=draft_pk).update(
        status=new_status,
        content="",
        updated_at=now,
    )


# --- main entrypoints -----------------------------------------------------


def generate_draft_for_conversation(
    *,
    conversation_id: uuid.UUID | str,
    master: CatalogMaster,
    actor_bot_user: Any,
    is_auto: bool = False,
) -> DraftResponse:
    """Generate a fresh AI draft for the master.

    Args:
      is_auto: When True the audit + analytics emit
        :data:`MASTER_AI_DRAFT_AUTO_GENERATED` instead of
        :data:`MASTER_AI_DRAFT_GENERATED`. Set by the
        :func:`apps.master_api.tasks.auto_generate_draft_for_inbound`
        Celery task; the on-demand view path leaves the default
        ``False`` so its analytics row reads as a manual «✨ Предложить
        ответ» tap. The rest of the flow (cost cap, rate guard,
        idempotency, staleness) is identical — auto and manual share
        the same quota by design (single $5/day per master).

    Flow (Blocker #1 + #3 + #5 + #6 hardened):
      1. Verify master is involved in the conversation (else 404 —
         mirrors :func:`_verify_master_involved`).
      2. Refuse on HUMAN_LOCKED (the master can't send anyway).
      3. Open a transaction + ``select_for_update`` on the Conversation
         row. Everything else happens inside this lock.
      4. Idempotency check (inside lock) — if an ACTIVE draft exists,
         was created within :data:`IDEMPOTENCY_WINDOW`, and its
         :attr:`AiDraft.trigger_message` matches the latest customer
         message, return that draft (no LLM call, no audit row).
      5. Per-master cumulative cost cap (inside lock, BEFORE LLM call).
         Raises ``cost_cap_exceeded`` 429 on trip — Blocker #3.
      6. Mark any prior ACTIVE row as REPLACED + clear its content
         (Blocker #5 Layer 1).
      7. Call the LLM (inside lock — Blocker #1 prevents double-bill).
      8. INSERT the new ACTIVE draft. Wrap in try/except IntegrityError
         and on catch SELECT the current ACTIVE → return it (Blocker #6).
      9. Audit + emit ``master.ai_draft_generated``.

    Raises:
      :class:`DraftActionError`:
        * ``not_found`` (404) — master not involved
        * ``conversation_locked`` (400) — HUMAN_LOCKED tier
        * ``cost_cap_exceeded`` (429) — per-master daily $ cap trip
        * ``llm_unavailable`` (503) — provider raised any LLMError /
          LLMProviderUnavailable, OR an unexpected exception bubbled
          from the upstream SDK
    """

    conv = _verify_master_involved(master, conversation_id)

    if conv.tier == Conversation.Tier.HUMAN_LOCKED:
        raise DraftActionError(
            "conversation_locked",
            "conversation is human-locked; draft generation refused",
            status=400,
        )

    # Blocker #1: idempotency + cost guard + LLM call all run INSIDE a
    # transaction with select_for_update on the Conversation row. The
    # lock serialises concurrent generate calls per conversation so
    # rapid-double-tap can't trigger two paid LLM calls.
    with transaction.atomic():
        # Lock the conversation row. Re-fetch via all_tenants so the
        # row matches what _verify_master_involved returned (which used
        # the tenant-scoped manager) — locks travel by id, not by
        # manager.
        locked_conv = (
            Conversation.all_tenants.select_for_update()
            .filter(id=conv.id, tenant_id=master.tenant_id)
            .first()
        )
        if locked_conv is None:
            # Conversation was deleted between the involvement check
            # and the lock — extremely unlikely under normal flow, but
            # treat as not-found rather than racing to a partial create.
            raise DraftActionError("not_found", "conversation not found", status=404)

        latest_customer_msg = _latest_customer_message(locked_conv)
        latest_customer_msg_id = latest_customer_msg.id if latest_customer_msg is not None else None
        now = dj_timezone.now()

        # Blocker #1 idempotency check (re-do inside lock) — if a
        # concurrent caller landed a fresh ACTIVE draft moments ago for
        # the same trigger message, we return that draft instead of
        # paying for a second LLM call.
        existing_active = (
            AiDraft.all_tenants.filter(
                tenant_id=master.tenant_id,
                conversation_id=locked_conv.id,
                master_id=master.id,
                status=AiDraft.Status.ACTIVE,
            )
            .order_by("-created_at")
            .first()
        )
        if existing_active is not None and existing_active.created_at is not None:
            same_trigger = existing_active.trigger_message_id == latest_customer_msg_id
            within_window = now - existing_active.created_at <= IDEMPOTENCY_WINDOW
            if same_trigger and within_window:
                logger.info(
                    "ai_drafts.generate.idempotent draft_id=%s conv=%s master=%s",
                    existing_active.id,
                    locked_conv.id,
                    master.id,
                )
                return DraftResponse(
                    draft_id=str(existing_active.id),
                    content=existing_active.content,
                    created_at=existing_active.created_at.isoformat(),
                    llm_provider=existing_active.llm_provider,
                    llm_model=existing_active.llm_model,
                )

        # Blocker #3: per-master cumulative cost cap BEFORE the LLM
        # call. Inside the lock so concurrent callers serialise their
        # cap checks correctly.
        cost_check = check_cost_cap(master_id=master.id, tenant_id=master.tenant_id)
        if not cost_check.allowed:
            raise DraftActionError(
                cost_check.slug,
                cost_check.detail,
                status=429,
            )

        # Mark prior ACTIVE as REPLACED + clear its content
        # (Blocker #5 Layer 1).
        if existing_active is not None:
            _clear_draft_content_inplace(
                existing_active.pk,
                new_status=AiDraft.Status.REPLACED,
                now=now,
            )

        # Build the prompt + call the LLM — inside the lock per
        # Blocker #1.  Cost of holding the lock during the 1-3s call
        # is acceptable: concurrent generate-for-same-conversation is
        # rare and we WANT to serialise.
        history = _recent_history(locked_conv)
        prompt_messages = _build_prompt_messages(master=master, history=history)
        try:
            provider = get_router().get_provider(
                locked_conv.tenant, skill=SKILL_NAME, op="complete"
            )
            model = getattr(provider, "default_completion_model", "") or ""
            result: CompletionResult = asyncio.run(provider.complete(prompt_messages, model=model))
        except LLMProviderUnavailable as exc:
            logger.warning(
                "ai_drafts.generate.provider_unavailable conv=%s master=%s err=%s",
                locked_conv.id,
                master.id,
                exc,
            )
            raise DraftActionError(
                "llm_unavailable",
                "LLM provider is currently unavailable",
                status=503,
            ) from exc
        except LLMError as exc:
            logger.warning(
                "ai_drafts.generate.llm_error conv=%s master=%s err=%s",
                locked_conv.id,
                master.id,
                exc,
            )
            raise DraftActionError(
                "llm_unavailable",
                "LLM call failed; please try again",
                status=503,
            ) from exc
        except Exception as exc:  # noqa: BLE001 — provider SDKs leak many exception classes
            logger.exception(
                "ai_drafts.generate.unexpected conv=%s master=%s",
                locked_conv.id,
                master.id,
            )
            raise DraftActionError(
                "llm_unavailable",
                "LLM call failed unexpectedly; please try again",
                status=503,
            ) from exc

        draft_text = (result.text or "").strip()
        if not draft_text:
            # Provider returned tool-calls-only or empty completion —
            # treat as failure rather than persisting an empty draft.
            logger.warning(
                "ai_drafts.generate.empty_completion conv=%s master=%s provider=%s model=%s",
                locked_conv.id,
                master.id,
                result.provider,
                result.model or model,
            )
            raise DraftActionError(
                "llm_unavailable",
                "LLM returned empty draft; please try again",
                status=503,
            )

        resolved_model = result.model or model or ""
        cost_usd = _safe_compute_cost(resolved_model, result)

        # Blocker #6: defensive INSERT. With Blocker #1's lock in place
        # the partial unique constraint is unreachable on the happy
        # path. If a future code path bypasses the lock and races us,
        # catch IntegrityError + SELECT the winning ACTIVE row + return
        # it — no 500.
        try:
            draft = AiDraft.all_tenants.create(
                tenant=master.tenant,
                conversation=locked_conv,
                master=master,
                content=draft_text,
                status=AiDraft.Status.ACTIVE,
                trigger_message=latest_customer_msg,
                llm_provider=result.provider or "",
                llm_model=resolved_model,
                llm_cost_usd=cost_usd,
            )
        except IntegrityError:
            logger.warning(
                "ai_drafts.generate.integrity_race conv=%s master=%s — "
                "falling back to existing ACTIVE",
                locked_conv.id,
                master.id,
            )
            winning = (
                AiDraft.all_tenants.filter(
                    tenant_id=master.tenant_id,
                    conversation_id=locked_conv.id,
                    master_id=master.id,
                    status=AiDraft.Status.ACTIVE,
                )
                .order_by("-created_at")
                .first()
            )
            if winning is None:
                # Pathological: integrity error but no ACTIVE exists.
                # Surface as 503 — we can't meaningfully recover.
                raise DraftActionError(
                    "llm_unavailable",
                    "draft persistence race; please try again",
                    status=503,
                ) from None
            return DraftResponse(
                draft_id=str(winning.id),
                content=winning.content,
                created_at=winning.created_at.isoformat()
                if winning.created_at
                else now.isoformat(),
                llm_provider=winning.llm_provider,
                llm_model=winning.llm_model,
            )

        payload = {
            "tenant_id": str(master.tenant_id),
            "conversation_id": str(locked_conv.id),
            "master_id": str(master.id),
            "draft_id": str(draft.id),
            "llm_provider": draft.llm_provider,
            "llm_model": draft.llm_model,
            "llm_cost_usd": str(cost_usd),
            "content_length": len(draft_text),
            "trigger_message_id": (
                str(latest_customer_msg_id) if latest_customer_msg_id is not None else ""
            ),
        }
        audit_slug = MASTER_AI_DRAFT_AUTO_GENERATED if is_auto else MASTER_AI_DRAFT_GENERATED
        write_audit(
            audit_slug,
            target="AiDraft",
            target_id=draft.id,
            payload=payload,
            actor_id=actor_bot_user.id if actor_bot_user is not None else None,
        )
        emit(audit_slug, properties=payload)

    logger.info(
        "ai_drafts.generate.persisted draft_id=%s conv=%s master=%s model=%s cost_usd=%s",
        draft.id,
        conv.id,
        master.id,
        resolved_model,
        cost_usd,
    )

    return DraftResponse(
        draft_id=str(draft.id),
        content=draft.content,
        created_at=draft.created_at.isoformat() if draft.created_at else now.isoformat(),
        llm_provider=draft.llm_provider,
        llm_model=draft.llm_model,
    )


def _validate_draft_actionable(draft: AiDraft) -> None:
    """Reject any send / release attempt on a non-ACTIVE draft.

    A draft becomes non-ACTIVE the moment one of the three terminal
    paths fires (SENT_AS_MASTER / RELEASED_TO_AI / REPLACED). Double-tap
    of the «Отправить от себя» button on the same draft must surface a
    clear 400 ``draft_already_acted`` rather than silently sending
    twice.
    """

    if draft.status != AiDraft.Status.ACTIVE:
        raise DraftActionError(
            "draft_already_acted",
            f"draft is already in terminal state {draft.status!r}",
            status=400,
        )


def _validate_draft_fresh(draft: AiDraft, conv: Conversation) -> None:
    """Blocker #4 — refuse to ship a draft after a newer customer message.

    Compares the draft's ``trigger_message_id`` to the conversation's
    latest USER message. Mismatch → 409 ``draft_stale``. Surfaces the
    latest message id so the frontend can force-regenerate.

    Spec rationale (PR #535 follow-up Blocker #4):
      Customer: «можно записаться?»
      Master taps generate → draft about booking
      While master reads, customer: «отмените запись»
      Master taps «Отправить от себя» → MUST NOT ship the booking
      answer in response to a cancellation message.
    """

    latest_id = _latest_customer_message_id(conv)
    if draft.trigger_message_id != latest_id:
        raise DraftActionError(
            "draft_stale",
            (
                "a newer customer message has arrived since this draft "
                "was generated; please regenerate"
            ),
            status=409,
        )


def send_draft_as_master(
    *,
    conversation_id: uuid.UUID | str,
    draft_id: uuid.UUID | str,
    master: CatalogMaster,
    actor_bot_user: Any,
    override_content: str | None = None,
) -> DraftMessageResponse:
    """Master tapped «Отправить от себя» (or «Отредактировать» + send).

    Steps:
      1. Verify master involvement → 404 if not.
      2. Refuse on HUMAN_LOCKED (mirrors send_master_message in
         conversation_detail.py).
      3. Resolve draft → 404 if cross-master / cross-tenant.
      4. Validate draft.status == ACTIVE → else 400 ``draft_already_acted``.
      5. Validate freshness (Blocker #4) → else 409 ``draft_stale``.
      6. Validate override_content length (≤ :data:`MAX_OVERRIDE_LENGTH`).
      7. Atomically:
         * create assistant Message with attribution metadata
           ``{actor_type: master, composed_by: <master_id>}``
         * mark draft SENT_AS_MASTER + clear content (Blocker #5 Layer 1)
         * audit + emit ``master.draft_sent_as_self``
    """

    conv = _verify_master_involved(master, conversation_id)

    if conv.tier == Conversation.Tier.HUMAN_LOCKED:
        raise DraftActionError(
            "tier_locked",
            "conversation is human-locked; only admin/owner can reply",
            status=403,
        )

    draft = _resolve_draft(master, conv, draft_id)
    _validate_draft_actionable(draft)
    _validate_draft_fresh(draft, conv)

    # Resolve the body — override path uses the master's edited text,
    # otherwise the LLM-generated draft content.
    was_edited = override_content is not None
    if was_edited:
        body = (override_content or "").strip()
        if not body:
            raise DraftActionError(
                "bad_request",
                "override_content must be non-empty",
                status=400,
            )
        if len(body) > MAX_OVERRIDE_LENGTH:
            raise DraftActionError(
                "bad_request",
                f"override_content exceeds {MAX_OVERRIDE_LENGTH} characters",
                status=400,
            )
    else:
        body = draft.content

    with transaction.atomic():
        # Lock the draft row so a concurrent release-to-ai / regenerate
        # can't transition it under us between the status check and
        # the status update.
        locked = AiDraft.all_tenants.select_for_update().get(pk=draft.pk)
        _validate_draft_actionable(locked)
        # Re-check staleness inside the lock — a customer message could
        # have arrived between the outer check and the lock acquisition.
        _validate_draft_fresh(locked, conv)

        msg = record_message(
            conv,
            role=Message.Role.ASSISTANT,
            content=body,
            rendered_text=body,
            action_type="master_compose",
            action_data={
                "actor_type": "master",
                "composed_by": str(master.id),
                "from_draft_id": str(draft.id),
                "was_edited": was_edited,
            },
        )

        # Blocker #5 Layer 1: clear content + flip status atomically.
        _clear_draft_content_inplace(
            locked.pk,
            new_status=AiDraft.Status.SENT_AS_MASTER,
            now=dj_timezone.now(),
        )

        payload = {
            "tenant_id": str(master.tenant_id),
            "conversation_id": str(conv.id),
            "master_id": str(master.id),
            "draft_id": str(draft.id),
            "message_id": str(msg.id),
            "was_edited": was_edited,
        }
        write_audit(
            MASTER_DRAFT_SENT_AS_SELF,
            target="AiDraft",
            target_id=draft.id,
            payload=payload,
            actor_id=actor_bot_user.id if actor_bot_user is not None else None,
        )
        emit(MASTER_DRAFT_SENT_AS_SELF, properties=payload)

    return DraftMessageResponse(
        message_id=str(msg.id),
        content=body,
        sent_at=msg.created_at.isoformat() if msg.created_at else "",
        composed_by_master=True,
        was_edited=was_edited,
    )


def release_draft_to_ai(
    *,
    conversation_id: uuid.UUID | str,
    draft_id: uuid.UUID | str,
    master: CatalogMaster,
    actor_bot_user: Any,
) -> DraftMessageResponse:
    """Master tapped «Пусть помощник ответит».

    The draft text is sent as a plain assistant message with NO master
    attribution. The customer never knows a master was involved (per
    §M6 single-identity policy). Master's audit trail still records
    the release via ``master.draft_released_to_ai``.

    Steps:
      1. Verify master involvement → 404 if not.
      2. Refuse on HUMAN_LOCKED.
      3. Resolve draft → 404 if cross-master / cross-tenant.
      4. Validate ACTIVE → else 400 ``draft_already_acted``.
      5. Validate freshness (Blocker #4) → else 409 ``draft_stale``.
      6. Atomically:
         * create plain assistant Message (action_type='ai_draft_released',
           no master attribution metadata)
         * mark draft RELEASED_TO_AI + clear content (Blocker #5 Layer 1)
         * audit + emit
    """

    conv = _verify_master_involved(master, conversation_id)

    if conv.tier == Conversation.Tier.HUMAN_LOCKED:
        raise DraftActionError(
            "tier_locked",
            "conversation is human-locked; only admin/owner can reply",
            status=403,
        )

    draft = _resolve_draft(master, conv, draft_id)
    _validate_draft_actionable(draft)
    _validate_draft_fresh(draft, conv)

    body = draft.content

    with transaction.atomic():
        locked = AiDraft.all_tenants.select_for_update().get(pk=draft.pk)
        _validate_draft_actionable(locked)
        _validate_draft_fresh(locked, conv)

        msg = record_message(
            conv,
            role=Message.Role.ASSISTANT,
            content=body,
            rendered_text=body,
            action_type="ai_draft_released",
            # NO master attribution metadata — the customer-facing
            # render is a plain «Помощник: …» line indistinguishable
            # from an auto-generated reply.
            action_data={
                "from_draft_id": str(draft.id),
                "released_by_master": str(master.id),
            },
        )

        # Blocker #5 Layer 1: clear content + flip status atomically.
        _clear_draft_content_inplace(
            locked.pk,
            new_status=AiDraft.Status.RELEASED_TO_AI,
            now=dj_timezone.now(),
        )

        payload = {
            "tenant_id": str(master.tenant_id),
            "conversation_id": str(conv.id),
            "master_id": str(master.id),
            "draft_id": str(draft.id),
            "message_id": str(msg.id),
        }
        write_audit(
            MASTER_DRAFT_RELEASED_TO_AI,
            target="AiDraft",
            target_id=draft.id,
            payload=payload,
            actor_id=actor_bot_user.id if actor_bot_user is not None else None,
        )
        emit(MASTER_DRAFT_RELEASED_TO_AI, properties=payload)

    return DraftMessageResponse(
        message_id=str(msg.id),
        content=body,
        sent_at=msg.created_at.isoformat() if msg.created_at else "",
        composed_by_master=False,
        was_edited=False,
    )


__all__ = [
    "DraftActionError",
    "DraftMessageResponse",
    "DraftResponse",
    "IDEMPOTENCY_WINDOW",
    "MAX_HISTORY_MESSAGES",
    "MAX_OVERRIDE_LENGTH",
    "SKILL_NAME",
    "generate_draft_for_conversation",
    "release_draft_to_ai",
    "send_draft_as_master",
]
