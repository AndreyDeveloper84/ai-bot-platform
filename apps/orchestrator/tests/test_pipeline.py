"""Pipeline.turn() tests (DRF-535 / Sprint 6 / O1).

Integration-style tests with mocked LLM. Each test drives a real
ChannelMessage through `turn()` and asserts the 19-step flow produces
the expected TurnResult shape.

The OpenAIProvider is patched to return a deterministic IntentDecision
so we don't burn LLM calls in CI.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import pytest
from asgiref.sync import sync_to_async

from apps.llm.protocol import CompletionResult
from apps.orchestrator.intent_router import IntentDecision
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


def _fake_intent_router(intent: str = "faq", risk_level: str = "low"):
    """Router stub whose intent provider answers with pinned IntentDecision JSON.

    #975 moved intent classification to the production path:
    ``_classify_production_path`` lazily imports ``apps.llm.router.get_router``
    and awaits ``provider.complete(...)`` on whatever the router returns.
    The deterministic seam is therefore the ROUTER — the Sprint-1
    ``intent_router.OpenAIProvider`` symbol is the legacy path and is no
    longer consulted by pipeline turns (that mock target was dead rot).
    """
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
    provider = AsyncMock()
    provider.complete.return_value = CompletionResult(text=json.dumps(payload))
    router = Mock()
    router.get_provider.return_value = provider
    return router


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
    with patch("apps.llm.router.get_router", return_value=_fake_intent_router()):
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


@pytest.fixture(autouse=True)
def _isolate_tracer_provider():
    """Neutralise the process-global OTel provider for the duration of each test.

    OTel's ``set_tracer_provider`` is one-shot: the first SDK provider
    installed by ANY test module stays active for the rest of the process
    (later calls are ignored with a warning). While one is active,
    ``apps.replay.recorder._prefer_otel_trace_id`` binds
    ``ReplayTrace.trace_id`` to the OTel span id instead of the message
    trace_id and TestReplayCapture can't find its row. Force the no-op
    proxy here (span assertions live in test_otel*.py, not in this
    module) and restore the previous provider afterwards.
    """
    from opentelemetry import trace as otel_trace

    original = otel_trace._TRACER_PROVIDER
    otel_trace._TRACER_PROVIDER = None
    yield
    otel_trace._TRACER_PROVIDER = original


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


class TestConfidenceFloor:
    """Tier-A #4 (P1 PRE_PILOT, 2026-05-27, founder pilot_scope_discipline #5).

    Pipeline-level defense-in-depth: pipeline step 10.5 enforces
    ``confidence < threshold`` even when skill forgets to set
    ``should_handoff=True``. AdminTask reason carries the diagnostic
    trace ``pipeline_confidence_floor(confidence=X, threshold=Y)``.
    """

    async def _patched_dispatch_with_confidence(
        self,
        *,
        confidence: float | None,
        should_handoff: bool = False,
        handoff_reason: str = "",
        meta: dict | None = None,
        reply_text: str = "fake reply",
    ):
        from apps.skills.base import SkillResult

        result = SkillResult(
            reply_text=reply_text,
            confidence=confidence,
            should_handoff=should_handoff,
            handoff_reason=handoff_reason,
            meta=meta or {"skill": "faq"},
        )
        return patch("apps.skills.registry.dispatch", return_value=result)

    async def test_low_confidence_triggers_handoff_even_without_flag(self, tenant, settings):
        """Skill returned ``confidence=0.3`` but did NOT set
        ``should_handoff=True``. Pipeline catches it and escalates."""
        settings.AI_CONFIDENCE_HANDOFF_THRESHOLD = 0.5
        settings.SKILL_CONFIDENCE_HANDOFF_THRESHOLD = {}

        with await self._patched_dispatch_with_confidence(confidence=0.3):
            result = await turn(_message(text="что-то непонятное"))
        assert result.short_circuited_at_step == 10.5

        from apps.handoff.models import AdminTask

        rows = await sync_to_async(lambda: list(AdminTask.all_tenants.all()))()
        assert any("pipeline_confidence_floor" in (r.reason or "") for r in rows)

    async def test_low_confidence_diagnostic_format(self, tenant, settings):
        """AdminTask.reason carries ``confidence=X, threshold=Y`` trace."""
        settings.AI_CONFIDENCE_HANDOFF_THRESHOLD = 0.5
        settings.SKILL_CONFIDENCE_HANDOFF_THRESHOLD = {}

        with await self._patched_dispatch_with_confidence(confidence=0.32):
            await turn(_message(text="?"))

        from apps.handoff.models import AdminTask

        rows = await sync_to_async(lambda: list(AdminTask.all_tenants.all()))()
        match = next(
            (r for r in rows if "pipeline_confidence_floor" in (r.reason or "")),
            None,
        )
        assert match is not None
        assert "confidence=0.32" in match.reason
        assert "threshold=0.50" in match.reason

    async def test_high_confidence_does_not_trigger(self, tenant, settings):
        """``confidence=0.8`` ≥ threshold → no handoff."""
        settings.AI_CONFIDENCE_HANDOFF_THRESHOLD = 0.5
        settings.SKILL_CONFIDENCE_HANDOFF_THRESHOLD = {}

        with await self._patched_dispatch_with_confidence(confidence=0.8):
            result = await turn(_message(text="вопрос"))
        # Pipeline runs к completion (no short-circuit at 10.5).
        assert result.short_circuited_at_step != 10.5

    async def test_none_confidence_does_not_trigger(self, tenant, settings):
        """Skills с ``confidence=None`` (Sprint 3 deterministic) — no
        enforcement. Skill retains decision via ``should_handoff``."""
        settings.AI_CONFIDENCE_HANDOFF_THRESHOLD = 0.5
        settings.SKILL_CONFIDENCE_HANDOFF_THRESHOLD = {}

        with await self._patched_dispatch_with_confidence(confidence=None):
            result = await turn(_message(text="вопрос"))
        assert result.short_circuited_at_step != 10.5

    async def test_per_skill_threshold_overrides_global(self, tenant, settings):
        """Per-skill dict wins over global default."""
        settings.AI_CONFIDENCE_HANDOFF_THRESHOLD = 0.9  # strict global
        settings.SKILL_CONFIDENCE_HANDOFF_THRESHOLD = {"faq": 0.3}  # lax FAQ

        with await self._patched_dispatch_with_confidence(confidence=0.5):
            result = await turn(_message(text="?"))
        # 0.5 < 0.9 (global) would trigger, BUT 0.5 ≥ 0.3 (per-skill) → no trigger.
        assert result.short_circuited_at_step != 10.5

    async def test_per_skill_none_disables_enforcement(self, tenant, settings):
        """Setting threshold к ``None`` for a skill disables enforcement
        even when confidence is below global."""
        settings.AI_CONFIDENCE_HANDOFF_THRESHOLD = 0.5
        settings.SKILL_CONFIDENCE_HANDOFF_THRESHOLD = {"faq": None}

        with await self._patched_dispatch_with_confidence(confidence=0.1):
            result = await turn(_message(text="?"))
        # Disabled per-skill → no handoff даже at very low confidence.
        assert result.short_circuited_at_step != 10.5

    async def test_skill_should_handoff_plus_low_confidence_concatenates(self, tenant, settings):
        """Skill explicitly handoffs с reason 'faq_low_confidence' AND
        confidence is below threshold → AdminTask reason includes BOTH
        the skill reason AND the diagnostic trace."""
        settings.AI_CONFIDENCE_HANDOFF_THRESHOLD = 0.5
        settings.SKILL_CONFIDENCE_HANDOFF_THRESHOLD = {}

        with await self._patched_dispatch_with_confidence(
            confidence=0.3,
            should_handoff=True,
            handoff_reason="faq_low_confidence",
        ):
            await turn(_message(text="?"))

        from apps.handoff.models import AdminTask

        rows = await sync_to_async(lambda: list(AdminTask.all_tenants.all()))()
        match = next(
            (r for r in rows if "pipeline_confidence_floor" in (r.reason or "")),
            None,
        )
        assert match is not None
        # Tech-lead Q4 trace preservation: skill reason | pipeline reason.
        assert "faq_low_confidence" in match.reason
        assert "pipeline_confidence_floor" in match.reason

    async def test_boundary_confidence_equal_threshold_does_not_trigger(self, tenant, settings):
        """``confidence == threshold`` → does NOT trigger (strict <)."""
        settings.AI_CONFIDENCE_HANDOFF_THRESHOLD = 0.5
        settings.SKILL_CONFIDENCE_HANDOFF_THRESHOLD = {}

        with await self._patched_dispatch_with_confidence(confidence=0.5):
            result = await turn(_message(text="?"))
        assert result.short_circuited_at_step != 10.5


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


class TestTenantQuotaExhaustedFallback:
    """Phase 1 / PI9 (DRF-860) — when any LLM call site raises
    TenantQuotaExceeded inside turn(), the orchestrator catches once at
    the outer boundary and serves the static Russian fallback."""

    async def test_quota_exhausted_returns_fallback_reply(self, tenant):
        from apps.llm.cost_tracker import TenantQuotaExceeded

        # Make `classify` raise TenantQuotaExceeded — simulates the gate
        # tripping inside the intent step.
        def _raise(*args, **kwargs):
            raise TenantQuotaExceeded(
                tenant_id=str(tenant.id),
                which_cap="token",
                cap_value=10_000,
                current_value=10_500,
            )

        with patch(
            "apps.orchestrator.pipeline.classify",
            side_effect=_raise,
        ):
            result = await turn(_message(text="hi"))

        assert result.ok is True
        assert result.reply is not None
        assert "лимит" in result.reply.text.lower()
        assert result.error == "tenant_quota_exhausted"

    async def test_quota_exhausted_writes_audit_row(self, tenant):
        from apps.llm.cost_tracker import (
            AUDIT_QUOTA_FALLBACK,
            TenantQuotaExceeded,
        )

        def _raise(*args, **kwargs):
            raise TenantQuotaExceeded(
                tenant_id=str(tenant.id),
                which_cap="cost",
                cap_value="5.00",
                current_value="5.10",
            )

        with patch("apps.orchestrator.pipeline.classify", side_effect=_raise):
            result = await turn(_message(text="hi"))

        from apps.audit.models import AuditLog

        def _fetch():
            return list(AuditLog.all_tenants.filter(action=AUDIT_QUOTA_FALLBACK))

        rows = await sync_to_async(_fetch)()
        assert len(rows) >= 1
        assert rows[0].payload["which_cap"] == "cost"
        assert rows[0].payload["trace_id"] == result.trace_id


class TestRetryExhaustedFallback:
    """Phase 1 / PI7 (DRF-858) — when any LLM call site raises
    RetriableLLMError inside turn() (all transient-retry attempts
    failed), the orchestrator catches at the outer boundary and
    serves the static Russian fallback + writes an audit row + emits
    a Telegram alert to the salon manager (deduped per tenant per
    hour).
    """

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from django.core.cache import cache

        cache.clear()
        yield
        cache.clear()

    async def test_retry_exhausted_returns_fallback_reply(self, tenant):
        from apps.llm.retry import RetriableLLMError

        class _FakeUpstream(Exception):
            pass

        _FakeUpstream.__name__ = "RateLimitError"

        def _raise(*args, **kwargs):
            raise RetriableLLMError(attempts=3, last_error=_FakeUpstream("429"))

        with patch("apps.orchestrator.pipeline.classify", side_effect=_raise):
            result = await turn(_message(text="hi"))

        assert result.ok is True
        assert result.reply is not None
        # Static Russian fallback line — the manager has been alerted.
        assert "сейчас не могу ответить" in result.reply.text.lower()
        assert "менедж" in result.reply.text.lower()
        assert result.error == "llm_retry_exhausted"

    async def test_retry_exhausted_writes_audit_row(self, tenant):
        from apps.llm.retry import AUDIT_RETRY_EXHAUSTED, RetriableLLMError

        class _FakeUpstream(Exception):
            pass

        _FakeUpstream.__name__ = "InternalServerError"

        def _raise(*args, **kwargs):
            raise RetriableLLMError(attempts=3, last_error=_FakeUpstream("503 service unavailable"))

        with patch("apps.orchestrator.pipeline.classify", side_effect=_raise):
            await turn(_message(text="hi"))

        from apps.audit.models import AuditLog

        rows = await sync_to_async(
            lambda: list(AuditLog.all_tenants.filter(action=AUDIT_RETRY_EXHAUSTED))
        )()
        assert len(rows) >= 1
        payload = rows[0].payload
        assert payload["attempts"] == 3
        assert payload["last_error_class"] == "InternalServerError"
        assert payload["tenant_id"] == str(tenant.id)

    async def test_retry_exhausted_sends_manager_alert(self, tenant):
        """When manager_chat_id is set, the orchestrator sends one
        Telegram alert to the manager so they can check the vendor's
        status page."""
        from apps.llm.retry import RetriableLLMError

        # Set manager chat id so the alert is dispatched.
        tenant.manager_chat_id = "100200300"
        await sync_to_async(tenant.save)()

        class _FakeUpstream(Exception):
            pass

        _FakeUpstream.__name__ = "RateLimitError"

        def _raise(*args, **kwargs):
            raise RetriableLLMError(attempts=3, last_error=_FakeUpstream("429"))

        with (
            patch("apps.orchestrator.pipeline.classify", side_effect=_raise),
            patch("apps.channels.max.outbound.send_message") as mock_send,
        ):
            await turn(_message(text="hi"))

        # The manager alert is one specific send_message call;
        # the outbound to the user (the static fallback) is another.
        # We look for the alert text marker "LLM провайдер недоступен".
        alert_calls = [
            c
            for c in mock_send.call_args_list
            if c.kwargs.get("chat_id") == "100200300"
            and "LLM провайдер недоступен" in (c.kwargs.get("text") or "")
        ]
        assert len(alert_calls) == 1

    async def test_retry_exhausted_alert_deduped_within_hour(self, tenant):
        """Two retry-exhausted turns within the dedup window send
        exactly ONE manager alert."""
        from apps.llm.retry import RetriableLLMError

        tenant.manager_chat_id = "200300400"
        await sync_to_async(tenant.save)()

        class _FakeUpstream(Exception):
            pass

        _FakeUpstream.__name__ = "RateLimitError"

        def _raise(*args, **kwargs):
            raise RetriableLLMError(attempts=3, last_error=_FakeUpstream("429"))

        with (
            patch("apps.orchestrator.pipeline.classify", side_effect=_raise),
            patch("apps.channels.max.outbound.send_message") as mock_send,
        ):
            await turn(_message(text="hi"))
            await turn(_message(text="hi again"))

        alert_calls = [
            c
            for c in mock_send.call_args_list
            if c.kwargs.get("chat_id") == "200300400"
            and "LLM провайдер недоступен" in (c.kwargs.get("text") or "")
        ]
        # Dedup: exactly one alert despite two exhausted turns.
        assert len(alert_calls) == 1

    async def test_retry_exhausted_no_manager_chat_id_skips_alert(self, tenant):
        """Empty manager_chat_id → no alert sent (log + skip), but
        the user-facing fallback still happens and the audit row
        still gets written."""
        from apps.llm.retry import AUDIT_RETRY_EXHAUSTED, RetriableLLMError

        # tenant fixture creates a Tenant without manager_chat_id.
        assert not (tenant.manager_chat_id or "")

        class _FakeUpstream(Exception):
            pass

        _FakeUpstream.__name__ = "RateLimitError"

        def _raise(*args, **kwargs):
            raise RetriableLLMError(attempts=3, last_error=_FakeUpstream("429"))

        with (
            patch("apps.orchestrator.pipeline.classify", side_effect=_raise),
            patch("apps.channels.max.outbound.send_message") as mock_send,
        ):
            result = await turn(_message(text="hi"))

        # User-facing fallback served as normal.
        assert result.ok is True
        assert result.error == "llm_retry_exhausted"
        # No alert calls (only the user-facing outbound from step 19).
        alert_calls = [
            c
            for c in mock_send.call_args_list
            if "LLM провайдер недоступен" in (c.kwargs.get("text") or "")
        ]
        assert len(alert_calls) == 0
        # Audit row written regardless.
        from apps.audit.models import AuditLog

        rows = await sync_to_async(
            lambda: list(AuditLog.all_tenants.filter(action=AUDIT_RETRY_EXHAUSTED))
        )()
        assert len(rows) >= 1
        assert rows[0].payload["trace_id"] == result.trace_id
