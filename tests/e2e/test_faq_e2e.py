"""Sprint 7 / G2 — FAQ pipeline end-to-end (DRF-598).

Drives one real ``ChannelMessage`` through ``turn()`` with the
FAQ-shaped intent classifier and asserts the full KB-driven flow:

1. Intent → faq (mocked classifier).
2. F2 ``FAQSkill.handle`` runs: first LLM call emits ``search_knowledge_base``
   tool_call, F1 tool fetches chunks from real ChromaDB, second LLM call
   produces grounded answer.
3. ``TurnResult.reply.text`` carries the grounded text.
4. Audit rows include ``kb.search_invoked`` (F1) + ``kb.faq_answered`` (F2).
5. No handoff initiated (clean turn).
6. Negative path: empty chromadb collection → handoff at step 10.5.

LLM is fully mocked; ChromaDB + Django ORM are real. This catches
regressions in the F1↔F2↔O1↔O2 contract that the unit suite cannot.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from asgiref.sync import sync_to_async
from django.core.cache import cache

from apps.kb.chromadb_client import ChromaClient, KbItem, reset_client_cache
from apps.llm.protocol import CompletionResult, ToolCall
from apps.llm.router import reset_router_cache
from apps.orchestrator.llm.openai_provider import LLMResponse
from apps.orchestrator.pipeline import ChannelMessage, turn
from apps.tenancy.models import Tenant


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
    pytest.mark.asyncio,
]


def _fake_intent_provider() -> AsyncMock:
    """Intent classifier that pins to FAQ for these tests."""
    provider = AsyncMock()
    provider.complete.return_value = LLMResponse(
        content=json.dumps(
            {
                "intent": "faq",
                "skill": "faq",
                "confidence": 0.92,
                "risk_level": "low",
                "missing_slots": [],
                "reply_mode": "text",
                "needs_rag": True,
                "needs_tool": True,
            }
        ),
        model="gpt-4o-mini-mock",
        is_fallback=False,
        tokens_in=8,
        tokens_out=16,
    )
    return provider


def _completion(
    *,
    text: str = "",
    tool_calls: list[ToolCall] | None = None,
) -> CompletionResult:
    return CompletionResult(
        text=text,
        tool_calls=tool_calls or [],
        prompt_tokens=10,
        completion_tokens=25,
        model="gpt-4o-mini-mock",
        provider="openai",
        finish_reason="tool_calls" if tool_calls else "stop",
    )


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, settings):
    """Per-test isolation: scoped chromadb, recorder on, router cache reset."""
    settings.BASE_DIR = tmp_path
    settings.CHROMA_HTTP_HOST = ""
    settings.LLM_PROVIDER = "openai"
    settings.SKILL_LLM_PROVIDER = {}
    settings.REPLAY_SAMPLE_RATE_TEST = 1.0
    settings.STRICT_TENANT_SCOPE = "strict"
    reset_client_cache()
    reset_router_cache()
    cache.clear()
    yield
    cache.clear()
    reset_router_cache()
    reset_client_cache()


@pytest.fixture
def e2e_tenant():
    return Tenant.objects.create(slug="e2e-faq", name="E2E FAQ")


def _seed_chunk(
    tenant: Tenant,
    *,
    text: str = "Часы работы: с 10:00 до 22:00, без выходных.",
    chunk_id: str = "c1",
) -> None:
    ChromaClient().upsert(
        tenant,
        [
            KbItem(
                id=chunk_id,
                text=text,
                embedding=[0.9] * 8,
                metadata={
                    "doc_id": chunk_id,
                    "doc_type": "help_article",
                    "source_uri": f"mysite://help-articles/{chunk_id}",
                },
            )
        ],
    )


def _message(text: str) -> ChannelMessage:
    return ChannelMessage(
        tenant_slug="e2e-faq",
        channel="max",
        channel_user_id="e2e-faq-uid",
        chat_id="e2e-faq-chat",
        text=text,
        display_name="FAQ Tester",
        trace_id=str(uuid4()),
    )


@pytest.fixture
def _stub_external():
    """Mock the two LLM seams + MAX outbound. Yields no value."""

    intent_provider = _fake_intent_provider()
    tc = ToolCall(
        id="call-1",
        name="search_knowledge_base",
        arguments={"query": "часы работы"},
    )
    completions = [
        _completion(tool_calls=[tc]),
        _completion(text="Часы работы: с 10:00 до 22:00, без выходных."),
    ]

    from apps.llm.providers.openai_provider import OpenAIProvider

    with (
        patch(
            "apps.orchestrator.intent_router.OpenAIProvider",
            return_value=intent_provider,
        ),
        patch.object(OpenAIProvider, "complete", side_effect=completions),
        patch.object(OpenAIProvider, "embedding", return_value=[0.9] * 8),
        patch("apps.channels.max.outbound.send_message", return_value={"ok": True}),
    ):
        yield


@pytest.fixture
def _stub_external_empty():
    """Intent + first LLM call still hits — empty chromadb forces handoff
    BEFORE the second call, so we only need ONE completion in the side_effect.
    """
    intent_provider = _fake_intent_provider()
    tc = ToolCall(
        id="call-1",
        name="search_knowledge_base",
        arguments={"query": "часы работы"},
    )
    completions = [_completion(tool_calls=[tc])]

    from apps.llm.providers.openai_provider import OpenAIProvider

    with (
        patch(
            "apps.orchestrator.intent_router.OpenAIProvider",
            return_value=intent_provider,
        ),
        patch.object(OpenAIProvider, "complete", side_effect=completions),
        patch.object(OpenAIProvider, "embedding", return_value=[0.9] * 8),
        patch("apps.channels.max.outbound.send_message", return_value={"ok": True}),
    ):
        yield


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_faq_e2e_happy_path(e2e_tenant: Tenant, _stub_external: Any) -> None:
    """Real ChromaDB + mocked LLM → grounded answer threads through."""
    await sync_to_async(_seed_chunk)(e2e_tenant)

    result = await turn(_message("Когда вы работаете?"))

    assert result.ok is True, f"turn ok=False err={result.error}"
    assert result.intent is not None and result.intent.intent == "faq"
    assert result.reply is not None
    assert "10:00" in result.reply.text or "22:00" in result.reply.text

    # No short-circuit before step 11 — full skill pipeline ran. 0 is
    # the falsy default sentinel (no early-exit); the post-skill handoff
    # branch sets 10.5 when it fires.
    assert not result.short_circuited_at_step or result.short_circuited_at_step in (11,), (
        f"unexpected short-circuit at step {result.short_circuited_at_step}"
    )

    # Audit rows: F1 + F2 both wrote.
    from apps.audit.models import AuditLog

    audit_kinds = await sync_to_async(
        lambda: set(AuditLog.all_tenants.filter(tenant=e2e_tenant).values_list("action", flat=True))
    )()
    assert "kb.search_invoked" in audit_kinds, f"F1 audit missing; saw {audit_kinds}"
    assert "kb.faq_answered" in audit_kinds, f"F2 success audit missing; saw {audit_kinds}"
    assert "kb.faq_handoff" not in audit_kinds, "handoff fired on a clean turn"

    # No AdminTask (no handoff path).
    from apps.handoff.models import AdminTask

    admin_count = await sync_to_async(
        lambda: AdminTask.all_tenants.filter(tenant=e2e_tenant).count()
    )()
    assert admin_count == 0, f"unexpected AdminTask on happy path: {admin_count}"


async def test_faq_e2e_assistant_message_persisted(e2e_tenant: Tenant, _stub_external: Any) -> None:
    """Grounded text lands on the assistant Message row."""
    await sync_to_async(_seed_chunk)(e2e_tenant)

    result = await turn(_message("Когда вы работаете?"))

    from apps.conversations.models import Message

    msgs = await sync_to_async(
        lambda: list(
            Message.all_tenants.filter(trace_id=UUID(result.trace_id)).order_by("created_at")
        )
    )()
    assistant = [m for m in msgs if m.role == "assistant"]
    assert assistant, "no assistant Message persisted"
    assert "10:00" in assistant[0].content or "22:00" in assistant[0].content


# ---------------------------------------------------------------------------
# Negative path — empty KB → handoff at 10.5
# ---------------------------------------------------------------------------


async def test_faq_e2e_handoff_on_empty_kb(e2e_tenant: Tenant, _stub_external_empty: Any) -> None:
    """No seeded chunks → F2 hits handoff branch at step 10.5."""

    result = await turn(_message("Когда вы работаете?"))

    assert result.ok is True
    # O2 step 10.5 short-circuit lands here.
    assert result.short_circuited_at_step == 10.5, (
        f"expected short-circuit at 10.5 (post-skill handoff), got {result.short_circuited_at_step}"
    )

    # Audit: F2 handoff row written, F2 success row NOT.
    from apps.audit.models import AuditLog

    audit_kinds = await sync_to_async(
        lambda: set(AuditLog.all_tenants.filter(tenant=e2e_tenant).values_list("action", flat=True))
    )()
    assert "kb.faq_handoff" in audit_kinds, f"handoff audit missing; saw {audit_kinds}"
    assert "kb.faq_answered" not in audit_kinds, "answered fired on handoff path"

    # AdminTask was created by O2 step 10.5 handoff branch.
    from apps.handoff.models import AdminTask

    admin_count = await sync_to_async(
        lambda: AdminTask.all_tenants.filter(tenant=e2e_tenant).count()
    )()
    assert admin_count == 1, f"expected 1 AdminTask from handoff, got {admin_count}"
