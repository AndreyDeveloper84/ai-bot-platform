"""Sprint 6 / G2 — orchestrator end-to-end exit-gate (DRF-551).

Drives one real ChannelMessage through `turn()` and asserts EVERY layer's
side-effect lands as expected:

  1. Tenant scope is honoured (no CrossTenantError under STRICT_TENANT_SCOPE=strict)
  2. Conversation row created (one per bot_user)
  3. Message rows persisted (user + assistant, both with the same trace_id)
  4. ReplayTrace captured (Sprint 5 / O8 wiring under sampling=1.0)
  5. ClientProfile populated (Sprint 6 / P1 signal auto-create)
  6. AdminTask only on handoff/DLQ (negative assertion)

This is the meaningful Sprint 6 exit-gate: every component sprints 1-6 built
must be reachable from one production call.

LLM + MAX outbound are mocked (cost + reliability); ORM/audit/events/replay
are real.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from asgiref.sync import sync_to_async

from apps.orchestrator.llm.openai_provider import LLMResponse
from apps.orchestrator.pipeline import ChannelMessage, turn
from apps.tenancy.models import Tenant


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
    pytest.mark.asyncio,
]


def _fake_intent_provider() -> AsyncMock:
    """LLM that returns a pinned FAQ IntentDecision JSON."""
    provider = AsyncMock()
    provider.complete.return_value = LLMResponse(
        content=json.dumps(
            {
                "intent": "faq",
                "skill": "faq",
                "confidence": 0.9,
                "risk_level": "low",
                "missing_slots": [],
                "reply_mode": "text",
                "needs_rag": False,
                "needs_tool": False,
            }
        ),
        model="gpt-4o-mini-mock",
        is_fallback=False,
        tokens_in=10,
        tokens_out=20,
    )
    return provider


@pytest.fixture
def e2e_tenant(settings):
    """Tenant + opt into recorder writes + strict scope enforcement.

    STRICT_TENANT_SCOPE=strict raises CrossTenantError on any unscoped
    ORM call — Sprint 6 exit-gate must complete cleanly under strict.
    """

    settings.REPLAY_SAMPLE_RATE_TEST = 1.0
    settings.STRICT_TENANT_SCOPE = "strict"
    return Tenant.objects.create(slug="e2e-orchestrator", name="E2E Orchestrator")


@pytest.fixture(autouse=True)
def _stub_external():
    """Mock the only two external dependencies — LLM + MAX API."""
    with (
        patch(
            "apps.orchestrator.intent_router.OpenAIProvider",
            return_value=_fake_intent_provider(),
        ),
        patch("apps.channels.max.outbound.send_message", return_value={"ok": True}),
    ):
        yield


def _message(text: str) -> ChannelMessage:
    return ChannelMessage(
        tenant_slug="e2e-orchestrator",
        channel="max",
        channel_user_id="e2e-uid",
        chat_id="e2e-chat",
        text=text,
        display_name="E2E Tester",
        trace_id=str(uuid4()),
    )


async def test_exit_gate_full_stack(e2e_tenant):
    """One real ChannelMessage exercises every layer end-to-end."""

    result = await turn(_message("Когда вы работаете?"))

    # 1. Result shape
    assert result.ok is True, f"turn() expected ok=True, got: {result.error}"
    assert result.trace_id != ""
    assert result.reply is not None
    assert result.reply.text != ""
    assert result.intent is not None
    assert result.intent.intent == "faq"

    # 2. Conversation row created (one for the bot_user)
    from apps.conversations.models import Conversation

    convs = await sync_to_async(
        lambda: list(Conversation.all_tenants.filter(bot_user__channel_user_id="e2e-uid"))
    )()
    assert len(convs) == 1, f"expected 1 Conversation, got {len(convs)}"

    # 3. User + assistant Messages with matching trace_id
    from apps.conversations.models import Message

    msgs = await sync_to_async(
        lambda: list(
            Message.all_tenants.filter(trace_id=UUID(result.trace_id)).order_by("created_at")
        )
    )()
    roles = [m.role for m in msgs]
    assert "user" in roles, f"missing user Message, roles seen: {roles}"
    assert "assistant" in roles, f"missing assistant Message, roles seen: {roles}"

    # 4. ReplayTrace captured (recorder wired into step 18)
    from apps.replay.models import ReplayTrace

    traces = await sync_to_async(
        lambda: list(ReplayTrace.all_tenants.filter(trace_id=result.trace_id))
    )()
    assert len(traces) == 1, f"expected 1 ReplayTrace row, got {len(traces)}"
    # Multi-step shape per Sprint 6 / O8 refinement
    steps = traces[0].pipeline_steps
    step_names = {s.get("step") for s in steps if isinstance(s, dict)}
    assert {"inbound", "intent", "skill", "composer"}.issubset(step_names)

    # 5. ClientProfile created (P1 signal auto-creates on first BotUser save)
    from apps.identity.models import BotUser, ClientProfile

    bot_user = await sync_to_async(
        lambda: BotUser.all_tenants.get(channel_user_id="e2e-uid", tenant=e2e_tenant)
    )()
    profile_count = await sync_to_async(
        lambda: ClientProfile.all_tenants.filter(bot_user=bot_user).count()
    )()
    assert profile_count == 1, f"expected ClientProfile auto-created, got {profile_count}"

    # 6. No AdminTask (this turn was clean — no handoff, no outbound failure)
    from apps.handoff.models import AdminTask

    admin_count = await sync_to_async(
        lambda: AdminTask.all_tenants.filter(tenant=e2e_tenant).count()
    )()
    assert admin_count == 0, f"expected 0 AdminTasks (clean turn), got {admin_count}"


async def test_exit_gate_block_path(e2e_tenant):
    """Pre-check BLOCK short-circuit — still under STRICT_TENANT_SCOPE."""

    # "ибупрофен" is a default BLOCK pattern from O3.
    result = await turn(_message("посоветуйте ибупрофен"))

    assert result.ok is True  # block is a graceful exit
    assert result.pre_check_verdict == "block"
    assert result.short_circuited_at_step == 8

    # Still saves a user Message + assistant disclaimer Message
    from apps.conversations.models import Message

    msgs = await sync_to_async(
        lambda: list(Message.all_tenants.filter(trace_id=UUID(result.trace_id)))
    )()
    assert len(msgs) == 2

    # Assistant message carries the block action_type
    assistant = [m for m in msgs if m.role == "assistant"][0]
    assert assistant.action_type == "block"


async def test_exit_gate_handoff_path(e2e_tenant):
    """Pre-check HANDOFF — AdminTask created."""

    result = await turn(_message("я думаю о самоубийстве"))

    assert result.ok is True
    assert result.pre_check_verdict == "handoff"
    assert result.short_circuited_at_step == 9

    from apps.handoff.models import AdminTask

    tasks = await sync_to_async(lambda: list(AdminTask.all_tenants.filter(task_type="handoff")))()
    assert len(tasks) >= 1
