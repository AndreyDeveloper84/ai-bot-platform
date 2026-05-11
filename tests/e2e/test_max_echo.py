"""Sprint 2 / G1 — E2E MAX echo (DRF-449).

Exercises the full Sprint 2 chain against real Redis + Django test
client + real DB:

    POST /api/v1/ingress/max/         ← apps.ingress.views.max_webhook
        ↓ (verifies X-Max-Bot-Api-Secret, records WebhookJournal,
           XADD onto ingress:max, returns 200)
    consume_once([ingress:max])       ← apps.workers.consumer
        ↓ (XREADGROUP → MaxHandler → enter tenant_scope + trace_id_scope)
    handle_max_event(payload)         ← apps.channels.max.handler
        ↓ (parse → resolve BotUser → resolve Conversation →
           record user msg + assistant msg + memory append +
           send_message)

Outbound `send_message` is mocked at the module level. Redis +
Postgres are real.

Run conditions:
- CI: postgres + redis services up → tests run.
- Local with `docker compose up` → tests run.
- Bare laptop without Redis → tests SKIPPED.

Marker: `@pytest.mark.e2e` (matches Sprint 1 G1 pattern).
"""

from __future__ import annotations

import json
import uuid

import pytest
import redis as redis_lib
from django.conf import settings as django_settings
from django.test import Client

from apps.channels.handlers import MaxHandler
from apps.channels.max import handler as max_handler
from apps.conversations.models import Conversation, Message
from apps.identity.models import BotUser
from apps.ingress import streams as ingress_streams
from apps.ingress.models import WebhookJournal
from apps.orchestrator.memory import short_term
from apps.tenancy.models import Tenant
from apps.workers import consumer
from apps.workers.registry import register


def _redis_reachable() -> bool:
    try:
        client = redis_lib.Redis.from_url(
            getattr(django_settings, "REDIS_URL", "redis://localhost:6379/0"),
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        client.ping()
        client.close()
        return True
    except (redis_lib.ConnectionError, OSError, redis_lib.TimeoutError):
        return False


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not _redis_reachable(),
        reason="Redis not reachable on REDIS_URL — start `docker compose up` "
        "or rely on CI's redis service.",
    ),
    pytest.mark.django_db(transaction=True),
]


_WEBHOOK_SECRET = "g1-secret"  # pragma: allowlist secret — test-only literal
_TENANT_SLUG = "g1-formula"


def _build_payload(*, text="Привет", user_id=42, chat_id=99, mid=None) -> dict:
    mid = mid or f"g1-mid-{uuid.uuid4().hex[:8]}"
    return {
        "update_id": mid,
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "G1 User"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {
                "mid": mid,
                "seq": 1,
                "text": text,
                "attachments": [],
            },
        },
    }


@pytest.fixture
def isolated_stream(monkeypatch):
    prefix = f"g1test-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(ingress_streams, "_stream_prefix", lambda: prefix)
    # Re-register MaxHandler under the isolated stream name so the
    # consumer's lookup finds it. The default registration in
    # apps/channels/apps.py::ready binds to "ingress:max" — under the
    # isolated prefix the stream is "g1test-XXXX:max" and `lookup`
    # would return None without this.
    isolated_stream_name = f"{prefix}:max"
    register(isolated_stream_name)(MaxHandler)
    yield prefix
    # Cleanup
    try:
        client = ingress_streams._client()
        client.delete(isolated_stream_name)
    except Exception:  # noqa: BLE001 — cleanup is best-effort
        pass


@pytest.fixture
def _clear_client_cache():
    ingress_streams._client.cache_clear()
    yield
    ingress_streams._client.cache_clear()


@pytest.fixture
def g1_tenant(settings):
    """Tenant + channel-token mapping + webhook secret all in one place."""

    t = Tenant.objects.create(slug=_TENANT_SLUG, name="G1 Tenant")
    settings.CHANNEL_TOKEN_TO_TENANT_SLUG = f"{_WEBHOOK_SECRET}={_TENANT_SLUG}"
    settings.MAX_WEBHOOK_SECRET = _WEBHOOK_SECRET
    settings.STRICT_TENANT_SCOPE = "audit"  # webhook view exempts /api/v1/ingress/
    return t


@pytest.fixture
def mock_outbound(monkeypatch):
    """Mock `send_message` at the handler module level so the e2e
    doesn't hit MAX API. The handler imports send_message directly,
    so we patch the binding in `apps.channels.max.handler`.
    """

    calls = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def mock_redis_memory(monkeypatch):
    """Mock Redis for `short_term` — keeps the e2e focused on the
    ingress→DB chain. Sprint 1 G1 already proves real-Redis works for
    Streams; here we don't need real Redis for memory writes.
    """

    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


def test_max_webhook_to_echo_happy_path(
    g1_tenant,
    isolated_stream,
    _clear_client_cache,
    mock_outbound,
    mock_redis_memory,
):
    """POST → 200, then consume_once → handler executes, DB has full chain."""

    client = Client()
    payload = _build_payload(text="Привет")
    response = client.post(
        "/api/v1/ingress/max/",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_MAX_BOT_API_SECRET=_WEBHOOK_SECRET,
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "dedup": False}

    # WebhookJournal row exists with tenant resolved.
    journal = WebhookJournal.objects.get(channel="max")
    assert journal.resolved_tenant_id == g1_tenant.id

    # Now run the consumer once — it XREADGROUP's from ingress:max,
    # dispatches to MaxHandler.
    stream = f"{isolated_stream}:max"
    processed = consumer.consume_once(streams=[stream], block_ms=1000, count=10)
    assert processed == 1

    # 1 BotUser, 1 Conversation, 2 Messages (user + assistant).
    assert BotUser.all_tenants.filter(tenant=g1_tenant).count() == 1
    bot = BotUser.all_tenants.get(tenant=g1_tenant)
    assert bot.channel == "max"
    assert bot.channel_user_id == "42"

    convs = Conversation.all_tenants.filter(tenant=g1_tenant, bot_user=bot)
    assert convs.count() == 1
    conv = convs.first()
    assert conv.is_active is True

    msgs = list(Message.all_tenants.filter(conversation=conv).order_by("created_at"))
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].content == "Привет"
    assert msgs[1].content == "Привет"

    # Outbound called with the echo.
    assert len(mock_outbound) == 1
    assert mock_outbound[0]["chat_id"] == "99"
    assert mock_outbound[0]["text"] == "Привет"


def test_max_webhook_replay_dedup(
    g1_tenant,
    isolated_stream,
    _clear_client_cache,
    mock_outbound,
    mock_redis_memory,
):
    """Same payload twice → second POST is a dedup (200 with dedup=True),
    no second WebhookJournal row, no second XADD."""

    client = Client()
    payload = _build_payload(text="duplicate", mid="dup-mid-1")
    headers = {"HTTP_X_MAX_BOT_API_SECRET": _WEBHOOK_SECRET}

    r1 = client.post(
        "/api/v1/ingress/max/",
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
    )
    r2 = client.post(
        "/api/v1/ingress/max/",
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
    )
    assert r1.status_code == 200
    assert r1.json()["dedup"] is False
    assert r2.status_code == 200
    assert r2.json()["dedup"] is True

    # Only one journal row.
    assert WebhookJournal.objects.filter(channel="max").count() == 1


def test_max_webhook_rejects_missing_secret(
    g1_tenant,
    isolated_stream,
    _clear_client_cache,
    mock_outbound,
    mock_redis_memory,
):
    """No `X-Max-Bot-Api-Secret` header → 401, no DB writes."""

    client = Client()
    response = client.post(
        "/api/v1/ingress/max/",
        data=json.dumps(_build_payload()),
        content_type="application/json",
    )
    assert response.status_code == 401
    assert WebhookJournal.objects.count() == 0
