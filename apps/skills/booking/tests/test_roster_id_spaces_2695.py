"""DRF-2695: one master, two ids — both must name the same master everywhere.

The roster comes from the catalog and carries catalog ids. Inside the bot a
master is the mirror row's primary key: the concierge card puts it into
``cb:book:pick_master`` and every button drawn after that keeps it. For a
master the sync created the two are equal; for a glued invite or a solo
master they differ (DRF-1933). Measured on ``f9f79a1d`` for such a master:

* named by the mirror key — the roster allow-set does not know the id:
  ``invalid_master_id`` at the part-of-day tap, ``unknown_master`` at the
  slot tap, after the days and times were drawn;
* named by the catalog id — the roster knows it, but the local edge is keyed
  by the mirror row, is «not found», and the health gate, closed on unknown,
  hands every booking to a human — for a service whose verdict is «no check».

Real ``BookingSkill.handle`` over the real ``AylaYClientsAdapter`` bound to
the salon, over a catalog stand-in whose roster names masters by catalog id.
The control is a master the sync created (one id): it worked before and must
answer the same.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, cast
from unittest.mock import patch

import pytest

from apps.booking.models import PendingBookingAction
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import AylaMaster, AylaService, AylaSlot
from apps.integrations.ayla.offer_refusal import client_text_for
from apps.llm.protocol import ToolCall
from apps.skills.base import SkillContext
from apps.skills.booking.provider import AylaYClientsAdapter
from apps.skills.booking.skill import (
    BookingSkill,
    _offer_refusal_for_edge,
    _resolved_health_check_for_edge,
    _roster_lookup,
)
from apps.skills.booking.tests.test_skill import (  # noqa: F401 — _isolated_env: настройки LLM
    _completion,
    _isolated_env,
    _patch_provider_complete,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

SVC = "0c5f0000-0000-4000-8000-000000002695"
NAME = "Соло-Мастерица"
DAY = (date.today() + timedelta(days=3)).isoformat()
MORNING = f"{DAY}T10:40:00+03:00"
EVENING = f"{DAY}T18:40:00+03:00"
_TS = datetime(2026, 9, 30, tzinfo=timezone.utc)

NAMINGS = pytest.mark.parametrize("named_by", ["mirror_pk", "catalog_id"])
SHAPES = pytest.mark.parametrize("shape", ["glued", "synced"])


class _Catalog:
    """The catalog behind the adapter: the roster is in catalog ids."""

    def __init__(self) -> None:
        self.roster: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []

    def get_services(self) -> list[AylaService]:
        return [
            AylaService(
                id=SVC,
                title="Маникюр",
                price_min=1500.0,
                price_max=1500.0,
                duration_s=3600,
                category_id=None,
            )
        ]

    def get_masters(self, *, specialist_id: str | None = None, **_: Any) -> list[AylaMaster]:
        assert specialist_id is None, "every master here has a mirror row — no profile read"
        return [
            AylaMaster(id=i, name=n, specialization="", rating=0.0, position="")
            for i, n in self.roster
        ]

    def get_available_times(self, *, specialist_id: str, date: str, service_id: str) -> list[Any]:
        self.sent.append(("times", specialist_id))
        return [
            AylaSlot(time="10:40", datetime=MORNING, duration_s=3600),
            AylaSlot(time="18:40", datetime=EVENING, duration_s=3600),
        ]

    def get_specialist_service_edges(self, *, specialist_id: str, service_id: str) -> list[dict]:
        self.sent.append(("edges", specialist_id))
        return []


@pytest.fixture(autouse=True)
def _ayla(settings: Any) -> None:
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def salon(db) -> Tenant:
    return Tenant.objects.create(slug="ids-2695", name="Свой салон", timezone="Europe/Moscow")


@pytest.fixture
def bot_user(salon: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=salon,
        channel="max",
        channel_user_id="bu-2695",
        chat_id="bu-2695",
        phone="79990000000",
        client_name="Анна",
        ayla_user_id=uuid.uuid4(),
    )


@pytest.fixture
def conversation(salon: Tenant, bot_user: BotUser) -> Conversation:
    return Conversation.all_tenants.create(tenant=salon, bot_user=bot_user)


@pytest.fixture
def service(salon: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=salon,
        external_id=26950,
        external_updated_at=_TS,
        slug="manicure-2695",
        name="Маникюр",
        duration_min=60,
        is_active=True,
        ayla_service_id=uuid.UUID(SVC),
    )


@pytest.fixture
def catalog() -> _Catalog:
    return _Catalog()


def _mirror_row(
    tenant: Tenant, *, shape: str, catalog_id: uuid.UUID | None = None
) -> CatalogMaster:
    """``glued``: primary key != catalog id. ``synced``: one id for both."""
    row = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=CatalogMaster.all_tenants.count() + 1,
        external_updated_at=_TS,
        name=NAME,
        is_active=True,
    )
    column = (catalog_id or uuid.uuid4()) if shape == "glued" else row.pk
    CatalogMaster.all_tenants.filter(pk=row.pk).update(catalog_specialist_id=column)
    row.refresh_from_db()
    assert (row.pk != row.catalog_specialist_id) is (shape == "glued")
    return row


def _master(
    salon: Tenant,
    service: CatalogService,
    catalog: _Catalog,
    *,
    shape: str,
    on_roster: bool = True,
    **edge: Any,
) -> CatalogMaster:
    row = _mirror_row(salon, shape=shape)
    if on_roster:
        catalog.roster.append((str(row.catalog_specialist_id), NAME))
    edge.setdefault("resolved_requires_health_check", False)
    MasterService.all_tenants.create(tenant=salon, master=row, service=service, **edge)
    return row


def _named(row: CatalogMaster, named_by: str) -> str:
    return str(row.pk) if named_by == "mirror_pk" else str(row.catalog_specialist_id)


def _handle(
    text: str,
    *,
    salon: Tenant,
    bot_user: BotUser,
    conversation: Conversation,
    catalog: _Catalog,
    completions: list[Any] | None = None,
) -> Any:
    adapter = AylaYClientsAdapter(
        client=cast(Any, catalog), external_user_id="bot:max:2695", client_id="c-2695", tenant=salon
    )
    context = SkillContext(
        conversation=conversation, bot_user=bot_user, message_text=text, trace_id="t-2695"
    )
    with (
        patch("apps.skills.booking.provider.get_booking_provider", return_value=adapter),
        _patch_provider_complete(completions or []),
        tenant_scope(salon),
    ):
        return BookingSkill().handle(context)


def _callbacks(result: Any) -> list[str]:
    out: list[str] = []
    for attachment in (result.action_data or {}).get("attachments", []):
        for row in attachment.get("payload", {}).get("buttons", []):
            for button in row if isinstance(row, list) else [row]:
                if isinstance(button, dict) and button.get("callback"):
                    out.append(button["callback"])
    return out


def _pending() -> list[dict[str, Any]]:
    return list(PendingBookingAction.all_tenants.values_list("payload", flat=True))


@pytest.fixture
def handle(salon, bot_user, conversation, catalog):
    def _run(text: str, completions: list[Any] | None = None) -> Any:
        return _handle(
            text,
            salon=salon,
            bot_user=bot_user,
            conversation=conversation,
            catalog=catalog,
            completions=completions,
        )

    return _run


# ── the three places the roster is compared with the id in hand ──────────────


@SHAPES
@NAMINGS
def test_the_part_of_day_tap_lists_the_times(shape, named_by, handle, salon, service, catalog):
    """``cb:book:pick_part`` → ``show_slots`` against the roster allow-set."""
    row = _master(salon, service, catalog, shape=shape)
    named = _named(row, named_by)

    result = handle(f"cb:book:pick_part:{named}:{DAY}:any:{SVC}")

    assert result.should_handoff is False, result.reply_text
    assert f"cb:book:pick_slot:{named}:{SVC}:{MORNING}" in _callbacks(result)
    assert catalog.sent == [("times", str(row.catalog_specialist_id))]


@SHAPES
@NAMINGS
def test_the_slot_tap_previews_the_same_master(shape, named_by, handle, salon, service, catalog):
    """``cb:book:pick_slot``: roster check, edge gates, name in the preview."""
    row = _master(salon, service, catalog, shape=shape)
    named = _named(row, named_by)

    result = handle(f"cb:book:pick_slot:{named}:{SVC}:{MORNING}")

    assert result.should_handoff is False, result.reply_text
    assert NAME in result.reply_text
    assert [(p["master_id"], p["master_name"]) for p in _pending()] == [(named, NAME)]
    assert {sid for _door, sid in catalog.sent} == {str(row.catalog_specialist_id)}


@SHAPES
@NAMINGS
def test_the_models_confirm_previews_the_same_master(
    shape, named_by, handle, salon, service, catalog
):
    """``confirm_booking`` named by the model: the gate before it, the roster in it."""
    row = _master(salon, service, catalog, shape=shape)
    named = _named(row, named_by)
    call = ToolCall(
        id="c1",
        name="confirm_booking",
        arguments={"master_id": named, "service_id": SVC, "slot_datetime": MORNING},
    )

    result = handle("запишите", [_completion(tool_calls=[call]), _completion(text="ок")])

    assert result.should_handoff is False, result.reply_text
    assert [(p["master_id"], p["master_name"]) for p in _pending()] == [(named, NAME)]


# ── the alias admits nobody the roster does not list ─────────────────────────


@NAMINGS
def test_a_master_off_the_roster_is_still_refused(named_by, handle, salon, service, catalog):
    """The mirror holds the row, the catalog's roster does not list the
    master: neither id is let through."""
    listed = _master(salon, service, catalog, shape="glued")
    unlisted = _master(salon, service, catalog, shape="glued", on_roster=False)
    assert [i for i, _ in catalog.roster] == [str(listed.catalog_specialist_id)]

    result = handle(f"cb:book:pick_slot:{_named(unlisted, named_by)}:{SVC}:{MORNING}")

    assert result.should_handoff is False
    assert NAME not in result.reply_text
    assert _pending() == []
    assert catalog.sent == []


def test_another_salons_row_gets_no_alias(salon, service, catalog):
    """A row of another salon carrying the same catalog id is not this
    roster's master: the alias is read from this salon's mirror only."""
    ours = _master(salon, service, catalog, shape="glued")
    other = Tenant.objects.create(slug="ids-2695-other", name="Чужой салон")
    theirs = _mirror_row(other, shape="glued", catalog_id=ours.catalog_specialist_id)
    rows = [
        AylaMaster(id=i, name=n, specialization="", rating=0.0, position="")
        for i, n in catalog.roster
    ]

    lookup = _roster_lookup(salon, cast(Any, rows))

    assert lookup == {str(ours.catalog_specialist_id): NAME, str(ours.pk): NAME}
    assert str(theirs.pk) not in lookup


# ── the edge gates, by either id ─────────────────────────────────────────────


@SHAPES
@NAMINGS
def test_a_screened_edge_still_goes_to_a_human(shape, named_by, handle, salon, service, catalog):
    """The gate is found by either id — and still closes when it must."""
    row = _master(salon, service, catalog, shape=shape, resolved_requires_health_check=True)

    result = handle(f"cb:book:pick_slot:{_named(row, named_by)}:{SVC}:{MORNING}")

    assert (result.should_handoff, result.handoff_reason) == (
        True,
        "booking_health_check_required",
    )
    assert _pending() == []


@SHAPES
@NAMINGS
def test_an_unsellable_edge_is_still_the_refusal(shape, named_by, handle, salon, service, catalog):
    row = _master(
        salon,
        service,
        catalog,
        shape=shape,
        sellable=False,
        unsellable_reason="price_below_minimum",
    )

    result = handle(f"cb:book:pick_slot:{_named(row, named_by)}:{SVC}:{MORNING}")

    assert (result.should_handoff, result.reply_text) == (
        False,
        client_text_for("price_below_minimum"),
    )
    assert _pending() == []


@NAMINGS
def test_no_edge_keeps_the_gate_closed(named_by, salon, service):
    """Fail-closed is untouched: no edge in THIS salon is «unknown», by either id."""
    row = _mirror_row(salon, shape="glued")
    other = Tenant.objects.create(slug="ids-2695-edge", name="Чужой салон")
    theirs = _mirror_row(other, shape="glued", catalog_id=row.catalog_specialist_id)
    their_service = CatalogService.all_tenants.create(
        tenant=other,
        external_id=26951,
        external_updated_at=_TS,
        slug="manicure-2695-other",
        name="Маникюр",
        duration_min=60,
        is_active=True,
        ayla_service_id=uuid.UUID(SVC),
    )
    MasterService.all_tenants.create(
        tenant=other, master=theirs, service=their_service, resolved_requires_health_check=False
    )
    named = _named(row, named_by)

    assert _resolved_health_check_for_edge(salon, named, SVC) is None
    assert _offer_refusal_for_edge(salon, named, SVC) is None
    assert _resolved_health_check_for_edge(other, str(theirs.pk), SVC) is False
