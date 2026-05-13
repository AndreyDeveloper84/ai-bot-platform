"""Pipeline.turn() tests (DRF-535 / Sprint 6 / O1).

Integration-style tests with mocked LLM. Each test drives a real
ChannelMessage through `turn()` and asserts the 19-step flow produces
the expected TurnResult shape.

The OpenAIProvider is patched to return a deterministic IntentDecision
so we don't burn LLM calls in CI.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from asgiref.sync import sync_to_async

from apps.orchestrator.intent_router import IntentDecision
from apps.orchestrator.llm.openai_provider import LLMResponse
from apps.orchestrator.pipeline import (
    ChannelMessage,
    TurnResult,
    turn,
)
from apps.tenancy.models import Tenant


pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.asyncio,
]


def _fake_llm(intent: str = "faq", risk_level: str = "low"):
    """Build a fake provider whose complete() returns a pinned IntentDecision JSON."""
    import json

    payload = {
        "intent": intent,
        "skill": intent,
        "confidence": 0.9,
        "risk_level": risk_level,
        "missing_slots": [],
        "reply_mode": "text",
        "needs_rag": False,
        "needs_tool": False,
    }
    response = LLMResponse(
        content=json.dumps(payload),
        model="gpt-4o-mini-mock",
        is_fallback=False,
        tokens_in=10,
        tokens_out=20,
    )
    provider = AsyncMock()
    provider.complete.return_value = response
    return provider


def _message(text: str = "сколько стоит массаж?", tenant_slug: str = "o1-test"):
    return ChannelMessage(
        tenant_slug=tenant_slug,
        channel="max",
        channel_user_id="o1-uid",
        chat_id="o1-chat",
        text=text,
        display_name="O1 Tester",
        trace_id=str(uuid4()),
    )


@pytest.fixture
def tenant():
    return Tenant.objects.create(slug="o1-test", name="O1 test")


@pytest.fixture(autouse=True)
def _stub_provider():
    """Default: every test gets the faq-intent fake LLM unless overridden."""
    with patch(
        "apps.orchestrator.intent_router.OpenAIProvider",
        return_value=_fake_llm(),
    ):
        yield


@pytest.fixture(autouse=True)
def _stub_outbound():
    """Stub MAX outbound — MAX_BOT_TOKEN isn't set in test env (and we don't
    want to hit the real API). Patched at import-site inside pipeline._send_outbound;
    individual tests can re-patch with side_effect to test retry/DLQ.
    """
    with patch(
        "apps.channels.max.outbound.send_message",
        return_value={"ok": True},
    ):
        yield


class TestHappyPath:
    async def test_returns_turn_result(self, tenant):
        result = await turn(_message())
        assert isinstance(result, TurnResult)
        assert result.ok is True
        assert result.reply is not None
        assert result.reply.text != ""

    async def test_intent_captured(self, tenant):
        result = await turn(_message())
        assert result.intent is not None
        assert isinstance(result.intent, IntentDecision)
        assert result.intent.intent == "faq"

    async def test_pre_check_verdict_allow(self, tenant):
        result = await turn(_message(text="when do you open"))
        assert result.pre_check_verdict in ("allow", "clarify")

    async def test_trace_id_preserved(self, tenant):
        msg = _message()
        result = await turn(msg)
        assert result.trace_id == msg.trace_id

    async def test_trace_id_generated_if_missing(self, tenant):
        msg = ChannelMessage(
            tenant_slug="o1-test",
            channel="max",
            channel_user_id="uid",
            chat_id="chat",
            text="hello",
            trace_id="",
        )
        result = await turn(msg)
        assert result.trace_id != ""


class TestUnknownTenant:
    async def test_returns_error_result(self):
        # No tenant fixture — slug doesn't exist.
        msg = _message(tenant_slug="does-not-exist")
        result = await turn(msg)
        assert result.ok is False
        assert result.error == "unknown_tenant"
        assert result.short_circuited_at_step == 1


class TestPreCheckBlock:
    async def test_block_short_circuits(self, tenant):
        # "ибупрофен" is a default BLOCK pattern from O3.
        result = await turn(_message(text="посоветуйте ибупрофен"))
        assert result.ok is True
        assert result.short_circuited_at_step == 8
        assert result.pre_check_verdict == "block"
        assert "не могу ответить" in result.reply.text or "менеджер" in result.reply.text

    async def test_block_saves_assistant_message(self, tenant):
        result = await turn(_message(text="дайте парацетамол"))
        from apps.conversations.models import Message

        def _check():
            qs = Message.all_tenants.filter(trace_id=UUID(result.trace_id), role="assistant")
            return qs.count(), qs.first().action_type if qs.exists() else ""

        count, action_type = await sync_to_async(_check)()
        assert count == 1
        assert action_type == "block"


class TestPreCheckHandoff:
    async def test_handoff_creates_admin_task(self, tenant):
        # "самоубийств" is a default HANDOFF pattern.
        result = await turn(_message(text="я думаю о самоубийстве"))
        assert result.ok is True
        assert result.short_circuited_at_step == 9
        assert result.pre_check_verdict == "handoff"

        from apps.handoff.models import AdminTask

        count = await sync_to_async(
            lambda: AdminTask.all_tenants.filter(task_type="handoff").count()
        )()
        assert count >= 1


class TestSkillDispatch:
    async def test_skill_response_composed(self, tenant):
        result = await turn(_message(text="когда вы работаете?"))
        # FAQ skill stub returns canned text.
        assert result.ok is True
        assert result.reply is not None
        # The FAQ stub reply OR safety-revised text — both acceptable.
        assert result.reply.text != ""

    async def test_user_and_assistant_messages_saved(self, tenant):
        result = await turn(_message(text="когда работаете"))
        from apps.conversations.models import Message

        def _roles():
            return list(
                Message.all_tenants.filter(trace_id=UUID(result.trace_id))
                .order_by("created_at")
                .values_list("role", flat=True)
            )

        roles = await sync_to_async(_roles)()
        assert "user" in roles
        assert "assistant" in roles


class TestPostSkillHandoff:
    """Step 10.5 — Sprint 7 / O2 (DRF-556).

    The dispatched skill can ask the pipeline to escalate after running
    (low-confidence retrieval, contraindication detected, etc.). The
    branch reuses :func:`_create_handoff` so a single AdminTask flow
    handles both pre-skill (step 9) and post-skill (10.5) cases.
    """

    async def _patched_dispatch(self, *, reply_text: str = "", reason: str = ""):
        """Yield a context that overrides skill dispatch to return a
        SkillResult requesting handoff. Returns the patcher so the test
        can keep it active for the whole turn().
        """
        from apps.skills.base import SkillResult

        result = SkillResult(
            reply_text=reply_text,
            should_handoff=True,
            handoff_reason=reason,
        )
        return patch("apps.skills.registry.dispatch", return_value=result)

    async def test_short_circuits_at_step_10_5(self, tenant):
        with await self._patched_dispatch(reason="faq_low_confidence"):
            result = await turn(_message(text="расскажите про вакуумно-роликовый"))
        assert result.ok is True
        assert result.short_circuited_at_step == 10.5

    async def test_creates_admin_task(self, tenant):
        with await self._patched_dispatch(reason="faq_low_confidence"):
            result = await turn(_message(text="странный вопрос"))

        from apps.handoff.models import AdminTask

        rows = await sync_to_async(
            lambda: list(AdminTask.all_tenants.filter(task_type="handoff"))
        )()
        # Latest task carries our reason.
        matched = [r for r in rows if "faq_low_confidence" in (r.reason or "")]
        assert matched, f"no AdminTask carried faq_low_confidence reason; got {rows}"
        assert result.reply is not None

    async def test_default_reason_when_skill_omits(self, tenant):
        # Skill emits should_handoff=True but leaves handoff_reason="".
        with await self._patched_dispatch(reason=""):
            await turn(_message(text="неожиданное"))

        from apps.handoff.models import AdminTask

        rows = await sync_to_async(lambda: list(AdminTask.all_tenants.all()))()
        # Pipeline fills in the fallback reason slug.
        assert any("skill_requested_handoff" in (r.reason or "") for r in rows)

    async def test_skill_reply_text_honoured_when_non_empty(self, tenant):
        with await self._patched_dispatch(
            reply_text="Извините, лучше уточнить у мастера — переключаю.",
            reason="faq_low_confidence",
        ):
            result = await turn(_message(text="?"))

        assert result.reply is not None
        assert "уточнить у мастера" in result.reply.text

    async def test_canned_fallback_when_skill_reply_blank(self, tenant):
        with await self._patched_dispatch(reply_text="", reason="faq_low_confidence"):
            result = await turn(_message(text="?"))

        assert result.reply is not None
        # _FALLBACK_HANDOFF — "Передаю менеджеру…"
        assert "менеджер" in result.reply.text.lower()


class TestErrorPath:
    async def test_unhandled_exception_returns_fallback(self, tenant):
        # Patch the intent classifier to raise mid-pipeline.
        with patch(
            "apps.orchestrator.pipeline.classify",
            side_effect=RuntimeError("synthetic"),
        ):
            result = await turn(_message())
        assert result.ok is False
        assert "synthetic" in result.error or "unhandled" in result.error
        assert result.reply is not None
        assert "что-то пошло не так" in result.reply.text


class TestReplayCapture:
    async def test_trace_captured(self, tenant, settings):
        settings.REPLAY_SAMPLE_RATE_TEST = 1.0
        result = await turn(_message(text="когда работаете"))

        from apps.replay.models import ReplayTrace

        count = await sync_to_async(
            lambda: ReplayTrace.all_tenants.filter(trace_id=result.trace_id).count()
        )()
        assert count == 1


class TestAuditTrail:
    async def test_pipeline_audit_row_written(self, tenant):
        result = await turn(_message(text="когда работаете"))

        from apps.audit.models import AuditLog

        def _find():
            for r in AuditLog.all_tenants.filter(action="pipeline.turn.completed"):
                if r.payload.get("trace_id") == result.trace_id:
                    return r
            return None

        match = await sync_to_async(_find)()
        assert match is not None
        assert match.payload["intent"] == "faq"


class TestOutboundRetryDLQ:
    """Sprint 6 / O9 — outbound retry + AdminTask DLQ."""

    async def test_outbound_success_sets_ok_true(self, tenant):
        result = await turn(_message(text="когда работаете"))
        assert result.ok is True
        assert result.error == ""

    async def test_outbound_failure_after_retries_writes_dlq(self, tenant):
        from apps.channels.max.outbound import MaxAPIError

        with patch(
            "apps.channels.max.outbound.send_message",
            side_effect=MaxAPIError(502, "bad gateway"),
        ):
            result = await turn(_message(text="когда работаете"))

        assert result.ok is False
        assert result.error == "outbound_failed"

        # AdminTask DLQ row created.
        from apps.handoff.models import AdminTask

        manual_tasks = await sync_to_async(
            lambda: list(AdminTask.all_tenants.filter(task_type="manual"))
        )()
        outbound_dlq = [t for t in manual_tasks if "outbound_failed" in (t.reason or "")]
        assert len(outbound_dlq) >= 1

    async def test_outbound_retries_3_times(self, tenant):
        from apps.channels.max.outbound import MaxAPIError

        call_count = {"n": 0}

        def flaky(**kwargs):
            call_count["n"] += 1
            raise MaxAPIError(502, "transient")

        with patch("apps.channels.max.outbound.send_message", side_effect=flaky):
            await turn(_message(text="когда работаете"))

        assert call_count["n"] == 3  # Initial + 2 retries = 3 total attempts

    async def test_outbound_succeeds_on_second_attempt(self, tenant):
        from apps.channels.max.outbound import MaxAPIError

        call_count = {"n": 0}

        def flaky(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise MaxAPIError(502, "transient")
            return {"ok": True}

        with patch("apps.channels.max.outbound.send_message", side_effect=flaky):
            result = await turn(_message(text="когда работаете"))

        assert result.ok is True
        assert call_count["n"] == 2


class TestEventEmission:
    async def test_message_sent_emitted(self, tenant):
        # emit is imported INSIDE the helper, so patch at the source module.
        with patch("apps.events.services.emit") as mock_emit:
            await turn(_message(text="когда работаете"))
        event_calls = [
            c for c in mock_emit.call_args_list if c.args and c.args[0] == "message_sent"
        ]
        assert len(event_calls) >= 1
