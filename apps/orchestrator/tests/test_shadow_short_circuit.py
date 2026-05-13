"""Shadow short-circuit at step 19 (DRF-717 / Sprint 8 / S2)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from apps.orchestrator.llm.openai_provider import LLMResponse
from apps.orchestrator.pipeline import ChannelMessage, turn
from apps.tenancy.models import Tenant


pytestmark = [pytest.mark.django_db(transaction=True)]


_INTENT_JSON = json.dumps(
    {
        "intent": "faq",
        "skill": "faq",
        "confidence": 0.92,
        "risk_level": "low",
        "missing_slots": [],
        "reply_mode": "text",
        "needs_rag": False,
        "needs_tool": False,
    }
)


def _intent_provider() -> AsyncMock:
    provider = AsyncMock()
    provider.complete.return_value = LLMResponse(
        content=_INTENT_JSON,
        model="mock",
        is_fallback=False,
        tokens_in=5,
        tokens_out=10,
    )
    return provider


@pytest.fixture
def shadow_tenant_message() -> ChannelMessage:
    """Tenant-level shadow mode → ChannelMessage.is_shadow=False but
    tenant.shadow_mode=True flips the pipeline to shadow.
    """
    return ChannelMessage(
        tenant_slug="s2-tenant",
        channel="max",
        channel_user_id="s2-uid",
        chat_id="s2-chat",
        text="когда работаете?",
    )


@pytest.fixture
def header_shadow_message() -> ChannelMessage:
    """Per-message shadow (X-Shadow header path)."""
    return ChannelMessage(
        tenant_slug="s2-primary",
        channel="max",
        channel_user_id="s2-uid",
        chat_id="s2-chat",
        text="когда работаете?",
        is_shadow=True,
    )


@pytest.fixture(autouse=True)
def _stub_external() -> Any:
    """Mock both LLM seams + MAX outbound. Capture outbound calls so we
    can assert they did or didn't happen.

    Two LLM seams: the intent classifier uses
    `apps.orchestrator.llm.openai_provider.OpenAIProvider`; the FAQ
    skill (Sprint 7) calls
    `apps.llm.providers.openai_provider.OpenAIProvider.complete`. Both
    must be neutralised so the test doesn't hit the network.
    """
    from apps.llm.protocol import CompletionResult
    from apps.llm.providers.openai_provider import OpenAIProvider as FaqProvider

    faq_completion = CompletionResult(
        text="ok",
        tool_calls=[],
        prompt_tokens=1,
        completion_tokens=1,
        model="mock",
        provider="openai",
        finish_reason="stop",
    )

    with (
        patch(
            "apps.orchestrator.intent_router.OpenAIProvider",
            return_value=_intent_provider(),
        ),
        patch.object(FaqProvider, "complete", return_value=faq_completion),
        patch.object(FaqProvider, "embedding", return_value=[0.5] * 8),
        patch(
            "apps.channels.max.outbound.send_message", return_value={"ok": True}
        ) as mock_outbound,
    ):
        yield mock_outbound


class TestTenantShadowMode:
    def test_tenant_shadow_mode_skips_outbound(
        self, shadow_tenant_message: ChannelMessage, _stub_external
    ) -> None:
        Tenant.objects.create(slug="s2-tenant", name="S2", shadow_mode=True)
        result = asyncio.run(turn(shadow_tenant_message))

        assert result.ok is True
        assert result.error == ""
        # No outbound call.
        _stub_external.send_message.assert_not_called() if hasattr(
            _stub_external, "send_message"
        ) else None
        assert _stub_external.call_count == 0


class TestPerMessageShadow:
    def test_header_is_shadow_skips_outbound(
        self, header_shadow_message: ChannelMessage, _stub_external
    ) -> None:
        Tenant.objects.create(slug="s2-primary", name="S2 primary", shadow_mode=False)
        result = asyncio.run(turn(header_shadow_message))

        assert result.ok is True
        assert _stub_external.call_count == 0


class TestShadowConversationPersisted:
    def test_shadow_conversation_marked(self, _stub_external) -> None:
        from apps.conversations.models import Conversation

        Tenant.objects.create(slug="s2-c", name="S2-C", shadow_mode=True)
        msg = ChannelMessage(
            tenant_slug="s2-c",
            channel="max",
            channel_user_id="s2c-uid",
            chat_id="s2c-chat",
            text="x",
        )
        asyncio.run(turn(msg))

        convs = list(Conversation.all_tenants.filter(bot_user__channel_user_id="s2c-uid"))
        assert len(convs) == 1
        assert convs[0].is_shadow is True


class TestAuditRow:
    def test_shadow_drop_audit_written(self, _stub_external) -> None:
        from apps.audit.models import AuditLog

        Tenant.objects.create(slug="s2-audit", name="S2 audit", shadow_mode=True)
        msg = ChannelMessage(
            tenant_slug="s2-audit",
            channel="max",
            channel_user_id="s2a-uid",
            chat_id="s2a-chat",
            text="x",
        )
        asyncio.run(turn(msg))

        actions = set(AuditLog.all_tenants.values_list("action", flat=True))
        assert "pipeline.shadow_dropped_outbound" in actions


class TestNonShadowGoesThrough:
    def test_primary_traffic_still_sends_outbound(self, _stub_external) -> None:
        Tenant.objects.create(slug="s2-primary-2", name="S2 primary 2", shadow_mode=False)
        msg = ChannelMessage(
            tenant_slug="s2-primary-2",
            channel="max",
            channel_user_id="s2p-uid",
            chat_id="s2p-chat",
            text="x",
        )
        asyncio.run(turn(msg))
        # Outbound was called at least once.
        assert _stub_external.call_count >= 1
