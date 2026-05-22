"""Tests for the M6 AI drafts endpoints (Bundle B / item 4 backend).

Covers the three new endpoints under ``/api/v1/master/conversations/<id>/drafts/``:

* ``POST .../generate``
* ``POST .../<draft_id>/send-as-me``
* ``POST .../<draft_id>/release-to-ai``

Plus the GET integration in
:func:`apps.master_api.services.conversation_detail.get_conversation_detail`.

The LLM provider is patched at the ``OpenAIProvider.complete`` boundary
— the same pattern used by ``apps/skills/faq/tests/test_skill.py``. We
do NOT exercise real OpenAI / Anthropic SDKs in unit tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogMaster
from apps.conversations.models import AiDraft, Conversation, Message
from apps.events.models import Event
from apps.identity.models import BotUser
from apps.llm.protocol import CompletionResult, LLMError, LLMProviderUnavailable
from apps.llm.router import reset_router_cache
from apps.master_api.tests.conftest import init_data_header
from apps.tenancy.models import Tenant

MSK = ZoneInfo("Europe/Moscow")


# --- isolated env (LLM + router + cache) ----------------------------------


@pytest.fixture(autouse=True)
def _isolated_env(settings: Any) -> Any:
    """Pin the LLM provider + reset router cache between tests.

    Without this, a previously-instantiated provider (with its
    constructor-time API key check) would leak across tests. The
    router cache is per-process; reset both ends so the mocks
    intercept reliably.
    """

    settings.LLM_PROVIDER = "openai"
    settings.SKILL_LLM_PROVIDER = {}
    reset_router_cache()
    cache.clear()
    yield
    cache.clear()
    reset_router_cache()


# --- helpers (mirror those in test_conversation_detail.py) ----------------


def _utc(dt_local: datetime) -> datetime:
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=MSK)
    return dt_local.astimezone(timezone.utc)


def _make_bot_user(
    *,
    tenant: Tenant,
    name: str = "Ксения Леонова",
    channel_user_id: str = "client-1",
    phone: str = "+79161112233",
) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=channel_user_id,
        display_name=name,
        client_name=name,
        chat_id=channel_user_id,
        phone=phone,
    )


def _make_conversation(
    *,
    tenant: Tenant,
    bot_user: BotUser,
    tier: str = Conversation.Tier.AI_CONTINUITY,
) -> Conversation:
    return Conversation.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        is_active=True,
        tier=tier,
    )


def _add_message(
    *,
    tenant: Tenant,
    conversation: Conversation,
    role: str,
    content: str,
    created_at: datetime | None = None,
) -> Message:
    msg = Message.all_tenants.create(
        tenant=tenant,
        conversation=conversation,
        role=role,
        content=content,
    )
    if created_at is not None:
        Message.all_tenants.filter(pk=msg.pk).update(created_at=created_at)
        msg.refresh_from_db()
    return msg


def _make_booking(
    *,
    tenant: Tenant,
    master: CatalogMaster,
    bot_user: BotUser,
    status: str = BookingRequest.Status.CONFIRMED,
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
        status=status,
    )


def _detail_url(conversation_id: Any) -> str:
    return reverse(
        "master_api:conversation_detail",
        kwargs={"conversation_id": str(conversation_id)},
    )


def _generate_url(conversation_id: Any) -> str:
    return reverse(
        "master_api:conversation_draft_generate",
        kwargs={"conversation_id": str(conversation_id)},
    )


def _send_as_me_url(conversation_id: Any, draft_id: Any) -> str:
    return reverse(
        "master_api:conversation_draft_send_as_me",
        kwargs={"conversation_id": str(conversation_id), "draft_id": str(draft_id)},
    )


def _release_url(conversation_id: Any, draft_id: Any) -> str:
    return reverse(
        "master_api:conversation_draft_release_to_ai",
        kwargs={"conversation_id": str(conversation_id), "draft_id": str(draft_id)},
    )


# --- LLM mocking helpers --------------------------------------------------


def _completion(
    *,
    text: str = "Привет! Готовы записать на следующую неделю.",
    prompt_tokens: int = 50,
    completion_tokens: int = 30,
    model: str = "gpt-4o-mini",
) -> CompletionResult:
    return CompletionResult(
        text=text,
        tool_calls=[],
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        model=model,
        provider="openai",
        finish_reason="stop",
    )


def _patch_complete(*, side_effect: Any = None, return_value: Any = None) -> Any:
    """Patch ``OpenAIProvider.complete`` with the given async behaviour.

    Default: returns a fixed ``_completion()`` for every call so a
    test that doesn't care about the LLM body still proceeds.
    """

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


# =========================================================================
# generate happy paths
# =========================================================================


@pytest.mark.django_db
class TestGenerateHappyPaths:
    def test_generate_creates_active_draft(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="Можно записаться на следующую неделю?",
        )
        with _patch_complete():
            resp = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"] == "Привет! Готовы записать на следующую неделю."
        assert body["llm_provider"] == "openai"
        # One ACTIVE draft persisted.
        active = AiDraft.all_tenants.filter(conversation_id=conv.id, status=AiDraft.Status.ACTIVE)
        assert active.count() == 1
        draft = active.first()
        assert draft is not None
        assert draft.master_id == accepted_master.id
        assert draft.tenant_id == tenant.id

    def test_generate_replaces_previous_active_draft(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)

        # First generation — first customer message.
        msg_1 = _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="первый вопрос",
        )
        with _patch_complete(return_value=_completion(text="первый ответ")):
            resp_1 = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )
        assert resp_1.status_code == 200
        draft_1_id = resp_1.json()["draft_id"]

        # New customer message arrives — defeats idempotency.
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="второй вопрос",
            created_at=msg_1.created_at + timedelta(minutes=5),
        )
        with _patch_complete(return_value=_completion(text="второй ответ")):
            resp_2 = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )
        assert resp_2.status_code == 200
        draft_2_id = resp_2.json()["draft_id"]
        assert draft_1_id != draft_2_id

        # Previous draft must now be REPLACED.
        old = AiDraft.all_tenants.get(pk=draft_1_id)
        assert old.status == AiDraft.Status.REPLACED
        # And the new one is the only ACTIVE.
        active_ids = list(
            AiDraft.all_tenants.filter(
                conversation_id=conv.id, status=AiDraft.Status.ACTIVE
            ).values_list("id", flat=True)
        )
        assert [str(i) for i in active_ids] == [draft_2_id]

    def test_generate_idempotency_within_60s(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        # Same trigger message + within window → return existing draft,
        # do NOT invoke the LLM again.
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="вопрос",
        )

        call_counter = {"n": 0}

        async def counting_complete(self: Any, *a: Any, **kw: Any) -> CompletionResult:
            call_counter["n"] += 1
            return _completion(text="первая версия")

        from apps.llm.providers.openai_provider import OpenAIProvider

        with patch.object(OpenAIProvider, "complete", new=counting_complete):
            resp_1 = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )
            resp_2 = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )
        assert resp_1.status_code == 200
        assert resp_2.status_code == 200
        assert resp_1.json()["draft_id"] == resp_2.json()["draft_id"]
        assert call_counter["n"] == 1

    def test_generate_uses_last_20_messages_in_context(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        # Seed 25 messages — only the latest 20 should land in the prompt.
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        base = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        for i in range(25):
            role = Message.Role.USER if i % 2 == 0 else Message.Role.ASSISTANT
            _add_message(
                tenant=tenant,
                conversation=conv,
                role=role,
                content=f"msg-{i}",
                created_at=base + timedelta(minutes=i),
            )

        captured: dict[str, Any] = {}

        async def capturing(
            self: Any,
            messages: list[dict[str, Any]],
            **kw: Any,
        ) -> CompletionResult:
            captured["messages"] = messages
            return _completion()

        from apps.llm.providers.openai_provider import OpenAIProvider

        with patch.object(OpenAIProvider, "complete", new=capturing):
            resp = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )
        assert resp.status_code == 200
        # 1 system + 20 history rows.
        msgs = captured["messages"]
        assert msgs[0]["role"] == "system"
        history = msgs[1:]
        assert len(history) == 20
        # First in window is msg-5 (25 - 20 = 5).
        assert history[0]["content"] == "msg-5"
        assert history[-1]["content"] == "msg-24"

    def test_generate_records_cost_from_provider(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="x",
        )
        with _patch_complete(
            return_value=_completion(
                prompt_tokens=1000,
                completion_tokens=500,
                model="gpt-4o-mini",
            ),
        ):
            resp = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )
        assert resp.status_code == 200
        draft = AiDraft.all_tenants.get(pk=resp.json()["draft_id"])
        # gpt-4o-mini = $0.00015/1k input + $0.00060/1k output.
        # 1000 input + 500 output → 0.00015 + 0.00030 = 0.00045.
        assert float(draft.llm_cost_usd) == pytest.approx(0.00045, rel=1e-3)
        assert draft.llm_model == "gpt-4o-mini"
        assert draft.llm_provider == "openai"


# =========================================================================
# generate failure paths
# =========================================================================


@pytest.mark.django_db
class TestGenerateFailures:
    def test_generate_llm_unavailable_returns_503(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="x",
        )

        async def raising(self: Any, *a: Any, **kw: Any) -> CompletionResult:
            raise LLMError("transport blew up")

        from apps.llm.providers.openai_provider import OpenAIProvider

        with patch.object(OpenAIProvider, "complete", new=raising):
            resp = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )
        assert resp.status_code == 503
        assert resp.json()["error"] == "llm_unavailable"
        # No draft persisted.
        assert AiDraft.all_tenants.filter(conversation_id=conv.id).count() == 0

    def test_generate_provider_unavailable_returns_503(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        # LLMProviderUnavailable raised at the router level → 503.
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)

        from apps.llm.router import LLMRouter

        def raising(self: Any, *a: Any, **kw: Any) -> Any:
            raise LLMProviderUnavailable("missing key")

        with patch.object(LLMRouter, "get_provider", new=raising):
            resp = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )
        assert resp.status_code == 503
        assert resp.json()["error"] == "llm_unavailable"

    def test_generate_conversation_not_involving_master_returns_404(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        # Stranger customer with NO booking link to the master.
        stranger = _make_bot_user(tenant=tenant, channel_user_id="stranger")
        conv = _make_conversation(tenant=tenant, bot_user=stranger)
        with _patch_complete():
            resp = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_generate_cross_tenant_returns_404(
        self,
        client: Client,
        tenant: Tenant,
        other_tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        foreign_user = BotUser.all_tenants.create(
            tenant=other_tenant,
            channel="max",
            channel_user_id="foreign",
            display_name="Чужой",
            client_name="Чужой",
            chat_id="foreign",
        )
        conv = Conversation.all_tenants.create(
            tenant=other_tenant, bot_user=foreign_user, is_active=True
        )
        with _patch_complete():
            resp = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )
        assert resp.status_code == 404

    def test_generate_on_human_locked_conversation_returns_400(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(
            tenant=tenant, bot_user=customer, tier=Conversation.Tier.HUMAN_LOCKED
        )
        with _patch_complete():
            resp = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )
        assert resp.status_code == 400
        assert resp.json()["error"] == "conversation_locked"

    def test_generate_empty_completion_returns_503(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        # Provider returned empty text — we refuse to persist an empty
        # draft and surface 503 so the master can retry.
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        with _patch_complete(return_value=_completion(text="   ")):
            resp = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )
        assert resp.status_code == 503
        assert resp.json()["error"] == "llm_unavailable"


# =========================================================================
# send-as-me
# =========================================================================


def _seed_active_draft(
    *,
    tenant: Tenant,
    master: CatalogMaster,
    conversation: Conversation,
    content: str = "Помощник: спасибо!",
) -> AiDraft:
    return AiDraft.all_tenants.create(
        tenant=tenant,
        conversation=conversation,
        master=master,
        content=content,
        status=AiDraft.Status.ACTIVE,
        llm_provider="openai",
        llm_model="gpt-4o-mini",
    )


@pytest.mark.django_db
class TestSendAsMe:
    def test_send_as_me_creates_master_attributed_message(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        draft = _seed_active_draft(tenant=tenant, master=accepted_master, conversation=conv)
        resp = client.post(
            _send_as_me_url(conv.id, draft.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
            content_type="application/json",
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["composed_by_master"] is True
        assert body["was_edited"] is False
        msg = Message.all_tenants.get(pk=body["message_id"])
        assert msg.role == Message.Role.ASSISTANT
        assert msg.action_type == "master_compose"
        data = msg.action_data or {}
        assert data["actor_type"] == "master"
        assert data["composed_by"] == str(accepted_master.id)
        assert data["from_draft_id"] == str(draft.id)
        assert data["was_edited"] is False

    def test_send_as_me_with_override_uses_override_text(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        draft = _seed_active_draft(
            tenant=tenant,
            master=accepted_master,
            conversation=conv,
            content="LLM text",
        )
        edited = "Спасибо, рада была работать! Ждём вас снова."
        resp = client.post(
            _send_as_me_url(conv.id, draft.id),
            data={"override_content": edited},
            HTTP_AUTHORIZATION=init_data_header("12345"),
            content_type="application/json",
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["content"] == edited
        assert body["was_edited"] is True
        msg = Message.all_tenants.get(pk=body["message_id"])
        assert msg.content == edited
        assert (msg.action_data or {})["was_edited"] is True

    def test_send_as_me_marks_draft_sent_as_master(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        draft = _seed_active_draft(tenant=tenant, master=accepted_master, conversation=conv)
        resp = client.post(
            _send_as_me_url(conv.id, draft.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
            content_type="application/json",
        )
        assert resp.status_code == 201
        draft.refresh_from_db()
        assert draft.status == AiDraft.Status.SENT_AS_MASTER

    def test_send_as_me_already_acted_returns_400(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        draft = AiDraft.all_tenants.create(
            tenant=tenant,
            conversation=conv,
            master=accepted_master,
            content="x",
            status=AiDraft.Status.REPLACED,
        )
        resp = client.post(
            _send_as_me_url(conv.id, draft.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "draft_already_acted"

    def test_send_as_me_override_too_long_returns_400(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        draft = _seed_active_draft(tenant=tenant, master=accepted_master, conversation=conv)
        resp = client.post(
            _send_as_me_url(conv.id, draft.id),
            data={"override_content": "x" * 3000},
            HTTP_AUTHORIZATION=init_data_header("12345"),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"

    def test_send_as_me_on_locked_conversation_returns_403(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(
            tenant=tenant, bot_user=customer, tier=Conversation.Tier.HUMAN_LOCKED
        )
        draft = _seed_active_draft(tenant=tenant, master=accepted_master, conversation=conv)
        resp = client.post(
            _send_as_me_url(conv.id, draft.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
            content_type="application/json",
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "tier_locked"


# =========================================================================
# release-to-ai
# =========================================================================


@pytest.mark.django_db
class TestReleaseToAi:
    def test_release_creates_plain_assistant_message(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        draft = _seed_active_draft(
            tenant=tenant,
            master=accepted_master,
            conversation=conv,
            content="Спасибо, мы вас ждём.",
        )
        resp = client.post(
            _release_url(conv.id, draft.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
            content_type="application/json",
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["composed_by_master"] is False
        assert body["was_edited"] is False
        msg = Message.all_tenants.get(pk=body["message_id"])
        assert msg.role == Message.Role.ASSISTANT
        assert msg.action_type == "ai_draft_released"
        data = msg.action_data or {}
        # No master attribution metadata in action_data — the customer
        # render is indistinguishable from a fully-auto reply.
        assert data.get("actor_type") is None
        assert data.get("composed_by") is None
        # But the audit trail records who released it.
        assert data["released_by_master"] == str(accepted_master.id)

    def test_release_marks_draft_released(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        draft = _seed_active_draft(tenant=tenant, master=accepted_master, conversation=conv)
        resp = client.post(
            _release_url(conv.id, draft.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
            content_type="application/json",
        )
        assert resp.status_code == 201
        draft.refresh_from_db()
        assert draft.status == AiDraft.Status.RELEASED_TO_AI

    def test_release_already_acted_returns_400(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        draft = AiDraft.all_tenants.create(
            tenant=tenant,
            conversation=conv,
            master=accepted_master,
            content="x",
            status=AiDraft.Status.SENT_AS_MASTER,
        )
        resp = client.post(
            _release_url(conv.id, draft.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "draft_already_acted"


# =========================================================================
# GET detail integration
# =========================================================================


@pytest.mark.django_db
class TestDetailGetIntegration:
    def test_detail_get_returns_active_draft(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        draft = _seed_active_draft(
            tenant=tenant,
            master=accepted_master,
            conversation=conv,
            content="живой драфт",
        )
        resp = client.get(
            _detail_url(conv.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ai_draft"]["draft_id"] == str(draft.id)
        assert body["ai_draft"]["content"] == "живой драфт"
        assert body["ai_draft"]["created_at"] is not None

    def test_detail_get_returns_null_when_no_active(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        resp = client.get(
            _detail_url(conv.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.json()["ai_draft"] == {
            "draft_id": None,
            "content": None,
            "created_at": None,
        }

    def test_detail_get_does_not_return_replaced_draft(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        AiDraft.all_tenants.create(
            tenant=tenant,
            conversation=conv,
            master=accepted_master,
            content="старая",
            status=AiDraft.Status.REPLACED,
        )
        resp = client.get(
            _detail_url(conv.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.json()["ai_draft"]["draft_id"] is None


# =========================================================================
# audit + events
# =========================================================================


@pytest.mark.django_db
class TestAuditAndEvents:
    def test_generate_writes_audit_row(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="x",
        )
        with _patch_complete():
            resp = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )
        assert resp.status_code == 200
        rows = AuditLog.all_tenants.filter(action="master.ai_draft_generated")
        assert rows.count() == 1
        ev = Event.objects.filter(event_name="master.ai_draft_generated")
        assert ev.exists()

    def test_send_writes_audit_row(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        draft = _seed_active_draft(tenant=tenant, master=accepted_master, conversation=conv)
        resp = client.post(
            _send_as_me_url(conv.id, draft.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
            content_type="application/json",
        )
        assert resp.status_code == 201
        rows = AuditLog.all_tenants.filter(action="master.draft_sent_as_self")
        assert rows.count() == 1

    def test_release_writes_audit_row(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        draft = _seed_active_draft(tenant=tenant, master=accepted_master, conversation=conv)
        resp = client.post(
            _release_url(conv.id, draft.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
            content_type="application/json",
        )
        assert resp.status_code == 201
        rows = AuditLog.all_tenants.filter(action="master.draft_released_to_ai")
        assert rows.count() == 1
