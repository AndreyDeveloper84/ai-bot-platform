"""Miniapp create_booking on the Ayla path (AMD-019 / D6).

Pins: payment_required passthrough (default FALSE for miniapp),
verbatim Ayla status in the response, C1-neutral 409 on
SUBSCRIPTION_PAST_DUE, fail-closed grounding, and the local default
path staying untouched when BOOKING_VIA_AYLA_REST is off.

DRF-1057 adds the identity-resolve pins: an unlinked person gets the
link established here (same ``ensure_ayla_link`` the chat path uses)
instead of an unconditional 403; a linked person costs no network; a
failed resolve still refuses honestly; and the subject is never taken
from the request body.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import time as time_module
import uuid
from typing import Any
from urllib.parse import urlencode

import pytest
from django.test import Client as DjangoClient, override_settings

from apps.catalog.models import CatalogMaster, CatalogService
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import (
    AylaBookingRecord,
    BookingBadRequestError,
)
from apps.integrations.ayla.identity_client import IdentityResolveError, ResolvedIdentity
from apps.tenancy.models import Tenant

# A fixed calendar date silently rots: it is in the future when the test
# is written and in the past forever after, at which point the view
# rejects the booking (400) and the assertion fails for a reason that
# has nothing to do with what the test covers. `2026-08-01` did exactly
# that, and the failure only became visible once CI started running the
# whole `apps/` suite (DRF-1121) instead of 41 files.
#
# Anchored 14 days out so the window stays clear of month/DST edges,
# with the weekday derived from the date rather than asserted about it —
# the schedule fixture below opens whichever day this lands on.
_VISIT_DATE = dt.date.today() + dt.timedelta(days=14)
_VISIT_AT = f"{_VISIT_DATE.isoformat()}T14:00:00+03:00"


pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-xyz"
AYLA_UID = uuid.uuid4()
SERVICE_AYLA_ID = uuid.uuid4()
MASTER_AYLA_ID = uuid.uuid4()


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
    settings.MAX_BOT_TENANT_SLUG = "ayla-create-test"
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="ayla-create-test", name="Ayla Create")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="12345",
        chat_id="12345",
        ayla_user_id=AYLA_UID,
    )


@pytest.fixture
def master(tenant) -> CatalogMaster:
    from django.utils import timezone as tz

    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=tz.now(),
        name="Ольга",
        specialization="Маникюр",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        ayla_user_id=MASTER_AYLA_ID,
    )


@pytest.fixture
def service(tenant) -> CatalogService:
    from django.utils import timezone as tz

    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_updated_at=tz.now(),
        name="Маникюр",
        slug="manikyur",
        duration_min=60,
        is_active=True,
        ayla_service_id=SERVICE_AYLA_ID,
    )


class _StubAylaClient:
    def __init__(self, *, exc: Exception | None = None) -> None:
        self.exc = exc
        self.calls: list[dict] = []

    def create_appointment(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return AylaBookingRecord(
            appointment_id=str(uuid.uuid4()),
            raw={"id": str(uuid.uuid4()), "status": kwargs.get("_status", "confirmed")},
        )


@pytest.fixture
def stub_client(monkeypatch) -> _StubAylaClient:
    stub = _StubAylaClient()
    monkeypatch.setattr(
        "apps.integrations.ayla.booking_client.get_ayla_booking_client",
        lambda: stub,
    )
    return stub


@pytest.fixture
def stub_resolve(monkeypatch) -> Any:
    """Patch the identity HTTP leg (DRF-1057).

    Patched at the client module, because ``ensure_ayla_link`` imports
    ``resolve_identity`` lazily from there — the same convention as
    ``apps/identity/services/tests/test_ayla_link.py``.
    """

    calls: list[str] = []
    state: dict[str, Any] = {"uuid": uuid.uuid4(), "error": None}

    def _fake(external_user_id: str) -> ResolvedIdentity:
        calls.append(external_user_id)
        if state["error"] is not None:
            raise state["error"]
        return ResolvedIdentity(ayla_user_id=state["uuid"], is_proxy=True)

    monkeypatch.setattr(
        "apps.integrations.ayla.identity_client.resolve_identity", _fake, raising=True
    )
    return type("Stub", (), {"calls": calls, "state": state})()


def _post_as(
    client: DjangoClient,
    service,
    master,
    *,
    user_id: str,
    extra: dict | None = None,
):
    body = {
        "service_id": str(service.id),
        "master_id": str(master.id),
        "visit_at": _VISIT_AT,
    }
    if extra:
        body.update(extra)
    return client.post(
        "/api/v1/customer/bookings",
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header(user_id),
    )


def _post(client: DjangoClient, service, master, extra: dict | None = None):
    return _post_as(client, service, master, user_id="12345", extra=extra)


class TestPassthrough:
    def test_default_false(self, client, bot_user, service, master, stub_client) -> None:
        resp = _post(client, service, master)
        assert resp.status_code == 201
        call = stub_client.calls[0]
        assert call["payment_required"] is False
        assert call["specialist_id"] == str(master.id)
        # #1027: SpecialistProfile UUID (= CatalogMaster.id), NEVER the
        # Ayla User UUID (that one is the AMD-005 billing key only).
        assert call["specialist_id"] != str(master.ayla_user_id)
        assert call["service_id"] == str(SERVICE_AYLA_ID)
        assert call["client_id"] == str(AYLA_UID)
        assert resp.json()["booking"]["status"] == "confirmed"

    def test_true_passed_verbatim(self, client, bot_user, service, master, stub_client) -> None:
        resp = _post(client, service, master, {"payment_required": True})
        assert resp.status_code == 201
        assert stub_client.calls[0]["payment_required"] is True

    def test_false_passed_verbatim(self, client, bot_user, service, master, stub_client) -> None:
        resp = _post(client, service, master, {"payment_required": False})
        assert resp.status_code == 201
        assert stub_client.calls[0]["payment_required"] is False

    def test_awaiting_payment_status_verbatim(
        self, client, bot_user, service, master, monkeypatch
    ) -> None:
        record = AylaBookingRecord(
            appointment_id=str(uuid.uuid4()),
            raw={"id": str(uuid.uuid4()), "status": "awaiting_payment"},
        )
        monkeypatch.setattr(
            "apps.integrations.ayla.booking_client.get_ayla_booking_client",
            lambda: type("C", (), {"create_appointment": lambda self, **kw: record})(),
        )
        resp = _post(client, service, master, {"payment_required": True})
        assert resp.status_code == 201
        assert resp.json()["booking"]["status"] == "awaiting_payment"

    def test_idempotency_key_differs_by_payment_required(
        self, client, bot_user, service, master, stub_client
    ) -> None:
        _post(client, service, master, {"payment_required": False})
        _post(client, service, master, {"payment_required": True})
        keys = [c["idempotency_key"] for c in stub_client.calls]
        assert len(keys) == 2 and keys[0] != keys[1]


class TestErrors:
    def test_c1_conflict_maps_unavailable(
        self, client, bot_user, service, master, stub_client
    ) -> None:
        stub_client.exc = BookingBadRequestError(
            "http_409_subscription_past_due",
            status_code=409,
            code="SUBSCRIPTION_PAST_DUE",
        )
        resp = _post(client, service, master)
        assert resp.status_code == 409
        assert resp.json()["error"] == "unavailable"
        assert "past_due" not in resp.text
        assert "subscription" not in resp.text.lower()

    def test_grounding_miss_fails_closed(
        self, client, bot_user, tenant, master, stub_client
    ) -> None:
        from django.utils import timezone as tz

        unlinked = CatalogService.all_tenants.create(
            tenant=tenant,
            external_updated_at=tz.now(),
            name="Не синкано",
            slug="unsynced",
            is_active=True,
            ayla_service_id=None,
        )
        resp = _post(client, unlinked, master)
        assert resp.status_code == 409
        assert resp.json()["error"] == "service_unbookable"
        assert stub_client.calls == []

    def test_unresolvable_user_403(
        self, client, tenant, service, master, stub_client, stub_resolve
    ) -> None:
        """DRF-1057: 403 is the LAST resort — only after the resolve is
        attempted and Ayla could not answer."""
        BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="777", ayla_user_id=None
        )
        stub_resolve.state["error"] = IdentityResolveError("network: ReadTimeout")

        resp = _post_as(client, service, master, user_id="777")

        assert resp.status_code == 403
        assert resp.json()["error"] == "identity_not_linked"
        assert stub_client.calls == []
        # …and the resolve really was attempted (the pre-1057 defect was
        # refusing without ever trying).
        assert stub_resolve.calls == ["bot:max:777"]


class TestIdentityResolve:
    """DRF-1057 — the Mini App must establish the Ayla link the same way the
    chat path does (``get_booking_provider`` → ``ensure_ayla_link``)."""

    def test_unlinked_user_gets_linked_and_books(
        self, client, tenant, service, master, stub_client, stub_resolve
    ) -> None:
        unlinked = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="777", ayla_user_id=None
        )

        resp = _post_as(client, service, master, user_id="777")

        assert resp.status_code == 201
        # The booking really went to Ayla, bound to the freshly resolved subject.
        assert stub_client.calls[0]["client_id"] == str(stub_resolve.state["uuid"])
        # …and the link was persisted, so the next call costs no network.
        unlinked.refresh_from_db()
        assert unlinked.ayla_user_id == stub_resolve.state["uuid"]

    def test_second_booking_costs_no_network(
        self, client, tenant, service, master, stub_client, stub_resolve
    ) -> None:
        BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="777", ayla_user_id=None
        )

        _post_as(client, service, master, user_id="777")
        _post_as(client, service, master, user_id="777", extra={"payment_required": True})

        assert len(stub_client.calls) == 2
        assert len(stub_resolve.calls) == 1  # resolved once, then cache_hit

    def test_already_linked_user_never_resolves(
        self, client, bot_user, service, master, stub_client, stub_resolve
    ) -> None:
        resp = _post(client, service, master)

        assert resp.status_code == 201
        assert stub_client.calls[0]["client_id"] == str(AYLA_UID)
        assert stub_resolve.calls == []  # no network for a linked person

    def test_body_supplied_identity_is_ignored(
        self, client, bot_user, service, master, stub_client, stub_resolve
    ) -> None:
        """DRF-1036 boundary: the subject comes from the SESSION BotUser.

        A client-supplied ``ayla_user_id`` in the body must never select the
        subject — the create still binds to the session identity.
        """
        foreign = str(uuid.uuid4())

        resp = _post(client, service, master, {"ayla_user_id": foreign})

        assert resp.status_code == 201
        assert stub_client.calls[0]["client_id"] == str(AYLA_UID)
        assert stub_client.calls[0]["client_id"] != foreign
        assert stub_client.calls[0]["external_user_id"] == "bot:max:12345"
        assert stub_resolve.calls == []

    def test_unlinked_body_supplied_identity_is_ignored(
        self, client, tenant, service, master, stub_client, stub_resolve
    ) -> None:
        """Same boundary on the resolve path: a body id cannot pre-empt or
        steer the resolve, and cannot end up on the wire."""
        BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="777", ayla_user_id=None
        )
        foreign = str(uuid.uuid4())

        resp = _post_as(client, service, master, user_id="777", extra={"ayla_user_id": foreign})

        assert resp.status_code == 201
        assert stub_resolve.calls == ["bot:max:777"]  # subject from the session row
        assert stub_client.calls[0]["client_id"] == str(stub_resolve.state["uuid"])
        assert stub_client.calls[0]["client_id"] != foreign


class TestLocalPathUnchanged:
    @override_settings(BOOKING_VIA_AYLA_REST=False)
    def test_flag_off_keeps_local_path(
        self, client, bot_user, service, master, stub_client
    ) -> None:
        """payment_required is accepted but inert when the flag is off —
        the local create path is untouched and Ayla is never called."""
        import datetime as dt

        from apps.catalog.models import MasterService
        from apps.scheduling.models import Weekday, WorkingHours

        MasterService.all_tenants.create(tenant=bot_user.tenant, master=master, service=service)
        # Open whichever weekday `_VISIT_DATE` happens to fall on, rather
        # than naming one. The previous version hardcoded SATURDAY to match
        # a hardcoded 2026-08-01; when the date rotted the two drifted apart
        # independently, so the same test could fail for either reason.
        # `Weekday` is Monday=0 like `date.weekday()`, so the index maps
        # straight across.
        WorkingHours.all_tenants.create(
            tenant=bot_user.tenant,
            master=master,
            day_of_week=Weekday(_VISIT_DATE.weekday()),
            start_time=dt.time(9, 0),
            end_time=dt.time(18, 0),
            is_working=True,
        )
        resp = _post(client, service, master, {"payment_required": True})
        assert resp.status_code == 201
        assert stub_client.calls == []
        assert resp.json()["booking"]["status"] == "confirmed"
