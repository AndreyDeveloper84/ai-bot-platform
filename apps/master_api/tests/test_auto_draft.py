"""Tests for the M6 AI drafts auto-trigger backend.

Spec §M6 line 660 «— помощник готовит ответ —» — every inbound customer
message proactively generates an AiDraft (deferred from PR #535 / #540
manual «✨ Предложить ответ» flow).

Two layers under test:

* **Hook layer** — :func:`apps.conversations.services.record_message`
  enqueues :func:`apps.master_api.tasks.auto_generate_draft_for_inbound`
  via ``transaction.on_commit`` exactly when ``role == USER``.
* **Task layer** — the Celery task itself, with all five pre-LLM
  gates (feature flag, tier, master involvement, staleness, debounce).

We exercise the task body via direct ``__wrapped__``-style invocation
(running the function under test, not the broker) so we can assert on
return values; the broker is mocked at ``.delay`` for the hook layer.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import cache

from apps.audit.models import AuditLog
from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogMaster
from apps.conversations.models import AiDraft, Conversation, Message
from apps.conversations.services import record_message
from apps.identity.models import BotUser
from apps.llm.protocol import CompletionResult, LLMError
from apps.llm.router import reset_router_cache
from apps.master_api.services.ai_drafts import DraftActionError
from apps.master_api.tasks import auto_generate_draft_for_inbound
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


# ---------------------------------------------------------------------------
# Fixtures + isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_env(settings: Any) -> Any:
    """Reset shared state between tests — flag, router cache, locmem cache."""

    settings.LLM_PROVIDER = "openai"
    settings.SKILL_LLM_PROVIDER = {}
    settings.AI_DRAFTS_AUTO_TRIGGER_ENABLED = True
    reset_router_cache()
    cache.clear()
    yield
    cache.clear()
    reset_router_cache()


def _make_tenant(slug: str = "auto-draft-test") -> Tenant:
    return Tenant.objects.create(slug=slug, name=f"Salon {slug}", timezone="Europe/Moscow")


def _make_bot_user(tenant: Tenant, *, channel_user_id: str = "client-1") -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=channel_user_id,
        display_name="Клиент",
        client_name="Клиент",
        chat_id=channel_user_id,
        phone="+79161112233",
    )


def _make_master(tenant: Tenant, *, external_id: int = 1) -> CatalogMaster:
    now = datetime.now(tz=timezone.utc)
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=external_id,
        external_updated_at=now,
        name=f"Master {external_id}",
        specialization="маникюр",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        invited_at=now,
    )


def _make_conversation(
    tenant: Tenant,
    bot_user: BotUser,
    *,
    tier: str = Conversation.Tier.AI_CONTINUITY,
) -> Conversation:
    return Conversation.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        is_active=True,
        tier=tier,
    )


def _make_booking(
    tenant: Tenant,
    master: CatalogMaster,
    bot_user: BotUser,
) -> BookingRequest:
    return BookingRequest.all_tenants.create(
        tenant=tenant,
        master=master,
        bot_user=bot_user,
        service_name="маникюр",
        client_name=bot_user.client_name or bot_user.display_name,
        client_phone=bot_user.phone or "+79000000000",
        visit_at=datetime.now(tz=timezone.utc) + timedelta(days=1),
        duration_min=60,
        status=BookingRequest.Status.CONFIRMED,
    )


def _add_msg(
    tenant: Tenant,
    conv: Conversation,
    *,
    role: str,
    content: str = "x",
    created_at: datetime | None = None,
) -> Message:
    msg = Message.all_tenants.create(
        tenant=tenant,
        conversation=conv,
        role=role,
        content=content,
    )
    if created_at is not None:
        Message.all_tenants.filter(pk=msg.pk).update(created_at=created_at)
        msg.refresh_from_db()
    return msg


def _completion(text: str = "ок, записываю") -> CompletionResult:
    return CompletionResult(
        text=text,
        tool_calls=[],
        prompt_tokens=10,
        completion_tokens=5,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="stop",
    )


def _patch_complete(*, side_effect: Any = None, return_value: Any = None) -> Any:
    from apps.llm.providers.openai_provider import OpenAIProvider

    if side_effect is not None:

        async def _aside(*a: Any, **kw: Any) -> Any:
            if isinstance(side_effect, BaseException) or (
                isinstance(side_effect, type) and issubclass(side_effect, BaseException)
            ):
                raise side_effect
            if callable(side_effect):
                return await side_effect(*a, **kw)
            return side_effect

        return patch.object(OpenAIProvider, "complete", new=_aside)

    completion = return_value if return_value is not None else _completion()

    async def _areturn(self: Any, *a: Any, **kw: Any) -> CompletionResult:
        return completion

    return patch.object(OpenAIProvider, "complete", new=_areturn)


# ---------------------------------------------------------------------------
# Hook layer — record_message enqueues task on role=USER only
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestRecordMessageHook:
    """The on_commit hook in :func:`record_message` MUST enqueue exactly when
    the message role is USER. Outbound assistant / tool / system messages
    must NOT enqueue."""

    def test_inbound_customer_message_enqueues_auto_draft(self) -> None:
        tenant = _make_tenant()
        bot_user = _make_bot_user(tenant)
        conv = _make_conversation(tenant, bot_user)
        with patch("apps.master_api.tasks.auto_generate_draft_for_inbound.delay") as enqueue:
            with tenant_scope(tenant):
                msg = record_message(conv, role=Message.Role.USER, content="привет")
        assert enqueue.called
        kwargs = enqueue.call_args.kwargs
        assert kwargs["conversation_id"] == str(conv.id)
        assert kwargs["trigger_message_id"] == str(msg.id)
        assert kwargs["tenant_id"] == str(tenant.id)

    def test_outbound_assistant_message_does_not_enqueue(self) -> None:
        tenant = _make_tenant()
        bot_user = _make_bot_user(tenant)
        conv = _make_conversation(tenant, bot_user)
        with patch("apps.master_api.tasks.auto_generate_draft_for_inbound.delay") as enqueue:
            with tenant_scope(tenant):
                record_message(conv, role=Message.Role.ASSISTANT, content="привет")
        assert not enqueue.called

    def test_tool_and_system_messages_do_not_enqueue(self) -> None:
        tenant = _make_tenant()
        bot_user = _make_bot_user(tenant)
        conv = _make_conversation(tenant, bot_user)
        with patch("apps.master_api.tasks.auto_generate_draft_for_inbound.delay") as enqueue:
            with tenant_scope(tenant):
                record_message(conv, role=Message.Role.TOOL, content="tool-out")
                record_message(conv, role=Message.Role.SYSTEM, content="sys")
        assert not enqueue.called


# ---------------------------------------------------------------------------
# Task layer — pre-LLM gates
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTaskPreLLMGates:
    """Every gate before the LLM call must short-circuit cleanly without
    invoking the generate service."""

    def test_feature_flag_off_short_circuits(self, settings: Any) -> None:
        settings.AI_DRAFTS_AUTO_TRIGGER_ENABLED = False
        tenant = _make_tenant()
        bu = _make_bot_user(tenant)
        master = _make_master(tenant)
        conv = _make_conversation(tenant, bu)
        _make_booking(tenant, master, bu)
        msg = _add_msg(tenant, conv, role=Message.Role.USER)
        with patch("apps.master_api.services.ai_drafts.generate_draft_for_conversation") as gen:
            res = auto_generate_draft_for_inbound(
                conversation_id=str(conv.id),
                trigger_message_id=str(msg.id),
                tenant_id=str(tenant.id),
            )
        assert res == {"generated": False, "reason": "flag_off"}
        assert not gen.called

    def test_human_locked_conversation_skipped(self) -> None:
        tenant = _make_tenant()
        bu = _make_bot_user(tenant)
        master = _make_master(tenant)
        conv = _make_conversation(tenant, bu, tier=Conversation.Tier.HUMAN_LOCKED)
        _make_booking(tenant, master, bu)
        msg = _add_msg(tenant, conv, role=Message.Role.USER)
        with patch("apps.master_api.services.ai_drafts.generate_draft_for_conversation") as gen:
            res = auto_generate_draft_for_inbound(
                conversation_id=str(conv.id),
                trigger_message_id=str(msg.id),
                tenant_id=str(tenant.id),
            )
        assert res == {"generated": False, "reason": "human_locked"}
        assert not gen.called

    def test_no_master_linked_conversation_skipped(self) -> None:
        # Conversation exists, but no BookingRequest → no master involvement.
        tenant = _make_tenant()
        bu = _make_bot_user(tenant)
        conv = _make_conversation(tenant, bu)
        msg = _add_msg(tenant, conv, role=Message.Role.USER)
        with patch("apps.master_api.services.ai_drafts.generate_draft_for_conversation") as gen:
            res = auto_generate_draft_for_inbound(
                conversation_id=str(conv.id),
                trigger_message_id=str(msg.id),
                tenant_id=str(tenant.id),
            )
        assert res == {"generated": False, "reason": "no_master"}
        assert not gen.called

    def test_stale_trigger_message_skipped(self) -> None:
        tenant = _make_tenant()
        bu = _make_bot_user(tenant)
        master = _make_master(tenant)
        conv = _make_conversation(tenant, bu)
        _make_booking(tenant, master, bu)
        old = _add_msg(
            tenant,
            conv,
            role=Message.Role.USER,
            content="старый",
            created_at=datetime.now(tz=timezone.utc) - timedelta(minutes=2),
        )
        # Newer message → makes the task's trigger_message_id stale.
        _add_msg(
            tenant,
            conv,
            role=Message.Role.USER,
            content="новый",
        )
        with patch("apps.master_api.services.ai_drafts.generate_draft_for_conversation") as gen:
            res = auto_generate_draft_for_inbound(
                conversation_id=str(conv.id),
                trigger_message_id=str(old.id),
                tenant_id=str(tenant.id),
            )
        assert res == {"generated": False, "reason": "stale_trigger"}
        assert not gen.called

    def test_bad_uuid_arg_safely_no_ops(self) -> None:
        # Defence-in-depth — a bad caller arg shouldn't crash the worker.
        res = auto_generate_draft_for_inbound(
            conversation_id="not-a-uuid",
            trigger_message_id="not-a-uuid",
            tenant_id="not-a-uuid",
        )
        assert res == {"generated": False, "reason": "bad_uuid"}

    def test_missing_conversation_safely_no_ops(self) -> None:
        tenant = _make_tenant()
        import uuid

        res = auto_generate_draft_for_inbound(
            conversation_id=str(uuid.uuid4()),
            trigger_message_id=str(uuid.uuid4()),
            tenant_id=str(tenant.id),
        )
        assert res == {"generated": False, "reason": "conversation_missing"}


# ---------------------------------------------------------------------------
# Debounce
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDebounce:
    """Per-conversation SETNX with 15s TTL. Second customer message inside
    the window must not produce a second LLM call."""

    def test_per_conversation_debounce_within_15s(self) -> None:
        tenant = _make_tenant()
        bu = _make_bot_user(tenant)
        master = _make_master(tenant)
        conv = _make_conversation(tenant, bu)
        _make_booking(tenant, master, bu)
        msg1 = _add_msg(tenant, conv, role=Message.Role.USER, content="один")
        # Run first task — expect it to generate.
        with _patch_complete(return_value=_completion(text="ответ-1")):
            r1 = auto_generate_draft_for_inbound(
                conversation_id=str(conv.id),
                trigger_message_id=str(msg1.id),
                tenant_id=str(tenant.id),
            )
        assert r1["generated"] is True

        # Second customer message arrives within the debounce window —
        # the second task must skip with reason=debounced.
        msg2 = _add_msg(tenant, conv, role=Message.Role.USER, content="два")
        with _patch_complete(return_value=_completion(text="ответ-2")) as _:
            r2 = auto_generate_draft_for_inbound(
                conversation_id=str(conv.id),
                trigger_message_id=str(msg2.id),
                tenant_id=str(tenant.id),
            )
        assert r2 == {"generated": False, "reason": "debounced"}

    def test_per_conversation_debounce_releases_after_window(self) -> None:
        tenant = _make_tenant()
        bu = _make_bot_user(tenant)
        master = _make_master(tenant)
        conv = _make_conversation(tenant, bu)
        _make_booking(tenant, master, bu)
        msg1 = _add_msg(tenant, conv, role=Message.Role.USER, content="один")
        with _patch_complete(return_value=_completion(text="ответ-1")):
            auto_generate_draft_for_inbound(
                conversation_id=str(conv.id),
                trigger_message_id=str(msg1.id),
                tenant_id=str(tenant.id),
            )

        # Simulate TTL expiry — clear the debounce key manually (covers
        # what the 15s TTL would do in prod without slowing the test).
        cache.delete(f"auto_draft_conv:{conv.id}")
        msg2 = _add_msg(tenant, conv, role=Message.Role.USER, content="два")
        with _patch_complete(return_value=_completion(text="ответ-2")):
            r2 = auto_generate_draft_for_inbound(
                conversation_id=str(conv.id),
                trigger_message_id=str(msg2.id),
                tenant_id=str(tenant.id),
            )
        assert r2["generated"] is True


# ---------------------------------------------------------------------------
# Rate / cost integration
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRateAndCostHandling:
    """The auto-task shares the per-master quota with the manual path.
    A rate-limit or cost-cap hit MUST be swallowed (INFO log, no retry)
    so a hot master quota doesn't poison the Celery worker."""

    def test_rate_limit_hit_is_swallowed_not_retried(self) -> None:
        tenant = _make_tenant()
        bu = _make_bot_user(tenant)
        master = _make_master(tenant)
        conv = _make_conversation(tenant, bu)
        _make_booking(tenant, master, bu)
        msg = _add_msg(tenant, conv, role=Message.Role.USER)
        with patch(
            "apps.master_api.services.ai_drafts.generate_draft_for_conversation",
            side_effect=DraftActionError("rate_limit_exceeded", "too many", status=429),
        ):
            # No exception bubbles out — must return cleanly.
            res = auto_generate_draft_for_inbound(
                conversation_id=str(conv.id),
                trigger_message_id=str(msg.id),
                tenant_id=str(tenant.id),
            )
        assert res == {"generated": False, "reason": "rate_limit_exceeded"}

    def test_cost_cap_hit_swallowed_not_retried(self) -> None:
        tenant = _make_tenant()
        bu = _make_bot_user(tenant)
        master = _make_master(tenant)
        conv = _make_conversation(tenant, bu)
        _make_booking(tenant, master, bu)
        msg = _add_msg(tenant, conv, role=Message.Role.USER)
        with patch(
            "apps.master_api.services.ai_drafts.generate_draft_for_conversation",
            side_effect=DraftActionError("cost_cap_exceeded", "over budget", status=429),
        ):
            res = auto_generate_draft_for_inbound(
                conversation_id=str(conv.id),
                trigger_message_id=str(msg.id),
                tenant_id=str(tenant.id),
            )
        assert res == {"generated": False, "reason": "cost_cap_exceeded"}

    def test_llm_provider_error_re_raised_for_celery_retry(self) -> None:
        # The task converts DraftActionError(llm_unavailable) → LLMError so
        # Celery's autoretry_for=(LLMError,) takes over. We verify the
        # re-raise; Celery's retry loop itself is exercised by Celery's
        # own tests.
        tenant = _make_tenant()
        bu = _make_bot_user(tenant)
        master = _make_master(tenant)
        conv = _make_conversation(tenant, bu)
        _make_booking(tenant, master, bu)
        msg = _add_msg(tenant, conv, role=Message.Role.USER)
        with patch(
            "apps.master_api.services.ai_drafts.generate_draft_for_conversation",
            side_effect=DraftActionError("llm_unavailable", "provider down", status=503),
        ):
            with pytest.raises(LLMError):
                auto_generate_draft_for_inbound(
                    conversation_id=str(conv.id),
                    trigger_message_id=str(msg.id),
                    tenant_id=str(tenant.id),
                )

    def test_unknown_draft_error_slug_does_not_retry(self) -> None:
        tenant = _make_tenant()
        bu = _make_bot_user(tenant)
        master = _make_master(tenant)
        conv = _make_conversation(tenant, bu)
        _make_booking(tenant, master, bu)
        msg = _add_msg(tenant, conv, role=Message.Role.USER)
        with patch(
            "apps.master_api.services.ai_drafts.generate_draft_for_conversation",
            side_effect=DraftActionError("something_new", "future slug", status=400),
        ):
            res = auto_generate_draft_for_inbound(
                conversation_id=str(conv.id),
                trigger_message_id=str(msg.id),
                tenant_id=str(tenant.id),
            )
        assert res["generated"] is False
        assert "unknown_slug" in res["reason"]


# ---------------------------------------------------------------------------
# Idempotency with manual path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIdempotencyWithManual:
    """Auto + manual share the 60s idempotency window inside
    :func:`generate_draft_for_conversation`. A manual tap after an auto
    run within the window must return the SAME draft (no second LLM
    call)."""

    def test_auto_then_manual_within_60s_returns_same_draft(self) -> None:
        tenant = _make_tenant()
        bu = _make_bot_user(tenant)
        master = _make_master(tenant)
        conv = _make_conversation(tenant, bu)
        _make_booking(tenant, master, bu)
        msg = _add_msg(tenant, conv, role=Message.Role.USER, content="привет")

        call_counter = {"n": 0}

        async def counting_complete(*a: Any, **kw: Any) -> CompletionResult:
            call_counter["n"] += 1
            return _completion(text="ответ")

        from apps.llm.providers.openai_provider import OpenAIProvider

        with patch.object(OpenAIProvider, "complete", new=counting_complete):
            r_auto = auto_generate_draft_for_inbound(
                conversation_id=str(conv.id),
                trigger_message_id=str(msg.id),
                tenant_id=str(tenant.id),
            )
            assert r_auto["generated"] is True
            auto_draft_id = r_auto["draft_id"]

            # Now invoke the manual service directly — it should hit
            # the 60s idempotency window and return the SAME draft.
            from apps.master_api.services.ai_drafts import (
                generate_draft_for_conversation,
            )

            manual = generate_draft_for_conversation(
                conversation_id=conv.id,
                master=master,
                actor_bot_user=None,
                is_auto=False,
            )
        # Same draft id, single LLM call total.
        assert manual.draft_id == auto_draft_id
        assert call_counter["n"] == 1


# ---------------------------------------------------------------------------
# Audit + analytics — distinct slug
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAuditEvent:
    """Auto path writes :data:`MASTER_AI_DRAFT_AUTO_GENERATED`, not
    :data:`MASTER_AI_DRAFT_GENERATED`. Analytics depends on this split."""

    def test_auto_path_writes_auto_audit_event(self) -> None:
        tenant = _make_tenant()
        bu = _make_bot_user(tenant)
        master = _make_master(tenant)
        conv = _make_conversation(tenant, bu)
        _make_booking(tenant, master, bu)
        msg = _add_msg(tenant, conv, role=Message.Role.USER, content="привет")
        with _patch_complete(return_value=_completion(text="ок")):
            res = auto_generate_draft_for_inbound(
                conversation_id=str(conv.id),
                trigger_message_id=str(msg.id),
                tenant_id=str(tenant.id),
            )
        assert res["generated"] is True
        auto_logs = AuditLog.all_tenants.filter(action="master.ai_draft_auto_generated")
        manual_logs = AuditLog.all_tenants.filter(action="master.ai_draft_generated")
        assert auto_logs.count() == 1
        assert manual_logs.count() == 0
        # Adversarial check: the audit row MUST be attributed to the
        # tenant (not tenant=NULL).  The task wraps the generate call
        # in ``tenant_scope(master.tenant)`` so ``write_audit`` reads
        # the correct ContextVar.  A regression here would leave audit
        # rows orphaned — tenant-scoped audit search would miss them.
        first_log = auto_logs.first()
        assert first_log is not None
        assert first_log.tenant_id == tenant.id


# ---------------------------------------------------------------------------
# Concurrency — two inbound messages within window produce one draft
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConcurrencyTwoInbounds:
    """Rapid-fire customer typing scenario: two USER messages within the
    debounce window. Only ONE LLM call total; the second task's
    staleness check + debounce both fire."""

    def test_two_inbound_messages_within_window_produce_one_draft(
        self,
    ) -> None:
        tenant = _make_tenant()
        bu = _make_bot_user(tenant)
        master = _make_master(tenant)
        conv = _make_conversation(tenant, bu)
        _make_booking(tenant, master, bu)

        msg1 = _add_msg(tenant, conv, role=Message.Role.USER, content="один")
        # Second customer message arrives moments later.
        msg2 = _add_msg(tenant, conv, role=Message.Role.USER, content="два")

        call_counter = {"n": 0}

        async def counting_complete(*a: Any, **kw: Any) -> CompletionResult:
            call_counter["n"] += 1
            return _completion(text=f"call-{call_counter['n']}")

        from apps.llm.providers.openai_provider import OpenAIProvider

        with patch.object(OpenAIProvider, "complete", new=counting_complete):
            # Task for the FIRST message runs — but it's now stale
            # (msg2 is the latest USER message) → skipped before LLM.
            r1 = auto_generate_draft_for_inbound(
                conversation_id=str(conv.id),
                trigger_message_id=str(msg1.id),
                tenant_id=str(tenant.id),
            )
            # Task for the SECOND message runs — generates.
            r2 = auto_generate_draft_for_inbound(
                conversation_id=str(conv.id),
                trigger_message_id=str(msg2.id),
                tenant_id=str(tenant.id),
            )

        assert r1 == {"generated": False, "reason": "stale_trigger"}
        assert r2["generated"] is True
        # Exactly ONE paid LLM call — the second task. The first task
        # short-circuited at staleness BEFORE any LLM cost.
        assert call_counter["n"] == 1
        # One ACTIVE draft on the conversation.
        active = AiDraft.all_tenants.filter(conversation_id=conv.id, status=AiDraft.Status.ACTIVE)
        assert active.count() == 1
