"""Discovery → booking handoff transition (#1020, service context DRF-962).

Locks the heart of P3: the handoff enters tenant_scope(T) FIRST, then reads the
commercial native ids; the per-tenant booking entrypoint runs scoped to T; the
nullable yclients_staff_id is handled gracefully; and the bridge-read invariant
(commercial read at current_tenant()=None raises CrossTenantError) holds.

DRF-962 additions: a booking dispatch ALWAYS carries master + service (the
booking skill's pick_master guard refuses a serviceless tap with the
stale-context text — that was the live pilot dead-end). A tap without a
resolvable service — legacy two-id keyboard, forged/foreign service, inactive
service, unlinked native id — gets the ask-the-service reply and NO dispatch.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.services import resolve_or_create_global_bot_user
from apps.orchestrator.handoff import handoff_to_booking
from apps.skills.base import SkillResult
from apps.tenancy.context import current_tenant, tenant_scope
from apps.tenancy.exceptions import CrossTenantError
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

_TS = datetime(2026, 5, 18, tzinfo=timezone.utc)


def _tenant(slug: str = "t-handoff") -> Tenant:
    return Tenant.objects.create(slug=slug, name=slug, timezone="Europe/Moscow", city="Пенза")


def _master(tenant: Tenant, *, staff_id, name: str = "Анна") -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=1,
        external_updated_at=_TS,
        name=name,
        specialization="маникюр",
        yclients_staff_id=staff_id,
    )


def _service(
    tenant: Tenant,
    *,
    external_id=None,
    ayla_service_id=None,
    name: str = "Спортивный массаж",
    is_active: bool = True,
    slug: str = "svc",
) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        slug=slug,
        name=name,
        is_active=is_active,
        external_id=external_id,
        ayla_service_id=ayla_service_id,
        external_updated_at=_TS,
    )


def _link(tenant: Tenant, master: CatalogMaster, service: CatalogService) -> MasterService:
    return MasterService.all_tenants.create(tenant=tenant, master=master, service=service)


def test_handoff_enters_scope_bridges_identity_and_delegates(settings, monkeypatch) -> None:
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = False
    t = _tenant()
    master = _master(t, staff_id=777)
    service = _service(t, external_id=55)
    _link(t, master, service)
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="500", chat_id="500")
    assert current_tenant() is None

    seen: dict = {}

    def fake_dispatch(ctx):
        seen["tenant"] = current_tenant()
        seen["text"] = ctx.message_text
        seen["bot_user_tenant"] = ctx.bot_user.tenant_id
        return SkillResult(reply_text="Выберите дату")

    monkeypatch.setattr("apps.skills.registry.dispatch", fake_dispatch)

    reply = handoff_to_booking(
        global_bot_user=gbu,
        tenant_id=t.id,
        master_id=master.id,
        service_id=service.id,
        chat_id="500",
    )

    assert reply.text == "Выберите дату"
    assert seen["tenant"].id == t.id  # dispatch ran INSIDE tenant_scope(T)
    # Native ids (flag OFF): int staff_id + int mysite service external_id.
    assert seen["text"] == "cb:book:pick_master:777:55"
    assert seen["bot_user_tenant"] == t.id  # per-tenant BotUser bridged into T
    assert current_tenant() is None  # scope released after the handoff


def test_handoff_ayla_flag_dispatches_canonical_uuids(settings, monkeypatch) -> None:
    """Flag ON (the pilot path): both ids are canonical Ayla UUIDs — the
    master's mirror pk and the service's ``ayla_service_id``."""
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = True
    t = _tenant("t-ayla")
    master = _master(t, staff_id=None)  # native staff id irrelevant on this path
    ayla_uuid = uuid4()
    service = _service(t, ayla_service_id=ayla_uuid)
    _link(t, master, service)
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="503", chat_id="503")

    seen: dict = {}

    def fake_dispatch(ctx):
        seen["text"] = ctx.message_text
        return SkillResult(reply_text="Выберите дату")

    monkeypatch.setattr("apps.skills.registry.dispatch", fake_dispatch)

    reply = handoff_to_booking(
        global_bot_user=gbu,
        tenant_id=t.id,
        master_id=master.id,
        service_id=service.id,
        chat_id="503",
    )

    assert reply.text == "Выберите дату"
    assert seen["text"] == f"cb:book:pick_master:{master.id}:{ayla_uuid}"


def test_handoff_should_handoff_creates_admin_task(settings, monkeypatch) -> None:
    """#1047: if the booking entrypoint escalates (should_handoff) on the global
    booking path, an AdminTask must be created (operator notified) inside
    tenant_scope(T) — it was previously dropped."""
    from apps.handoff.models import AdminTask

    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = False
    t = _tenant()
    master = _master(t, staff_id=888)
    service = _service(t, external_id=56)
    _link(t, master, service)
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="501", chat_id="501")

    def fake_dispatch(ctx):
        return SkillResult(
            reply_text="Секунду, переключаю на менеджера.",
            should_handoff=True,
            handoff_reason="booking_gate_failed",
        )

    monkeypatch.setattr("apps.skills.registry.dispatch", fake_dispatch)

    reply = handoff_to_booking(
        global_bot_user=gbu,
        tenant_id=t.id,
        master_id=master.id,
        service_id=service.id,
        chat_id="501",
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


def test_handoff_without_service_asks_and_does_not_dispatch(settings, monkeypatch) -> None:
    """A serviceless tap (legacy 2-id keyboard / ambiguous query) must get the
    ask-the-service reply — dispatching would only produce the booking skill's
    stale-context dead-end (the DRF-962 live failure)."""
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = False
    t = _tenant("t-noservice")
    master = _master(t, staff_id=777, name="Анна")
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="504")

    called: list = []
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: called.append(1))

    reply = handoff_to_booking(global_bot_user=gbu, tenant_id=t.id, master_id=master.id)

    assert "уточните услугу" in reply.text
    assert "Анна" in reply.text
    assert called == []


@pytest.mark.parametrize("case", ["foreign", "no_edge", "inactive", "unlinked_native"])
def test_handoff_unresolvable_service_asks_and_does_not_dispatch(
    settings, monkeypatch, case: str
) -> None:
    """Forged/stale service ids must not smuggle a service into booking:
    foreign-tenant service, service without a MasterService edge, inactive
    service, and a service with no native id for the active path all collapse
    to the same honest ask-the-service reply, without a dispatch."""
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = False
    t = _tenant("t-badservice")
    master = _master(t, staff_id=777)
    if case == "foreign":
        other = _tenant("t-other")
        service = _service(other, external_id=55)
    elif case == "no_edge":
        service = _service(t, external_id=55)  # exists in T, master doesn't offer it
    elif case == "inactive":
        service = _service(t, external_id=55, is_active=False)
        _link(t, master, service)
    else:  # unlinked_native: edge ok, but no mysite external_id under flag OFF
        service = _service(t, external_id=None)
        _link(t, master, service)
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="505")

    called: list = []
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: called.append(1))

    reply = handoff_to_booking(
        global_bot_user=gbu, tenant_id=t.id, master_id=master.id, service_id=service.id
    )

    assert "уточните услугу" in reply.text
    assert called == []


def test_handoff_nullable_staff_id_is_graceful_no_dispatch(settings, monkeypatch) -> None:
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = False
    t = _tenant("t-null")
    master = _master(t, staff_id=None)  # master not linked to YClients
    service = _service(t, external_id=55)
    _link(t, master, service)
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="501")

    called: list = []
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: called.append(1))

    reply = handoff_to_booking(
        global_bot_user=gbu, tenant_id=t.id, master_id=master.id, service_id=service.id
    )
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
