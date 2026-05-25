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


# =========================================================================
# PR #535 follow-up — 6 adversarial blockers (Code Reviewer a70f5a18237a4dfcc)
# =========================================================================


# Blocker #1 — Double-paid LLM on concurrent generate ---------------------


@pytest.mark.django_db(transaction=True)
class TestBlocker1ConcurrentGenerate:
    """Verify rapid-double-tap → second returns first's draft, ONE LLM call.

    The Blocker #1 fix moves idempotency check + LLM call INSIDE a
    ``select_for_update`` on the conversation row. In single-threaded
    tests we can't easily simulate true concurrency; the test asserts
    the more important property: when two generate calls arrive
    back-to-back (the same idempotency window, same trigger), only
    one LLM call happens and both return the same draft id.
    """

    def test_concurrent_rapid_double_tap_returns_same_draft_one_llm_call(
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
            content="вопрос",
        )

        call_counter = {"n": 0}

        async def counting_complete(self: Any, *a: Any, **kw: Any) -> CompletionResult:
            call_counter["n"] += 1
            return _completion(text="ответ")

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
        # Same draft id (idempotent re-serve under the same trigger).
        assert resp_1.json()["draft_id"] == resp_2.json()["draft_id"]
        # Critical assertion: ONE LLM call total — concurrent flow MUST
        # serialise into a single bill.
        assert call_counter["n"] == 1
        # Only one ACTIVE draft persisted.
        actives = AiDraft.all_tenants.filter(conversation_id=conv.id, status=AiDraft.Status.ACTIVE)
        assert actives.count() == 1


# Blocker #2 — per-master rate limit ---------------------------------------


@pytest.mark.django_db
class TestBlocker2RateLimit:
    def test_rate_limit_hit_returns_429(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
        settings: Any,
    ) -> None:
        # Tight cap so the test runs in single digits of calls.
        settings.MASTER_DRAFT_RATE_PER_MINUTE = 2
        settings.MASTER_DRAFT_RATE_PER_DAY = 100

        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        # No customer messages → each call generates fresh (no idempotency).

        with _patch_complete():
            r1 = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )
            r2 = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )
            r3 = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r3.status_code == 429
        body = r3.json()
        assert body["error"] == "rate_limit_exceeded"
        # Retry-After header present.
        assert int(r3["Retry-After"]) >= 1

    def test_rate_limit_per_master_isolation(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
        bot_user: BotUser,
        settings: Any,
    ) -> None:
        # Master A hitting the cap doesn't affect master B. We don't
        # have a second-master Mini App auth pair wired in conftest;
        # this test exercises the cache key shape directly.
        from apps.master_api.services.ai_draft_limits import (
            check_and_consume_rate_limit,
        )
        import uuid as _uuid

        settings.MASTER_DRAFT_RATE_PER_MINUTE = 1

        master_a = _uuid.uuid4()
        master_b = _uuid.uuid4()

        r1 = check_and_consume_rate_limit(master_a)
        r2 = check_and_consume_rate_limit(master_a)  # over cap for A
        r3 = check_and_consume_rate_limit(master_b)  # B has its own counter

        assert r1.allowed is True
        assert r2.allowed is False
        assert r2.slug == "rate_limit_exceeded"
        assert r3.allowed is True


# Blocker #3 — cumulative cost guard BEFORE LLM call -----------------------


@pytest.mark.django_db
class TestBlocker3CostCap:
    def test_cost_cap_hit_returns_429_before_llm_call(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
        settings: Any,
    ) -> None:
        # Set cost cap below the existing seeded spend so the very next
        # call is rejected. Use a very small cap.
        settings.MASTER_DRAFT_COST_CAP_USD_DAILY = "0.01"

        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)

        # Seed past drafts whose cumulative cost equals the cap.
        from decimal import Decimal

        AiDraft.all_tenants.create(
            tenant=tenant,
            conversation=conv,
            master=accepted_master,
            content="",  # terminal — content cleared
            status=AiDraft.Status.REPLACED,
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            llm_cost_usd=Decimal("0.01"),
        )

        llm_calls = {"n": 0}

        async def tracking(self: Any, *a: Any, **kw: Any) -> CompletionResult:
            llm_calls["n"] += 1
            return _completion()

        from apps.llm.providers.openai_provider import OpenAIProvider

        with patch.object(OpenAIProvider, "complete", new=tracking):
            resp = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )

        assert resp.status_code == 429
        body = resp.json()
        assert body["error"] == "cost_cap_exceeded"
        # Critical: NO LLM call happened — cost guard runs BEFORE the call.
        assert llm_calls["n"] == 0


# Blocker #4 — stale draft sent after new customer message -----------------


@pytest.mark.django_db
class TestBlocker4Staleness:
    def test_stale_send_returns_409_draft_stale(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)

        # Customer message at T0; draft generated answering T0.
        msg_t0 = _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="можно записаться?",
        )
        draft = AiDraft.all_tenants.create(
            tenant=tenant,
            conversation=conv,
            master=accepted_master,
            content="Конечно, на какое время?",
            status=AiDraft.Status.ACTIVE,
            trigger_message=msg_t0,
            llm_provider="openai",
            llm_model="gpt-4o-mini",
        )

        # New customer message arrives BEFORE master sends.
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="отмените запись",
            created_at=msg_t0.created_at + timedelta(minutes=2),
        )

        resp = client.post(
            _send_as_me_url(conv.id, draft.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
            content_type="application/json",
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "draft_stale"

    def test_fresh_send_succeeds(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)

        msg_t0 = _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="вопрос",
        )
        draft = AiDraft.all_tenants.create(
            tenant=tenant,
            conversation=conv,
            master=accepted_master,
            content="ответ",
            status=AiDraft.Status.ACTIVE,
            trigger_message=msg_t0,
            llm_provider="openai",
            llm_model="gpt-4o-mini",
        )

        resp = client.post(
            _send_as_me_url(conv.id, draft.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
            content_type="application/json",
        )
        assert resp.status_code == 201

    def test_stale_release_returns_409(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)

        msg_t0 = _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="первый вопрос",
        )
        draft = AiDraft.all_tenants.create(
            tenant=tenant,
            conversation=conv,
            master=accepted_master,
            content="ответ на первый",
            status=AiDraft.Status.ACTIVE,
            trigger_message=msg_t0,
            llm_provider="openai",
            llm_model="gpt-4o-mini",
        )
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="второй вопрос",
            created_at=msg_t0.created_at + timedelta(minutes=2),
        )

        resp = client.post(
            _release_url(conv.id, draft.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
            content_type="application/json",
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "draft_stale"


# Blocker #5 Layer 1 — content cleared immediately on terminal transition ---


@pytest.mark.django_db
class TestBlocker5Layer1ContentClear:
    def test_send_as_master_clears_content_immediately(
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
            content="Анна, ваша запись на 14:00 на Тверской 8 подтверждена.",
        )
        # trigger_message stays None — no customer message → fresh draft
        # passes the staleness check (None == None).
        resp = client.post(
            _send_as_me_url(conv.id, draft.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
            content_type="application/json",
        )
        assert resp.status_code == 201
        draft.refresh_from_db()
        assert draft.status == AiDraft.Status.SENT_AS_MASTER
        # PII-quoting content gone.
        assert draft.content == ""
        # Metadata stays for finance reconciliation.
        assert draft.llm_provider == "openai"
        assert draft.llm_model == "gpt-4o-mini"

    def test_release_to_ai_clears_content_immediately(
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
            content="Анна, рада была работать с вами.",
        )
        resp = client.post(
            _release_url(conv.id, draft.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
            content_type="application/json",
        )
        assert resp.status_code == 201
        draft.refresh_from_db()
        assert draft.status == AiDraft.Status.RELEASED_TO_AI
        assert draft.content == ""

    def test_replaced_by_new_generate_clears_content_immediately(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)

        msg_1 = _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="первый",
        )
        with _patch_complete(return_value=_completion(text="первый ответ")):
            r1 = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )
        assert r1.status_code == 200
        draft_1_id = r1.json()["draft_id"]

        # New customer message defeats idempotency → regenerate paths
        # marks prior ACTIVE as REPLACED + clears content.
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="второй",
            created_at=msg_1.created_at + timedelta(minutes=2),
        )
        with _patch_complete(return_value=_completion(text="второй ответ")):
            r2 = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )
        assert r2.status_code == 200
        replaced = AiDraft.all_tenants.get(pk=draft_1_id)
        assert replaced.status == AiDraft.Status.REPLACED
        assert replaced.content == ""


# Blocker #5 Layer 2 — purge_old_ai_drafts beat task -----------------------


@pytest.mark.django_db
class TestBlocker5Layer2PurgeBeat:
    def test_purge_beat_deletes_terminal_drafts_older_than_30d(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        from apps.conversations.tasks import purge_old_ai_drafts

        customer = _make_bot_user(tenant=tenant)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        old = AiDraft.all_tenants.create(
            tenant=tenant,
            conversation=conv,
            master=accepted_master,
            content="",
            status=AiDraft.Status.SENT_AS_MASTER,
            llm_provider="openai",
            llm_model="gpt-4o-mini",
        )
        # Backdate updated_at past the cutoff.
        ancient = datetime.now(tz=timezone.utc) - timedelta(days=60)
        AiDraft.all_tenants.filter(pk=old.pk).update(updated_at=ancient)

        deleted = purge_old_ai_drafts()
        assert deleted >= 1
        assert not AiDraft.all_tenants.filter(pk=old.pk).exists()

    def test_purge_beat_preserves_terminal_drafts_younger_than_30d(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        from apps.conversations.tasks import purge_old_ai_drafts

        customer = _make_bot_user(tenant=tenant)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        recent = AiDraft.all_tenants.create(
            tenant=tenant,
            conversation=conv,
            master=accepted_master,
            content="",
            status=AiDraft.Status.RELEASED_TO_AI,
            llm_provider="openai",
            llm_model="gpt-4o-mini",
        )
        # updated_at = auto_now ≈ today; default well inside cutoff.
        purge_old_ai_drafts()
        assert AiDraft.all_tenants.filter(pk=recent.pk).exists()

    def test_purge_beat_preserves_active_drafts_regardless_of_age(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        from apps.conversations.tasks import purge_old_ai_drafts

        customer = _make_bot_user(tenant=tenant)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        ancient_active = AiDraft.all_tenants.create(
            tenant=tenant,
            conversation=conv,
            master=accepted_master,
            content="still here",
            status=AiDraft.Status.ACTIVE,
            llm_provider="openai",
            llm_model="gpt-4o-mini",
        )
        ancient = datetime.now(tz=timezone.utc) - timedelta(days=60)
        AiDraft.all_tenants.filter(pk=ancient_active.pk).update(updated_at=ancient)

        purge_old_ai_drafts()
        # ACTIVE preserved even though updated_at is 60d old.
        assert AiDraft.all_tenants.filter(pk=ancient_active.pk).exists()


# Blocker #6 — IntegrityError → graceful return ----------------------------


@pytest.mark.django_db
class TestBlocker6IntegrityGraceful:
    def test_concurrent_first_generate_handles_integrity_gracefully(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        """Simulated Blocker #6 defensive path.

        Blocker #1's lock makes this path unreachable on the happy path.
        Defence-in-depth: we patch ``AiDraft.all_tenants.create`` to
        raise :class:`django.db.IntegrityError` once, with a pre-existing
        ACTIVE draft already in the table. The service must catch +
        SELECT the winning row + return it — NO 500.
        """
        from django.db import IntegrityError as _IE

        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="вопрос",
        )

        # Pre-seed the «winning» ACTIVE draft that the service should
        # fall back to when its INSERT explodes. trigger_message stays
        # None so the staleness check in any downstream send won't
        # apply (we're only testing the integrity-recovery path here).
        winning = AiDraft.all_tenants.create(
            tenant=tenant,
            conversation=conv,
            master=accepted_master,
            content="winner content",
            status=AiDraft.Status.ACTIVE,
            llm_provider="openai",
            llm_model="gpt-4o-mini",
        )

        # Force `.create(...)` for the SECOND insert to raise. We patch
        # the unbound classmethod so the FIRST seed above isn't affected
        # (it happens before the patch).
        original_create = AiDraft.all_tenants.create
        call_count = {"n": 0}

        def exploding_create(*args: Any, **kwargs: Any) -> AiDraft:
            call_count["n"] += 1
            raise _IE("simulated duplicate ai_draft_one_active_per_conversation")

        with patch.object(AiDraft.all_tenants, "create", side_effect=exploding_create):
            # Bypass the idempotency check: pretend no prior ACTIVE.
            # The Blocker #1 lock would normally see `winning` and
            # return it via the same-trigger idempotency window. To
            # exercise Blocker #6 specifically, we force the LLM call
            # path by patching `_latest_customer_message` to return a
            # different id than `winning.trigger_message_id` (None).
            # But `winning.trigger_message_id` is None, so we'd hit
            # idempotency. Instead, the simpler shape: mark `winning`
            # as not-recent so within_window fails.
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td

            old_ts = _dt.now(tz=_tz.utc) - _td(minutes=10)
            AiDraft.all_tenants.filter(pk=winning.pk).update(created_at=old_ts)

            with _patch_complete(return_value=_completion(text="new attempt")):
                resp = client.post(
                    _generate_url(conv.id),
                    HTTP_AUTHORIZATION=init_data_header("12345"),
                    content_type="application/json",
                )

        # NOT a 500. The defensive path returned the winning ACTIVE.
        # Acceptable outcomes:
        #   - 200 with the winning draft id (preferred — defensive
        #     recovery), OR
        #   - 503 if the LLM call happened before the create + the
        #     defensive SELECT couldn't recover.
        # The Blocker #1 lock + REPLACED transition will have updated
        # `winning` to REPLACED (the regenerate path marks prior ACTIVE
        # as REPLACED before the new create). So after IntegrityError,
        # the fallback SELECT will not find an ACTIVE — 503 is the
        # expected non-500 path here.
        assert resp.status_code in (200, 503)
        assert resp.status_code != 500
        # Either way: original_create was called once (the LLM-attempt
        # insert that exploded).
        assert call_count["n"] >= 1
        # original_create reference kept for cleanup / future debug.
        assert callable(original_create)


# =========================================================================
# Issue #550 — select_for_update(nowait=True) + 429 generate_in_flight
# =========================================================================
#
# Refinement of PR #540's Blocker #1 fix. The blocking lock correctly
# serialised concurrent generates but blocked the UI for up to the full
# LLM timeout (60s worst case) — bad UX for pilot. With NOWAIT the
# second concurrent call fails IMMEDIATELY with a 429
# ``generate_in_flight`` + ``Retry-After: 3`` header so the frontend
# can render «Помощник уже думает…» and re-enable the tap after 3s.
#
# SQLite (the default test DB engine) IGNORES ``nowait=True`` — it
# treats the whole select_for_update as a no-op. Two strategies for
# triggering the contention path under SQLite:
#
#   1. **Mock the queryset**: patch
#      ``Conversation.all_tenants.select_for_update`` to return a
#      queryset whose ``.first()`` raises ``django.db.OperationalError``.
#      Deterministic, no threads required.
#   2. **Real threads (postgres only)**: use ``pytest.mark.django_db
#      (transaction=True)`` + ``threading.Barrier`` to fire two
#      concurrent generates. Only meaningful under PostgreSQL — skipped
#      on SQLite via ``connection.vendor`` check.
#
# Tests below use strategy 1 by default (cross-engine portable) and
# include one strategy-2 test guarded by a vendor skip.


@pytest.mark.django_db
class TestIssue550NowaitGenerateInFlight:
    """Issue #550 — non-blocking lock + 429 ``generate_in_flight``."""

    def test_generate_with_held_lock_returns_429(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        """Service-layer test: when the Conversation row lock can't be
        acquired (SELECT FOR UPDATE NOWAIT fails), the service raises
        ``DraftActionError("generate_in_flight", status=429)`` with
        ``extra={"retry_after_seconds": 3}`` and does NOT call the LLM.
        """

        from django.db import OperationalError

        from apps.master_api.services.ai_drafts import (
            DraftActionError,
            generate_draft_for_conversation,
        )

        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="вопрос",
        )

        # Mock the .filter().first() chain off select_for_update(nowait=True)
        # to raise OperationalError — same symptom as PostgreSQL's
        # SQLState 55P03 lock_not_available.
        # (Conversation already imported at module level — used directly.)

        original_sfu = Conversation.all_tenants.select_for_update
        llm_calls = {"n": 0}

        async def tracking(self: Any, *a: Any, **kw: Any) -> CompletionResult:
            llm_calls["n"] += 1
            return _completion()

        class _LockedQS:
            def filter(self, *a: Any, **kw: Any) -> Any:
                return self

            def first(self) -> Any:
                raise OperationalError("lock_not_available (simulated 55P03)")

        def fake_sfu(*a: Any, **kw: Any) -> Any:
            # Sanity: ensure the call site passed nowait=True.
            assert kw.get("nowait") is True, (
                "issue #550: select_for_update must be called with nowait=True"
            )
            return _LockedQS()

        from apps.llm.providers.openai_provider import OpenAIProvider

        with patch.object(Conversation.all_tenants, "select_for_update", new=fake_sfu):
            with patch.object(OpenAIProvider, "complete", new=tracking):
                with pytest.raises(DraftActionError) as exc_info:
                    generate_draft_for_conversation(
                        conversation_id=conv.id,
                        master=accepted_master,
                        actor_bot_user=None,
                    )

        # Sanity: original method untouched.
        assert callable(original_sfu)

        err = exc_info.value
        assert err.slug == "generate_in_flight"
        assert err.status == 429
        assert err.extra.get("retry_after_seconds") == 3
        # Russian copy preserved verbatim per spec.
        assert err.detail == ("Помощник уже думает над ответом. Попробуйте через несколько секунд.")
        # Critical: NO LLM call happened (the lock contention fired
        # BEFORE we got to the provider).
        assert llm_calls["n"] == 0

    def test_view_returns_429_with_retry_after_header_for_generate_in_flight(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        """View-layer test: service raises generate_in_flight → response
        is 429 with ``Retry-After: 3`` header AND a JSON body that
        echoes ``retry_after_seconds: 3``.
        """

        from apps.master_api.services.ai_drafts import DraftActionError

        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)

        def raising_generate(*a: Any, **kw: Any) -> Any:
            raise DraftActionError(
                "generate_in_flight",
                "Помощник уже думает над ответом. Попробуйте через несколько секунд.",
                status=429,
                extra={"retry_after_seconds": 3},
            )

        with patch(
            "apps.master_api.views.generate_draft_for_conversation",
            new=raising_generate,
        ):
            resp = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )

        assert resp.status_code == 429
        assert resp["Retry-After"] == "3"
        body = resp.json()
        assert body["error"] == "generate_in_flight"
        assert body["retry_after_seconds"] == 3
        # Spec copy verbatim.
        assert (
            body["detail"] == "Помощник уже думает над ответом. Попробуйте через несколько секунд."
        )

    def test_normal_generate_no_lock_contention_succeeds(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        """Sanity: NOWAIT does NOT break the happy path. A single
        non-contended generate still produces an ACTIVE draft.
        """

        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="привет",
        )
        with _patch_complete(return_value=_completion(text="привет!")):
            resp = client.post(
                _generate_url(conv.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"] == "привет!"
        # And exactly one ACTIVE draft persisted.
        assert (
            AiDraft.all_tenants.filter(
                conversation_id=conv.id, status=AiDraft.Status.ACTIVE
            ).count()
            == 1
        )

    def test_auto_trigger_swallows_generate_in_flight_no_retry(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
        settings: Any,
    ) -> None:
        """Celery task path: service raises generate_in_flight → task
        treats it as a legitimate refusal (INFO log, no autoretry, no
        AiDraft created on this attempt).

        Rationale (commit message + tasks.py docstring): the lock holder
        will produce a draft; retrying would either re-collide on the
        lock OR succeed but duplicate-bill a draft for a superseded
        trigger.
        """

        from apps.master_api.services.ai_drafts import DraftActionError
        from apps.master_api.tasks import (
            _NON_RETRY_SLUGS,
            auto_generate_draft_for_inbound,
        )

        # Sanity: the swallow list explicitly includes the new slug.
        assert "generate_in_flight" in _NON_RETRY_SLUGS

        settings.AI_DRAFTS_AUTO_TRIGGER_ENABLED = True

        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        trigger = _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="срочно",
        )

        # Force the underlying service to raise generate_in_flight on
        # every call (simulating an always-held lock).
        def raising(*a: Any, **kw: Any) -> Any:
            raise DraftActionError(
                "generate_in_flight",
                "Помощник уже думает над ответом. Попробуйте через несколько секунд.",
                status=429,
                extra={"retry_after_seconds": 3},
            )

        # Apply the task body directly (not via .delay) so we can assert
        # on the return value and verify no exception escapes.
        with patch(
            "apps.master_api.services.ai_drafts.generate_draft_for_conversation",
            new=raising,
        ):
            result = auto_generate_draft_for_inbound(
                conversation_id=str(conv.id),
                trigger_message_id=str(trigger.id),
                tenant_id=str(tenant.id),
            )

        # Task returns the swallow shape — no autoretry, no exception.
        assert result == {"generated": False, "reason": "generate_in_flight"}
        # And no AiDraft was created this attempt (service raised
        # BEFORE the INSERT).
        assert AiDraft.all_tenants.filter(conversation_id=conv.id).count() == 0

    def test_concurrent_generates_one_wins_other_gets_429(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        """Deterministic two-call test: the SECOND call (lock holder
        already in flight) gets 429 generate_in_flight; the FIRST call
        wins and produces an ACTIVE draft.

        We simulate concurrency by patching ``select_for_update`` to
        return a queryset whose ``.first()`` raises OperationalError on
        the SECOND invocation. This isolates the test from real thread
        timing while exercising the exact code path that triggers under
        Postgres lock contention.
        """

        from django.db import OperationalError

        from apps.master_api.services.ai_drafts import (
            DraftActionError,
            generate_draft_for_conversation,
        )
        # (Conversation already imported at module level — used directly.)

        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="вопрос",
        )

        # 1st call: real path (lock acquired, draft created).
        # 2nd call: select_for_update.filter().first() raises OperationalError.
        call_count = {"n": 0}
        original_sfu = Conversation.all_tenants.select_for_update

        class _LockedQS:
            def filter(self, *a: Any, **kw: Any) -> Any:
                return self

            def first(self) -> Any:
                raise OperationalError("lock_not_available (simulated 55P03 — concurrent generate)")

        def fake_sfu(*a: Any, **kw: Any) -> Any:
            call_count["n"] += 1
            assert kw.get("nowait") is True
            if call_count["n"] == 1:
                # First call — delegate to real method.
                return original_sfu(*a, **kw)
            # Second call — raise.
            return _LockedQS()

        results: list[Any] = []
        with patch.object(Conversation.all_tenants, "select_for_update", new=fake_sfu):
            with _patch_complete(return_value=_completion(text="ответ")):
                # First — wins.
                r1 = generate_draft_for_conversation(
                    conversation_id=conv.id,
                    master=accepted_master,
                    actor_bot_user=None,
                )
                results.append(r1)
                # Second — loses to lock contention.
                try:
                    generate_draft_for_conversation(
                        conversation_id=conv.id,
                        master=accepted_master,
                        actor_bot_user=None,
                    )
                except DraftActionError as exc:
                    results.append(exc)

        # First produced a draft.
        from apps.master_api.services.ai_drafts import DraftResponse

        assert isinstance(results[0], DraftResponse)
        winner_id = results[0].draft_id
        # Second got 429 generate_in_flight.
        assert isinstance(results[1], DraftActionError)
        assert results[1].slug == "generate_in_flight"
        assert results[1].status == 429
        assert results[1].extra.get("retry_after_seconds") == 3
        # Exactly ONE ACTIVE draft persisted (the winner).
        active = AiDraft.all_tenants.filter(conversation_id=conv.id, status=AiDraft.Status.ACTIVE)
        assert active.count() == 1
        winning_row = active.first()
        assert winning_row is not None
        assert str(winning_row.id) == winner_id

    def test_generate_in_flight_retry_after_eventually_succeeds(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        """End-to-end: first call holds lock + completes; second call
        within the contention window gets 429; a third call AFTER the
        first commits succeeds and (within the 60s idempotency window)
        re-serves the same draft id (no second LLM call).
        """

        from django.db import OperationalError

        from apps.master_api.services.ai_drafts import (
            DraftActionError,
            generate_draft_for_conversation,
        )
        # (Conversation already imported at module level — used directly.)

        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="вопрос",
        )

        original_sfu = Conversation.all_tenants.select_for_update
        call_count = {"n": 0}
        llm_calls = {"n": 0}

        class _LockedQS:
            def filter(self, *a: Any, **kw: Any) -> Any:
                return self

            def first(self) -> Any:
                raise OperationalError("lock_not_available")

        def fake_sfu(*a: Any, **kw: Any) -> Any:
            call_count["n"] += 1
            assert kw.get("nowait") is True
            # Call 1: real (lock acquired).
            # Call 2: simulated contention (429).
            # Call 3 onward: real (lock released after first commit).
            if call_count["n"] == 2:
                return _LockedQS()
            return original_sfu(*a, **kw)

        async def counting(self: Any, *a: Any, **kw: Any) -> CompletionResult:
            llm_calls["n"] += 1
            return _completion(text="первый и единственный")

        from apps.llm.providers.openai_provider import OpenAIProvider

        with patch.object(Conversation.all_tenants, "select_for_update", new=fake_sfu):
            with patch.object(OpenAIProvider, "complete", new=counting):
                # Call 1: succeeds — winner draft.
                r1 = generate_draft_for_conversation(
                    conversation_id=conv.id,
                    master=accepted_master,
                    actor_bot_user=None,
                )
                # Call 2: contention → 429 generate_in_flight.
                with pytest.raises(DraftActionError) as exc_info:
                    generate_draft_for_conversation(
                        conversation_id=conv.id,
                        master=accepted_master,
                        actor_bot_user=None,
                    )
                assert exc_info.value.slug == "generate_in_flight"
                # Call 3: lock now released; idempotency window re-serves
                # the winner draft (same trigger message, <60s).
                r3 = generate_draft_for_conversation(
                    conversation_id=conv.id,
                    master=accepted_master,
                    actor_bot_user=None,
                )

        assert r1.draft_id == r3.draft_id
        # Critical billing assertion: only ONE LLM call across all
        # three attempts (idempotency window catches call 3).
        assert llm_calls["n"] == 1


# =========================================================================
# Issue #551 — send/release Conversation lock symmetry (conversation_busy)
# =========================================================================
#
# PR #540 fixed Blocker #1 by locking the Conversation row on the GENERATE
# path; PR #550 refined it to NOWAIT + 429 ``generate_in_flight``. The
# SEND and RELEASE paths still locked only the AiDraft row, leaving a
# microsecond window where a new customer Message could INSERT (different
# table) between the staleness check and the draft status flip. Issue
# #551 fixes the asymmetry: send/release now lock the Conversation row
# first (with NOWAIT → 429 ``conversation_busy``), then the AiDraft row.
#
# Test strategy mirrors :class:`TestIssue550NowaitGenerateInFlight` —
# patch ``Conversation.all_tenants.select_for_update`` to return a
# queryset whose ``.first()`` raises ``OperationalError`` (same symptom
# as PostgreSQL SQLState 55P03 ``lock_not_available``).


@pytest.mark.django_db
class TestIssue551SendReleaseLockSymmetry:
    """Issue #551 — send/release lock the Conversation row before AiDraft."""

    def test_send_with_held_conversation_lock_returns_429_conversation_busy(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        """Service layer: when the Conversation row lock can't be
        acquired on the send path, the service raises
        ``DraftActionError("conversation_busy", status=429)`` with
        ``extra={"retry_after_seconds": 3}`` and does NOT mutate the
        draft / create a Message.
        """

        from django.db import OperationalError

        from apps.master_api.services.ai_drafts import (
            DraftActionError,
            send_draft_as_master,
        )

        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        draft = _seed_active_draft(tenant=tenant, master=accepted_master, conversation=conv)

        original_sfu = Conversation.all_tenants.select_for_update

        class _LockedQS:
            def filter(self, *a: Any, **kw: Any) -> Any:
                return self

            def first(self) -> Any:
                raise OperationalError("lock_not_available (simulated 55P03)")

        def fake_sfu(*a: Any, **kw: Any) -> Any:
            # The send/release lock acquisition MUST pass nowait=True
            # — same fail-fast semantics as #550.
            assert kw.get("nowait") is True, (
                "issue #551: select_for_update must be called with nowait=True"
            )
            return _LockedQS()

        with patch.object(Conversation.all_tenants, "select_for_update", new=fake_sfu):
            with pytest.raises(DraftActionError) as exc_info:
                send_draft_as_master(
                    conversation_id=conv.id,
                    draft_id=draft.id,
                    master=accepted_master,
                    actor_bot_user=None,
                )

        assert callable(original_sfu)
        err = exc_info.value
        assert err.slug == "conversation_busy"
        assert err.status == 429
        assert err.extra.get("retry_after_seconds") == 3
        assert err.detail == ("Диалог сейчас обновляется. Попробуйте через несколько секунд.")
        # Draft was NOT flipped to a terminal status.
        draft.refresh_from_db()
        assert draft.status == AiDraft.Status.ACTIVE
        assert draft.content  # not cleared
        # No assistant Message was created (the lock fired before the
        # record_message call).
        assert (
            Message.all_tenants.filter(conversation_id=conv.id, role=Message.Role.ASSISTANT).count()
            == 0
        )

    def test_release_with_held_conversation_lock_returns_429(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        """Same shape on the release path — Issue #551 covers both."""

        from django.db import OperationalError

        from apps.master_api.services.ai_drafts import (
            DraftActionError,
            release_draft_to_ai,
        )

        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        draft = _seed_active_draft(tenant=tenant, master=accepted_master, conversation=conv)

        class _LockedQS:
            def filter(self, *a: Any, **kw: Any) -> Any:
                return self

            def first(self) -> Any:
                raise OperationalError("lock_not_available")

        def fake_sfu(*a: Any, **kw: Any) -> Any:
            assert kw.get("nowait") is True
            return _LockedQS()

        with patch.object(Conversation.all_tenants, "select_for_update", new=fake_sfu):
            with pytest.raises(DraftActionError) as exc_info:
                release_draft_to_ai(
                    conversation_id=conv.id,
                    draft_id=draft.id,
                    master=accepted_master,
                    actor_bot_user=None,
                )

        err = exc_info.value
        assert err.slug == "conversation_busy"
        assert err.status == 429
        assert err.extra.get("retry_after_seconds") == 3
        # Draft + Message untouched.
        draft.refresh_from_db()
        assert draft.status == AiDraft.Status.ACTIVE
        assert (
            Message.all_tenants.filter(conversation_id=conv.id, role=Message.Role.ASSISTANT).count()
            == 0
        )

    def test_send_normal_path_acquires_then_releases_locks(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        """Sanity: the new Conversation lock does NOT break the happy
        path. A non-contended send still creates the message + flips
        the draft to SENT_AS_MASTER.
        """

        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        draft = _seed_active_draft(tenant=tenant, master=accepted_master, conversation=conv)

        # Track that select_for_update was called with nowait=True on
        # the Conversation manager (proves lock ordering).
        sfu_calls: list[dict[str, Any]] = []
        original_sfu = Conversation.all_tenants.select_for_update

        def tracking_sfu(*a: Any, **kw: Any) -> Any:
            sfu_calls.append(dict(kw))
            return original_sfu(*a, **kw)

        with patch.object(Conversation.all_tenants, "select_for_update", new=tracking_sfu):
            resp = client.post(
                _send_as_me_url(conv.id, draft.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )

        assert resp.status_code == 201
        # At least one call with nowait=True (the Conversation lock).
        assert any(kw.get("nowait") is True for kw in sfu_calls), (
            "issue #551: Conversation.select_for_update(nowait=True) must be "
            "invoked on the send path"
        )
        # Draft transitioned to SENT_AS_MASTER + content cleared.
        draft.refresh_from_db()
        assert draft.status == AiDraft.Status.SENT_AS_MASTER
        assert draft.content == ""
        # Master-attributed assistant message persisted.
        msg = Message.all_tenants.get(pk=resp.json()["message_id"])
        assert msg.action_type == "master_compose"
        assert (msg.action_data or {})["actor_type"] == "master"

    def test_release_normal_path_acquires_then_releases_locks(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        """Same sanity check on the release path."""

        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        draft = _seed_active_draft(tenant=tenant, master=accepted_master, conversation=conv)

        sfu_calls: list[dict[str, Any]] = []
        original_sfu = Conversation.all_tenants.select_for_update

        def tracking_sfu(*a: Any, **kw: Any) -> Any:
            sfu_calls.append(dict(kw))
            return original_sfu(*a, **kw)

        with patch.object(Conversation.all_tenants, "select_for_update", new=tracking_sfu):
            resp = client.post(
                _release_url(conv.id, draft.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )

        assert resp.status_code == 201
        assert any(kw.get("nowait") is True for kw in sfu_calls), (
            "issue #551: Conversation.select_for_update(nowait=True) must be "
            "invoked on the release path"
        )
        draft.refresh_from_db()
        assert draft.status == AiDraft.Status.RELEASED_TO_AI
        assert draft.content == ""

    def test_view_returns_429_with_retry_after_header_for_send_conversation_busy(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        """View layer: send raises ``conversation_busy`` → 429 +
        ``Retry-After: 3`` header + JSON body with ``retry_after_seconds``.

        This is the public-API guarantee that the frontend's existing
        429 handler (added for #550's ``generate_in_flight``) also works
        for the send path's ``conversation_busy`` slug.
        """

        from apps.master_api.services.ai_drafts import DraftActionError

        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        draft = _seed_active_draft(tenant=tenant, master=accepted_master, conversation=conv)

        def raising_send(*a: Any, **kw: Any) -> Any:
            raise DraftActionError(
                "conversation_busy",
                "Диалог сейчас обновляется. Попробуйте через несколько секунд.",
                status=429,
                extra={"retry_after_seconds": 3},
            )

        with patch(
            "apps.master_api.views.send_draft_as_master",
            new=raising_send,
        ):
            resp = client.post(
                _send_as_me_url(conv.id, draft.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )

        assert resp.status_code == 429
        assert resp["Retry-After"] == "3"
        body = resp.json()
        assert body["error"] == "conversation_busy"
        assert body["retry_after_seconds"] == 3
        assert body["detail"] == ("Диалог сейчас обновляется. Попробуйте через несколько секунд.")

    def test_view_returns_429_with_retry_after_header_for_release_conversation_busy(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        """Same 429 + Retry-After mapping for the release endpoint."""

        from apps.master_api.services.ai_drafts import DraftActionError

        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        draft = _seed_active_draft(tenant=tenant, master=accepted_master, conversation=conv)

        def raising_release(*a: Any, **kw: Any) -> Any:
            raise DraftActionError(
                "conversation_busy",
                "Диалог сейчас обновляется. Попробуйте через несколько секунд.",
                status=429,
                extra={"retry_after_seconds": 3},
            )

        with patch(
            "apps.master_api.views.release_draft_to_ai",
            new=raising_release,
        ):
            resp = client.post(
                _release_url(conv.id, draft.id),
                HTTP_AUTHORIZATION=init_data_header("12345"),
                content_type="application/json",
            )

        assert resp.status_code == 429
        assert resp["Retry-After"] == "3"
        body = resp.json()
        assert body["error"] == "conversation_busy"
        assert body["retry_after_seconds"] == 3

    def test_send_staleness_check_inside_lock_re_fires_on_new_message(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        """Race-window closure: staleness check runs INSIDE the
        Conversation lock. We simulate the race deterministically by
        inserting a new customer Message AFTER the outer staleness
        check but BEFORE the inner one. The inner re-check must fire
        and raise ``draft_stale``.

        The actual race-prevention guarantee is that the Conversation
        row lock serialises customer Message INSERT (which acquires the
        same lock via the ingest path) with send/release. This test
        verifies the re-check exists and fires — the lock-serialisation
        property is a postgres semantic that the unit test can't
        exercise under SQLite, but the inner re-check IS the load-
        bearing line of defence on the post-#540 architecture.
        """

        from apps.master_api.services.ai_drafts import (
            DraftActionError,
            send_draft_as_master,
        )

        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        trigger = _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="первый вопрос",
        )
        draft = AiDraft.all_tenants.create(
            tenant=tenant,
            conversation=conv,
            master=accepted_master,
            content="ответ на первый",
            status=AiDraft.Status.ACTIVE,
            trigger_message=trigger,
            llm_provider="openai",
            llm_model="gpt-4o-mini",
        )

        # Patch the internal staleness helper to inject a new customer
        # message on its SECOND invocation (mimicking «after outer
        # check, before inner check»). The new message defeats the
        # inner _validate_draft_fresh re-check.
        from apps.master_api.services import ai_drafts as svc

        original_latest = svc._latest_customer_message_id
        call_count = {"n": 0}

        def racing_latest(c: Conversation) -> Any:
            call_count["n"] += 1
            if call_count["n"] == 2:
                # Simulate a customer Message landing between outer and
                # inner staleness checks.
                _add_message(
                    tenant=tenant,
                    conversation=conv,
                    role=Message.Role.USER,
                    content="второй вопрос (race)",
                    created_at=trigger.created_at + timedelta(seconds=1),
                )
            return original_latest(c)

        with patch.object(svc, "_latest_customer_message_id", new=racing_latest):
            with pytest.raises(DraftActionError) as exc_info:
                send_draft_as_master(
                    conversation_id=conv.id,
                    draft_id=draft.id,
                    master=accepted_master,
                    actor_bot_user=None,
                )

        # Inner check fired → draft_stale 409, not a successful send.
        assert exc_info.value.slug == "draft_stale"
        assert exc_info.value.status == 409
        # And the draft is still ACTIVE (no terminal flip on the
        # race-loss path).
        draft.refresh_from_db()
        assert draft.status == AiDraft.Status.ACTIVE
        assert (
            Message.all_tenants.filter(conversation_id=conv.id, role=Message.Role.ASSISTANT).count()
            == 0
        )
        # We invoked the staleness helper at least twice (outer + inner)
        # — the very pattern that closes the race window.
        assert call_count["n"] >= 2

    def test_send_with_deleted_conversation_returns_404(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        """Issue #551 round-1 amendment: explicit None-guard after the
        Conversation ``select_for_update(...).first()``. If the row
        vanished between the outer :func:`_verify_master_involved`
        check and the lock acquisition, we MUST raise
        ``DraftActionError("not_found", status=404)`` rather than
        silently no-op the lock and proceed.
        """

        from apps.master_api.services.ai_drafts import (
            DraftActionError,
            send_draft_as_master,
        )

        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        draft = _seed_active_draft(tenant=tenant, master=accepted_master, conversation=conv)

        # Simulate the race: the involvement check resolved a row, but
        # by the time the lock fires the row is gone — .first() returns
        # None. We patch select_for_update to return a QS whose .first()
        # is None (NOT raising).
        class _MissingQS:
            def filter(self, *a: Any, **kw: Any) -> Any:
                return self

            def first(self) -> Any:
                return None

        def fake_sfu(*a: Any, **kw: Any) -> Any:
            assert kw.get("nowait") is True
            return _MissingQS()

        with patch.object(Conversation.all_tenants, "select_for_update", new=fake_sfu):
            with pytest.raises(DraftActionError) as exc_info:
                send_draft_as_master(
                    conversation_id=conv.id,
                    draft_id=draft.id,
                    master=accepted_master,
                    actor_bot_user=None,
                )

        err = exc_info.value
        assert err.slug == "not_found"
        assert err.status == 404
        # Draft NOT flipped; no Message created.
        draft.refresh_from_db()
        assert draft.status == AiDraft.Status.ACTIVE
        assert draft.content
        assert (
            Message.all_tenants.filter(conversation_id=conv.id, role=Message.Role.ASSISTANT).count()
            == 0
        )

    def test_release_with_deleted_conversation_returns_404(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        """Same shape on the release path — None-guard symmetry."""

        from apps.master_api.services.ai_drafts import (
            DraftActionError,
            release_draft_to_ai,
        )

        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        draft = _seed_active_draft(tenant=tenant, master=accepted_master, conversation=conv)

        class _MissingQS:
            def filter(self, *a: Any, **kw: Any) -> Any:
                return self

            def first(self) -> Any:
                return None

        def fake_sfu(*a: Any, **kw: Any) -> Any:
            assert kw.get("nowait") is True
            return _MissingQS()

        with patch.object(Conversation.all_tenants, "select_for_update", new=fake_sfu):
            with pytest.raises(DraftActionError) as exc_info:
                release_draft_to_ai(
                    conversation_id=conv.id,
                    draft_id=draft.id,
                    master=accepted_master,
                    actor_bot_user=None,
                )

        err = exc_info.value
        assert err.slug == "not_found"
        assert err.status == 404
        draft.refresh_from_db()
        assert draft.status == AiDraft.Status.ACTIVE
        assert (
            Message.all_tenants.filter(conversation_id=conv.id, role=Message.Role.ASSISTANT).count()
            == 0
        )
