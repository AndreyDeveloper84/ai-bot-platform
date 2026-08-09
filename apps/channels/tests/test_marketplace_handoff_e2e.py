"""End-to-end discovery → booking handoff via the global MAX bot (#1020).

A user on the global bot taps a master card (callback
`cb:discover:book:{T}:{M}:{S}` — the service id rides along since DRF-962).
The global handler must enter tenant_scope(T), bridge the user into a
per-tenant BotUser in T, and delegate into the per-tenant booking entrypoint
with the full master+service pick payload — all without leaking a tenant
scope. A legacy serviceless callback gets the ask-the-service reply, never a
doomed dispatch. LLM/send/redis/dispatch are mocked.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.channels.handlers import GlobalMaxHandler
from apps.channels.max import handler as max_handler
from apps.identity.models import BotUser
from apps.orchestrator.memory import short_term
from apps.tenancy.context import current_tenant
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _callback_payload(*, payload: str, user_id: int, chat_id: int) -> dict:
    return {
        "update_type": "message_callback",
        "timestamp": 1731320000000,
        "callback": {
            "callback_id": f"cb-{user_id}",
            "payload": payload,
            "user": {"user_id": user_id, "name": "Иван"},
        },
        "message": {
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": "m-1", "seq": 1, "text": ""},
        },
    }


def _raw_entry(payload: dict) -> dict:
    return {"data": json.dumps(payload), "trace_id": str(uuid.uuid4()), "resolved_tenant_id": ""}


@pytest.fixture
def mock_send(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        max_handler,
        "send_message",
        lambda *, chat_id, text, attachments=None, timeout=10.0: calls.append(
            {"chat_id": chat_id, "text": text, "attachments": attachments}
        ),
    )
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


def _fixture(t: Tenant) -> tuple[CatalogMaster, CatalogService]:
    ts = datetime(2026, 5, 18, tzinfo=timezone.utc)
    master = CatalogMaster.all_tenants.create(
        tenant=t,
        external_id=1,
        external_updated_at=ts,
        name="Анна",
        specialization="маникюр",
        yclients_staff_id=42,
    )
    service = CatalogService.all_tenants.create(
        tenant=t,
        slug="manicure",
        name="Маникюр",
        is_active=True,
        external_id=55,
        external_updated_at=ts,
    )
    MasterService.all_tenants.create(tenant=t, master=master, service=service)
    return master, service


def test_handoff_callback_enters_tenant_and_delegates(
    settings, monkeypatch, mock_send, fake_redis
) -> None:
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = False
    t = Tenant.objects.create(slug="t-e2e", name="Salon", timezone="Europe/Moscow", city="Пенза")
    master, service = _fixture(t)

    # The booking entrypoint is mocked; capture the scope it runs under to prove
    # the global→tenant transition happened (booking would land in T).
    from apps.skills.base import SkillResult

    seen: dict = {}

    def fake_dispatch(ctx):
        seen["tenant"] = current_tenant()
        seen["text"] = ctx.message_text
        return SkillResult(reply_text="Выберите дату к мастеру Анна")

    monkeypatch.setattr("apps.skills.registry.dispatch", fake_dispatch)

    payload = _callback_payload(
        payload=f"cb:discover:book:{t.id}:{master.id}:{service.id}", user_id=600, chat_id=700
    )
    GlobalMaxHandler()(_raw_entry(payload))

    # The booking entrypoint ran INSIDE tenant_scope(T) with the native
    # master + service ids (DRF-962: the service is REQUIRED by the pick
    # contract — serviceless dispatch dead-ends on the stale-context guard).
    assert seen["tenant"].id == t.id
    assert seen["text"] == "cb:book:pick_master:42:55"
    # A per-tenant BotUser was bridged into T (distinct from the global sentinel one).
    assert BotUser.all_tenants.filter(tenant=t, channel="max", channel_user_id="600").exists()
    # Reply sent; no tenant scope leaked out of the handler.
    assert len(mock_send) == 1 and "Анна" in mock_send[0]["text"]
    assert current_tenant() is None


def test_legacy_serviceless_callback_asks_for_service(
    settings, monkeypatch, mock_send, fake_redis
) -> None:
    """A pre-DRF-962 keyboard (2-id payload) must get the honest
    ask-the-service reply — not a dispatch that the booking skill would
    refuse with «Контекст записи устарел»."""
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = False
    t = Tenant.objects.create(slug="t-e2e-2", name="Salon", timezone="Europe/Moscow", city="Пенза")
    master, _service = _fixture(t)

    called: list = []
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: called.append(1))

    payload = _callback_payload(
        payload=f"cb:discover:book:{t.id}:{master.id}", user_id=601, chat_id=701
    )
    GlobalMaxHandler()(_raw_entry(payload))

    assert called == []
    assert len(mock_send) == 1
    assert "уточните услугу" in mock_send[0]["text"]
    assert current_tenant() is None
