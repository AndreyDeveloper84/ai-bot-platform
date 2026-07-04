"""Discovery → booking handoff transition (#1020).

Locks the heart of P3: the handoff enters tenant_scope(T) FIRST, then reads the
commercial native id; the per-tenant booking entrypoint runs scoped to T; the
nullable yclients_staff_id is handled gracefully; and the bridge-read invariant
(commercial read at current_tenant()=None raises CrossTenantError) holds.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from apps.catalog.models import CatalogMaster
from apps.identity.services import resolve_or_create_global_bot_user
from apps.orchestrator.handoff import handoff_to_booking
from apps.skills.base import SkillResult
from apps.tenancy.context import current_tenant, tenant_scope
from apps.tenancy.exceptions import CrossTenantError
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _tenant(slug: str = "t-handoff") -> Tenant:
    return Tenant.objects.create(slug=slug, name=slug, timezone="Europe/Moscow", city="Пенза")


def _master(tenant: Tenant, *, staff_id, name: str = "Анна") -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=1,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        name=name,
        specialization="маникюр",
        yclients_staff_id=staff_id,
    )


def test_handoff_enters_scope_bridges_identity_and_delegates(settings, monkeypatch) -> None:
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = False
    t = _tenant()
    master = _master(t, staff_id=777)
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="500", chat_id="500")
    assert current_tenant() is None

    seen: dict = {}

    def fake_dispatch(ctx):
        seen["tenant"] = current_tenant()
        seen["text"] = ctx.message_text
        seen["bot_user_tenant"] = ctx.bot_user.tenant_id
        return SkillResult(reply_text="Выберите услугу")

    monkeypatch.setattr("apps.skills.registry.dispatch", fake_dispatch)

    reply = handoff_to_booking(
        global_bot_user=gbu, tenant_id=t.id, master_id=master.id, chat_id="500"
    )

    assert reply.text == "Выберите услугу"
    assert seen["tenant"].id == t.id  # dispatch ran INSIDE tenant_scope(T)
    assert seen["text"] == "cb:book:pick_master:777"  # native int staff_id (flag OFF)
    assert seen["bot_user_tenant"] == t.id  # per-tenant BotUser bridged into T
    assert current_tenant() is None  # scope released after the handoff


def test_handoff_should_handoff_creates_admin_task(settings, monkeypatch) -> None:
    """#1047: if the booking entrypoint escalates (should_handoff) on the global
    booking path, an AdminTask must be created (operator notified) inside
    tenant_scope(T) — it was previously dropped."""
    from apps.handoff.models import AdminTask

    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = False
    t = _tenant()
    master = _master(t, staff_id=888)
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="501", chat_id="501")

    def fake_dispatch(ctx):
        return SkillResult(
            reply_text="Секунду, переключаю на менеджера.",
            should_handoff=True,
            handoff_reason="booking_gate_failed",
        )

    monkeypatch.setattr("apps.skills.registry.dispatch", fake_dispatch)

    reply = handoff_to_booking(
        global_bot_user=gbu, tenant_id=t.id, master_id=master.id, chat_id="501"
    )

    assert reply.text == "Секунду, переключаю на менеджера."
    tasks = AdminTask.all_tenants.filter(tenant=t)
    assert tasks.count() == 1
    task = tasks.first()
    assert task is not None
    assert task.task_type == AdminTask.TaskType.HANDOFF
    assert task.reason == "booking_gate_failed"
    # Operator's snapshot must carry the booking-pick context, not be empty (CR).
    assert task.transcript_snapshot["messages"], "handoff task transcript is empty"
    assert current_tenant() is None  # scope released after the handoff


def test_handoff_nullable_staff_id_is_graceful_no_dispatch(settings, monkeypatch) -> None:
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = False
    t = _tenant("t-null")
    master = _master(t, staff_id=None)  # master not linked to YClients
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="501")

    called: list = []
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: called.append(1))

    reply = handoff_to_booking(global_bot_user=gbu, tenant_id=t.id, master_id=master.id)
    assert "недоступна" in reply.text
    assert called == []  # no booking dispatch when the master has no native id


def test_handoff_unknown_tenant_or_master_graceful(settings, monkeypatch) -> None:
    settings.STRICT_TENANT_SCOPE = "strict"
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="502")
    called: list = []
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: called.append(1))

    # Unknown tenant id → graceful, no dispatch.
    reply = handoff_to_booking(global_bot_user=gbu, tenant_id=uuid4(), master_id=uuid4())
    assert "недоступна" in reply.text
    assert called == []


def test_bridge_read_invariant_raises_at_no_tenant(settings) -> None:
    """The commercial CatalogMaster read MUST raise CrossTenantError at
    current_tenant()=None — proving the handoff's read must be inside scope(T)."""
    settings.STRICT_TENANT_SCOPE = "strict"
    with tenant_scope(None):
        assert current_tenant() is None
        with pytest.raises(CrossTenantError):
            list(CatalogMaster.objects.all())
