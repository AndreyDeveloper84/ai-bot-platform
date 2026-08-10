"""Discovery → booking handoff transition (#1020, service context DRF-962).

Locks the heart of P3: the handoff enters tenant_scope(T) FIRST, then reads the
commercial native ids; the per-tenant booking entrypoint runs scoped to T; the
nullable yclients_staff_id is handled gracefully; and the bridge-read invariant
(commercial read at current_tenant()=None raises CrossTenantError) holds.

DRF-962 additions: a booking dispatch ALWAYS carries master + service, and
service grounding is Ayla-REST-only — ``ayla_service_id`` is the one proven
native id family (the mirror's ``external_id`` is the mysite pk, not a
verified YClients service id, so the legacy flag path never dispatches a
service). A tap without a resolvable service — legacy two-id keyboard,
forged/foreign service, inactive service, NULL ayla id, or the legacy flag —
gets the ask-the-service reply (listing the master's real services when it
has any) and NO dispatch.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from apps.booking.models import PendingBookingAction
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.services import resolve_or_create_bot_user, resolve_or_create_global_bot_user
from apps.orchestrator.handoff import handoff_to_booking, route_booking_callback
from apps.skills.base import SkillResult
from apps.tenancy.context import current_tenant, tenant_scope
from apps.tenancy.exceptions import CrossTenantError
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

_TS = datetime(2026, 5, 18, tzinfo=timezone.utc)


def _tenant(slug: str = "t-handoff") -> Tenant:
    return Tenant.objects.create(slug=slug, name=slug, timezone="Europe/Moscow", city="Пенза")


def _master(tenant: Tenant, *, staff_id=None, name: str = "Анна") -> CatalogMaster:
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
    ayla_service_id=None,
    external_id=None,
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
    """The primary (pilot) path: flag ON, canonical Ayla UUIDs for both ids."""
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = True
    t = _tenant()
    master = _master(t)
    ayla_uuid = uuid4()
    service = _service(t, ayla_service_id=ayla_uuid)
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
    # Native ids on the Ayla path: master mirror pk (= canonical Ayla
    # specialist id, S3B rekey) + the service's ayla_service_id.
    assert seen["text"] == f"cb:book:pick_master:{master.id}:{ayla_uuid}"
    assert seen["bot_user_tenant"] == t.id  # per-tenant BotUser bridged into T
    assert current_tenant() is None  # scope released after the handoff


def test_handoff_should_handoff_creates_admin_task(settings, monkeypatch) -> None:
    """#1047: if the booking entrypoint escalates (should_handoff) on the global
    booking path, an AdminTask must be created (operator notified) inside
    tenant_scope(T) — it was previously dropped."""
    from apps.handoff.models import AdminTask

    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = True
    t = _tenant()
    master = _master(t)
    service = _service(t, ayla_service_id=uuid4())
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


def test_handoff_without_service_asks_with_the_masters_real_services(settings, monkeypatch) -> None:
    """A serviceless tap (legacy 2-id keyboard / ambiguous query) must get the
    ask-the-service reply LISTING this master's actual services — a workable
    next step, not a hardcoded example — and no dispatch (a serviceless
    dispatch is the stale-context dead-end this fix removes)."""
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = True
    t = _tenant("t-noservice")
    master = _master(t, name="Анна")
    _link(t, master, _service(t, ayla_service_id=uuid4(), name="Классический массаж", slug="c"))
    _link(t, master, _service(t, ayla_service_id=uuid4(), name="Спортивный массаж", slug="s"))
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="504")

    called: list = []
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: called.append(1))

    reply = handoff_to_booking(global_bot_user=gbu, tenant_id=t.id, master_id=master.id)

    assert "Анна" in reply.text
    assert "«Классический массаж»" in reply.text
    assert "«Спортивный массаж»" in reply.text
    assert called == []


def test_handoff_without_service_and_without_menu_still_asks(settings, monkeypatch) -> None:
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = True
    t = _tenant("t-bare")
    master = _master(t, name="Анна")  # no services linked at all
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="506")

    called: list = []
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: called.append(1))

    reply = handoff_to_booking(global_bot_user=gbu, tenant_id=t.id, master_id=master.id)

    assert "какая услуга вас интересует" in reply.text
    assert called == []


@pytest.mark.parametrize("case", ["foreign", "no_edge", "inactive", "null_ayla_id", "legacy_flag"])
def test_handoff_unresolvable_service_asks_and_does_not_dispatch(
    settings, monkeypatch, case: str
) -> None:
    """Forged/stale/ungroundable service ids must not smuggle a service into
    booking: foreign-tenant service, service without a MasterService edge,
    inactive service, NULL ayla_service_id, and the legacy YClients flag
    (external_id is the mysite pk — an unverified id family) all collapse to
    the same honest ask-the-service reply, without a dispatch."""
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = case != "legacy_flag"
    t = _tenant("t-badservice")
    master = _master(t, staff_id=777)
    if case == "foreign":
        other = _tenant("t-other")
        service = _service(other, ayla_service_id=uuid4())
    elif case == "no_edge":
        service = _service(t, ayla_service_id=uuid4())  # in T, master doesn't offer it
    elif case == "inactive":
        service = _service(t, ayla_service_id=uuid4(), is_active=False)
        _link(t, master, service)
    elif case == "null_ayla_id":
        service = _service(t, ayla_service_id=None, external_id=55)
        _link(t, master, service)
    else:  # legacy_flag: everything valid, but flag OFF never grounds a service
        service = _service(t, ayla_service_id=uuid4(), external_id=55)
        _link(t, master, service)
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="505")

    called: list = []
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: called.append(1))

    reply = handoff_to_booking(
        global_bot_user=gbu, tenant_id=t.id, master_id=master.id, service_id=service.id
    )

    assert "напишите" in reply.text  # the ask-the-service family of replies
    assert called == []


def test_handoff_unresolved_service_emits_funnel_event(settings, monkeypatch) -> None:
    """Ops visibility: a cohort whose every tap fails to resolve must be
    distinguishable from zero traffic on the funnel dashboard."""
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = True
    t = _tenant("t-funnel")
    master = _master(t)
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="507")

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "apps.orchestrator.handoff.emit",
        lambda name, payload=None, **kw: events.append((name, payload or {})),
    )
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: None)

    handoff_to_booking(global_bot_user=gbu, tenant_id=t.id, master_id=master.id)

    names = [name for name, _ in events]
    assert "marketplace.handoff.service_unresolved" in names
    assert "marketplace.handoff.entered" not in names
    payload = dict(events)["marketplace.handoff.service_unresolved"]
    # A serviceless tap must serialize as a real null, not the string "None" —
    # cohort queries over the event stream depend on it.
    assert payload["service_id"] is None


def test_ask_service_menu_lists_only_deliverable_services(settings, monkeypatch) -> None:
    """The menu must not suggest a service the stamping gate cannot deliver:
    under the Ayla flag a NULL-ayla_service_id service, when typed back,
    still produces a serviceless card — naming it would keep the user in the
    loop the reply exists to break."""
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = True
    t = _tenant("t-menu")
    master = _master(t, name="Анна")
    _link(t, master, _service(t, ayla_service_id=uuid4(), name="Спортивный массаж", slug="s"))
    _link(t, master, _service(t, ayla_service_id=None, name="Легаси-услуга", slug="l"))
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="508")
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: None)

    reply = handoff_to_booking(global_bot_user=gbu, tenant_id=t.id, master_id=master.id)

    assert "«Спортивный массаж»" in reply.text
    assert "Легаси-услуга" not in reply.text


def test_handoff_nullable_staff_id_is_graceful_no_dispatch(settings, monkeypatch) -> None:
    """Legacy YClients flag: a master without a native staff id stays the
    graceful 'unavailable' reply (master resolution precedes the service gate)."""
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


# ---------------------------------------------------------------------------
# DRF-988 — post-handoff cb:book:* taps route back into T's skill pipeline
# ---------------------------------------------------------------------------


def test_route_pick_date_dispatches_into_tenant_pipeline(settings, monkeypatch) -> None:
    """The DRF-988 funnel: a pick_date tap must reach the skill pipeline inside
    tenant_scope(T) with the raw payload verbatim — not the concierge."""
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = True
    t = _tenant("t-route")
    master = _master(t)
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="600", chat_id="600")

    seen: dict = {}

    def fake_dispatch(ctx):
        seen["tenant"] = current_tenant()
        seen["text"] = ctx.message_text
        seen["bot_user_tenant"] = ctx.bot_user.tenant_id
        return SkillResult(reply_text="Выберите время:", action_data={"keyboard": []})

    monkeypatch.setattr("apps.skills.registry.dispatch", fake_dispatch)
    callback = f"cb:book:pick_date:{master.id}:2026-08-11:{uuid4()}"

    reply = route_booking_callback(global_bot_user=gbu, callback_text=callback, chat_id="600")

    assert reply.text == "Выберите время:"
    assert reply.action_data == {"keyboard": []}
    assert seen["tenant"].id == t.id  # dispatch ran INSIDE tenant_scope(T)
    assert seen["text"] == callback  # raw payload, verbatim
    assert seen["bot_user_tenant"] == t.id  # per-tenant BotUser bridged into T
    assert current_tenant() is None  # scope released after routing


def test_route_confirm_resolves_tenant_from_pending_token(settings, monkeypatch) -> None:
    """confirm/cancel taps carry only the PendingBookingAction token — tenant
    resolution goes through the row (globally-unique UUID pk, all_tenants)."""
    settings.STRICT_TENANT_SCOPE = "strict"
    t = _tenant("t-token")
    with tenant_scope(t):
        bu = resolve_or_create_bot_user(channel="max", channel_user_id="601", chat_id="601")
    row = PendingBookingAction.all_tenants.create(
        tenant=t,
        bot_user=bu,
        kind=PendingBookingAction.Kind.CONFIRM,
        payload={},
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="601", chat_id="601")

    seen: dict = {}

    def fake_dispatch(ctx):
        seen["tenant"] = current_tenant()
        return SkillResult(reply_text="Записала вас!")

    monkeypatch.setattr("apps.skills.registry.dispatch", fake_dispatch)

    reply = route_booking_callback(
        global_bot_user=gbu, callback_text=f"cb:book:confirm:{row.pk}", chat_id="601"
    )

    assert reply.text == "Записала вас!"
    assert seen["tenant"].id == t.id
    assert current_tenant() is None


def test_route_unresolvable_callbacks_reply_stale_without_dispatch(settings, monkeypatch) -> None:
    """Unknown master id, unknown token, flag-off int ids and garbage payloads
    never reach the skill pipeline — deterministic stale-context reply."""
    settings.STRICT_TENANT_SCOPE = "strict"
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="602", chat_id="602")
    called: list = []
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: called.append(1))

    for callback in (
        f"cb:book:pick_date:{uuid4()}:2026-08-11:{uuid4()}",  # unknown master
        f"cb:book:confirm:{uuid4()}",  # unknown token
        "cb:book:pick_date:12345:2026-08-11:678",  # flag-off int ids — never resolved
        "cb:book:confirm:not-a-uuid",  # garbage
    ):
        reply = route_booking_callback(global_bot_user=gbu, callback_text=callback, chat_id="602")
        assert "устарел" in reply.text, callback
    assert called == []
