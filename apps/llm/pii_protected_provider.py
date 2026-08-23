"""PII tokenizing decorator over :class:`LLMProvider` (Phase D / 152-ФЗ).

Single-point enforcement of PII tokenization at the LLM-call boundary.
Per tech-lead Phase D verdict (V2 wrap-at-router): logic lives in ONE
file, future providers auto-protected, compliance audit reviews a
single decorator instead of duplicated per-provider hooks.

### Activation contract

The decorator reads the active conversation scope from
:func:`apps.llm.pii_tokenizer.current_conversation_id` (ContextVar).
Two states:

* **None** — no active scope. Decorator is a no-op pass-through. Used
  для internal background flows that never see user PII (telemetry
  emit, daily reconciliation jobs, etc.).
* **Non-None** — scope active. Decorator tokenises every message
  ``content`` field before forwarding to wrapped provider, then
  detokenises the response ``text`` before returning to caller.

Skill code sets the scope explicitly::

    with pii_context(conversation.id):
        result = await provider.complete(messages, model="gpt-4")

A WARNING log fires when ``complete()`` is invoked without an active
scope while messages carry user content — signal для operator about
potentially-missed activation (this is policy guidance, NOT a hard
block, because background flows legitimately call complete без scope).

### What gets tokenised

Each entry в ``messages`` has shape ``{"role": ..., "content": ...}``
per OpenAI convention. Decorator walks ``messages`` and tokenises the
``content`` string for every entry — INCLUDING system messages (system
prompts may contain dynamic user context inserted by skill rendering).

Tool calls (``tools`` parameter spec) are NOT tokenised — these are
static function signatures, no PII surface.

Response ``CompletionResult.text`` is detokenised before return.
``tool_calls[*].arguments`` are NOT touched in Phase 0 scope — tool
arguments are typically structured booking/master IDs, not free-form
PII surfaces. Documented gap for Phase 1+ если adversarial review
surfaces leakage через tool-args path.

### Embeddings

``embedding(text)`` тоже tokenises input before vendor dispatch — the
text-к-vector mapping is preserved (same PII normalised к same token
→ same vector), but raw PII never reaches the vendor. No detokenize
on response (vectors don't contain reversible PII anyway).

### Why decorator, not provider modification

Provider-modification (Variant 1) would force every concrete provider
(OpenAI, Anthropic, future vendors) к duplicate the tokenize/detokenize
calls. Easy to forget on a new vendor → compliance gap. Decorator at
router emit point = single enforcement, single review surface, single
adversarial test target. Variant 2 was the tech-lead verdict.

### Future provider-specific concerns

Если future Claude tool-use blocks require different tokenization
treatment (e.g. structured argument PII), the decorator can dispatch
to provider-specific helpers via ``self._wrapped.name`` discriminator.
Architecture doesn't block; the abstraction stays at the router edge.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from asgiref.sync import sync_to_async

from apps.audit.services import write_audit
from apps.llm import pii_tokenizer
from apps.llm.protocol import CompletionResult

logger = logging.getLogger(__name__)


# Audit event name — emitted at the end of every LLM call routed
# через decorator. Payload contains tokenized-summary только; raw
# user content NEVER persisted (152-ФЗ §6 transit pseudonymisation
# contract per ADR-0011 §11.4).
_AUDIT_EVENT_LLM_CALL_COMPLETED = "llm.call_completed"

# Regex для counting tokenized-PII occurrences in the message stream
# sent к vendor. Matches the per-conversation token shape
# `<{CAT}_{NONCE}_{INDEX}>` — capture group 1 is the category.
_TOKEN_COUNT_RE = re.compile(r"<([A-Z_]+)_[0-9a-f]+_\d+>")


def _tokenized_pii_category_counts(messages: list[dict[str, Any]]) -> dict[str, int]:
    """Count tokenized PII occurrences per category across all messages.

    Operates on the ALREADY-TOKENIZED message list (post-tokenize, pre-
    detokenize). Returns ``{"PHONE": 2, "EMAIL": 1}`` style counts.
    NEVER touches raw user PII — counts ARE the audit signal.
    """
    counts: dict[str, int] = {}
    for msg in messages:
        content = msg.get("content", "") or ""
        for match in _TOKEN_COUNT_RE.finditer(content):
            category = match.group(1)
            counts[category] = counts.get(category, 0) + 1
    return counts


class PIITokenizingProvider:
    """Wraps any :class:`LLMProvider` to tokenize PII at the vendor boundary.

    The wrapper preserves the wrapped provider's ``name`` attribute so
    audit + cost-tracking attribute calls к the real provider. Other
    surface (``complete`` / ``embedding`` signature) matches the Protocol.
    """

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped
        # Expose wrapped's name verbatim — audit / cost tracker / router
        # telemetry look at this attribute and must see the real vendor.
        self.name = getattr(wrapped, "name", "")

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        tool_choice: str | None = None,
    ) -> CompletionResult:
        """Tokenize messages → wrapped.complete() → detokenize response."""
        # Settings gate — allows tests / smoke flows to disable PII
        # tokenization without monkey-patching the decorator. Defaults
        # to True (152-ФЗ §6 compliance MUST be on by default;
        # explicit opt-out is the only safe shape).
        from django.conf import settings

        if not getattr(settings, "PII_TOKENIZER_ENABLED", True):
            return await self._wrapped.complete(
                messages,
                model=model,
                temperature=temperature,
                tools=tools,
                max_tokens=max_tokens,
                tool_choice=tool_choice,
            )

        conversation_id = pii_tokenizer.current_conversation_id()
        if conversation_id is None:
            # No active scope. Pass through unchanged. WARNING для operator
            # observability — skill flows touching user content SHOULD set
            # pii_context; legitimate internal flows can ignore the warning.
            logger.warning(
                "pii_protected_provider.no_active_scope provider=%s "
                "messages_count=%d. Caller may be missing pii_context() "
                "wrapper, OR this is a known background flow.",
                self.name,
                len(messages),
            )
            return await self._wrapped.complete(
                messages,
                model=model,
                temperature=temperature,
                tools=tools,
                max_tokens=max_tokens,
                tool_choice=tool_choice,
            )

        tokenized_messages = [
            {**m, "content": pii_tokenizer.tokenize(m.get("content", ""), conversation_id)}
            for m in messages
        ]
        result: CompletionResult = await self._wrapped.complete(
            tokenized_messages,
            model=model,
            temperature=temperature,
            tools=tools,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
        )
        # Audit row — 152-ФЗ transit pseudonymisation evidence. Payload
        # carries ONLY tokenized-counts + structured metadata; raw user
        # content (including detokenized response) NEVER reaches audit
        # log per ADR-0011 §11.4 contract.
        await self._emit_call_audit_async(
            tokenized_messages=tokenized_messages,
            result=result,
            conversation_id=conversation_id,
            op="complete",
        )
        detokenized_text = pii_tokenizer.detokenize(result.text, conversation_id)

        # CompletionResult is frozen — rebuild с detokenized text +
        # detokenized tool_calls.
        #
        # #842 W3 HIGH-1 (2026-06-02) — when the LLM receives a message
        # with a `<PHONE_NONCE_INDEX>` token, it may helpfully echo the
        # token into tool_call arguments (e.g. `confirm_booking(
        # client_phone="<PHONE_abc12345_1>", ...)`). Without
        # detokenization downstream Ayla REST calls receive the token
        # literal as a phone number → 400 from Ayla AND the token
        # would land в replay/audit as the canonical record, breaking
        # forensic trace. Detokenize each string-valued arg before
        # rebuilding the ToolCall so call sites see the original PII
        # (or harmless non-PII strings unchanged).
        from apps.llm.protocol import ToolCall

        detokenized_tool_calls: list[ToolCall] = []
        for tc in result.tool_calls or []:
            new_args: dict[str, Any] = {}
            for k, v in (tc.arguments or {}).items():
                if isinstance(v, str):
                    new_args[k] = pii_tokenizer.detokenize(v, conversation_id)
                else:
                    new_args[k] = v
            detokenized_tool_calls.append(ToolCall(id=tc.id, name=tc.name, arguments=new_args))

        return CompletionResult(
            text=detokenized_text,
            tool_calls=detokenized_tool_calls,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            model=result.model,
            provider=result.provider,
            finish_reason=result.finish_reason,
        )

    async def embedding(self, text: str, *, model: str | None = None) -> list[float]:
        """Tokenize text → wrapped.embedding(). No detokenize (vectors)."""
        from django.conf import settings

        if not getattr(settings, "PII_TOKENIZER_ENABLED", True):
            return await self._wrapped.embedding(text, model=model)
        conversation_id = pii_tokenizer.current_conversation_id()
        if conversation_id is None:
            logger.warning(
                "pii_protected_provider.no_active_scope provider=%s op=embedding",
                self.name,
            )
            return await self._wrapped.embedding(text, model=model)
        tokenized = pii_tokenizer.tokenize(text, conversation_id)
        vector = await self._wrapped.embedding(tokenized, model=model)
        # Embedding audit — same tokenized-only contract as complete().
        await self._emit_call_audit_async(
            tokenized_messages=[{"role": "user", "content": tokenized}],
            result=None,
            conversation_id=conversation_id,
            op="embedding",
        )
        return vector

    async def _emit_call_audit_async(
        self,
        *,
        tokenized_messages: list[dict[str, Any]],
        result: CompletionResult | None,
        conversation_id: str,
        op: str,
    ) -> None:
        """Async wrapper around :meth:`_emit_call_audit_sync` — bridges
        Django's sync ORM through `sync_to_async` since `write_audit`
        does a synchronous DB INSERT, which Django forbids inside an
        async stack без an explicit bridge."""
        await sync_to_async(self._emit_call_audit_sync, thread_sensitive=False)(
            tokenized_messages=tokenized_messages,
            result=result,
            conversation_id=conversation_id,
            op=op,
        )

    def _emit_call_audit_sync(
        self,
        *,
        tokenized_messages: list[dict[str, Any]],
        result: CompletionResult | None,
        conversation_id: str,
        op: str,
    ) -> None:
        """Emit ``llm.call_completed`` audit row с tokenized-only payload.

        Raw user PII NEVER persisted к the audit log — payload carries
        per-category PII-occurrence counts (e.g. {"PHONE": 2}) and
        structured metadata only. AST lint contract: this is the SINGLE
        audit emit point в the LLM-call path; any future addition needs
        к pass through here OR explicitly justify divergence в the
        amendment к ADR-0011 §11.4.

        write_audit() never raises (forensic-infrastructure contract per
        :mod:`apps.audit.services`), so this call is fire-and-forget.
        """
        category_counts = _tokenized_pii_category_counts(tokenized_messages)
        payload: dict[str, Any] = {
            "provider": self.name,
            "op": op,
            "conversation_id": conversation_id,
            "pii_category_counts": category_counts,
            "messages_count": len(tokenized_messages),
        }
        if result is not None:
            payload["model"] = result.model
            payload["prompt_tokens"] = result.prompt_tokens
            payload["completion_tokens"] = result.completion_tokens
            payload["finish_reason"] = result.finish_reason
            # Response text was tokenized (vendor returned it that way)
            # before we detokenize for the caller. Audit the tokenized
            # form's category counts only — same contract as input.
            response_categories = _tokenized_pii_category_counts([{"content": result.text}])
            if response_categories:
                payload["response_pii_category_counts"] = response_categories
        write_audit(
            _AUDIT_EVENT_LLM_CALL_COMPLETED,
            target="LLMProvider",
            payload=payload,
        )
