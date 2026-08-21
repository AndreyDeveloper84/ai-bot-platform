"""Bot Runtime Shadow Integration tests (OR-SHADOW-1..6).

Covers the §15 synthetic scenarios, the §16 hard side-effect proof, the
§17 legacy invariance, the single feature flag, tenant-less shadow, and
pipeline.turn() isolation. Legacy brain is always faked/authoritative in
these tests — shadow must never touch it.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from apps.orchestrator.shadow_turn import (
    EXEC_ERROR,
    EXEC_NOT_EVALUABLE,
    EXEC_PASS,
    EXEC_TIMEOUT,
    MATCH,
    MISMATCH,
    NOT_EVALUABLE,
    compare_turns,
    compute_shadow_turn,
)
from apps.orchestrator.turn_seam import (
    SURFACE_GLOBAL,
    SURFACE_PER_TENANT,
    TurnContext,
    TurnReply,
    orchestrate_turn,
)


def _conversation():
    return SimpleNamespace(id=uuid.uuid4(), bot_user=SimpleNamespace())


def _fake_classify(intent="booking", skill="booking", confidence=0.9):
    from apps.orchestrator.intent_router import IntentDecision

    async def _classify(text, **kwargs):
        _fake_classify.captured = {"text": text, **kwargs}
        return IntentDecision(intent=intent, skill=skill, confidence=confidence, risk_level="low")

    return _classify


@pytest.fixture
def fake_classify(monkeypatch):
    fake = _fake_classify()
    monkeypatch.setattr("apps.orchestrator.intent_router.classify", fake)
    return fake


def _ctx(**overrides) -> TurnContext:
    kwargs = dict(
        surface=SURFACE_PER_TENANT,
        conversation=_conversation(),
        bot_user=SimpleNamespace(id=1),
        text="хочу записаться",
        channel="max",
        trace_id="t-1",
    )
    kwargs.update(overrides)
    return TurnContext(**kwargs)


class TestComputeShadowTurn:
    def test_informational_turn(self, fake_classify):
        """§15.1 — benign text: PASS, control undecidable without dispatch,
        intended action recorded, never a dispatch execution."""
        result = compute_shadow_turn(text="привет", conversation=_conversation(), tenant=None)
        assert result.execution_status == EXEC_PASS
        assert result.control_decision is None
        assert "control:dispatch_never_executed" in result.not_evaluable_reasons
        assert result.intended_actions == ("SKIPPED_SKILL_DISPATCH:mutating_or_unknown_tools",)
        assert result.latency_ms >= 0

    def test_route_fields_and_l2_match(self, fake_classify):
        """§15.2 — intent/skill captured; L2 MATCH when routes align."""
        result = compute_shadow_turn(
            text="хочу записаться", conversation=_conversation(), tenant=None
        )
        assert result.intent == "booking"
        assert result.skill == "booking"
        cmp_ = compare_turns(TurnReply(reply_text="x", action_type="booking"), result)
        assert cmp_.l2_route == MATCH

    def test_handoff_control_match(self, monkeypatch, fake_classify):
        """§15.3 — shadow pre_check HANDOFF vs legacy handoff → L1 MATCH."""
        from apps.orchestrator.safety.pre_check import SafetyResult, SafetyVerdict

        monkeypatch.setattr(
            "apps.orchestrator.safety.pre_check.pre_check",
            lambda text, intent_decision=None: SafetyResult(verdict=SafetyVerdict.HANDOFF),
        )
        result = compute_shadow_turn(
            text="вызывайте человека", conversation=_conversation(), tenant=None
        )
        assert result.control_decision == "handoff"
        cmp_ = compare_turns(TurnReply(reply_text="x", should_handoff=True), result)
        assert cmp_.l1_control == MATCH

    def test_silence_vs_shadow_mismatch_is_diagnostic(self, fake_classify, monkeypatch):
        """§15.4/15 — legacy silence vs shadow handoff: MISMATCH is data,
        not a gate; nothing raises."""
        from apps.orchestrator.safety.pre_check import SafetyResult, SafetyVerdict

        monkeypatch.setattr(
            "apps.orchestrator.safety.pre_check.pre_check",
            lambda text, intent_decision=None: SafetyResult(verdict=SafetyVerdict.HANDOFF),
        )
        result = compute_shadow_turn(text="x", conversation=_conversation(), tenant=None)
        cmp_ = compare_turns(TurnReply(reply_text="", should_send=False), result)
        assert cmp_.l1_control == MISMATCH
        assert cmp_.legacy_control == "silence"

    def test_tenant_less_global(self, fake_classify):
        """§15.5 — tenant=None reaches classify as None; nothing fabricated."""
        compute_shadow_turn(text="hi", conversation=_conversation(), tenant=None)
        assert _fake_classify.captured["tenant"] is None

    def test_per_tenant(self, fake_classify):
        """§15.6 — a real tenant object is threaded through."""
        tenant = SimpleNamespace(id=uuid.uuid4())
        compute_shadow_turn(text="hi", conversation=_conversation(), tenant=tenant)
        assert _fake_classify.captured["tenant"] is tenant

    def test_intent_ambiguity(self, monkeypatch):
        """§15.7 — unknown intent is not a failure; L2 degrades honestly."""
        monkeypatch.setattr(
            "apps.orchestrator.intent_router.classify", _fake_classify("unknown", "", 0.1)
        )
        result = compute_shadow_turn(text="??", conversation=_conversation(), tenant=None)
        assert result.execution_status == EXEC_PASS
        cmp_ = compare_turns(TurnReply(reply_text="x"), result)
        assert cmp_.l2_route == NOT_EVALUABLE  # legacy route empty → not a mismatch

    def test_mutating_dispatch_never_executed(self, monkeypatch, fake_classify):
        """§15.8 — skill registry is never touched by the shadow compute."""

        def _boom(ctx):
            raise AssertionError("shadow must not execute skill dispatch")

        monkeypatch.setattr("apps.skills.registry.dispatch", _boom)
        result = compute_shadow_turn(text="запиши", conversation=_conversation(), tenant=None)
        assert result.execution_status == EXEC_PASS
        assert "SKIPPED_SKILL_DISPATCH" in result.intended_actions[0]

    def test_shadow_exception(self, monkeypatch):
        """§15.10 — classify failure → ERROR result, never an exception out."""

        async def _raise(text, **kwargs):
            raise RuntimeError("llm down")

        monkeypatch.setattr("apps.orchestrator.intent_router.classify", _raise)
        result = compute_shadow_turn(text="hi", conversation=_conversation(), tenant=None)
        assert result.execution_status == EXEC_ERROR
        assert "classify:RuntimeError" in result.error

    def test_timeout_budget(self, fake_classify):
        """§15.11 — zero budget → TIMEOUT before the expensive step."""
        result = compute_shadow_turn(
            text="hi", conversation=_conversation(), tenant=None, timeout_ms=-1
        )
        assert result.execution_status == EXEC_TIMEOUT

    def test_memory_context_available(self, monkeypatch, fake_classify):
        """§15.12 — snapshot is passed into classify when available."""
        from apps.orchestrator.memory.coordinator import MemorySnapshot

        monkeypatch.setattr(
            "apps.orchestrator.memory.coordinator.load_snapshot",
            lambda conversation: MemorySnapshot(
                history=[{"role": "user", "content": "x"}],
                long_term={"rfm_segment": "vip"},
                slot_state={},
            ),
        )
        compute_shadow_turn(text="hi", conversation=_conversation(), tenant=None)
        snapshot_arg = _fake_classify.captured["memory_snapshot"]
        assert snapshot_arg["long_term"] == {"rfm_segment": "vip"}

    def test_memory_context_unavailable(self, monkeypatch, fake_classify):
        """§15.13 — recall failure degrades to empty memory, not a crash."""
        monkeypatch.setattr(
            "apps.orchestrator.memory.short_term.recall",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("redis down")),
        )
        result = compute_shadow_turn(text="hi", conversation=_conversation(), tenant=None)
        assert result.execution_status == EXEC_PASS

    def test_callback_text_executes_no_routing(self, monkeypatch, fake_classify):
        """§15.14 — callback payload as text: compute-only, no booking/callback
        routing executed inside shadow."""

        def _boom(ctx):
            raise AssertionError("no dispatch in shadow")

        monkeypatch.setattr("apps.skills.registry.dispatch", _boom)
        result = compute_shadow_turn(text="cb:book:123", conversation=_conversation(), tenant=None)
        assert result.execution_status == EXEC_PASS


@pytest.mark.django_db
class TestWorkerAndSideEffects:
    """§16 — hard side-effect proof + §15.9 missing context."""

    def test_missing_conversation_not_evaluable(self, monkeypatch):
        from apps.orchestrator import tasks as shadow_tasks

        logged = {}
        monkeypatch.setattr(
            "apps.orchestrator.shadow_turn.log_shadow_comparison",
            lambda **kw: logged.update(kw),
        )
        # patch the lazily-imported name resolution path instead
        import apps.orchestrator.shadow_turn as st

        monkeypatch.setattr(st, "log_shadow_comparison", lambda **kw: logged.update(kw))
        payload = {
            "trace_id": "t",
            "surface": "global",
            "text": "hi",
            "conversation_id": str(uuid.uuid4()),
            "legacy_reply": TurnReply(reply_text="x").__dict__,
        }
        shadow_tasks.run_shadow_from_payload(payload)
        assert logged["shadow"].execution_status == EXEC_NOT_EVALUABLE

    def test_shadow_enabled_full_path_no_mutations(self, monkeypatch, settings):
        """Shadow on: seam enqueues (captured), worker computes — and NOTHING
        mutates: Message/AdminTask/MemoryEntry/ConsentRecord/Conversation
        state/outbound."""
        settings.ORCHESTRATOR_SHADOW_ENABLED = True
        from apps.conversations.models import Conversation, Message
        from apps.consent.models import ConsentRecord
        from apps.handoff.models import AdminTask
        from apps.identity.models import BotUser, MemoryEntry, UserPersonalContext
        from apps.tenancy.models import Tenant

        enqueued = {}
        monkeypatch.setattr(
            "apps.orchestrator.shadow_turn.dispatch_shadow_turn",
            lambda context, reply: enqueued.update(payload=True),
        )
        monkeypatch.setattr(
            "apps.orchestrator.concierge.generate_concierge_reply",
            lambda text, **kw: __import__(
                "apps.orchestrator.discovery", fromlist=["DiscoveryReply"]
            ).DiscoveryReply(text="legacy"),
        )

        tenant = Tenant.objects.create(slug="shadow-t", name="Shadow T")
        uid = uuid.uuid4()
        bot_user = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="su1", ayla_user_id=uid
        )
        UserPersonalContext.objects.create(user_id=uid)
        conversation = Conversation.all_tenants.create(
            tenant=tenant, bot_user=bot_user, state="idle"
        )
        state_before = conversation.state

        # 1. Seam turn with shadow ON — legacy reply computed, enqueue captured.
        ctx = _ctx(
            surface=SURFACE_GLOBAL, tenant=None, conversation=conversation, bot_user=bot_user
        )
        legacy = orchestrate_turn(ctx)
        assert legacy.reply_text == "legacy"
        assert enqueued.get("payload") is True

        # 2. Worker run with faked classify + real rows.
        monkeypatch.setattr("apps.orchestrator.intent_router.classify", _fake_classify())
        import apps.orchestrator.shadow_turn as st

        logged = {}
        monkeypatch.setattr(st, "log_shadow_comparison", lambda **kw: logged.update(kw))
        from apps.orchestrator import tasks as shadow_tasks

        shadow_tasks.run_shadow_from_payload(
            {
                "trace_id": "t",
                "surface": "global",
                "text": "hi",
                "conversation_id": str(conversation.id),
                "legacy_reply": legacy.__dict__ | {"new_state": None},
            }
        )
        assert logged["shadow"].execution_status == EXEC_PASS

        # 3. Hard side-effect assertions.
        assert Message.objects.count() == 0
        assert AdminTask.objects.count() == 0
        assert MemoryEntry.objects.count() == 0
        assert ConsentRecord.objects.count() == 0
        conversation.refresh_from_db()
        assert conversation.state == state_before


class TestLegacyInvariance:
    """§17 — SHADOW off vs on: the authoritative result is identical."""

    def _run(self, monkeypatch, enabled):
        from apps.orchestrator.discovery import DiscoveryReply

        monkeypatch.setattr(
            "apps.orchestrator.concierge.generate_concierge_reply",
            lambda text, **kw: DiscoveryReply(text="authoritative", action_data={"b": 1}),
        )
        if enabled:
            monkeypatch.setattr(
                "apps.orchestrator.shadow_turn.dispatch_shadow_turn",
                lambda context, reply: None,
            )
        return orchestrate_turn(_ctx(surface=SURFACE_GLOBAL, tenant=None))

    def test_invariance(self, monkeypatch, settings):
        settings.ORCHESTRATOR_SHADOW_ENABLED = False
        off = self._run(monkeypatch, enabled=False)
        settings.ORCHESTRATOR_SHADOW_ENABLED = True
        on = self._run(monkeypatch, enabled=True)
        assert off == on
        assert on.reply_text == "authoritative"

    def test_enqueue_failure_never_breaks_legacy(self, monkeypatch, settings):
        settings.ORCHESTRATOR_SHADOW_ENABLED = True

        def _boom(context, reply):
            raise RuntimeError("redis down")

        monkeypatch.setattr("apps.orchestrator.shadow_turn.dispatch_shadow_turn", _boom)
        from apps.orchestrator.discovery import DiscoveryReply

        monkeypatch.setattr(
            "apps.orchestrator.concierge.generate_concierge_reply",
            lambda text, **kw: DiscoveryReply(text="authoritative"),
        )
        reply = orchestrate_turn(_ctx(surface=SURFACE_GLOBAL, tenant=None))
        assert reply.reply_text == "authoritative"

    def test_flag_off_zero_shadow_work(self, monkeypatch, settings):
        """§10 — flag off: no enqueue call at all."""
        settings.ORCHESTRATOR_SHADOW_ENABLED = False
        calls = []
        monkeypatch.setattr(
            "apps.orchestrator.shadow_turn.dispatch_shadow_turn",
            lambda context, reply: calls.append(1),
        )
        from apps.orchestrator.discovery import DiscoveryReply

        monkeypatch.setattr(
            "apps.orchestrator.concierge.generate_concierge_reply",
            lambda text, **kw: DiscoveryReply(text="x"),
        )
        orchestrate_turn(_ctx(surface=SURFACE_GLOBAL, tenant=None))
        assert calls == []


class TestIsolation:
    def test_worker_registered(self):
        from apps.orchestrator import tasks as shadow_tasks  # noqa: F401

        assert shadow_tasks.run_shadow_turn_task.name == "orchestrator.shadow_turn"

    def test_shadow_never_calls_pipeline_turn(self, monkeypatch, fake_classify):
        """OR-SHADOW-2 — the full 19-step pipeline is never entered."""

        async def _boom(message):
            raise AssertionError("pipeline.turn must not run in shadow")

        monkeypatch.setattr("apps.orchestrator.pipeline.turn", _boom)
        result = compute_shadow_turn(text="hi", conversation=_conversation(), tenant=None)
        assert result.execution_status == EXEC_PASS

    def test_shadow_source_has_no_pipeline_turn_call(self):
        import inspect

        import apps.orchestrator.shadow_turn as st
        import apps.orchestrator.tasks as sw

        for module in (st, sw):
            src = inspect.getsource(module)
            assert "from apps.orchestrator.pipeline import turn" not in src
            assert "await turn(" not in src
            assert "sync_to_async(turn)" not in src


class _FakeTask:
    def __init__(self):
        self.calls: list = []

    def apply_async(self, *, args):
        self.calls.append(args[0])


@pytest.fixture
def fake_task(monkeypatch):
    task = _FakeTask()
    monkeypatch.setattr("apps.orchestrator.tasks.run_shadow_turn_task", task)
    monkeypatch.setattr("apps.orchestrator.shadow_turn._celery_queue_len", lambda: 0)
    return task


class TestShadowGate:
    """§16.1-7 + backlog admission — all gates on dispatch_shadow_turn."""

    def _dispatch(self, settings, context, **kw):
        from apps.orchestrator.shadow_turn import dispatch_shadow_turn

        settings.ORCHESTRATOR_SHADOW_ENABLED = kw.get("enabled", True)
        settings.ORCHESTRATOR_SHADOW_SAMPLE_RATE = kw.get("rate", 1.0)
        settings.ORCHESTRATOR_SHADOW_SURFACES = kw.get("surfaces", "global")
        dispatch_shadow_turn(context, TurnReply(reply_text="x"))

    def test_disabled_no_dispatch(self, settings, fake_task):
        """1. Flag off → no job dispatched."""
        self._dispatch(settings, _ctx(surface=SURFACE_GLOBAL, tenant=None), enabled=False)
        assert fake_task.calls == []

    def test_sample_rate_zero_no_dispatch(self, settings, fake_task):
        """2. rate=0.0 → no job even when enabled."""
        self._dispatch(settings, _ctx(surface=SURFACE_GLOBAL, tenant=None), rate=0.0)
        assert fake_task.calls == []

    def test_deterministic_sampling(self, settings, fake_task):
        """3. Same trace_id → same bucket decision; reproducible."""
        from apps.orchestrator.shadow_turn import _in_sample

        ctx = _ctx(surface=SURFACE_GLOBAL, tenant=None, trace_id="stable-trace-1")
        assert _in_sample(ctx, 0.5) == _in_sample(ctx, 0.5)
        # Boundary rates are unconditional.
        assert _in_sample(ctx, 1.0) is True
        assert _in_sample(ctx, 0.0) is False
        # Rate 1.0 dispatches this same context exactly once per call.
        self._dispatch(settings, ctx, rate=1.0)
        self._dispatch(settings, ctx, rate=1.0)
        assert len(fake_task.calls) == 2

    def test_global_only_targeting(self, settings, fake_task):
        """4. Default surfaces: global pilot IS eligible."""
        self._dispatch(settings, _ctx(surface=SURFACE_GLOBAL, tenant=None))
        assert len(fake_task.calls) == 1

    def test_per_tenant_excluded(self, settings, fake_task):
        """5. per-tenant MAX surface excluded by default targeting."""
        self._dispatch(settings, _ctx(surface=SURFACE_PER_TENANT))
        assert fake_task.calls == []

    def test_telegram_excluded(self, settings, fake_task):
        """6. Telegram rides the per-tenant surface → excluded too."""
        self._dispatch(settings, _ctx(surface=SURFACE_PER_TENANT, channel="telegram"))
        assert fake_task.calls == []

    def test_tenant_none_accepted(self, settings, fake_task):
        """7. tenant=None dispatches fine (global pilot, OR-SHADOW-4)."""
        self._dispatch(settings, _ctx(surface=SURFACE_GLOBAL, tenant=None))
        assert fake_task.calls[0]["tenant_id"] is None

    def test_backlog_admission_limit(self, settings, monkeypatch, fake_task):
        """§8 — over MAX_BACKLOG the job is dropped (logged), legacy fine."""
        settings.ORCHESTRATOR_SHADOW_MAX_BACKLOG = 10
        monkeypatch.setattr("apps.orchestrator.shadow_turn._celery_queue_len", lambda: 10)
        self._dispatch(settings, _ctx(surface=SURFACE_GLOBAL, tenant=None))
        assert fake_task.calls == []

    def test_malformed_job_isolated(self):
        """§16.11 — a garbage payload never raises out of the task."""
        from apps.orchestrator.tasks import run_shadow_turn_task

        run_shadow_turn_task({})  # must not raise
        run_shadow_turn_task({"legacy_reply": {"bad": "payload"}})  # same

    def test_no_raw_pii_in_operational_logs(self, settings, fake_task):
        """§15/16.15 — operational shadow logs carry no user text/PII."""
        import logging

        from apps.orchestrator.shadow_turn import (
            ShadowTurnResult,
            compare_turns,
            log_shadow_comparison,
        )

        secret = "паспорт-4500-123456"
        records: list[str] = []

        class _H(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        lg = logging.getLogger("apps.orchestrator.shadow_turn")
        h = _H()
        lg.addHandler(h)
        try:
            log_shadow_comparison(
                trace_id="t",
                surface="global",
                shadow=ShadowTurnResult(execution_status="PASS"),
                comparison=compare_turns(
                    TurnReply(reply_text=secret),
                    ShadowTurnResult(execution_status="PASS"),
                ),
            )
            # The dispatch path (attempted log) never logs text either.
            self._dispatch(settings, _ctx(surface=SURFACE_GLOBAL, tenant=None, text=secret))
        finally:
            lg.removeHandler(h)
        assert secret not in "\n".join(records)


class TestStage1PreActivationIsolation:
    """Stage-1 pre-flight: dedicated queue routing, backlog key, retry policy."""

    def test_shadow_task_routes_to_shadow_queue(self, settings):
        """1. CELERY_TASK_ROUTES sends ONLY orchestrator.shadow_turn to `shadow`."""
        routes = settings.CELERY_TASK_ROUTES
        assert routes["orchestrator.shadow_turn"]["queue"] == "shadow"

    def test_production_tasks_not_routed_to_shadow(self, settings):
        """2. Critical production tasks keep the default queue — nothing else
        is redirected to `shadow`."""
        routes = settings.CELERY_TASK_ROUTES
        assert "bookings.send_due_reminders" not in routes
        assert "apps.eventbus.dispatch_pending_events" not in routes
        for task_name, route in routes.items():
            if task_name != "orchestrator.shadow_turn":
                assert route.get("queue") != "shadow", task_name

    def test_backlog_gate_reads_shadow_queue(self, monkeypatch):
        """3. The admission gate measures the `shadow` Redis list, not `celery`."""
        seen: list[str] = []

        class _FakeClient:
            def llen(self, key):
                seen.append(key)
                return 0

        monkeypatch.setattr("apps.ingress.streams._client", lambda: _FakeClient())
        from apps.orchestrator.shadow_turn import _celery_queue_len

        assert _celery_queue_len() == 0
        assert seen == ["shadow"]

    def test_no_retry_amplification(self):
        """9. The shadow task never redelivers: acks_late=False, no autoretry."""
        from apps.orchestrator.tasks import run_shadow_turn_task

        assert run_shadow_turn_task.acks_late is False
        assert tuple(getattr(run_shadow_turn_task, "autoretry_for", ())) == ()
