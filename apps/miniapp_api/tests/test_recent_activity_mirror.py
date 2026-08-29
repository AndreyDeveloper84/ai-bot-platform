"""GET /customer/recent-activity on the pilot's real booking store (DRF-1349).

## What this module pins

``customer_recent_activity`` used to read ``BookingRequest`` for both the
«next booking» card and the «this week» count, and its docstring claimed
the Ayla booking-event consumer filled that table. It never did:
``apps/eventbus/consumers/booking.py`` upserts ``RemoteBookingProxy`` and
its only mention of ``BookingRequest`` is a comment saying these surfaces
never write it. With ``BOOKING_VIA_AYLA_REST`` ON, ``BookingRequest`` gets
written by exactly two places in the bot's own dialog, so a visit booked
in the salon, in the Mini App or in the admin console was invisible and
the wellness dashboard told a customer with an appointment that she had
none.

## Why the fixtures look like this

The defect survived review because the existing fixtures built
``BookingRequest`` rows by hand — a shape the flag-ON path does not
produce. Every test here writes a **mirror row** instead, which is what
the pilot actually stores, and the module deliberately keeps a
``BookingRequest``-only case to pin that the mirror, not the local table,
is the source once the flag is on.

## Both halves, always

Per the contour rule, every negative assertion («someone else's booking is
not visible») sits next to a positive one on the same data («mine is»).
A check where everything is empty passes for free and proves nothing.
No literal dates anywhere — only offsets from ``now``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.booking.models import BookingRequest, RemoteBookingProxy
from apps.catalog.models import CatalogMaster, CatalogService
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-recent-mirror"  # noqa: S105 — test fixture  # pragma: allowlist secret


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Анна"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _settings(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.MAX_BOT_TENANT_SLUG = "mirror-recent"
    # The pilot's configuration. Everything below only makes sense here.
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(
        slug="mirror-recent", name="Формула тела", timezone="Europe/Moscow"
    )


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="93001",
        display_name="Анна",
    )


@pytest.fixture
def other_user(tenant: Tenant) -> BotUser:
    """A different customer inside the SAME tenant."""
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="93002",
        display_name="Другая",
    )


def _make_service(tenant: Tenant, *, name: str, ayla_service_id: uuid.UUID) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_updated_at=timezone.now(),
        name=name,
        slug=f"svc-{ayla_service_id.hex[:8]}",
        duration_min=60,
        is_active=True,
        ayla_service_id=ayla_service_id,
    )


def _make_master(tenant: Tenant, *, name: str) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=timezone.now(),
        name=name,
        specialization="Массаж",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        ayla_user_id=uuid.uuid4(),
    )


def make_mirror_visit(
    *,
    tenant: Tenant,
    bot_user: BotUser | None,
    visit_at: datetime,
    duration_min: int = 90,
    status: str = RemoteBookingProxy.Status.CONFIRMED,
    service: CatalogService | None = None,
    master: CatalogMaster | None = None,
) -> RemoteBookingProxy:
    """One booking, stored the way the pilot stores it.

    ``RemoteBookingProxy`` is what the Ayla booking-event consumers write —
    for every origin, not just the bot dialog. A test that builds a
    ``BookingRequest`` instead is testing a row shape the flag-ON path
    never produces, which is exactly how this bug stayed green through
    three reviews.
    """
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=uuid.uuid4(),
        tenant=tenant,
        bot_user=bot_user,
        start_at=visit_at,
        end_at=visit_at + timedelta(minutes=duration_min),
        status=status,
        source=RemoteBookingProxy.Source.ADMIN_CONSOLE,
        service_id=service.ayla_service_id if service else None,
        specialist_id=master.id if master else None,
    )


def _url() -> str:
    return reverse("miniapp_api:customer_recent_activity")


def _get(client: Client, bot_user: BotUser):
    return client.get(_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id))


def _week_start(tenant: Tenant) -> datetime:
    tz = ZoneInfo(tenant.timezone or "Europe/Moscow")
    local_now = datetime.now(dt_timezone.utc).astimezone(tz)
    return (local_now - timedelta(days=local_now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


class TestMirrorIsTheSource:
    def test_salon_made_booking_is_visible(self, client: Client, tenant: Tenant, bot_user: BotUser):
        """The whole point: a booking the customer did NOT make in the bot.

        ``source=admin_console`` — no BookingRequest row exists anywhere,
        because nothing on that path writes one. Before the fix this
        answered «no upcoming booking» to a customer who had one.
        """
        service = _make_service(tenant, name="Массаж лимфодренаж", ayla_service_id=uuid.uuid4())
        master = _make_master(tenant, name="Ирина")
        proxy = make_mirror_visit(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=timezone.now() + timedelta(days=2),
            duration_min=90,
            service=service,
            master=master,
        )

        resp = _get(client, bot_user)
        assert resp.status_code == 200
        nb = resp.json()["next_booking"]
        assert nb["service_name"] == "Массаж лимфодренаж"
        assert nb["master_name"] == "Ирина"
        assert nb["duration_min"] == 90
        assert nb["salon_name"] == "Формула тела"
        assert nb["address"] == ""  # documented gap — no Tenant.address
        assert nb["booking_id"] == str(proxy.appointment_id)
        assert "·" in nb["date_human"]

    def test_local_booking_request_row_is_not_the_source(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ):
        """Both halves on one dataset.

        A ``BookingRequest`` written by the bot dialog (the billing row —
        the only writer left on this path) is NOT what the dashboard
        reads; the mirror row for a *later* visit is. If the endpoint fell
        back to the local table the earlier local row would win and the
        assertion below would name «Локальная».
        """
        BookingRequest.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            service_name="Локальная",
            master_name="Локальный мастер",
            client_name="Анна",
            client_phone="+7-000",
            visit_at=timezone.now() + timedelta(days=1),
            duration_min=60,
            status=BookingRequest.Status.CONFIRMED,
            source="bot",
            booking_source="ai_direct",
            attribution_metadata={"actor_type": "customer", "created_by": "test"},
        )
        service = _make_service(tenant, name="Зеркальная", ayla_service_id=uuid.uuid4())
        make_mirror_visit(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=timezone.now() + timedelta(days=3),
            service=service,
        )

        data = _get(client, bot_user).json()
        # positive: the mirror row is seen …
        assert data["next_booking"]["service_name"] == "Зеркальная"
        # … negative: the earlier local row did not win
        assert data["next_booking"]["service_name"] != "Локальная"

    def test_earliest_upcoming_mirror_row_wins(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ):
        later = _make_service(tenant, name="Поздняя", ayla_service_id=uuid.uuid4())
        sooner = _make_service(tenant, name="Ранняя", ayla_service_id=uuid.uuid4())
        make_mirror_visit(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=timezone.now() + timedelta(days=5),
            service=later,
        )
        make_mirror_visit(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=timezone.now() + timedelta(days=1),
            service=sooner,
        )
        data = _get(client, bot_user).json()
        assert data["next_booking"]["service_name"] == "Ранняя"

    def test_awaiting_payment_is_upcoming_but_cancelled_is_not(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ):
        """``awaiting_payment`` is Ayla's wire value, absent from the enum.

        Positive half first, so «nothing is visible» cannot pass this.
        """
        held = _make_service(tenant, name="Ожидает оплаты", ayla_service_id=uuid.uuid4())
        make_mirror_visit(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=timezone.now() + timedelta(days=2),
            status="awaiting_payment",
            service=held,
        )
        make_mirror_visit(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=timezone.now() + timedelta(days=1),
            status=RemoteBookingProxy.Status.CANCELLED,
        )
        data = _get(client, bot_user).json()
        assert data["next_booking"]["service_name"] == "Ожидает оплаты"

    def test_past_mirror_row_omits_next_booking(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ):
        make_mirror_visit(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=timezone.now() - timedelta(days=3),
        )
        assert "next_booking" not in _get(client, bot_user).json()


class TestMirrorOwnershipBoundaries:
    def test_other_customer_same_tenant(
        self, client: Client, tenant: Tenant, bot_user: BotUser, other_user: BotUser
    ):
        """Negative + positive guard on one dataset."""
        mine = _make_service(tenant, name="Моя", ayla_service_id=uuid.uuid4())
        make_mirror_visit(
            tenant=tenant,
            bot_user=other_user,
            visit_at=timezone.now() + timedelta(days=1),
        )
        make_mirror_visit(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=timezone.now() + timedelta(days=2),
            service=mine,
        )

        data = _get(client, bot_user).json()
        assert data["next_booking"]["service_name"] == "Моя"  # positive guard
        assert data["this_week_booking_count"] <= 1  # the other customer's row never counts

    def test_orphan_proxy_belongs_to_nobody(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ):
        """A booking by someone who never opened the bot has no ``bot_user``."""
        mine = _make_service(tenant, name="Моя", ayla_service_id=uuid.uuid4())
        make_mirror_visit(
            tenant=tenant,
            bot_user=None,
            visit_at=timezone.now() + timedelta(days=1),
        )
        make_mirror_visit(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=timezone.now() + timedelta(days=2),
            service=mine,
        )
        data = _get(client, bot_user).json()
        assert data["next_booking"]["service_name"] == "Моя"

    def test_other_tenant(self, client: Client, tenant: Tenant, bot_user: BotUser):
        other_tenant = Tenant.objects.create(
            slug="other-salon-mirror", name="Другой", timezone="Europe/Moscow"
        )
        other_tenant_user = BotUser.all_tenants.create(
            tenant=other_tenant, channel="max", channel_user_id="93003"
        )
        foreign = _make_service(other_tenant, name="Чужая", ayla_service_id=uuid.uuid4())
        make_mirror_visit(
            tenant=other_tenant,
            bot_user=other_tenant_user,
            visit_at=timezone.now() + timedelta(days=1),
            service=foreign,
        )
        mine = _make_service(tenant, name="Моя", ayla_service_id=uuid.uuid4())
        make_mirror_visit(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=timezone.now() + timedelta(days=2),
            service=mine,
        )

        data = _get(client, bot_user).json()
        assert data["next_booking"]["service_name"] == "Моя"  # positive guard
        assert data["next_booking"]["salon_name"] == "Формула тела"


class TestMirrorWeekCount:
    def test_counts_this_week_only(self, client: Client, tenant: Tenant, bot_user: BotUser):
        week_start = _week_start(tenant)
        make_mirror_visit(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=week_start + timedelta(days=1, hours=12),
        )
        make_mirror_visit(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=week_start + timedelta(days=3),
        )
        make_mirror_visit(  # next week — outside the window
            tenant=tenant,
            bot_user=bot_user,
            visit_at=week_start + timedelta(days=9),
        )
        make_mirror_visit(  # cancelled — never counts
            tenant=tenant,
            bot_user=bot_user,
            visit_at=week_start + timedelta(days=2),
            status=RemoteBookingProxy.Status.CANCELLED,
        )
        assert _get(client, bot_user).json()["this_week_booking_count"] == 2

    def test_local_booking_request_does_not_inflate_the_count(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ):
        """Both halves, anchored inside the week window.

        Anchoring on ``_week_start`` rather than ``now + N days`` is
        deliberate: a «+3 days» row silently leaves the window whenever
        the run happens late in the week, which is the same class of
        time-bomb that turned ``dev`` red today.
        """
        week_start = _week_start(tenant)
        BookingRequest.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            service_name="Локальная",
            master_name="Локальный мастер",
            client_name="Анна",
            client_phone="+7-000",
            visit_at=week_start + timedelta(days=1, hours=12),
            duration_min=60,
            status=BookingRequest.Status.CONFIRMED,
            source="bot",
            booking_source="ai_direct",
            attribution_metadata={"actor_type": "customer", "created_by": "test"},
        )
        make_mirror_visit(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=week_start + timedelta(days=2, hours=12),
        )
        # One mirror row in the window (positive), and the local row is
        # not added on top of it (negative).
        assert _get(client, bot_user).json()["this_week_booking_count"] == 1


class TestMirrorNameGaps:
    def test_unmirrored_service_yields_empty_name_not_a_guess(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ):
        """The «у Тихонова Ольга» case, pinned.

        The mirror carries ids, not copy. When the catalog row is missing
        the name is empty — visibly absent, never invented. The rest of
        the card must still render, which is what the master_name
        assertion guards.
        """
        master = _make_master(tenant, name="Ольга")
        start = timezone.now() + timedelta(days=1)
        proxy = RemoteBookingProxy.all_tenants.create(
            appointment_id=uuid.uuid4(),
            tenant=tenant,
            bot_user=bot_user,
            start_at=start,
            end_at=start + timedelta(minutes=45),
            status=RemoteBookingProxy.Status.CONFIRMED,
            service_id=uuid.uuid4(),  # no CatalogService mirrors this id
            specialist_id=master.id,
        )
        nb = _get(client, bot_user).json()["next_booking"]
        assert nb["service_name"] == ""
        assert nb["master_name"] == "Ольга"
        assert nb["booking_id"] == str(proxy.appointment_id)

    def test_unmirrored_master_yields_empty_name(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ):
        service = _make_service(tenant, name="Массаж", ayla_service_id=uuid.uuid4())
        make_mirror_visit(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=timezone.now() + timedelta(days=1),
            service=service,
            master=None,
        )
        nb = _get(client, bot_user).json()["next_booking"]
        assert nb["service_name"] == "Массаж"  # positive guard
        assert nb["master_name"] == ""


class TestAylaPathGatesOnBookingRequestReaders:
    """The three ``_get_booking_owned`` callers that had no flag gate.

    ``_get_booking_owned`` reads ``BookingRequest``. With the flag ON that
    table holds nothing the Ayla path wrote, so every one of these answered
    404 «booking not found» about a visit the customer can see in her own
    list. 404 is a lie about existence; 409 says what is actually true.
    """

    def _proxy_id(self, tenant: Tenant, bot_user: BotUser) -> str:
        proxy = make_mirror_visit(
            tenant=tenant,
            bot_user=bot_user,
            visit_at=timezone.now() + timedelta(days=2),
        )
        return str(proxy.appointment_id)

    def test_reschedule_request_is_gated(self, client: Client, tenant: Tenant, bot_user: BotUser):
        booking_id = self._proxy_id(tenant, bot_user)
        resp = client.post(
            reverse("miniapp_api:booking_reschedule_request", args=[booking_id]),
            data=json.dumps(
                {
                    "new_master_id": str(uuid.uuid4()),
                    "new_service_id": str(uuid.uuid4()),
                    "new_visit_at": (timezone.now() + timedelta(days=4)).isoformat(),
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "invalid_state"

    def test_reschedule_confirm_is_gated(self, client: Client, tenant: Tenant, bot_user: BotUser):
        booking_id = self._proxy_id(tenant, bot_user)
        resp = client.post(
            reverse("miniapp_api:booking_reschedule_confirm", args=[booking_id]),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "invalid_state"

    def test_submit_feedback_is_gated(self, client: Client, tenant: Tenant, bot_user: BotUser):
        booking_id = self._proxy_id(tenant, bot_user)
        resp = client.post(
            reverse("miniapp_api:submit_feedback", args=[booking_id]),
            data=json.dumps({"rating": 5, "comment": ""}),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "invalid_state"
