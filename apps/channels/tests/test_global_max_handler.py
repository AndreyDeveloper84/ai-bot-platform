"""Tests for the tenant-less global MAX path (#1019 + #1026 / EPIC #1014).

Covers the GlobalMaxHandler (requires_tenant=False) + handle_global_max_event,
and the headline acceptance invariant: any commercial-model read attempted on
the tenant-less discovery path raises CrossTenantError in strict mode.

The full discovery turn (persist → reply → re-read) lives in
``test_tenant_less_discovery_e2e.py``; here we keep the identity / no-refuse /
invariant guards, mocking the reply + memory + outbound so the handler runs.
"""

from __future__ import annotations

import json
import uuid

import pytest

from apps.channels.handlers import GlobalMaxHandler
from apps.channels.max import handler as max_handler
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.identity.models import BotUser
from apps.orchestrator.memory import short_term
from apps.tenancy.context import current_tenant, tenant_scope
from apps.tenancy.exceptions import CrossTenantError

pytestmark = pytest.mark.django_db


def _payload(*, text: str = "Привет", user_id: int = 7777, chat_id: int = 8888) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Иван"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": "m-1", "seq": 1, "text": text, "attachments": []},
        },
    }


def _raw_entry(payload: dict) -> dict:
    return {
        "data": json.dumps(payload),
        "trace_id": str(uuid.uuid4()),
        "resolved_tenant_id": "",  # tenant-less by design
    }


@pytest.fixture
def mock_send(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def mock_discovery(monkeypatch):
    """Stub the LLM discovery reply so the handler doesn't make a live call."""
    from apps.orchestrator.discovery import DiscoveryReply

    monkeypatch.setattr(
        "apps.orchestrator.concierge.generate_concierge_reply",
        lambda *a, **k: DiscoveryReply(text="Привет! Какая услуга интересует?"),
    )


def test_global_handler_resolves_under_sentinel_no_tenant_required(
    settings, mock_send, fake_redis, mock_discovery
) -> None:
    """requires_tenant=False → no TenantRequiredButMissing even with strict refuse;
    identity resolved under the global_bot sentinel at current_tenant()=None.
    """
    settings.STRICT_TENANT_REFUSE = True

    GlobalMaxHandler()(_raw_entry(_payload(user_id=7777, chat_id=8888)))

    bot_user = BotUser.all_tenants.get(channel="max", channel_user_id="7777")
    assert bot_user.tenant.slug == GLOBAL_BOT_TENANT_SLUG
    # Reply was sent and the handler did not leak a tenant scope.
    assert len(mock_send) == 1
    assert current_tenant() is None


def test_global_handler_skips_unsupported_update_type(settings, mock_send, fake_redis) -> None:
    settings.STRICT_TENANT_REFUSE = True
    # An unsupported lifecycle update must be tolerated (no raise, no PEL storm).
    GlobalMaxHandler()(_raw_entry({"update_type": "bot_started"}))
    assert BotUser.all_tenants.filter(channel="max").count() == 0
    assert len(mock_send) == 0


def test_commercial_read_before_booking_raises_crosstenanterror(settings) -> None:
    """Acceptance invariant guard: a commercial TenantScoped read at
    ``current_tenant()=None`` (strict) raises CrossTenantError.

    This guards the *manager* invariant the global handler relies on — the
    handler itself (``handle_global_max_event``) deliberately performs NO
    commercial reads, so this proves the safety net is armed for the discovery
    context, not that the handler trips it.
    """
    settings.STRICT_TENANT_SCOPE = "strict"
    from apps.booking.models import BookingRequest

    with tenant_scope(None):
        assert current_tenant() is None
        with pytest.raises(CrossTenantError):
            list(BookingRequest.objects.all())


def test_booking_callback_routes_to_booking_pipeline_not_concierge(
    settings, mock_send, fake_redis, monkeypatch
) -> None:
    """DRF-988: a post-handoff ``cb:book:*`` tap routes into the booking
    pipeline (``route_booking_callback``) — never to the concierge LLM, which
    previously received the raw payload as chat text (the «2026 год» refusal).
    """
    from apps.orchestrator.discovery import DiscoveryReply

    seen: dict = {}

    def fake_route(**kwargs):
        seen["routed"] = kwargs.get("callback_text")
        return DiscoveryReply(text="Выберите время:")

    def fake_concierge(*args, **kwargs):
        seen["concierge"] = True
        return DiscoveryReply(text="concierge")

    monkeypatch.setattr(max_handler, "route_booking_callback", fake_route)
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", fake_concierge)

    GlobalMaxHandler()(
        _raw_entry(
            _payload(text="cb:book:pick_date:abc:2026-08-11:def", user_id=7779, chat_id=8889)
        )
    )

    assert seen.get("routed") == "cb:book:pick_date:abc:2026-08-11:def"
    assert "concierge" not in seen
    assert mock_send[-1]["text"] == "Выберите время:"
    assert current_tenant() is None

    # Routed callbacks must NOT be persisted as global user messages — the
    # raw payload must never re-enter the concierge's LLM history (№1/3).
    from apps.conversations.models import Message

    assert (
        Message.all_tenants.filter(
            role="user", content="cb:book:pick_date:abc:2026-08-11:def"
        ).count()
        == 0
    )


def test_foreign_callback_still_goes_to_concierge(
    settings, mock_send, fake_redis, monkeypatch
) -> None:
    """Non-booking ``cb:*`` payloads (e.g. cb:foo:…) keep the pre-DRF-988
    behaviour: a normal concierge turn with the user turn persisted."""
    from apps.conversations.models import Message
    from apps.orchestrator.discovery import DiscoveryReply

    seen: dict = {}

    def fake_concierge(*args, **kwargs):
        seen["concierge"] = True
        return DiscoveryReply(text="concierge reply")

    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", fake_concierge)
    monkeypatch.setattr(
        max_handler,
        "route_booking_callback",
        lambda **kw: (_ for _ in ()).throw(AssertionError("must not route cb:foo")),
    )

    GlobalMaxHandler()(_raw_entry(_payload(text="cb:foo:bar", user_id=7780, chat_id=8890)))

    assert seen.get("concierge") is True
    assert Message.all_tenants.filter(role="user", content="cb:foo:bar").count() == 1
