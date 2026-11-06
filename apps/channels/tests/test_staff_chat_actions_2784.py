"""DRF-2784 — the salon works in the chat: reject a request, settle a visit.

Mini App stays where it was — its buttons are untouched; these are the bot's
own paths beside them.

* **Reject** — a button beside every «✅ approve» in the request list. One
  implementation (``staff_actions.reject_request``) for this button and the
  request notice's «Отклонить»; the reason the master reads is the template
  ``REJECT_REASON_BY_CODE["chat_declined"]``.
* **«Состоялся» / «Не пришёл»** — a button per visit under the salon's day.
  The tap reads the canonical version and asks «Визит состоялся?»; the
  answer buttons carry THAT version back, and the write is checked against
  it — the same «through the operator» rule as the admin Mini App
  (``views_booking_complete``), through the same service.

Ayla's two clients are replaced by recording stubs at the module attribute
the service reads lazily — the same seam the admin view's own tests use.
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
from apps.identity.models import BotUser
from apps.identity.services.staff_invites import issue_staff_invite, redeem_staff_invite
from apps.integrations.ayla.booking_client import BookingUnavailableError
from apps.integrations.ayla.salon_client import SalonStaleVersion
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import StaffInvite, Tenant

pytestmark = pytest.mark.django_db

MSK = ZoneInfo("Europe/Moscow")
CHANNEL_USER_ID = "700784"

ENTRY = BotEntry(
    slug="salon",
    webhook_secret="wh",  # pragma: allowlist secret
    api_token="tok-salon",  # pragma: allowlist secret
    tenant_slug="formula-tela",
    stream="max_salon",
    web_app="id583_salon_bot",
)


@pytest.fixture(autouse=True)
def _staff_are_linked(monkeypatch):
    # Связь с каталогом — не предмет файла (стережётся в test_salon_entry_2113).
    monkeypatch.setattr("apps.channels.max.salon_entry.unlinked_reason", lambda *a, **kw: "")


@pytest.fixture(autouse=True)
def _registry(settings):
    settings.MAX_BOT_REGISTRY = (ENTRY,)
    settings.MAX_BOT_TOKEN = "tok-client"  # pragma: allowlist secret


@pytest.fixture
def tenant() -> Tenant:
    obj, _ = Tenant.all_objects.get_or_create(
        slug="formula-tela", defaults={"name": "Формула тела", "timezone": "Europe/Moscow"}
    )
    return obj


@pytest.fixture
def sent():
    with patch("apps.channels.max.outbound.send_message") as mock:
        yield mock


class _Version:
    def __init__(self, *, appointment_id, version=3, status="confirmed", start=None):
        self.id = str(appointment_id)
        self.version = version
        self.status = status
        self.start_datetime = start


class _StubBooking:
    """The canonical read (``get_appointment_version``)."""

    def __init__(self, *, exc=None, version=3, status="confirmed", start=None):
        self.exc = exc
        self.version = version
        self.status = status
        self.start = start
        self.calls: list[dict] = []

    def get_appointment_version(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return _Version(
            appointment_id=kwargs["booking_id"],
            version=self.version,
            status=self.status,
            start=self.start,
        )


class _StubSalon:
    """The salon-surface writes."""

    def __init__(self, *, exc=None):
        self.exc = exc
        self.calls: list[tuple[str, dict]] = []

    def complete_appointment(self, **kwargs):
        self.calls.append(("complete_appointment", kwargs))
        if self.exc:
            raise self.exc
        return {"status": "completed"}

    def mark_no_show(self, **kwargs):
        self.calls.append(("mark_no_show", kwargs))
        if self.exc:
            raise self.exc
        return {"status": "no_show"}


@pytest.fixture
def ayla(monkeypatch):
    def _install(*, booking=None, salon=None):
        b = booking or _StubBooking()
        s = salon or _StubSalon()
        monkeypatch.setattr(
            "apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: b
        )
        monkeypatch.setattr("apps.integrations.ayla.salon_client.get_salon_client", lambda: s)
        return b, s

    return _install


def _make_master(tenant, name="Тихонова Ольга") -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        name=name,
        external_id=None,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
    )


def _make_visit(tenant, master, *, hour, client, service="массаж", status="confirmed", day=0):
    start_local = (datetime.now(MSK) + timedelta(days=day)).replace(
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
    client_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"c-{uuid4().hex[:10]}",
        client_name=client,
    )
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=uuid4(),
        tenant=tenant,
        bot_user=client_user,
        specialist_id=master.id,
        service_id=service_id,
        start_at=start,
        end_at=start + timedelta(hours=1),
        status=status,
    )


def _make_staff(tenant, role=StaffInvite.Role.ADMIN) -> BotUser:
    person = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=CHANNEL_USER_ID
    )
    _, code = issue_staff_invite(tenant=tenant, role=role)
    redeem_staff_invite(code=code, bot_user=person, tenant=tenant)
    return person


def _make_linked_master(tenant) -> BotUser:
    person = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=CHANNEL_USER_ID
    )
    master = _make_master(tenant, name="Мастер Без Прав")
    master.linked_bot_user = person
    master.save(update_fields=["linked_bot_user"])
    return person


def _tap(tenant, payload: str, *, cb_id: str | None = None) -> None:
    from apps.channels.max.salon_handler import handle_salon_max_event

    with tenant_scope(tenant):
        handle_salon_max_event(
            {
                "update_type": "message_callback",
                "timestamp": 1_700_000_000_000,
                "callback": {
                    "callback_id": cb_id or f"cb-{uuid4().hex[:8]}",
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


def _rows(sent) -> list[list[tuple[str, str]]]:
    """The reply keyboard as ``[[(text, payload), …], …]`` — the wire, not our dicts."""

    out: list[list[tuple[str, str]]] = []
    for attachment in sent.call_args.kwargs.get("attachments") or []:
        for row in (attachment.get("payload") or {}).get("buttons") or []:
            out.append([(b.get("text", ""), b.get("payload", "")) for b in row])
    return out


def _payloads(sent) -> list[str]:
    return [payload for row in _rows(sent) for _, payload in row]


def _pending(tenant, master):
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


# ─── Отклонение заявки ────────────────────────────────────────────────────


class TestRejectFromChat:
    def test_r1_reject_moves_the_request_and_carries_the_template_reason(self, tenant):
        from apps.scheduling.models import ScheduleChangeRequest

        admin = _make_staff(tenant)
        req = _pending(tenant, _make_master(tenant))

        with tenant_scope(tenant):
            outcome = staff_actions.reject_request(
                tenant=tenant, request_id=str(req.id), actor=admin
            )

        req.refresh_from_db()
        assert outcome == "Заявка отклонена. Мастер получит уведомление."
        assert req.status == ScheduleChangeRequest.Status.REJECTED
        assert req.resolution_note == (
            "Отклонено владельцем в чате; подробности спросите у администратора"
        )

    def test_r2_rejecting_twice_says_so(self, tenant):
        admin = _make_staff(tenant)
        req = _pending(tenant, _make_master(tenant))

        with tenant_scope(tenant):
            staff_actions.reject_request(tenant=tenant, request_id=str(req.id), actor=admin)
            second = staff_actions.reject_request(
                tenant=tenant, request_id=str(req.id), actor=admin
            )

        assert second == "Эту заявку уже рассмотрели."

    def test_r3_every_request_row_pairs_approve_with_reject(self, tenant, sent):
        _make_staff(tenant)
        master = _make_master(tenant)
        first, second = _pending(tenant, master), _pending(tenant, master)

        _tap(tenant, "cb:staff:requests")

        pairs = [row for row in _rows(sent) if len(row) == 2]
        assert {tuple(p for _, p in row) for row in pairs} == {
            (f"cb:staff:req_ok:{first.id}", f"cb:staff:req_no:{first.id}"),
            (f"cb:staff:req_ok:{second.id}", f"cb:staff:req_no:{second.id}"),
        }
        assert all(row[1][0] == "Отклонить" for row in pairs)
        assert "Одобрить или отклонить — кнопками ниже." in sent.call_args.kwargs["text"]

    def test_r4_tapping_reject_rejects_and_relists(self, tenant, sent):
        from apps.scheduling.models import ScheduleChangeRequest

        _make_staff(tenant)
        req = _pending(tenant, _make_master(tenant))

        _tap(tenant, f"cb:staff:req_no:{req.id}")

        req.refresh_from_db()
        assert req.status == ScheduleChangeRequest.Status.REJECTED
        text = sent.call_args.kwargs["text"]
        assert text.startswith("Заявка отклонена. Мастер получит уведомление.")
        assert "Заявок от мастеров нет." in text

    def test_r5_the_notice_button_goes_through_the_same_implementation(self, tenant):
        from apps.channels.max import salon_notify_actions

        admin = _make_staff(tenant)
        req = _pending(tenant, _make_master(tenant))

        with (
            tenant_scope(tenant),
            patch.object(
                staff_actions, "reject_request", wraps=staff_actions.reject_request
            ) as spy,
        ):
            outcome = salon_notify_actions._reject(str(req.id), tenant, admin)

        assert spy.call_count == 1
        assert outcome == "Заявка отклонена. Мастер получит уведомление."


# ─── «Состоялся» / «не пришёл» ────────────────────────────────────────────


class TestDayCarriesVisitButtons:
    def test_v1_admin_day_has_a_button_per_visit_still_to_settle(self, tenant, sent):
        _make_staff(tenant)
        master = _make_master(tenant)
        open_visit = _make_visit(tenant, master, hour=11, client="Мария")
        closed = _make_visit(tenant, master, hour=12, client="Анна", status="completed")
        tomorrow = _make_visit(tenant, master, hour=13, client="Олег", day=1)

        _tap(tenant, "cb:staff:day")

        payloads = _payloads(sent)
        assert f"cb:staff:visit:{open_visit.appointment_id}" in payloads
        assert f"cb:staff:visit:{closed.appointment_id}" not in payloads
        assert f"cb:staff:visit:{tomorrow.appointment_id}" not in payloads
        labels = [text for row in _rows(sent) for text, _ in row]
        assert "✔ 11:00 · Мария" in labels
        # The menu (and the Mini App door) still rides under the day.
        assert "cb:staff:day" in payloads and "staff_open_app" in payloads

    def test_v2_front_desk_reads_the_day_without_visit_buttons(self, tenant, sent):
        # Control: green before as well — the reception never had these.
        _make_staff(tenant, role=StaffInvite.Role.RECEPTIONIST)
        master = _make_master(tenant)
        _make_visit(tenant, master, hour=11, client="Мария")

        _tap(tenant, "cb:staff:day")

        assert "Мария" in sent.call_args.kwargs["text"]
        assert not [p for p in _payloads(sent) if p.startswith("cb:staff:visit:")]


class TestVisitQuestion:
    def test_v3_tap_reads_the_version_and_puts_it_in_the_answers(self, tenant, sent, ayla):
        admin = _make_staff(tenant)
        visit = _make_visit(tenant, _make_master(tenant), hour=11, client="Мария")
        start = visit.start_at.astimezone(MSK).replace(hour=15).isoformat()
        booking, salon = ayla(booking=_StubBooking(version=7, start=start))

        _tap(tenant, f"cb:staff:visit:{visit.appointment_id}")

        assert booking.calls == [
            {
                "external_user_id": f"bot:max:{admin.channel_user_id}",
                "booking_id": str(visit.appointment_id),
            }
        ]
        assert salon.calls == []  # a question, not a write
        text = sent.call_args.kwargs["text"]
        assert text.startswith("*Визит состоялся?*")
        # The time is the schedule's (a moved visit shows where it is now).
        assert "Мария · 15:00 · массаж" in text
        assert "клиенту придёт запрос отзыва" in text
        assert _rows(sent) == [
            [
                ("Да, состоялся", f"cb:staff:done:{visit.appointment_id}:7"),
                ("Не пришёл", f"cb:staff:noshow:{visit.appointment_id}:7"),
            ],
            [("Не сейчас", "cb:staff:day")],
        ]

    def test_v4_no_version_no_answer_buttons(self, tenant, sent, ayla):
        _make_staff(tenant)
        visit = _make_visit(tenant, _make_master(tenant), hour=11, client="Мария")
        ayla(booking=_StubBooking(exc=BookingUnavailableError("down")))

        _tap(tenant, f"cb:staff:visit:{visit.appointment_id}")

        assert sent.call_args.kwargs["text"] == (
            "Не удалось прочитать запись в расписании. Попробуйте ещё раз."
        )
        assert _rows(sent) == [[("Не сейчас", "cb:staff:day")]]


class TestSettleFromChat:
    def test_v5_done_writes_with_the_shown_version_and_reads_nothing(self, tenant, sent, ayla):
        admin = _make_staff(tenant)
        visit = _make_visit(tenant, _make_master(tenant), hour=11, client="Мария")
        booking, salon = ayla()

        _tap(tenant, f"cb:staff:done:{visit.appointment_id}:7")

        # The version is the one the question showed — not re-read here,
        # or the guard could never fire.
        assert booking.calls == []
        assert salon.calls == [
            (
                "complete_appointment",
                {
                    "actor_external_id": f"bot:max:{admin.channel_user_id}",
                    "tenant_slug": "formula-tela",
                    "appointment_id": str(visit.appointment_id),
                    "expected_version": 7,
                },
            )
        ]
        assert sent.call_args.kwargs["text"].startswith("Визит закрыт.\n\n")

    def test_v6_no_show_writes_the_other_transition(self, tenant, sent, ayla):
        _make_staff(tenant)
        visit = _make_visit(tenant, _make_master(tenant), hour=11, client="Мария")
        _, salon = ayla()

        _tap(tenant, f"cb:staff:noshow:{visit.appointment_id}:7")

        assert [name for name, _ in salon.calls] == ["mark_no_show"]
        assert salon.calls[0][1]["expected_version"] == 7
        assert sent.call_args.kwargs["text"].startswith("Отмечено: клиент не пришёл.\n\n")

    def test_v7_a_visit_that_changed_comes_back_as_a_conflict(self, tenant, sent, ayla):
        _make_staff(tenant)
        visit = _make_visit(tenant, _make_master(tenant), hour=11, client="Мария")
        ayla(salon=_StubSalon(exc=SalonStaleVersion("stale")))

        _tap(tenant, f"cb:staff:done:{visit.appointment_id}:7")

        text = sent.call_args.kwargs["text"]
        assert text.startswith("Запись изменилась — день обновлён, посмотрите ещё раз.\n\n")
        # …and the day is shown again, with its buttons.
        assert f"cb:staff:visit:{visit.appointment_id}" in _payloads(sent)

    def test_v8_a_master_cannot_settle(self, tenant, sent, ayla):
        _make_linked_master(tenant)
        visit = _make_visit(tenant, _make_master(tenant), hour=11, client="Мария")
        _, salon = ayla()

        _tap(tenant, f"cb:staff:done:{visit.appointment_id}:7")

        assert salon.calls == []

    def test_v9_another_salons_visit_is_not_found_and_not_written(self, tenant, sent, ayla):
        _make_staff(tenant)
        other, _ = Tenant.all_objects.get_or_create(
            slug="other-salon", defaults={"name": "Другой", "timezone": "Europe/Moscow"}
        )
        foreign = _make_visit(other, _make_master(other), hour=11, client="Чужая")
        _, salon = ayla()

        _tap(tenant, f"cb:staff:done:{foreign.appointment_id}:7")

        assert salon.calls == []
        assert sent.call_args.kwargs["text"].startswith("Запись не найдена.")

    def test_v10_a_payload_without_a_version_never_reaches_the_write(self, tenant, sent, ayla):
        _make_staff(tenant)
        visit = _make_visit(tenant, _make_master(tenant), hour=11, client="Мария")
        _, salon = ayla()

        _tap(tenant, f"cb:staff:done:{visit.appointment_id}")

        assert salon.calls == []
        assert sent.call_args.kwargs["text"].startswith("Запись не найдена.")
