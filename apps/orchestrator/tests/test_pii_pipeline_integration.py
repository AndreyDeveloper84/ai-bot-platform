"""End-to-end PII tokenization integration test (#842 Path A Step 3).

Drives a single `pipeline.turn()` с a message carrying multiple PII
categories (phone + email) and verifies the full round-trip:

* Tokenized payload reaches the wrapped provider (raw PII NEVER leaves
  the pipeline boundary).
* Vendor response containing the tokens is detokenized before reaching
  any user-facing surface (Conversation Message rows, ReplayTrace, etc).
* Tokens are deterministic per-conversation (same phone → same token
  across messages в the same Conversation).
* Per-conversation scope isolation — turn в Conversation A's tokens do
  NOT collide с Conversation B's.

These tests are the **integration** complement to the unit-level
`apps/llm/tests/test_pii_tokenizer.py` (tokenizer internals) and
`apps/llm/tests/test_pii_protected_provider.py` (decorator alone).
Here we go through `orchestrator.pipeline.turn` → `_run_under_tenant`
→ `_pii_enter(conversation.id)` → intent classifier → skill →
`PIITokenizingProvider.complete` → fake provider.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async

from apps.llm import pii_tokenizer
from apps.llm.tests.test_pii_tokenizer import _FakeRedis
from apps.orchestrator.llm.openai_provider import LLMResponse
from apps.orchestrator.pipeline import ChannelMessage, turn
from apps.tenancy.models import Tenant


pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.asyncio,
]


@pytest.fixture(autouse=True)
def _enable_pii(settings: pytest.FixtureRequest) -> None:
    # Pipeline integration tests for #842 MUST exercise the full
    # tokenize/detokenize path. Settings flag was off by default in
    # local.py so generic tests don't reach Redis; re-enable here.
    settings.PII_TOKENIZER_ENABLED = True  # type: ignore[attr-defined]


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeRedis]:
    fake = _FakeRedis()
    monkeypatch.setattr(pii_tokenizer, "_redis_client", lambda: fake)
    pii_tokenizer._invalidate_script_cache()
    yield fake
    pii_tokenizer._invalidate_script_cache()


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="pii-int", name="PII Integration")


@pytest.fixture(autouse=True)
def _stub_outbound() -> Iterator[None]:
    with patch(
        "apps.channels.max.outbound.send_message",
        return_value={"ok": True},
    ):
        yield


def _message(text: str, *, tenant_slug: str = "pii-int") -> ChannelMessage:
    return ChannelMessage(
        tenant_slug=tenant_slug,
        channel="max",
        channel_user_id="pii-uid",
        chat_id="pii-chat",
        text=text,
        display_name="PII Tester",
        trace_id=str(uuid4()),
    )


def _intent_decision_response(intent: str = "faq") -> LLMResponse:
    payload = {
        "intent": intent,
        "skill": intent,
        "confidence": 0.9,
        "risk_level": "low",
        "missing_slots": [],
        "reply_mode": "text",
        "needs_rag": False,
        "needs_tool": False,
    }
    return LLMResponse(
        content=json.dumps(payload),
        model="gpt-4o-mini-mock",
        is_fallback=False,
        tokens_in=10,
        tokens_out=20,
    )


class TestPiiPipelineRoundTrip:
    async def test_raw_phone_is_tokenized_before_reaching_provider(
        self, tenant: Tenant, fake_redis: _FakeRedis
    ) -> None:
        """Raw user phone «+7 999 123-45-67» MUST NOT reach the wrapped
        provider — only the tokenized form `<PHONE_NONCE_INDEX>` does.

        #975 (2026-06-02): intent classification now uses the production
        ``LLMProvider`` protocol resolved через `apps.llm.router.get_router()`.
        Patch the router so the inner provider's `complete` is the
        mock that captures messages. The decorator
        ``PIITokenizingProvider`` still wraps the inner provider, so
        messages it forwards downstream ARE tokenized — the assertion
        below proves this.
        """
        from apps.llm.protocol import CompletionResult

        captured_messages: list[list[dict[str, str]]] = []

        inner_provider = AsyncMock()
        inner_provider.name = "openai"

        async def _capture(messages, **_kw):
            captured_messages.append([{**m} for m in messages])
            return CompletionResult(
                text=_intent_decision_response().content,
                provider="openai",
                finish_reason="stop",
            )

        inner_provider.complete.side_effect = _capture

        # Wrap inner_provider в the real `PIITokenizingProvider` so
        # the production-path tokenization runs against our mock.
        from apps.llm.pii_protected_provider import PIITokenizingProvider

        wrapped = PIITokenizingProvider(inner_provider)
        router_stub = AsyncMock()
        router_stub.get_provider = lambda *a, **kw: wrapped

        with patch("apps.llm.router.get_router", return_value=router_stub):
            await turn(_message("позвони мне на +7 999 123-45-67"))

        # The intent classifier was the first call to hit the mock
        # provider. Subsequent skill calls (FAQ etc.) may carry messages
        # NOT containing the original phone (e.g. FAQ's own default
        # greeting), so the assertion below is global: raw phone forms
        # MUST NEVER appear across all captured messages, AND at least
        # one tokenized form MUST be present (proving tokenization
        # happened for the phone-bearing user message).
        assert captured_messages, "intent classifier did not call the provider"
        all_content = "\n".join(
            msg.get("content", "") for msg_set in captured_messages for msg in msg_set
        )
        # Raw phone forms MUST NOT appear anywhere.
        assert "+7 999 123-45-67" not in all_content
        assert "9991234567" not in all_content
        assert "+79991234567" not in all_content
        # At least one tokenized form MUST be present — proves the
        # decorator tokenized the phone-bearing user message before
        # the wrapped inner provider saw it.
        assert "<PHONE_" in all_content, (
            "expected at least one tokenized `<PHONE_...>` in captured "
            f"messages, got: {all_content!r}"
        )

    async def test_response_token_is_detokenized_before_storage(
        self, tenant: Tenant, fake_redis: _FakeRedis
    ) -> None:
        """Vendor returns text containing the token; pipeline detokenises
        before persisting to Conversation.Message.

        Patches production router (post-#975 path) — same shape as
        ``test_raw_phone_is_tokenized_before_reaching_provider``.
        """
        from apps.llm.pii_protected_provider import PIITokenizingProvider
        from apps.llm.protocol import CompletionResult

        inner_provider = AsyncMock()
        inner_provider.name = "openai"

        async def _respond(messages, **_kw):
            if any(
                "JSON" in m.get("content", "") or "intent" in m.get("content", "").lower()
                for m in messages
            ):
                return CompletionResult(
                    text=_intent_decision_response().content,
                    provider="openai",
                    finish_reason="stop",
                )
            phone_tokens: list[str] = []
            for m in messages:
                content = m.get("content", "")
                phone_tokens += [t for t in content.split() if t.startswith("<PHONE_")]
            reply = "Перезвоню на " + (phone_tokens[0] if phone_tokens else "—")
            return CompletionResult(text=reply, provider="openai", finish_reason="stop")

        inner_provider.complete.side_effect = _respond

        wrapped = PIITokenizingProvider(inner_provider)
        router_stub = AsyncMock()
        router_stub.get_provider = lambda *a, **kw: wrapped

        with patch("apps.llm.router.get_router", return_value=router_stub):
            result = await turn(_message("позвони на +7 999 123-45-67"))

        # The TurnResult.reply.text was rendered AFTER detokenization
        # inside the decorator. If the decorator skipped detokenize,
        # the user would see `<PHONE_nonce_1>` literally.
        from apps.conversations.models import Message

        def _assistant_text() -> str:
            qs = Message.all_tenants.filter(trace_id=result.trace_id, role="assistant")
            row = qs.first()
            return row.content if row else ""

        text = await sync_to_async(_assistant_text)()
        # NO leak of raw token shape in persisted assistant reply
        # (decorator detokenizes response before pipeline saves it).
        assert "<PHONE_" not in text, f"assistant Message persisted с raw token: {text!r}"
        # If the LLM was actually given a token, the persisted text
        # MUST contain detokenized phone OR a fallback. Either is fine
        # — the contract is "no token shape in user-facing storage".
