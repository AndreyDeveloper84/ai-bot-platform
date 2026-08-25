"""Characterization: the Mini App create key is derived, deterministic, and narrow.

### Why this file exists, and why it has no red run

DRF-1066 was filed believing the 14.08 double booking happened because
the Mini App never sent an idempotency key. It does. The key is not
sent by the client at all — ``_create_booking_via_ayla`` *derives* it
server-side from the person, the master, the service, the visit time
and the payment choice, and a client that wanted to defeat it could
not: there is no field in the request body that reaches the seed.

So there is nothing here to fix, and no honest failing run to show.
What the behaviour does not have is a **guard**. It is four lines of
`hashlib` inside a 200-line view; adding ``time.time()`` or a
``uuid4()`` to that seed would delete the protection in one keystroke
and every existing test would stay green — the create tests assert the
call reached Ayla, never that a repeat of it collapses.

That regression has already happened once in this codebase, in this
exact shape: DRF-1232, where "a fresh idempotency key was invented per
request and a unique constraint stood but never triggered"
(``apps/integrations/ayla/booking_client.py``). A unique constraint
nobody can trip is indistinguishable from no constraint. This file is
the tripwire that was missing then.

### What is asserted, and what is deliberately not

Behaviour at our boundary, never the seed's composition. Nothing here
knows that the key is a SHA-256, that it is 32 characters, or which
fields feed it — a test that read the seed would go red on any lawful
refactor of it while still passing if a nonce were added alongside.
What it asserts is the property the seed exists to produce: **identical
create requests must carry an identical key, and materially different
ones must not.**

The upstream stub models Ayla's documented ``X-Idempotency-Key``
contract (same key → the original appointment, no second row), so the
tests read as booking outcomes rather than as header comparisons. That
is the level the incident was argued at.

### The positive guard (per the DRF-1411 rule)

"The second request created nothing" is a negative claim, and a
negative claim passes for free on a fixture where nothing is created at
all. Every such assertion here is paired, on the same fixture and in
the same test, with the positive one it depends on: the *first* request
did create a booking. ``test_first_request_really_creates`` states it
alone as well, so a fixture that silently stops booking takes this file
red instead of quietly making it vacuous.

### What this file also puts on the record

``test_different_requests_are_not_collapsed`` is not a formality. It
reproduces the 14.08 incident's actual shape — two bookings, different
services, different masters — and pins that idempotency correctly lets
it through. Those were two genuine intents from a person who did not
know the first had worked, not one intent delivered twice. No
idempotency key may collapse them, and if some later change made this
test pass by over-collapsing, the bug would be worse than the one
DRF-1066 was opened for: it would silently swallow real second
bookings.

The fix for the incident is therefore not here. It is
``apps/booking/client_notify.py`` — telling the person in chat, off the
booking event, that the tap worked.

Dates are relative for the reason ``test_create_booking_ayla`` spells
out: a pinned visit date is a test that schedules its own failure.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest
from django.test import Client as DjangoClient

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import AylaBookingRecord
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-idem"
TENANT_SLUG = "ayla-idem-test"
AYLA_UID = uuid.uuid4()
SALON_TZ = ZoneInfo("Europe/Moscow")


def visit_at(*, days: int = 7, hour: int = 14) -> datetime:
    """A visit time comfortably in the future, named as such.

    Relative on purpose — see the module docstring. ``create_booking``
    refuses a visit in the past, so a literal date turns this whole
    file red on the day it passes.
    """

    return (datetime.now(SALON_TZ) + timedelta(days=days)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str = "12345") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Мария"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _settings(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.MAX_BOT_TENANT_SLUG = TENANT_SLUG
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug=TENANT_SLUG, name="Ayla Idem")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="12345",
        chat_id="12345",
        ayla_user_id=AYLA_UID,
    )


def _make_master(tenant, name: str) -> CatalogMaster:
    from django.utils import timezone as tz

    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=tz.now(),
        name=name,
        specialization="Маникюр",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        ayla_user_id=uuid.uuid4(),
    )


def _make_service(tenant, master, name: str, slug: str) -> CatalogService:
    from django.utils import timezone as tz

    svc = CatalogService.all_tenants.create(
        tenant=tenant,
        external_updated_at=tz.now(),
        name=name,
        slug=slug,
        duration_min=60,
        is_active=True,
        ayla_service_id=uuid.uuid4(),
    )
    # DRF-1164 — a service nobody performs is refused before the Ayla
    # branch; without the mapping these tests would measure that gate.
    MasterService.all_tenants.create(tenant=tenant, master=master, service=svc)
    return svc


@pytest.fixture
def master(tenant) -> CatalogMaster:
    return _make_master(tenant, "Ольга")


@pytest.fixture
def service(tenant, master) -> CatalogService:
    return _make_service(tenant, master, "Маникюр", "manikyur")


class _DedupingAylaClient:
    """Upstream that honours ``X-Idempotency-Key`` the way Ayla documents it.

    A repeated key returns the appointment the first call made and
    creates nothing new. Modelling it here is what lets the tests below
    assert *bookings* — the thing the incident was about — instead of
    comparing header strings, while still going red the moment the key
    stops being stable.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._by_key: dict[str, AylaBookingRecord] = {}

    @property
    def created_appointment_ids(self) -> set[str]:
        """Distinct appointments that actually came into existence."""

        return {r.appointment_id for r in self._by_key.values()}

    def create_appointment(self, **kwargs) -> AylaBookingRecord:
        self.calls.append(kwargs)
        key = kwargs.get("idempotency_key")
        # A missing key is the regression this file guards against, so
        # it must never quietly behave like a present one.
        assert key, "the Mini App create must send an idempotency key"
        if key in self._by_key:
            return self._by_key[key]
        record = AylaBookingRecord(
            appointment_id=str(uuid.uuid4()),
            raw={"id": str(uuid.uuid4()), "status": "confirmed"},
        )
        self._by_key[key] = record
        return record


@pytest.fixture
def ayla(monkeypatch) -> _DedupingAylaClient:
    stub = _DedupingAylaClient()
    monkeypatch.setattr(
        "apps.integrations.ayla.booking_client.get_ayla_booking_client",
        lambda: stub,
    )
    return stub


def _book(
    client: DjangoClient,
    *,
    service,
    master,
    when: datetime,
    payment_required: bool = False,
):
    return client.post(
        "/api/v1/customer/bookings",
        data=json.dumps(
            {
                "service_id": str(service.id),
                "master_id": str(master.id),
                "visit_at": when.isoformat(),
                "payment_required": payment_required,
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header(),
    )


def _appointment_id(resp) -> str:
    return resp.json()["booking"]["id"]


class TestRepeatOfTheSameRequest:
    """One intent, delivered more than once, must stay one booking."""

    def test_first_request_really_creates(
        self, client, bot_user, service, master, ayla
    ) -> None:
        """The positive guard the negative assertions below stand on.

        Stated on its own so that a fixture which stops creating
        bookings fails loudly here, instead of making every "did not
        create a second one" assertion in this file vacuously true.
        """

        resp = _book(client, service=service, master=master, when=visit_at())

        assert resp.status_code == 201
        assert _appointment_id(resp)
        assert len(ayla.created_appointment_ids) == 1

    def test_repeat_returns_the_same_booking_and_makes_no_second_one(
        self, client, bot_user, service, master, ayla
    ) -> None:
        """The claim, paired with its positive guard on one fixture.

        A retry, a reload, a second device: the same person asking for
        the same master, service and time. Whatever the transport did,
        the salon must end up expecting them once.
        """

        when = visit_at()

        first = _book(client, service=service, master=master, when=when)
        second = _book(client, service=service, master=master, when=when)

        # Positive half — the first request booked something.
        assert first.status_code == 201
        assert _appointment_id(first)

        # Negative half — the second added nothing, and told the caller
        # about the booking that already exists rather than refusing.
        assert second.status_code == 201
        assert _appointment_id(second) == _appointment_id(first)
        assert len(ayla.created_appointment_ids) == 1

    def test_the_key_is_derived_server_side_not_taken_from_the_body(
        self, client, bot_user, service, master, ayla
    ) -> None:
        """A client cannot steer the key, so it cannot opt out of dedupe.

        Asserted as behaviour: a body that tries to smuggle its own key
        still collapses onto the derived one. If the view ever started
        honouring a client-supplied key, a double-tapping Mini App
        could send two and book twice.
        """

        when = visit_at()
        common = {
            "service_id": str(service.id),
            "master_id": str(master.id),
            "visit_at": when.isoformat(),
        }
        for smuggled in ("attacker-key-1", "attacker-key-2"):
            resp = client.post(
                "/api/v1/customer/bookings",
                data=json.dumps({**common, "idempotency_key": smuggled}),
                content_type="application/json",
                HTTP_AUTHORIZATION=_init_data_header(),
            )
            assert resp.status_code == 201

        assert len(ayla.created_appointment_ids) == 1
        assert {c["idempotency_key"] for c in ayla.calls} == {ayla.calls[0]["idempotency_key"]}
        assert ayla.calls[0]["idempotency_key"] not in {"attacker-key-1", "attacker-key-2"}

    def test_the_key_is_stable_across_requests(
        self, client, bot_user, service, master, ayla
    ) -> None:
        """No clock, no counter, no randomness in the derivation.

        The composition of the seed is none of this test's business —
        only that repeating a request twice does not move the key. A
        ``time.time()`` or ``uuid4()`` added to it fails here, which is
        the whole reason the file exists.
        """

        when = visit_at()
        _book(client, service=service, master=master, when=when)
        _book(client, service=service, master=master, when=when)

        assert len(ayla.calls) == 2
        assert ayla.calls[0]["idempotency_key"] == ayla.calls[1]["idempotency_key"]


class TestDistinctIntentsSurvive:
    """Dedupe must be narrow — over-collapsing would swallow real bookings."""

    def test_different_requests_are_not_collapsed(
        self, client, bot_user, tenant, service, master, ayla
    ) -> None:
        """The 14.08 incident's actual shape, and why idempotency let it through.

        The customer did not send one request twice. Not knowing the
        first had worked, she walked the funnel again and picked a
        different service and a different master — two genuine intents,
        which no idempotency key may merge. This test pins that they
        stay two bookings, and in doing so records why the key was
        never what could have prevented the incident: the chat
        confirmation is (``apps/booking/client_notify.py``).
        """

        other_master = _make_master(tenant, "Карина")
        other_service = _make_service(tenant, other_master, "Педикюр", "pedikyur")

        first = _book(client, service=service, master=master, when=visit_at())
        second = _book(
            client,
            service=other_service,
            master=other_master,
            when=visit_at(hour=16),
        )

        assert first.status_code == 201
        assert second.status_code == 201
        assert _appointment_id(first) != _appointment_id(second)
        assert len(ayla.created_appointment_ids) == 2

    @pytest.mark.parametrize(
        "label,changed",
        [
            ("time", {"when": visit_at(hour=16)}),
            ("payment choice", {"payment_required": True}),
        ],
    )
    def test_one_changed_field_is_a_different_booking(
        self, client, bot_user, service, master, ayla, label, changed
    ) -> None:
        """Each field the person can actually change must move the key.

        Same master and service, one differing choice: still a separate
        request the customer meant to make. Named by what the customer
        changed rather than by seed position, so reordering the seed
        does not touch this test.
        """

        first = _book(client, service=service, master=master, when=visit_at())
        second = _book(
            client,
            service=service,
            master=master,
            when=changed.get("when", visit_at()),
            payment_required=changed.get("payment_required", False),
        )

        assert first.status_code == 201
        assert second.status_code == 201, label
        assert _appointment_id(first) != _appointment_id(second), label
        assert len(ayla.created_appointment_ids) == 2, label

    def test_two_people_booking_the_same_slot_are_two_bookings(
        self, client, bot_user, tenant, service, master, ayla
    ) -> None:
        """The key is per-person, so one customer cannot mask another.

        Slot conflicts are Ayla's to refuse on the merits; what must
        never happen is a second customer's request being silently
        answered with the first customer's appointment.
        """

        other = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="67890",
            chat_id="67890",
            ayla_user_id=uuid.uuid4(),
        )
        when = visit_at()
        body = json.dumps(
            {
                "service_id": str(service.id),
                "master_id": str(master.id),
                "visit_at": when.isoformat(),
            }
        )

        first = client.post(
            "/api/v1/customer/bookings",
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        second = client.post(
            "/api/v1/customer/bookings",
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header(other.channel_user_id),
        )

        assert first.status_code == 201
        assert second.status_code == 201
        assert ayla.calls[0]["idempotency_key"] != ayla.calls[1]["idempotency_key"]
        assert len(ayla.created_appointment_ids) == 2
