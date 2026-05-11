"""Sprint 3 / G1 — E2E PrivacyConsentSkill flow (DRF-473).

Drives the full chain against real Redis + Django test client + real
DB:

    POST /api/v1/ingress/max/   ← apps.ingress.views.max_webhook
        ↓ XADD to ingress:max stream
    consume_once                ← apps.workers.consumer
        ↓ MaxHandler.handle → handle_max_event
        ↓ dispatch(SkillContext) → PrivacyConsentSkill.handle
        ↓ data_delete → delete_bot_user_data
    send_message                ← mocked

Mirrors the Sprint 2 G1 pattern (`test_max_echo.py`) — same isolation
fixtures (`isolated_stream`, `_clear_client_cache`, `g1_tenant`,
`mock_outbound`, `mock_redis_memory`).

Marker: `@pytest.mark.e2e` — skipped when Redis isn't reachable.
"""

from __future__ import annotations

import json
import uuid

import pytest
import redis as redis_lib
from django.conf import settings as django_settings
from django.test import Client

from apps.audit.models import AuditLog
from apps.channels.handlers import MaxHandler
from apps.channels.max import handler as max_handler
from apps.identity.models import BotUser
from apps.ingress import streams as ingress_streams
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
        reason="Redis not reachable on REDIS_URL — start `docker compose up` or rely on CI's redis service.",
    ),
    pytest.mark.django_db(transaction=True),
]


_WEBHOOK_SECRET = "g1-privacy-secret"  # pragma: allowlist secret — test-only literal
_TENANT_SLUG = "g1-privacy"


def _payload(*, text, user_id=2001, chat_id=3001, mid=None) -> dict:
    mid = mid or f"g1p-{uuid.uuid4().hex[:8]}"
    return {
        "update_id": mid,
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "PG1 User"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


@pytest.fixture
def isolated_stream(monkeypatch):
    prefix = f"g1p-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(ingress_streams, "_stream_prefix", lambda: prefix)
    isolated_stream_name = f"{prefix}:max"
    register(isolated_stream_name)(MaxHandler)
    yield prefix
    try:
        client = ingress_streams._client()
        client.delete(isolated_stream_name)
    except Exception:  # noqa: BLE001 — best-effort
        pass


@pytest.fixture
def _clear_client_cache():
    ingress_streams._client.cache_clear()
    yield
    ingress_streams._client.cache_clear()


@pytest.fixture
def g1_tenant(settings):
    t = Tenant.objects.create(slug=_TENANT_SLUG, name="G1 Privacy")
    settings.CHANNEL_TOKEN_TO_TENANT_SLUG = f"{_WEBHOOK_SECRET}={_TENANT_SLUG}"
    settings.MAX_WEBHOOK_SECRET = _WEBHOOK_SECRET
    settings.STRICT_TENANT_SCOPE = "audit"
    return t


@pytest.fixture
def mock_outbound(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def mock_redis_memory(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


def _post_and_consume(client: Client, payload: dict, stream_prefix: str) -> int:
    response = client.post(
        "/api/v1/ingress/max/",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_MAX_BOT_API_SECRET=_WEBHOOK_SECRET,
    )
    assert response.status_code == 200
    return consumer.consume_once(streams=[f"{stream_prefix}:max"], block_ms=1000, count=10)


def test_delete_data_triggers_privacy_skill_and_wipes_user(
    g1_tenant, isolated_stream, _clear_client_cache, mock_outbound, mock_redis_memory
):
    """POST 'удалить мои данные' → PrivacyConsentSkill → data_delete → wipe."""

    client = Client()
    processed = _post_and_consume(client, _payload(text="удалить мои данные"), isolated_stream)
    assert processed == 1

    # BotUser hard-deleted; no row survives.
    assert BotUser.all_tenants.filter(tenant=g1_tenant).count() == 0

    # Reply went out once with the confirmation text.
    assert len(mock_outbound) == 1
    assert "удалены" in mock_outbound[0]["text"]

    # Audit row written BEFORE the delete — must still exist with the
    # original target despite the BotUser row being gone.
    assert AuditLog.all_tenants.filter(
        tenant=g1_tenant, action="privacy.data_delete.requested"
    ).exists()
    assert AuditLog.all_tenants.filter(
        tenant=g1_tenant, action="privacy.data_delete.completed"
    ).exists()


def test_export_data_returns_json_inline(
    g1_tenant, isolated_stream, _clear_client_cache, mock_outbound, mock_redis_memory
):
    client = Client()
    processed = _post_and_consume(client, _payload(text="выгрузить мои данные"), isolated_stream)
    assert processed == 1

    # BotUser still exists (export doesn't delete).
    assert BotUser.all_tenants.filter(tenant=g1_tenant).count() == 1

    # Reply contains JSON payload.
    assert len(mock_outbound) == 1
    reply = mock_outbound[0]["text"]
    assert "Ваши данные" in reply
    assert "conversations" in reply

    # Audit rows for export.
    assert AuditLog.all_tenants.filter(
        tenant=g1_tenant, action="privacy.data_export.requested"
    ).exists()
    assert AuditLog.all_tenants.filter(
        tenant=g1_tenant, action="privacy.data_export.completed"
    ).exists()
