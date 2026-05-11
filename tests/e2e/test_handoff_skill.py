"""Sprint 3 / G1 — E2E HumanHandoffSkill + state-guard flow (DRF-473).

End-to-end:
  POST /api/v1/ingress/max/ "оператор"
    → consume_once → HumanHandoffSkill.handle
    → create_admin_task: AdminTask row + state HUMAN_HANDOFF + event +
      audit
    → send_message("Передаю менеджеру …")

Follow-up POST → consume_once → dispatcher returns should_send=False
→ no new AdminTask, no second send.

After resolve_admin_task → state IDLE → next message echoed
verbatim.

Mirrors Sprint 2 G1 pattern + Sprint 3 G1 privacy pattern.
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
from apps.conversations.models import Conversation
from apps.handoff.models import AdminTask
from apps.handoff.services import resolve_admin_task
from apps.identity.models import BotUser
from apps.ingress import streams as ingress_streams
from apps.orchestrator.memory import short_term
from apps.tenancy.context import tenant_scope
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


_WEBHOOK_SECRET = "g1-handoff-secret"  # pragma: allowlist secret — test-only literal
_TENANT_SLUG = "g1-handoff"


def _payload(*, text, user_id=4001, chat_id=5001, mid=None) -> dict:
    mid = mid or f"g1h-{uuid.uuid4().hex[:8]}"
    return {
        "update_id": mid,
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "HG1 User"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


@pytest.fixture
def isolated_stream(monkeypatch):
    prefix = f"g1h-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(ingress_streams, "_stream_prefix", lambda: prefix)
    isolated_stream_name = f"{prefix}:max"
    register(isolated_stream_name)(MaxHandler)
    yield prefix
    try:
        client = ingress_streams._client()
        client.delete(isolated_stream_name)
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture
def _clear_client_cache():
    ingress_streams._client.cache_clear()
    yield
    ingress_streams._client.cache_clear()


@pytest.fixture
def g1_tenant(settings):
    t = Tenant.objects.create(slug=_TENANT_SLUG, name="G1 Handoff")
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


def test_handoff_flow_creates_task_silences_followup_resumes_on_resolve(
    g1_tenant, isolated_stream, _clear_client_cache, mock_outbound, mock_redis_memory
):
    client = Client()

    # 1. Trigger handoff.
    processed = _post_and_consume(client, _payload(text="оператор"), isolated_stream)
    assert processed == 1

    bu = BotUser.all_tenants.get(tenant=g1_tenant)
    conv = Conversation.all_tenants.get(bot_user=bu)
    tasks = list(AdminTask.all_tenants.filter(conversation=conv))
    assert len(tasks) == 1
    task = tasks[0]

    # Snapshot contains message history + bot_user shape (phone hashed).
    snap = task.transcript_snapshot
    assert "bot_user" in snap
    assert "channel" in snap["bot_user"]
    # No raw phone (user had no phone — but the shape contains phone_hash key).
    assert "phone_hash" in snap["bot_user"]
    assert "messages" in snap

    conv.refresh_from_db()
    assert conv.state == "human_handoff"
    assert len(mock_outbound) == 1
    assert "менеджеру" in mock_outbound[0]["text"]

    # 2. Follow-up message must be silenced.
    processed_2 = _post_and_consume(
        client, _payload(text="ещё вопрос", mid="g1h-followup"), isolated_stream
    )
    assert processed_2 == 1

    # No second AdminTask, no second send.
    assert AdminTask.all_tenants.filter(conversation=conv).count() == 1
    assert len(mock_outbound) == 1  # unchanged

    # 3. Operator resolves → state back to IDLE.
    with tenant_scope(g1_tenant):
        resolve_admin_task(task, resolution_note="done")
    conv.refresh_from_db()
    assert conv.state == "idle"

    # 4. Next message echoes normally.
    processed_3 = _post_and_consume(
        client, _payload(text="привет снова", mid="g1h-resume"), isolated_stream
    )
    assert processed_3 == 1
    assert len(mock_outbound) == 2
    assert mock_outbound[1]["text"] == "привет снова"
