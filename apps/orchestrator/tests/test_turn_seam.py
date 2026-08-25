"""Orchestration seam tests (apps.orchestrator.turn_seam).

Pins: normalized DTO validity (per-tenant + tenant-less global), 1:1
adapter mappings to the legacy brains, behavior parity through the seam,
the side-effect boundary (the seam itself persists/sends/mutates NOTHING),
tenant semantics (fail-closed per-tenant, tenant=None global, never a
fake tenant), and pipeline.turn() isolation (OR-BOT-4).
"""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from apps.orchestrator import turn_seam
from apps.orchestrator.discovery import DiscoveryReply
from apps.orchestrator.turn_seam import (
    SURFACE_GLOBAL,
    SURFACE_PER_TENANT,
    TurnContext,
    TurnReply,
    orchestrate_turn,
    turn_reply_to_skill_result,
)
from apps.skills.base import SkillResult


def _ctx(**overrides) -> TurnContext:
    kwargs: dict[str, Any] = dict(
        surface=SURFACE_PER_TENANT,
        conversation=SimpleNamespace(id=uuid.uuid4()),
        bot_user=SimpleNamespace(id=1),
        text="привет",
        channel="max",
        trace_id="t-1",
    )
    kwargs.update(overrides)
    return TurnContext(**kwargs)


def _skill_result(**overrides) -> SkillResult:
    kwargs: dict[str, Any] = dict(reply_text="ответ")
    kwargs.update(overrides)
    return SkillResult(**kwargs)


class TestTurnContext:
    def test_per_tenant_context_valid(self):
        """1. Per-tenant context constructs; tenant handle optional."""
        ctx = _ctx()
        assert ctx.surface == SURFACE_PER_TENANT
        assert ctx.tenant is None  # handle only — the seam never resolves it

    def test_global_context_tenant_none_valid(self):
        """2./10. tenant=None is a VALID global-pilot input (OR-BOT-3)."""
        ctx = _ctx(surface=SURFACE_GLOBAL, tenant=None)
        assert ctx.tenant is None
        assert ctx.surface == SURFACE_GLOBAL


class TestAdapterMapping:
    def test_skill_dispatch_mapping(self, monkeypatch):
        """3. SkillResult → TurnReply field mapping is exact."""
        monkeypatch.setattr(
            "apps.skills.registry.dispatch",
            lambda skill_ctx: _skill_result(
                reply_text="ок",
                action_type="faq",
                action_data={"buttons": [{"label": "x", "callback": "cb:x"}]},
                should_handoff=True,
                handoff_reason="faq_low_confidence",
                meta={"reply_kind": "faq_card"},
            ),
        )
        from apps.tenancy.context import tenant_scope

        with tenant_scope(SimpleNamespace(id=uuid.uuid4())):
            reply = orchestrate_turn(_ctx())
        assert reply.matched is True
        assert reply.reply_text == "ок"
        assert reply.action_type == "faq"
        assert reply.action_data == {"buttons": [{"label": "x", "callback": "cb:x"}]}
        assert reply.should_handoff is True
        assert reply.handoff_reason == "faq_low_confidence"
        assert reply.meta == {"reply_kind": "faq_card"}

    def test_no_skill_matched_maps_to_unmatched(self, monkeypatch):
        """3b. None SkillResult → matched=False (echo fallback preserved)."""
        monkeypatch.setattr("apps.skills.registry.dispatch", lambda skill_ctx: None)
        from apps.tenancy.context import tenant_scope

        with tenant_scope(SimpleNamespace(id=uuid.uuid4())):
            reply = orchestrate_turn(_ctx())
        assert reply.matched is False
        assert turn_reply_to_skill_result(reply) is None

    def test_concierge_mapping(self, monkeypatch):
        """4. DiscoveryReply → TurnReply: text/action_data/persisted."""
        monkeypatch.setattr(
            "apps.orchestrator.concierge.generate_concierge_reply",
            lambda text, **kwargs: DiscoveryReply(
                text=" concierge ", action_data={"a": 1}, persisted=True
            ),
        )
        reply = orchestrate_turn(_ctx(surface=SURFACE_GLOBAL, tenant=None))
        assert reply.reply_text == " concierge "
        assert reply.action_data == {"a": 1}
        assert reply.assistant_persisted is True
        assert reply.should_send is True


class TestBehaviorParity:
    """5./6./8./9. Same input → same observable result via seam vs direct."""

    def test_per_tenant_roundtrip_matches_direct(self, monkeypatch):
        direct_result = _skill_result(
            reply_text="прямой ответ",
            action_type="booking",
            action_data={"k": "v"},
            new_state="ACTIVE",
            should_close_conversation=False,
            meta={"reply_kind": "booking_card"},
            confidence=0.42,
        )
        monkeypatch.setattr("apps.skills.registry.dispatch", lambda skill_ctx: direct_result)
        from apps.tenancy.context import tenant_scope

        with tenant_scope(SimpleNamespace(id=uuid.uuid4())):
            via_seam = turn_reply_to_skill_result(orchestrate_turn(_ctx()))
        assert via_seam is not direct_result  # normalized through DTOs
        for field in (
            "reply_text",
            "action_type",
            "action_data",
            "should_send",
            "should_handoff",
            "handoff_reason",
            "new_state",
            "should_close_conversation",
            "meta",
            "confidence",
        ):
            assert getattr(via_seam, field) == getattr(direct_result, field)

    def test_handoff_parity(self, monkeypatch):
        """8. should_handoff + handoff_reason survive the roundtrip."""
        monkeypatch.setattr(
            "apps.skills.registry.dispatch",
            lambda skill_ctx: _skill_result(
                reply_text="переключаю на менеджера",
                should_handoff=True,
                handoff_reason="booking_unknown_master",
            ),
        )
        from apps.tenancy.context import tenant_scope

        with tenant_scope(SimpleNamespace(id=uuid.uuid4())):
            result = turn_reply_to_skill_result(orchestrate_turn(_ctx()))
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_unknown_master"
        assert result.reply_text == "переключаю на менеджера"

    def test_silence_parity(self, monkeypatch):
        """9. Operator-mute (should_send=False + meta) survives; the seam
        itself records nothing (see TestSideEffectBoundary)."""
        monkeypatch.setattr(
            "apps.skills.registry.dispatch",
            lambda skill_ctx: _skill_result(
                reply_text="", should_send=False, meta={"silenced_by": "human_handoff"}
            ),
        )
        from apps.tenancy.context import tenant_scope

        with tenant_scope(SimpleNamespace(id=uuid.uuid4())):
            result = turn_reply_to_skill_result(orchestrate_turn(_ctx()))
        assert result.should_send is False
        assert result.meta == {"silenced_by": "human_handoff"}

    def test_global_parity(self, monkeypatch):
        """6./10. Global adapter calls the concierge with identical kwargs
        under tenant=None."""
        captured = {}

        def fake_concierge(text, **kwargs):
            captured.update(kwargs)
            return DiscoveryReply(text="глобальный ответ", persisted=True)

        monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", fake_concierge)
        conversation = SimpleNamespace(id=uuid.uuid4())
        bot_user = SimpleNamespace(id=7)
        reply = orchestrate_turn(
            _ctx(
                surface=SURFACE_GLOBAL,
                tenant=None,
                conversation=conversation,
                bot_user=bot_user,
                text="хочу маникюр",
                user_message_id=uuid.uuid4(),
                memory_block="block",
                extra_system="extra",
                trace_id="tr",
            )
        )
        assert captured["conversation"] is conversation
        assert captured["bot_user"] is bot_user
        assert captured["memory_block"] == "block"
        assert captured["extra_system"] == "extra"
        assert captured["trace_id"] == "tr"
        assert reply.reply_text == "глобальный ответ"

    def test_telegram_call_site_uses_seam(self, monkeypatch):
        """7. Telegram handler routes the same brain through the seam with
        the same context mapping (surface=per_tenant, text, attachments)."""
        from apps.channels.telegram import handler as tg_handler

        seen = {}

        def fake_orchestrate(ctx):
            seen["ctx"] = ctx
            return TurnReply(reply_text="tg-ответ", action_type="echo")

        monkeypatch.setattr(tg_handler, "orchestrate_turn", fake_orchestrate)
        tenant = SimpleNamespace(id=uuid.uuid4())
        monkeypatch.setattr(
            tg_handler,
            "resolve_or_create_bot_user",
            lambda **kw: SimpleNamespace(id=1, tenant_id=tenant.id),
        )
        monkeypatch.setattr(
            tg_handler,
            "resolve_active_conversation",
            lambda bot_user: SimpleNamespace(id=uuid.uuid4(), pk=1, state="ACTIVE"),
        )
        monkeypatch.setattr(tg_handler, "record_message", lambda *a, **kw: None)
        monkeypatch.setattr(tg_handler.short_term, "append", lambda *a, **kw: None)
        monkeypatch.setattr(tg_handler.outbound, "send_message", lambda **kw: True)
        monkeypatch.setattr(tg_handler, "write_audit", lambda *a, **kw: None)
        monkeypatch.setattr(tg_handler, "emit", lambda *a, **kw: None)
        event = SimpleNamespace(
            text="привет",
            attachments=[],
            chat_id="c1",
            channel_user_id="u1",
            raw={},
        )
        monkeypatch.setattr(tg_handler, "parse_inbound", lambda payload: event)
        monkeypatch.setattr(tg_handler, "_extract_keyboard", lambda action_data: None)

        tg_handler.handle_inbound({"update_id": 1}, tenant=tenant)

        ctx = seen["ctx"]
        assert ctx.surface == SURFACE_PER_TENANT
        assert ctx.text == "привет"
        assert ctx.has_attachments is False


@pytest.mark.django_db
class TestSideEffectBoundary:
    """11-14. The seam itself performs NO persistence, outbound, memory or
    booking mutations — brains are faked, so anything in the DB afterwards
    would be the seam's own side effect."""

    def test_seam_has_no_side_effects(self, monkeypatch):
        from apps.conversations.models import Message
        from apps.handoff.models import AdminTask
        from apps.identity.models import MemoryEntry
        from apps.tenancy.context import tenant_scope
        from apps.tenancy.models import Tenant

        monkeypatch.setattr(
            "apps.skills.registry.dispatch",
            lambda skill_ctx: _skill_result(reply_text="x", should_handoff=True),
        )
        monkeypatch.setattr(
            "apps.orchestrator.concierge.generate_concierge_reply",
            lambda text, **kw: DiscoveryReply(text="y"),
        )
        tenant = Tenant.objects.create(slug="seam-t", name="Seam T")
        with tenant_scope(tenant):
            orchestrate_turn(_ctx())
        orchestrate_turn(_ctx(surface=SURFACE_GLOBAL, tenant=None))

        assert Message.objects.count() == 0  # no assistant/user persistence
        assert AdminTask.objects.count() == 0  # handoff creation is the CALLER's job
        assert MemoryEntry.objects.count() == 0  # no memory writes


class TestTenantSemantics:
    def test_per_tenant_without_scope_fails_closed(self, monkeypatch):
        """10./J. The per-tenant brain is tenant-scoped: no active tenant
        scope → fail-closed error, never a fake/default tenant."""
        monkeypatch.setattr("apps.skills.registry.dispatch", lambda skill_ctx: _skill_result())
        from apps.tenancy.context import tenant_scope

        with tenant_scope(None):
            with pytest.raises(RuntimeError, match="tenant_scope"):
                orchestrate_turn(_ctx())

    def test_seam_never_resolves_tenant(self, monkeypatch):
        """J. No Tenant query happens inside the seam."""
        import apps.tenancy.models as tenancy_models

        class _Boom:
            def get(self, *a, **kw):
                raise AssertionError("seam must not resolve tenants")

        monkeypatch.setattr(tenancy_models.Tenant, "objects", _Boom())
        monkeypatch.setattr(
            "apps.orchestrator.concierge.generate_concierge_reply",
            lambda text, **kw: DiscoveryReply(text="ok"),
        )
        reply = orchestrate_turn(_ctx(surface=SURFACE_GLOBAL, tenant=None))
        assert reply.reply_text == "ok"


class TestPipelineIsolation:
    def test_seam_does_not_call_pipeline_turn(self, monkeypatch):
        """15./L. OR-BOT-4: the full 19-step pipeline is never entered."""

        async def _boom(message):
            raise AssertionError("pipeline.turn must not be called from the seam")

        monkeypatch.setattr("apps.orchestrator.pipeline.turn", _boom)
        monkeypatch.setattr("apps.skills.registry.dispatch", lambda skill_ctx: _skill_result())
        monkeypatch.setattr(
            "apps.orchestrator.concierge.generate_concierge_reply",
            lambda text, **kw: DiscoveryReply(text="ok"),
        )
        from apps.tenancy.context import tenant_scope

        with tenant_scope(SimpleNamespace(id=uuid.uuid4())):
            orchestrate_turn(_ctx())
        orchestrate_turn(_ctx(surface=SURFACE_GLOBAL, tenant=None))

    def test_seam_source_has_no_pipeline_reference(self):
        """15b. Static guard: no pipeline import/call in the seam module."""
        src = inspect.getsource(turn_seam)
        assert "from apps.orchestrator.pipeline" not in src
        assert "import pipeline" not in src
        assert "pipeline.turn(" not in src
