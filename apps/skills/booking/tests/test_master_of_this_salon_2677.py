"""DRF-2677: a master reaches the catalog only if they are THIS salon's.

The model cannot name a foreign master: its four tools check the id against
an allow-set built from the salon's roster. The open way in is a callback
typed by hand — ``cb:book:pick_master:<id>:<svc>`` sent as ordinary text,
which ``BookingSkill.matches`` takes — and the callbacks for master, «more
dates» and day go to the schedule with no roster check. The catalog's
``specialists/{id}/slots/`` takes a master by id and carries no salon, so a
foreign master's free days and times came back.

The adapter now asks «is this master ours» before every door that sends a
``staff_id`` to the catalog. Real ``AylaYClientsAdapter`` bound to the salon,
over a catalog stand-in that behaves as the catalog does: slots for a known
(master, service) pair of ANY salon, 404 otherwise; ``specialists/{id}/``
answers a profile with the ``tenant`` stamp (uuid | null —
``InternalSpecialistDetailSerializer``, beautygo_backend ``c3f06e4``), 404 for
an id it does not know.

The foreign master is always asked about together with THEIR OWN service —
the pair the catalog does answer — so «refused» is never the catalog's 404
passing for this check.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

import pytest

from apps.catalog.models import CatalogMaster
from apps.integrations.ayla.booking_client import (
    AylaBookingRecord,
    AylaMaster,
    AylaSlot,
    BookingBadRequestError,
    BookingUnavailableError,
)
from apps.integrations.yclients.client import YClientsAPIError, YClientsUnavailableError
from apps.skills.booking.provider import AylaYClientsAdapter
from apps.skills.booking.skill import _render_date_picker, _render_part_picker
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant
from tests.support.catalog_mirror import sync_shaped

pytestmark = pytest.mark.django_db

FOREIGN_NAME = "Чужая-Мастерица"
DAY = (date.today() + timedelta(days=3)).isoformat()
SLOT_TIME = "10:40"
SLOT_ISO = f"{DAY}T{SLOT_TIME}:00+03:00"


class _Catalog:
    """The catalog as the bot's client sees it; every call recorded."""

    def __init__(self) -> None:
        self.pairs: set[tuple[str, str]] = set()
        self.profiles: dict[str, dict[str, Any]] = {}
        self.profile_error: Exception | None = None
        self.profile_calls: list[str] = []
        self.slot_calls: list[str] = []
        self.created: list[str] = []

    def knows(self, specialist_id: str, service_id: str, *, tenant: str | None, name: str) -> None:
        self.pairs.add((specialist_id, service_id))
        self.profiles[specialist_id] = {
            "id": specialist_id,
            "display_name": name,
            "tenant": tenant,
        }

    def get_masters(self, *, specialist_id: str | None = None, **_: Any) -> list[AylaMaster]:
        assert specialist_id, "the roster is not what this check reads"
        self.profile_calls.append(specialist_id)
        if self.profile_error is not None:
            raise self.profile_error
        row = self.profiles.get(specialist_id)
        if row is None:
            raise BookingBadRequestError("http_404_not_found", status_code=404, code="not_found")
        return [
            AylaMaster(
                id=row["id"],
                name=row["display_name"],
                specialization="",
                rating=0.0,
                position="",
                raw=row,
            )
        ]

    def get_available_times(self, *, specialist_id: str, date: str, service_id: str) -> list[Any]:
        self.slot_calls.append(specialist_id)
        if (specialist_id, service_id) not in self.pairs:
            raise BookingBadRequestError("http_404_not_found", status_code=404, code="not_found")
        return [AylaSlot(time=SLOT_TIME, datetime=SLOT_ISO, duration_s=3600)]

    def get_available_dates(self, *, specialist_id: str, service_id: str) -> list[str]:
        self.slot_calls.append(specialist_id)
        if (specialist_id, service_id) not in self.pairs:
            raise BookingBadRequestError("http_404_not_found", status_code=404, code="not_found")
        return [DAY]

    def create_appointment(self, *, specialist_id: str, **_: Any) -> AylaBookingRecord:
        self.created.append(specialist_id)
        return AylaBookingRecord(appointment_id=str(uuid.uuid4()), raw={})


def _master(tenant: Tenant, name: str, **extra: Any) -> CatalogMaster:
    return sync_shaped(
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=CatalogMaster.all_tenants.count() + 1,
            external_updated_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
            name=name,
            **extra,
        )
    )


@pytest.fixture
def salon(db) -> Tenant:
    return Tenant.objects.create(slug="member-2677", name="Свой салон", timezone="Europe/Moscow")


@pytest.fixture
def other_salon(db) -> Tenant:
    return Tenant.objects.create(slug="member-2677-other", name="Чужой салон")


@pytest.fixture
def own_service() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def foreign_service() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def catalog() -> _Catalog:
    return _Catalog()


@pytest.fixture
def foreign(other_salon: Tenant, foreign_service: str, catalog: _Catalog) -> str:
    """Another salon's master, in THEIR mirror and in the catalog."""
    row = _master(other_salon, FOREIGN_NAME)
    catalog.knows(str(row.pk), foreign_service, tenant=str(other_salon.id), name=FOREIGN_NAME)
    return str(row.pk)


@pytest.fixture
def adapter(salon: Tenant, catalog: _Catalog, settings: Any) -> AylaYClientsAdapter:
    settings.BOOKING_VIA_AYLA_REST = True
    return AylaYClientsAdapter(
        client=cast(Any, catalog), external_user_id="bot:max:2677", client_id="c-2677", tenant=salon
    )


def _dates(adapter: AylaYClientsAdapter, salon: Tenant, master: str, service: str) -> Any:
    """What ``cb:book:pick_master`` / ``cb:book:more_dates`` answer."""
    with tenant_scope(salon):
        return _render_date_picker(
            master_id=master,
            service_id=service,
            yclients=adapter,
            tenant_id=str(salon.id),
            tenant=salon,
        )


def _times(adapter: AylaYClientsAdapter, salon: Tenant, master: str, service: str) -> Any:
    """What ``cb:book:pick_date`` answers."""
    with tenant_scope(salon):
        return _render_part_picker(
            master_id=master,
            service_id=service,
            date=DAY,
            yclients=adapter,
            tenant_id=str(salon.id),
            tenant=salon,
        )


def _create(adapter: AylaYClientsAdapter, master: str, service: str) -> Any:
    return adapter.create_record(
        staff_id=master,
        services=[service],
        datetime=SLOT_ISO,
        client_phone="",
        client_name="",
    )


def _seen(result: Any) -> tuple[Any, ...]:
    """Everything of a reply a person on the other side can see."""
    return (result.should_handoff, result.handoff_reason, result.reply_text, result.action_data)


def _callbacks(result: Any) -> list[str]:
    out: list[str] = []
    for attachment in (result.action_data or {}).get("attachments", []):
        for row in attachment.get("payload", {}).get("buttons", []):
            for button in row if isinstance(row, list) else [row]:
                if isinstance(button, dict) and button.get("callback"):
                    out.append(button["callback"])
    return out


_LIVE_DOORS = pytest.mark.parametrize("door", [_dates, _times], ids=["dates", "times"])


# ── a foreign master is an invented id ───────────────────────────────────────


@_LIVE_DOORS
def test_a_foreign_master_is_answered_as_an_invented_id(
    door, adapter, salon, catalog, foreign, foreign_service
):
    invented = str(uuid.uuid4())

    to_foreign = door(adapter, salon, foreign, foreign_service)
    to_invented = door(adapter, salon, invented, foreign_service)

    assert _seen(to_foreign) == _seen(to_invented)
    assert to_foreign.should_handoff is True
    assert catalog.slot_calls == []
    for leak in (FOREIGN_NAME, DAY, SLOT_TIME, foreign):
        assert leak not in to_foreign.reply_text
        assert leak not in str(to_foreign.action_data)


def test_a_foreign_master_is_not_booked(adapter, catalog, foreign, foreign_service):
    with pytest.raises(YClientsAPIError) as to_foreign:
        _create(adapter, foreign, foreign_service)
    with pytest.raises(YClientsAPIError) as to_invented:
        _create(adapter, str(uuid.uuid4()), foreign_service)

    assert type(to_foreign.value) is type(to_invented.value) is YClientsAPIError
    assert str(to_foreign.value) == str(to_invented.value)
    assert catalog.created == []


def test_the_log_tells_the_three_refusals_apart(
    adapter, salon, catalog, foreign, foreign_service, caplog
):
    unstamped, invented = str(uuid.uuid4()), str(uuid.uuid4())
    catalog.knows(unstamped, foreign_service, tenant=None, name="Без штампа")
    caplog.set_level(logging.WARNING, logger="apps.skills.booking.provider")

    seen = [
        _seen(_times(adapter, salon, m, foreign_service)) for m in (foreign, unstamped, invented)
    ]

    assert seen[0] == seen[1] == seen[2]
    assert catalog.slot_calls == []
    reasons = [
        r.getMessage().split("reason=")[1].split()[0]
        for r in caplog.records
        if "booking.master.not_of_this_salon" in r.getMessage()
    ]
    assert reasons == ["membership_foreign", "membership_unverifiable", "membership_unknown"]


def test_an_unreadable_profile_keeps_the_door_shut(adapter, catalog, foreign, foreign_service):
    """Fail-closed: an outage is an outage — not «ours», and not «no such master»."""
    catalog.profile_error = BookingUnavailableError("http_503")

    with pytest.raises(YClientsUnavailableError):
        adapter.get_available_times(staff_id=foreign, date=DAY, service_ids=[foreign_service])

    assert catalog.slot_calls == []


# ── the salon's own master is not caught by the check ────────────────────────


@_LIVE_DOORS
def test_own_master_the_mirror_has_not_caught_up_with(door, adapter, salon, catalog, own_service):
    """No mirror row at all, the catalog stamps this salon → as usual."""
    unmirrored = str(uuid.uuid4())
    catalog.knows(unmirrored, own_service, tenant=str(salon.id), name="Новая")

    result = door(adapter, salon, unmirrored, own_service)

    assert result.should_handoff is False
    assert _callbacks(result), result.reply_text
    assert catalog.profile_calls == [unmirrored]
    assert catalog.slot_calls == [unmirrored]


def test_own_unmirrored_master_is_booked(adapter, salon, catalog, own_service):
    unmirrored = str(uuid.uuid4())
    catalog.knows(unmirrored, own_service, tenant=str(salon.id), name="Новая")

    _create(adapter, unmirrored, own_service)

    assert catalog.created == [unmirrored]


@_LIVE_DOORS
def test_own_master_out_of_sale_is_still_own(door, adapter, salon, catalog, own_service):
    """«Which salon», not «is it sold»: the mirror row answers, with no network."""
    row = _master(salon, "Не в продаже", is_active=False)
    catalog.pairs.add((str(row.pk), own_service))

    result = door(adapter, salon, str(row.pk), own_service)

    assert result.should_handoff is False
    assert catalog.profile_calls == []
    assert catalog.slot_calls == [str(row.pk)]


@_LIVE_DOORS
@pytest.mark.parametrize("named_by", ["mirror_pk", "catalog_id"])
def test_own_glued_master_by_either_id(door, named_by, adapter, salon, catalog, own_service):
    """Mirror pk != catalog id (DRF-1933): ours by either, the catalog gets its own."""
    catalog_id = str(uuid.uuid4())
    row = CatalogMaster.all_tenants.create(
        tenant=salon,
        external_id=2677,
        external_updated_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
        name="Соло",
        catalog_specialist_id=catalog_id,
    )
    assert str(row.pk) != catalog_id
    catalog.pairs.add((catalog_id, own_service))
    named = str(row.pk) if named_by == "mirror_pk" else catalog_id

    result = door(adapter, salon, named, own_service)

    assert result.should_handoff is False
    assert _callbacks(result), result.reply_text
    assert catalog.profile_calls == []
    assert catalog.slot_calls == [catalog_id]


def test_the_plain_manager_does_not_hide_the_unsold(salon, other_salon):
    """What ``_mirror_places_here`` stands on: ``objects`` is the salon's rows,
    sold or not. ``.bookable()`` is the positive control — the sale filter
    exists and does hide the row."""
    hidden = _master(salon, "Не в продаже", is_active=False)
    _master(other_salon, FOREIGN_NAME)

    with tenant_scope(salon):
        plain = set(CatalogMaster.objects.values_list("pk", flat=True))
        bookable = set(CatalogMaster.objects.bookable().values_list("pk", flat=True))

    assert plain == {hidden.pk}
    assert bookable == set()


# ── the dead door ────────────────────────────────────────────────────────────


def test_the_roster_read_takes_no_master_id(adapter):
    """``get_staff(staff_id=…)`` read ``specialists/{id}/`` with no salon in
    it and had no caller; the parameter is gone rather than ignored."""
    with pytest.raises(TypeError):
        adapter.get_staff(staff_id=str(uuid.uuid4()))  # type: ignore[call-arg]
