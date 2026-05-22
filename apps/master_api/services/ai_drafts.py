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

### Idempotency window

A second ``generate_draft_for_conversation`` call within 60 seconds
returns the existing ACTIVE draft when:
  * the most recent customer message hasn't changed since generation
  * AND the existing row is still :attr:`AiDraft.Status.ACTIVE`

Otherwise the prior ACTIVE row is marked :attr:`AiDraft.Status.REPLACED`
and a fresh LLM call runs. This guards against double-tap of the
«Generate» button burning two LLM bills.

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

from django.db import transaction
from django.utils import timezone as dj_timezone

from apps.audit.services import write_audit
from apps.catalog.models import CatalogMaster
from apps.conversations.models import AiDraft, Conversation, Message
from apps.conversations.services import record_message
from apps.events.services import emit
from apps.events.vocabulary import (
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


# --- main entrypoints -----------------------------------------------------


def generate_draft_for_conversation(
    *,
    conversation_id: uuid.UUID | str,
    master: CatalogMaster,
    actor_bot_user: Any,
) -> DraftResponse:
    """Generate a fresh AI draft for the master.

    Flow:
      1. Verify master is involved in the conversation (else 404 —
         mirrors :func:`_verify_master_involved`).
      2. Refuse on HUMAN_LOCKED (the master can't send anyway).
      3. Idempotency check: if an ACTIVE draft exists, was created
         within :data:`IDEMPOTENCY_WINDOW`, and its
         :attr:`AiDraft.trigger_message` matches the latest customer
         message, return that draft (no LLM call, no audit row).
      4. Call the LLM via the router → provider.complete().
      5. Persist a new AiDraft inside a transaction with the prior
         ACTIVE row select_for_update()-locked + marked REPLACED.
      6. Audit + emit ``master.ai_draft_generated``.

    Raises:
      :class:`DraftActionError`:
        * ``not_found`` (404) — master not involved
        * ``conversation_locked`` (400) — HUMAN_LOCKED tier
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

    latest_customer_msg = _latest_customer_message(conv)
    now = dj_timezone.now()

    # Idempotency: serve the existing ACTIVE row when nothing material
    # has changed since the previous generate. Read OUTSIDE the
    # transaction — if the window check passes, no DB write is needed.
    existing_active = (
        AiDraft.all_tenants.filter(
            tenant_id=master.tenant_id,
            conversation_id=conv.id,
            master_id=master.id,
            status=AiDraft.Status.ACTIVE,
        )
        .order_by("-created_at")
        .first()
    )
    if existing_active is not None and existing_active.created_at is not None:
        same_trigger = existing_active.trigger_message_id == (
            latest_customer_msg.id if latest_customer_msg is not None else None
        )
        within_window = now - existing_active.created_at <= IDEMPOTENCY_WINDOW
        if same_trigger and within_window:
            logger.info(
                "ai_drafts.generate.idempotent draft_id=%s conv=%s master=%s",
                existing_active.id,
                conv.id,
                master.id,
            )
            return DraftResponse(
                draft_id=str(existing_active.id),
                content=existing_active.content,
                created_at=existing_active.created_at.isoformat(),
                llm_provider=existing_active.llm_provider,
                llm_model=existing_active.llm_model,
            )

    # LLM call — happens BEFORE the transaction so an upstream timeout
    # doesn't hold a row-level lock for the full retry window.
    history = _recent_history(conv)
    prompt_messages = _build_prompt_messages(master=master, history=history)
    try:
        provider = get_router().get_provider(conv.tenant, skill=SKILL_NAME, op="complete")
        model = getattr(provider, "default_completion_model", "") or ""
        result: CompletionResult = asyncio.run(provider.complete(prompt_messages, model=model))
    except LLMProviderUnavailable as exc:
        logger.warning(
            "ai_drafts.generate.provider_unavailable conv=%s master=%s err=%s",
            conv.id,
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
            conv.id,
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
            conv.id,
            master.id,
        )
        raise DraftActionError(
            "llm_unavailable",
            "LLM call failed unexpectedly; please try again",
            status=503,
        ) from exc

    draft_text = (result.text or "").strip()
    if not draft_text:
        # Provider returned tool-calls-only or empty completion — treat
        # as failure rather than persisting an empty draft.
        logger.warning(
            "ai_drafts.generate.empty_completion conv=%s master=%s provider=%s model=%s",
            conv.id,
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

    # Persist atomically — lock prior ACTIVE row, mark REPLACED, insert
    # the new ACTIVE row, write audit + emit event. The select_for_update
    # serialises concurrent generate calls so the partial unique
    # constraint (one ACTIVE per conversation) holds without a race.
    with transaction.atomic():
        prior = list(
            AiDraft.all_tenants.select_for_update().filter(
                tenant_id=master.tenant_id,
                conversation_id=conv.id,
                master_id=master.id,
                status=AiDraft.Status.ACTIVE,
            )
        )
        for old in prior:
            AiDraft.all_tenants.filter(pk=old.pk).update(
                status=AiDraft.Status.REPLACED,
                updated_at=now,
            )

        draft = AiDraft.all_tenants.create(
            tenant=master.tenant,
            conversation=conv,
            master=master,
            content=draft_text,
            status=AiDraft.Status.ACTIVE,
            trigger_message=latest_customer_msg,
            llm_provider=result.provider or "",
            llm_model=resolved_model,
            llm_cost_usd=cost_usd,
        )

        payload = {
            "tenant_id": str(master.tenant_id),
            "conversation_id": str(conv.id),
            "master_id": str(master.id),
            "draft_id": str(draft.id),
            "llm_provider": draft.llm_provider,
            "llm_model": draft.llm_model,
            "llm_cost_usd": str(cost_usd),
            "content_length": len(draft_text),
            "trigger_message_id": (
                str(latest_customer_msg.id) if latest_customer_msg is not None else ""
            ),
        }
        write_audit(
            MASTER_AI_DRAFT_GENERATED,
            target="AiDraft",
            target_id=draft.id,
            payload=payload,
            actor_id=actor_bot_user.id if actor_bot_user is not None else None,
        )
        emit(MASTER_AI_DRAFT_GENERATED, properties=payload)

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
      5. Validate override_content length (≤ :data:`MAX_OVERRIDE_LENGTH`).
      6. Atomically:
         * create assistant Message with attribution metadata
           ``{actor_type: master, composed_by: <master_id>}``
         * mark draft SENT_AS_MASTER
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

        AiDraft.all_tenants.filter(pk=locked.pk).update(
            status=AiDraft.Status.SENT_AS_MASTER,
            updated_at=dj_timezone.now(),
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
      5. Atomically:
         * create plain assistant Message (action_type='ai_draft_released',
           no master attribution metadata)
         * mark draft RELEASED_TO_AI
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

    body = draft.content

    with transaction.atomic():
        locked = AiDraft.all_tenants.select_for_update().get(pk=draft.pk)
        _validate_draft_actionable(locked)

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

        AiDraft.all_tenants.filter(pk=locked.pk).update(
            status=AiDraft.Status.RELEASED_TO_AI,
            updated_at=dj_timezone.now(),
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
