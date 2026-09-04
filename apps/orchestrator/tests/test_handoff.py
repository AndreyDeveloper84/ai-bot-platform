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

DRF-1070 additions: that ask-the-service reply carries the services as
BUTTONS, so nothing has to be spelled. The locks here are the ones the live
2026-08-14 failure would have caught — a button that actually reaches the
date/slot step, buttons (not only text) on a serviceless tap, a named answer
when the master does not offer the tapped service, a truncated-but-honest
list when the roster outgrows the keyboard, and a text list that stands on
its own for a channel that drops keyboards.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from apps.booking.models import PendingBookingAction
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.channels.max.handler import _discovery_handoff_reply
from apps.channels.max.parser import CanonicalEvent
from apps.identity.services import resolve_or_create_bot_user, resolve_or_create_global_bot_user
from apps.orchestrator.discovery import CALLBACK_DISCOVER_BOOK_PREFIX, DiscoveryReply
from apps.orchestrator.handoff import (
    _UNRESOLVED_BOOKING_CALLBACK_REPLY,
    handoff_to_booking,
    route_booking_callback,
)
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


def _buttons(reply: DiscoveryReply) -> list[dict[str, str]]:
    """The reply's inline-keyboard buttons, or ``[]`` when it carries none.

    Reads the same ``action_data`` shape the MAX handler's
    ``_build_attachments`` consumes — asserting on the rendered contract, not
    on an internal helper's return value.
    """
    for attachment in (reply.action_data or {}).get("attachments", []):
        if attachment.get("type") == "inline_keyboard":
            return list(attachment["payload"]["buttons"])
    return []


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


def test_handoff_without_service_offers_the_masters_services_as_buttons(
    settings, monkeypatch
) -> None:
    """DRF-1070: a serviceless tap (legacy 2-id keyboard / ambiguous query)
    must answer with BUTTONS for this master's actual services — text alone is
    what failed live on 2026-08-14 — and no dispatch (a serviceless dispatch is
    the stale-context dead-end DRF-962 removed).

    Each button carries the already-working
    ``cb:discover:book:<tenant>:<master>:<service>`` contract, i.e. the same
    grammar the master cards emit: no new callback namespace is introduced.
    """
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = True
    t = _tenant("t-noservice")
    master = _master(t, name="Анна")
    classic = _service(t, ayla_service_id=uuid4(), name="Классический массаж", slug="c")
    sport = _service(t, ayla_service_id=uuid4(), name="Спортивный массаж", slug="s")
    _link(t, master, classic)
    _link(t, master, sport)
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="504")

    called: list = []
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: called.append(1))

    reply = handoff_to_booking(global_bot_user=gbu, tenant_id=t.id, master_id=master.id)

    buttons = _buttons(reply)
    assert [b["label"] for b in buttons] == ["Классический массаж", "Спортивный массаж"]
    assert [b["callback"] for b in buttons] == [
        f"{CALLBACK_DISCOVER_BOOK_PREFIX}{t.id}:{master.id}:{classic.id}",
        f"{CALLBACK_DISCOVER_BOOK_PREFIX}{t.id}:{master.id}:{sport.id}",
    ]
    assert "Анна" in reply.text
    assert called == []


def test_service_button_reaches_the_date_step(settings, monkeypatch) -> None:
    """The whole point of the ticket: tapping one of those buttons must reach
    booking's date/slot step, not loop back to the same question.

    The tap is replayed through the REAL callback parser
    (``_discovery_handoff_reply`` in the MAX global handler), so the button's
    payload is proven parseable by the code that will receive it in production
    — not merely well-formed to this test's eye.
    """
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = True
    t = _tenant("t-button")
    master = _master(t, name="Инна")
    ayla_uuid = uuid4()
    _link(
        t,
        master,
        _service(t, ayla_service_id=ayla_uuid, name="Биоэнергетический массаж", slug="bio"),
    )
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="509", chat_id="509")

    seen: dict = {}

    def fake_dispatch(ctx):
        seen["text"] = ctx.message_text
        seen["tenant"] = current_tenant()
        return SkillResult(reply_text="Выберите дату:", action_data={"keyboard": []})

    monkeypatch.setattr("apps.skills.registry.dispatch", fake_dispatch)

    serviceless = handoff_to_booking(
        global_bot_user=gbu, tenant_id=t.id, master_id=master.id, chat_id="509"
    )
    buttons = _buttons(serviceless)
    assert [b["label"] for b in buttons] == ["Биоэнергетический массаж"]
    assert seen == {}, "the serviceless tap must not dispatch"

    event = CanonicalEvent(
        channel="max",
        channel_user_id="509",
        channel_message_id="42",
        chat_id="509",
        text=buttons[0]["callback"],
    )
    reply = _discovery_handoff_reply(event, gbu, None)

    assert reply.text == "Выберите дату:"
    assert reply.action_data == {"keyboard": []}
    # Service context stamped from the button's id — the name was never typed.
    assert seen["text"] == f"cb:book:pick_master:{master.id}:{ayla_uuid}"
    assert seen["tenant"].id == t.id
    assert current_tenant() is None


def test_service_the_master_does_not_offer_says_so_and_offers_the_real_ones(
    settings, monkeypatch
) -> None:
    """A service this master does not do must produce a NAMED answer plus the
    real alternatives — the old reply re-asked the same question, leaving the
    user to conclude their spelling was wrong when the master was.

    The funnel event separates this cohort from every other miss.
    """
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = True
    t = _tenant("t-notoffered")
    master = _master(t, name="Инна")
    offered = _service(t, ayla_service_id=uuid4(), name="Классический массаж", slug="c")
    _link(t, master, offered)
    # Exists in T (another master does it) but has no edge to THIS master.
    foreign_to_master = _service(t, ayla_service_id=uuid4(), name="Маникюр", slug="m")
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="510")

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "apps.orchestrator.handoff.emit",
        lambda name, payload=None, **kw: events.append((name, payload or {})),
    )
    called: list = []
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: called.append(1))

    reply = handoff_to_booking(
        global_bot_user=gbu,
        tenant_id=t.id,
        master_id=master.id,
        service_id=foreign_to_master.id,
    )

    assert "нет услуги «Маникюр»" in reply.text
    assert [b["label"] for b in _buttons(reply)] == ["Классический массаж"]
    assert called == []
    payload = dict(events)["marketplace.handoff.service_unresolved"]
    assert payload["not_offered_by_master"] is True


def test_menu_longer_than_the_keyboard_budget_is_truncated_and_says_so(
    settings, monkeypatch
) -> None:
    """A long roster must not silently lose services: the keyboard is capped,
    and the text says the list is partial and keeps typing as the escape
    hatch. Silently showing 10 of 25 would be the old bug with better UI —
    the user would never learn the missing service exists."""
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = True
    t = _tenant("t-long")
    master = _master(t, name="Инна")
    for i in range(12):
        _link(
            t,
            master,
            _service(t, ayla_service_id=uuid4(), name=f"Услуга {i:02d}", slug=f"s{i:02d}"),
        )
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="511")
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: None)

    reply = handoff_to_booking(global_bot_user=gbu, tenant_id=t.id, master_id=master.id)

    buttons = _buttons(reply)
    # Capped, deterministically by name — never a random 10 of 12.
    assert [b["label"] for b in buttons] == [f"Услуга {i:02d}" for i in range(10)]
    assert "Показаны первые 10" in reply.text
    assert "напишите" in reply.text  # typing stays available for the rest
    assert "Услуга 11" not in reply.text


def test_keyboardless_channel_gets_the_same_list_as_text(settings, monkeypatch) -> None:
    """A channel that drops keyboards must still get a workable next step: the
    text repeats every button's label verbatim, so typing one back is an exact
    catalog name — the path that already resolves — instead of a guess."""
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = True
    t = _tenant("t-textonly")
    master = _master(t, name="Инна")
    _link(t, master, _service(t, ayla_service_id=uuid4(), name="Классический массаж", slug="c"))
    _link(
        t,
        master,
        _service(t, ayla_service_id=uuid4(), name="Биоэнергетический массаж", slug="bio"),
    )
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="512")
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: None)

    reply = handoff_to_booking(global_bot_user=gbu, tenant_id=t.id, master_id=master.id)

    buttons = _buttons(reply)
    assert buttons, "precondition: the keyboard-capable rendering carries buttons"
    for button in buttons:
        assert f"• {button['label']}" in reply.text
    # Text-only readers see the master and a list, not a bare keyboard header.
    assert "Инна" in reply.text
    assert reply.text.count("•") == len(buttons)


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

    # ``no_edge`` is the one case the reply can name a cause for (DRF-1070):
    # the service is real and visible in T, this master simply does not do it.
    # The rest are ungroundable-for-other-reasons misses with no offerable
    # alternative, so they stay the neutral ask.
    expected = "нет услуги" if case == "no_edge" else "напишите"
    assert expected in reply.text, case
    assert _buttons(reply) == [], case  # nothing deliverable → no dead buttons
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
    """The menu must not offer a service the stamping gate cannot deliver:
    under the Ayla flag a NULL-ayla_service_id service, tapped or typed back,
    still produces a serviceless card — offering it would keep the user in the
    loop the reply exists to break (DRF-1070: as a button that would be the
    same loop, one tap instead of one typo)."""
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = True
    t = _tenant("t-menu")
    master = _master(t, name="Анна")
    _link(t, master, _service(t, ayla_service_id=uuid4(), name="Спортивный массаж", slug="s"))
    _link(t, master, _service(t, ayla_service_id=None, name="Легаси-услуга", slug="l"))
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="508")
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: None)

    reply = handoff_to_booking(global_bot_user=gbu, tenant_id=t.id, master_id=master.id)

    assert [b["label"] for b in _buttons(reply)] == ["Спортивный массаж"]
    assert "Спортивный массаж" in reply.text
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
    never reach the skill pipeline — deterministic refusal, no dispatch.

    DRF-1473: the refusal no longer claims the context «устарел». Nothing
    here is about time — the tap's tenant is derived from the master id it
    carries, and none of these four carry one this contour can resolve.
    """
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
        assert reply.text == _UNRESOLVED_BOOKING_CALLBACK_REPLY, callback
        assert "устарел" not in reply.text, callback
    assert called == []
