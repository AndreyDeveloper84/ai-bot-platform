"""DRF-2785 (variant «в») — the master's own buttons in the salon bot.

* the mirror learns the appointment's version from the catalog's events
  (``appointment_version``) — only ever raised, never into
  ``last_applied_appointment_version`` (that one orders canonical reschedules);
* «У вас новая запись» carries «✅ Подтверждаю» | «❌ Не смогу» when it goes
  out as the salon bot;
* the taps act as the master (``act_as_specialist``): acknowledge, cancel
  after a confirmation, complete / no-show from «📅 Мой день»;
* a 409 ``STALE_VERSION`` shows the new time and asks again with the new
  version — never a silent retry.

The catalog is a recording stub of ``act_as_specialist``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.booking.models import RemoteBookingProxy
from apps.catalog.models import CatalogMaster
from apps.channels.bot_registry import BotEntry
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.identity.models import BotUser
from apps.identity.services.staff_invites import issue_staff_invite, redeem_staff_invite
from apps.integrations.ayla.booking_client import BookingBadRequestError
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import StaffInvite, Tenant

pytestmark = pytest.mark.django_db

MSK = ZoneInfo("Europe/Moscow")
TENANT_ID = "6e5c9a3d-6f74-4b0a-95c3-9e4f0a3b2785"
CHANNEL_USER_ID = "702785"

ENTRY = BotEntry(
    slug="salon",
    webhook_secret="wh",  # pragma: allowlist secret
    api_token="tok-salon",  # pragma: allowlist secret
    tenant_slug="buttons-salon",
    stream="max_salon",
    web_app="id583_salon_bot",
)


@pytest.fixture(autouse=True)
def _staff_are_linked(monkeypatch):
    monkeypatch.setattr("apps.channels.max.salon_entry.unlinked_reason", lambda *a, **kw: "")


@pytest.fixture(autouse=True)
def _registry(settings):
    settings.MAX_BOT_REGISTRY = (ENTRY,)
    settings.MAX_BOT_TOKEN = "tok-client"  # pragma: allowlist secret
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = False
    settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset({TENANT_ID})
    settings.EVENT_INGEST_ALLOWED_EVENTS = frozenset(
        {
            "booking.created",
            "booking.rescheduled",
            "appointment.rescheduled",
            "booking.cancelled",
        }
    )


@pytest.fixture
def tenant() -> Tenant:
    obj, _ = Tenant.all_objects.get_or_create(
        id=TENANT_ID,
        defaults={"slug": "buttons-salon", "name": "Кнопки", "timezone": "Europe/Moscow"},
    )
    return obj


@pytest.fixture
def sent():
    with patch("apps.channels.max.outbound.send_message") as mock:
        yield mock


class _Catalog:
    """Records ``act_as_specialist``; raises what it is told to."""

    def __init__(self, *, exc: Exception | None = None, once: bool = False) -> None:
        self.exc = exc
        self.once = once
        self.calls: list[dict[str, Any]] = []

    def act_as_specialist(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc and not (self.once and len(self.calls) > 1):
            raise self.exc
        return None


@pytest.fixture
def catalog(monkeypatch):
    def _install(**kw) -> _Catalog:
        stub = _Catalog(**kw)
        monkeypatch.setattr(
            "apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: stub
        )
        return stub

    return _install


def _linked_master(tenant: Tenant) -> tuple[BotUser, CatalogMaster]:
    person = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=CHANNEL_USER_ID
    )
    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        name="Тихонова Ольга",
        external_id=None,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
        linked_bot_user=person,
    )
    CatalogMaster.all_tenants.filter(pk=master.pk).update(catalog_specialist_id=master.pk)
    master.refresh_from_db()
    return person, master


def _visit(tenant, master, *, hour=11, version=None, client="Мария") -> RemoteBookingProxy:
    start = (
        datetime.now(MSK)
        .replace(hour=hour, minute=0, second=0, microsecond=0)
        .astimezone(dt_timezone.utc)
    )
    client_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"c-{uuid.uuid4().hex[:10]}",
        client_name=client,
    )
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=uuid.uuid4(),
        tenant=tenant,
        bot_user=client_user,
        specialist_id=master.catalog_specialist_id,
        start_at=start,
        end_at=start + timedelta(hours=1),
        status="confirmed",
        appointment_version=version,
    )


def _tap(tenant, payload: str) -> None:
    from apps.channels.max.salon_handler import handle_salon_max_event

    with tenant_scope(tenant):
        handle_salon_max_event(
            {
                "update_type": "message_callback",
                "timestamp": 1_700_000_000_000,
                "callback": {
                    "callback_id": f"cb-{uuid.uuid4().hex[:8]}",
                    "payload": payload,
                    "timestamp": 1_700_000_000_000,
                    "user": {"user_id": int(CHANNEL_USER_ID), "name": "Мастер"},
                },
                "message": {
                    "body": {"mid": "m1", "seq": 1, "text": ""},
                    "sender": {"user_id": 999, "name": "bot", "is_bot": True},
                    "recipient": {"chat_id": 555, "user_id": 999, "chat_type": "dialog"},
                },
            }
        )


def _rows(sent) -> list[list[tuple[str, str]]]:
    out: list[list[tuple[str, str]]] = []
    for attachment in sent.call_args.kwargs.get("attachments") or []:
        for row in (attachment.get("payload") or {}).get("buttons") or []:
            out.append([(b.get("text", ""), b.get("payload", "")) for b in row])
    return out


def _stale(version: int, start_at: str) -> BookingBadRequestError:
    return BookingBadRequestError(
        "http_409_STALE_VERSION",
        status_code=409,
        code="STALE_VERSION",
        details={"current_version": version, "start_at": start_at, "status": "confirmed"},
    )


def _envelope(name: str, data: dict[str, Any], *, event_id: str) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=event_id,
        event_name=name,
        event_version=1,
        occurred_at=dt.datetime(2026, 10, 5, 12, 0, tzinfo=dt.timezone.utc),
        tenant_id=TENANT_ID,
        user_id="e1a2b3c4-d5e6-4789-9abc-def012342785",
        actor="admin",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234562785",
        causation_id=None,
        data=data,
    )


# ─── версия в зеркале ─────────────────────────────────────────────────────


class TestMirrorLearnsTheVersion:
    APPOINTMENT_ID = "c8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f62785"

    def _created(self, version: int | None, *, event_id: str):
        from apps.eventbus.consumers.booking import handle_booking_created

        data: dict[str, Any] = {
            "appointment_id": self.APPOINTMENT_ID,
            "client_id": "e1a2b3c4-d5e6-4789-9abc-def012342785",
            "specialist_id": "7c2d8e1f-0a5c-4c3a-9e1b-4d52f8eb2785",
            "service_id": "3d5f7e1c-8a2d-4e6f-b9c0-1d2e3f4a2785",
            "start_at": "2026-10-06T15:00:00+03:00",
            "end_at": "2026-10-06T16:00:00+03:00",
            "status": "confirmed",
            "source": "mobile_app",
        }
        if version is not None:
            data["version"] = version
        handle_booking_created(_envelope("booking.created", data, event_id=event_id))

    def _proxy(self) -> RemoteBookingProxy:
        return RemoteBookingProxy.all_tenants.get(appointment_id=self.APPOINTMENT_ID)

    def test_m1_created_writes_the_version_and_not_the_reschedule_counter(self, tenant):
        self._created(1, event_id="01J9VER0000000000000002785")

        proxy = self._proxy()
        assert proxy.appointment_version == 1
        # The canonical-reschedule ordering field stays untouched: writing 1
        # there would make the first canonical reschedule look out of order.
        assert proxy.last_applied_appointment_version is None

    def test_m2_legacy_reschedule_raises_it_and_a_late_created_never_lowers_it(self, tenant):
        from apps.eventbus.consumers.booking import handle_booking_rescheduled

        self._created(1, event_id="01J9VER0000000000000012785")
        handle_booking_rescheduled(
            _envelope(
                "booking.rescheduled",
                {
                    "appointment_id": self.APPOINTMENT_ID,
                    "old_start_at": "2026-10-06T15:00:00+03:00",
                    "new_start_at": "2026-10-06T17:00:00+03:00",
                    "version": 2,
                },
                event_id="01J9VER0000000000000022785",
            )
        )
        self._created(1, event_id="01J9VER0000000000000032785")

        assert self._proxy().appointment_version == 2

    def test_m3_an_event_without_version_leaves_it_unknown(self, tenant):
        # Control: an older catalog without data.version — nothing invented.
        self._created(None, event_id="01J9VER0000000000000042785")

        assert self._proxy().appointment_version is None


# ─── кнопки на «У вас новая запись» ──────────────────────────────────────


class TestNoticeButtons:
    def _notify(self, tenant, master, proxy):
        from apps.booking.master_notify import notify_booking_created

        notify_booking_created(
            tenant=tenant,
            appointment_id=proxy.appointment_id,
            start_at=proxy.start_at,
            specialist_id=master.catalog_specialist_id,
            service_id=None,
            raw_source="mobile_app",
        )

    def _personal(self, sent_mock):
        calls = [c for c in sent_mock.call_args_list if c.kwargs.get("user_id") == CHANNEL_USER_ID]
        assert len(calls) == 1
        return calls[0]

    def test_n1_the_master_copy_carries_acknowledge_and_cant(self, tenant):
        _, master = _linked_master(tenant)
        proxy = _visit(tenant, master, version=1)

        with patch("apps.handoff.notify.send_message") as send:
            self._notify(tenant, master, proxy)

        call = self._personal(send)
        assert "У вас новая запись" in call.kwargs["text"]
        rows = [
            [(b["text"], b["payload"]) for b in row]
            for a in call.kwargs["attachments"]
            for row in a["payload"]["buttons"]
        ]
        assert rows == [
            [
                ("✅ Подтверждаю", f"cb:staff:ack:{proxy.appointment_id}:1"),
                ("❌ Не смогу", f"cb:staff:cant:{proxy.appointment_id}:1"),
            ]
        ]

    def test_n2_without_a_salon_bot_no_buttons(self, tenant, settings):
        # Control: the copy then goes out as the client bot, whose handler
        # does not know these taps — so it goes without them, as before.
        settings.MAX_BOT_REGISTRY = ()
        _, master = _linked_master(tenant)
        proxy = _visit(tenant, master, version=1)

        with patch("apps.handoff.notify.send_message") as send:
            self._notify(tenant, master, proxy)

        assert "attachments" not in self._personal(send).kwargs


# ─── нажатия мастера ──────────────────────────────────────────────────────


class TestMasterTaps:
    def test_t1_acknowledge_goes_as_the_master_with_the_shown_version(self, tenant, sent, catalog):
        person, master = _linked_master(tenant)
        proxy = _visit(tenant, master, version=3)
        stub = catalog()

        _tap(tenant, f"cb:staff:ack:{proxy.appointment_id}:3")

        assert stub.calls == [
            {
                "external_user_id": f"bot:max:{CHANNEL_USER_ID}",
                "specialist_id": str(master.catalog_specialist_id),
                "appointment_id": str(proxy.appointment_id),
                "action": "acknowledge",
                "expected_version": 3,
            }
        ]
        assert sent.call_args.kwargs["text"] == "Запись подтверждена."

    def test_t2_unknown_version_sends_what_the_mirror_knows_else_one(self, tenant, sent, catalog):
        _, master = _linked_master(tenant)
        known = _visit(tenant, master, hour=11, version=4)
        unknown = _visit(tenant, master, hour=12, version=None)
        stub = catalog()

        _tap(tenant, f"cb:staff:ack:{known.appointment_id}:0")
        _tap(tenant, f"cb:staff:ack:{unknown.appointment_id}:0")

        assert [c["expected_version"] for c in stub.calls] == [4, 1]

    def test_t3_a_moved_record_is_shown_and_asked_again_not_retried(self, tenant, sent, catalog):
        _, master = _linked_master(tenant)
        proxy = _visit(tenant, master, version=1)
        # Refuses once, as the catalog would; a retry WOULD succeed — so
        # only the absence of a second call proves there was none.
        stub = catalog(exc=_stale(2, "2026-10-06T17:30:00+03:00"), once=True)

        _tap(tenant, f"cb:staff:ack:{proxy.appointment_id}:1")

        assert len(stub.calls) == 1  # never a silent second write
        assert sent.call_args.kwargs["text"] == "Время записи изменилось: теперь 06.10 в 17:30."
        assert _rows(sent) == [
            [
                ("✅ Подтверждаю", f"cb:staff:ack:{proxy.appointment_id}:2"),
                ("❌ Не смогу", f"cb:staff:cant:{proxy.appointment_id}:2"),
            ]
        ]

    def test_t4_cant_asks_first_and_writes_nothing(self, tenant, sent, catalog):
        _, master = _linked_master(tenant)
        proxy = _visit(tenant, master, hour=11, version=1)
        stub = catalog()

        _tap(tenant, f"cb:staff:cant:{proxy.appointment_id}:1")

        assert stub.calls == []
        when = proxy.start_at.astimezone(MSK).strftime("%d.%m в %H:%M")
        assert sent.call_args.kwargs["text"] == (
            f"Отменить запись {when}? Клиенту вернём оплату полностью."
        )
        assert _rows(sent) == [
            [
                ("Да, отменить", f"cb:staff:cant_ok:{proxy.appointment_id}:1"),
                ("Нет", "cb:staff:day"),
            ]
        ]

    def test_t5_yes_cancels_and_without_a_version_sends_none(self, tenant, sent, catalog):
        _, master = _linked_master(tenant)
        proxy = _visit(tenant, master, version=None)
        stub = catalog()

        _tap(tenant, f"cb:staff:cant_ok:{proxy.appointment_id}:0")

        assert [(c["action"], c["expected_version"]) for c in stub.calls] == [("cancel", None)]
        assert sent.call_args.kwargs["text"] == "Запись отменена. Клиент получит уведомление."

    def test_t6_a_closed_record_says_so(self, tenant, sent, catalog):
        _, master = _linked_master(tenant)
        proxy = _visit(tenant, master, version=1)
        catalog(
            exc=BookingBadRequestError(
                "http_422_INVALID_STATUS", status_code=422, code="INVALID_STATUS"
            )
        )

        _tap(tenant, f"cb:staff:ack:{proxy.appointment_id}:1")

        assert sent.call_args.kwargs["text"] == "Эта запись уже отменена или закрыта."

    def test_t7_someone_without_a_master_card_cannot_act(self, tenant, sent, catalog):
        person = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id=CHANNEL_USER_ID
        )
        _, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        redeem_staff_invite(code=code, bot_user=person, tenant=tenant)
        stub = catalog()

        _tap(tenant, f"cb:staff:ack:{uuid.uuid4()}:1")

        assert stub.calls == []


class TestMasterDay:
    def test_d1_my_day_has_a_button_per_open_visit(self, tenant, sent, catalog):
        _, master = _linked_master(tenant)
        proxy = _visit(tenant, master, hour=11, version=2)

        _tap(tenant, "cb:staff:day")

        payloads = [p for row in _rows(sent) for _, p in row]
        assert f"cb:staff:mvisit:{proxy.appointment_id}" in payloads
        assert "cb:staff:day" in payloads  # the menu still rides under it

    def test_d2_visit_question_then_done_as_the_master(self, tenant, sent, catalog):
        _, master = _linked_master(tenant)
        proxy = _visit(tenant, master, hour=11, version=2, client="Мария")
        stub = catalog()

        _tap(tenant, f"cb:staff:mvisit:{proxy.appointment_id}")

        assert sent.call_args.kwargs["text"].startswith("*Визит состоялся?*\nМария · ")
        assert _rows(sent) == [
            [
                ("Да, состоялся", f"cb:staff:mdone:{proxy.appointment_id}:2"),
                ("Не пришёл", f"cb:staff:mnoshow:{proxy.appointment_id}:2"),
            ],
            [("Не сейчас", "cb:staff:day")],
        ]
        assert stub.calls == []

        _tap(tenant, f"cb:staff:mnoshow:{proxy.appointment_id}:2")

        assert [(c["action"], c["expected_version"]) for c in stub.calls] == [("no-show", 2)]
        assert sent.call_args.kwargs["text"] == "Отмечено: клиент не пришёл."


# ─── клиенту: мастер отменил ──────────────────────────────────────────────


class TestClientToldOfMasterCancellation:
    def _cancel(self, proxy, *, by: str, event_id: str) -> None:
        from apps.eventbus.consumers.booking import handle_booking_cancelled

        handle_booking_cancelled(
            _envelope(
                "booking.cancelled",
                {
                    "appointment_id": str(proxy.appointment_id),
                    "cancelled_by": by,
                    "reason_code": "master_unavailable" if by == "master" else "",
                },
                event_id=event_id,
            )
        )

    def _to_client(self, send, proxy) -> list:
        client_id = proxy.bot_user.channel_user_id
        return [c for c in send.call_args_list if c.kwargs.get("user_id") == client_id]

    def test_c1_master_cancel_tells_the_client_with_a_way_to_rebook(
        self, tenant, django_capture_on_commit_callbacks
    ):
        _, master = _linked_master(tenant)
        proxy = _visit(tenant, master, hour=15, version=1)

        with (
            patch("apps.handoff.notify.send_message") as send,
            django_capture_on_commit_callbacks(execute=True),
        ):
            self._cancel(proxy, by="master", event_id="01J9CAN0000000000000002785")

        (call,) = self._to_client(send, proxy)
        when = proxy.start_at.astimezone(MSK)
        assert call.kwargs["text"] == (
            f"К сожалению, мастер не сможет провести твою запись {when:%d.%m} в {when:%H:%M}. "
            "Запись отменена, оплата вернётся, если была. Подобрать другое время?"
        )
        buttons = [
            (b["text"], b["payload"])
            for a in call.kwargs["attachments"]
            for row in a["payload"]["buttons"]
            for b in row
        ]
        assert buttons == [("Подобрать время", f"cb:visit:repeat:{proxy.appointment_id}")]

    def test_c2_a_client_cancel_writes_the_client_nothing(
        self, tenant, django_capture_on_commit_callbacks
    ):
        # Control: green before as well — only the master's cancellation is new.
        _, master = _linked_master(tenant)
        proxy = _visit(tenant, master, hour=15, version=1)

        with (
            patch("apps.handoff.notify.send_message") as send,
            django_capture_on_commit_callbacks(execute=True),
        ):
            self._cancel(proxy, by="client", event_id="01J9CAN0000000000000012785")

        proxy.refresh_from_db()
        assert proxy.status == RemoteBookingProxy.Status.CANCELLED  # the event did apply
        # empty-assert-ok: a client's own cancellation writes the client nothing
        assert self._to_client(send, proxy) == []
