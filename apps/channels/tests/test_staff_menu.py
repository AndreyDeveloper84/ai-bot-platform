"""Salon bot menu — buttons, taps, and what they answer (DRF-1061).

The menu is the salon's control panel, so these tests care about two
things a screenshot would not show:

* **only working capabilities appear.** A button that opens «скоро» costs a
  tap, teaches the person the bot is unfinished, and hides the live entries
  among dead ones. Manual booking / complete / no-show are absent because
  Ayla has no endpoint where the actor is a salon employee — a button would
  be a promise the backend cannot keep.
* **the day comes from the mirror that has data.** RemoteBookingProxy, not
  the local BookingRequest which holds four masterless rows on the pilot.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.booking.models import RemoteBookingProxy
from apps.catalog.models import CatalogMaster, CatalogService
from apps.channels.bot_registry import BotEntry
from apps.channels.max import staff_actions
from apps.channels.max.staff_menu import (
    CB_DAY,
    CB_REQUESTS,
    menu_buttons,
)
from apps.identity.models import BotUser
from apps.identity.services.staff_invites import issue_staff_invite, redeem_staff_invite
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import StaffInvite, Tenant

pytestmark = pytest.mark.django_db

MSK = ZoneInfo("Europe/Moscow")
CHANNEL_USER_ID = "700700"

WITH_APP = BotEntry(
    slug="salon",
    webhook_secret="wh",  # pragma: allowlist secret
    api_token="tok-salon",  # pragma: allowlist secret
    tenant_slug="formula-tela",
    stream="max_salon",
    web_app="id583_salon_bot",
)
WITHOUT_APP = BotEntry(
    slug="salon",
    webhook_secret="wh",  # pragma: allowlist secret
    api_token="tok-salon",  # pragma: allowlist secret
    tenant_slug="formula-tela",
    stream="max_salon",
)


@pytest.fixture
def tenant() -> Tenant:
    obj, _ = Tenant.all_objects.get_or_create(
        slug="formula-tela", defaults={"name": "Формула тела", "timezone": "Europe/Moscow"}
    )
    return obj


@pytest.fixture(autouse=True)
def _registry(settings):
    settings.MAX_BOT_REGISTRY = (WITH_APP,)
    settings.MAX_BOT_TOKEN = "tok-client"  # pragma: allowlist secret


@pytest.fixture
def sent():
    with patch("apps.channels.max.outbound.send_message") as mock:
        yield mock


class _Role:
    """Stand-in for the resolved role context."""

    def __init__(self, **flags):
        self.is_owner = flags.get("owner", False)
        self.is_admin = flags.get("admin", False)
        self.is_receptionist = flags.get("receptionist", False)
        self.is_master = flags.get("master", False)
        self.primary_role = flags.get("primary", "customer")


def _scoped(tenant, fn, *args, **kwargs):
    """Call a staff action the way production does — inside tenant_scope.

    These read through the tenant-scoped manager, so calling them bare
    would raise CrossTenantError. Wrapping here keeps the tests honest
    about the context the code actually runs in.
    """

    with tenant_scope(tenant):
        return fn(*args, **kwargs)


def _make_master(tenant, name="Тихонова Ольга") -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        name=name,
        external_id=None,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
    )


def _make_visit(tenant, master, *, hour: int, client: str, service: str, day_offset: int = 0):
    start_local = (datetime.now(MSK) + timedelta(days=day_offset)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    start = start_local.astimezone(dt_timezone.utc)
    service_id = uuid4()
    CatalogService.all_tenants.create(
        tenant=tenant,
        ayla_service_id=service_id,
        external_id=None,
        external_updated_at=timezone.now(),
        name=service,
        duration_min=60,
    )
    bot_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"c-{uuid4().hex[:10]}",
        client_name=client,
    )
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=uuid4(),
        tenant=tenant,
        bot_user=bot_user,
        specialist_id=master.id,
        service_id=service_id,
        start_at=start,
        end_at=start + timedelta(hours=1),
        status="confirmed",
    )


class TestMenuComposition:
    def test_admin_sees_day_requests_and_the_cabinet(self):
        labels = [b["label"] for b in menu_buttons(_Role(admin=True), WITH_APP)]

        assert any("Сегодня" in x for x in labels)
        assert any("Заявки" in x for x in labels)
        assert any("Кабинет" in x for x in labels)

    def test_master_sees_their_own_day(self):
        labels = [b["label"] for b in menu_buttons(_Role(master=True), WITH_APP)]

        assert any("Мой день" in x for x in labels)
        # The requests queue is an admin action — a master files them, does
        # not decide them.
        assert not any("Заявки" in x for x in labels)

    def test_owner_who_is_also_a_master_gets_one_day_button(self):
        buttons = menu_buttons(_Role(owner=True, master=True), WITH_APP)

        # Roles are additive, but "the day" is one concept — showing it
        # twice would just be confusing.
        assert sum(1 for b in buttons if b.get("callback") == CB_DAY) == 1

    def test_no_buttons_promise_what_the_backend_cannot_do(self):
        labels = " ".join(b["label"] for b in menu_buttons(_Role(owner=True), WITH_APP))

        # Ayla has no salon-employee actor for these; a button would lie.
        for forbidden in ("Записать", "Завершить", "Не пришёл", "Отменить"):
            assert forbidden not in labels

    def test_callbacks_follow_the_shared_contract(self):
        from apps.orchestrator.ui.keyboards import parse_callback

        for button in menu_buttons(_Role(owner=True), WITH_APP):
            callback = button.get("callback")
            if callback:
                # parse_callback needs >=3 segments; `cb:staff` alone would
                # be silently dropped by the shared decoder.
                assert parse_callback(callback) is not None, callback


class TestMiniAppButton:
    def test_native_open_app_when_the_bot_has_one(self):
        button = [b for b in menu_buttons(_Role(admin=True), WITH_APP) if "Кабинет" in b["label"]][
            0
        ]

        assert button["web_app"] == "id583_salon_bot"
        # MAX rejects '=', '&', '?' in an open_app payload (HTTP 400).
        assert not set("=&?") & set(button["callback"])

    def test_link_fallback_when_only_a_url_is_configured(self):
        entry = BotEntry(
            slug="salon",
            webhook_secret="wh",  # pragma: allowlist secret
            api_token="tok",  # pragma: allowlist secret
            tenant_slug="formula-tela",
            stream="max_salon",
            miniapp_url="https://miniapp-dev.example/",
        )

        button = [b for b in menu_buttons(_Role(admin=True), entry) if "Кабинет" in b["label"]][0]

        assert button["url"] == "https://miniapp-dev.example/"

    def test_omitted_entirely_when_the_bot_has_no_app(self):
        # A dead button is worse than a missing one: it costs a tap and
        # teaches the person the bot is broken. Pilot state today — both
        # MAX_BOT_WEB_APP and MAX_MINIAPP_URL are empty.
        labels = [b["label"] for b in menu_buttons(_Role(admin=True), WITHOUT_APP)]

        assert not any("Кабинет" in x for x in labels)
        # The working buttons survive.
        assert any("Сегодня" in x for x in labels)


class TestSalonDay:
    def test_groups_todays_visits_by_master(self, tenant):
        olga = _make_master(tenant, "Тихонова Ольга")
        denis = _make_master(tenant, "Архипкин Денис")
        _make_visit(tenant, olga, hour=11, client="Мария", service="УЗ-кавитация")
        _make_visit(tenant, denis, hour=14, client="Анна", service="массаж")

        text = _scoped(tenant, staff_actions.salon_day, tenant)

        assert "Тихонова Ольга" in text
        assert "Архипкин Денис" in text
        assert "Мария" in text and "УЗ-кавитация" in text
        assert "11:00" in text and "14:00" in text

    def test_reads_the_mirror_that_actually_has_data(self, tenant):
        # The whole point of DRF-1085: BookingRequest is empty/masterless on
        # the pilot, RemoteBookingProxy is where bookings live.
        master = _make_master(tenant)
        _make_visit(tenant, master, hour=10, client="Мария", service="маникюр")

        assert "Мария" in _scoped(tenant, staff_actions.salon_day, tenant)

    def test_tomorrow_is_not_today(self, tenant):
        master = _make_master(tenant)
        _make_visit(tenant, master, hour=10, client="Завтрашняя", service="маникюр", day_offset=1)

        assert "Завтрашняя" not in _scoped(tenant, staff_actions.salon_day, tenant)

    def test_empty_day_says_so_plainly(self, tenant):
        _make_master(tenant)

        assert "записей нет" in _scoped(tenant, staff_actions.salon_day, tenant)

    def test_salon_without_masters_says_that_instead(self, tenant):
        # Distinct from "no bookings" — the fix is different.
        assert "нет мастеров" in _scoped(tenant, staff_actions.salon_day, tenant)

    def test_never_leaks_a_phone_number(self, tenant):
        master = _make_master(tenant)
        visit = _make_visit(tenant, master, hour=10, client="Мария", service="маникюр")
        visit.bot_user.phone = "+79001234567"
        visit.bot_user.save(update_fields=["phone"])

        # DRF-1039: the executor never receives the customer's phone.
        assert "79001234567" not in _scoped(tenant, staff_actions.salon_day, tenant)


class TestMasterDay:
    def test_shows_only_that_master(self, tenant):
        olga = _make_master(tenant, "Тихонова Ольга")
        denis = _make_master(tenant, "Архипкин Денис")
        _make_visit(tenant, olga, hour=11, client="Мария", service="массаж")
        _make_visit(tenant, denis, hour=12, client="Пётр", service="стрижка")

        text = _scoped(tenant, staff_actions.master_day, olga)

        assert "Мария" in text
        assert "Пётр" not in text


class TestPendingRequests:
    def test_empty_queue_says_so(self, tenant):
        assert "Заявок от мастеров нет" in _scoped(tenant, staff_actions.pending_requests, tenant)

    def test_lists_pending_and_points_at_the_cabinet(self, tenant):
        from apps.scheduling.models import ScheduleChangeRequest

        master = _make_master(tenant)
        start = timezone.now() + timedelta(days=1)
        ScheduleChangeRequest.all_tenants.create(
            tenant=tenant,
            master=master,
            status=ScheduleChangeRequest.Status.PENDING,
            requested_start=start,
            requested_end=start + timedelta(hours=2),
            requested_change={},
            reason_class="personal",
        )

        text = _scoped(tenant, staff_actions.pending_requests, tenant)

        assert "Тихонова Ольга" in text
        # Deciding still happens where the audit trail lives.
        assert "кабинете" in text


class TestTaps:
    """End to end: a tap produces the answer and keeps the panel up."""

    def _tap(self, tenant, payload: str, *, cb_id: str = "cb-1") -> None:
        from apps.channels.max.salon_handler import handle_salon_max_event

        with tenant_scope(tenant):
            handle_salon_max_event(
                {
                    "update_type": "message_callback",
                    "timestamp": 1_700_000_000_000,
                    "callback": {
                        "callback_id": cb_id,
                        "payload": payload,
                        "timestamp": 1_700_000_000_000,
                        "user": {"user_id": int(CHANNEL_USER_ID), "name": "Владелец"},
                    },
                    "message": {
                        "body": {"mid": "m1", "seq": 1, "text": ""},
                        "sender": {"user_id": 999, "name": "bot", "is_bot": True},
                        "recipient": {"chat_id": 555, "user_id": 999, "chat_type": "dialog"},
                    },
                }
            )

    def _make_admin(self, tenant) -> BotUser:
        person = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id=CHANNEL_USER_ID
        )
        _, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        redeem_staff_invite(code=code, bot_user=person, tenant=tenant)
        return person

    def test_day_tap_answers_with_the_day(self, tenant, sent):
        self._make_admin(tenant)
        master = _make_master(tenant)
        _make_visit(tenant, master, hour=11, client="Мария", service="массаж")

        self._tap(tenant, CB_DAY)

        text = sent.call_args.kwargs["text"]
        assert "Мария" in text
        # The menu comes back with the answer — the panel must not vanish
        # after one use.
        assert sent.call_args.kwargs.get("attachments")

    def test_requests_tap_answers(self, tenant, sent):
        self._make_admin(tenant)

        self._tap(tenant, CB_REQUESTS)

        assert "Заявок от мастеров нет" in sent.call_args.kwargs["text"]

    def test_unknown_action_shows_the_menu_not_an_error(self, tenant, sent):
        self._make_admin(tenant)

        self._tap(tenant, "cb:staff:nonexistent")

        # An old message's stale button should show what you CAN do.
        assert "Формула тела" in sent.call_args.kwargs["text"]

    def test_a_tap_from_someone_with_no_role_is_not_an_invite_attempt(self, tenant, sent):
        BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id=CHANNEL_USER_ID)

        self._tap(tenant, CB_DAY)

        # Must ask for a code, not burn a rate-limit attempt on a payload
        # the person never typed.
        assert "код приглашения" in sent.call_args.kwargs["text"]


class TestApproveFromChat:
    """Deciding a schedule request with one tap (DRF-1061 block 3.2).

    Only APPROVAL lives in chat. Rejection requires a written reason the
    master will read — the service makes `rejection_reason` mandatory and
    surfaces it in their DM — and asking for free text in chat would mean
    an FSM, i.e. a "now send me the reason" state to get stuck in.
    Approval needs no text, so it is the tap-sized half.
    """

    def _pending(self, tenant, master):
        from apps.scheduling.models import ScheduleChangeRequest

        start = timezone.now() + timedelta(days=1)
        return ScheduleChangeRequest.all_tenants.create(
            tenant=tenant,
            master=master,
            status=ScheduleChangeRequest.Status.PENDING,
            requested_start=start,
            requested_end=start + timedelta(hours=2),
            requested_change={},
            reason_class="personal",
        )

    def test_approving_moves_the_request_out_of_pending(self, tenant):
        from apps.scheduling.models import ScheduleChangeRequest

        master = _make_master(tenant)
        req = self._pending(tenant, master)

        with tenant_scope(tenant):
            outcome = staff_actions.approve_request(
                tenant=tenant, request_id=str(req.id), actor=None
            )

        req.refresh_from_db()
        assert req.status != ScheduleChangeRequest.Status.PENDING
        assert "одобрена" in outcome

    def test_approving_twice_says_so_instead_of_crashing(self, tenant):
        master = _make_master(tenant)
        req = self._pending(tenant, master)

        with tenant_scope(tenant):
            staff_actions.approve_request(tenant=tenant, request_id=str(req.id), actor=None)
            second = staff_actions.approve_request(
                tenant=tenant, request_id=str(req.id), actor=None
            )

        # Two people tapping the same button is normal, not an error.
        assert "уже рассмотрели" in second

    def test_unknown_id_is_answered_not_raised(self, tenant):
        with tenant_scope(tenant):
            assert "не найдена" in staff_actions.approve_request(
                tenant=tenant, request_id=str(uuid4()), actor=None
            )

    def test_malformed_id_is_answered_not_raised(self, tenant):
        with tenant_scope(tenant):
            assert "не найдена" in staff_actions.approve_request(
                tenant=tenant, request_id="не-uuid", actor=None
            )

    def test_one_button_per_pending_request(self, tenant):
        master = _make_master(tenant)
        first = self._pending(tenant, master)
        second = self._pending(tenant, master)

        with tenant_scope(tenant):
            rows = staff_actions.pending_request_rows(tenant)

        assert {r[0] for r in rows} == {str(first.id), str(second.id)}
        assert all("Тихонова Ольга" in r[1] for r in rows)

    def test_the_list_says_where_rejection_lives(self, tenant):
        master = _make_master(tenant)
        self._pending(tenant, master)

        with tenant_scope(tenant):
            text = staff_actions.pending_requests(tenant)

        # The person must not hunt for a reject button that is not there.
        assert "Отклонить" in text and "кабинете" in text
