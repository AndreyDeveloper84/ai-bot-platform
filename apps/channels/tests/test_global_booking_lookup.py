"""Global-path personal booking lookup (DRF-911).

On the tenant-less discovery path a «покажи мои записи» turn used to fall
through to the concierge LLM, which answered with reasoning instead of data
(or escalated to a human). The tenant-side machinery was already built —
the detector (``apps.skills.booking.lookup.is_personal_booking_lookup``) and
the read-only tool (``show_my_bookings``) — but unreachable from the global
route. These tests pin the fix:

* a deterministic branch BEFORE the concierge LLM answers with the caller's
  real bookings (acceptance: red before the fix);
* §3 decision — bookings are AGGREGATED across every tenant where the
  caller has CONFIRMED bookings, sectioned per salon, and a section that
  fails to load is marked explicitly (the list must never look complete
  when it is not);
* the caller sees only their OWN bookings; cancelled / rescheduled rows
  never show as live; the lookup writes nothing;
* the branch stays silent while an operator drives any of the user's
  dialogs (DRF-1015 mute), and FAQ / mutation phrasings never enter it.
"""

from __future__ import annotations

from datetime import timedelta

import uuid

import pytest

from django.utils import timezone

from apps.booking.models import BookingRequest, RemoteBookingProxy
from apps.channels.max import handler as max_handler
from apps.conversations.models import Conversation
from apps.handoff.models import AdminTask
from apps.handoff.services import resolve_admin_task
from apps.identity.models import BotUser
from apps.orchestrator.memory import short_term
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _payload(*, text: str, user_id: int, chat_id: int, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Иван"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _run_global(text: str, *, user_id: int = 222, chat_id: int = 222, mid: str) -> None:
    max_handler.handle_global_max_event(
        _payload(text=text, user_id=user_id, chat_id=chat_id, mid=mid),
        trace_id=str(uuid.uuid4()),
    )


@pytest.fixture(autouse=True)
def _ayla_booking_flag(settings):
    """The pilot runs the Ayla mirror path — ``show_my_bookings`` reads the
    local ``RemoteBookingProxy`` + billing rows, never the network."""
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def mock_send(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def spy_concierge(monkeypatch):
    from unittest.mock import MagicMock

    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Какая услуга интересует?"))
    monkeypatch.setattr(max_handler, "generate_concierge_reply", spy)
    return spy


def _make_booking(
    slug: str,
    *,
    user_id: int = 222,
    service: str = "УЗ-кавитация",
    master: str = "Анна",
    start_at=None,
    booking_status: str = BookingRequest.Status.CONFIRMED,
    proxy_status: str = RemoteBookingProxy.Status.CONFIRMED,
) -> Tenant:
    """A CONFIRMED billing row + live mirror row for ``user_id`` in tenant ``slug``."""
    tenant = Tenant.objects.create(slug=slug, name=slug.upper())
    start = start_at or (timezone.now() + timedelta(days=2))
    appt = uuid.uuid4()
    with tenant_scope(tenant):
        bot_user = BotUser.objects.create(
            tenant=tenant, channel="max", channel_user_id=str(user_id)
        )
        BookingRequest.objects.create(
            tenant=tenant,
            bot_user=bot_user,
            service_name=service,
            master_name=master,
            client_name="Иван",
            client_phone="+70000000000",
            status=booking_status,
            comment=f"yclients_record_id={appt}",
        )
        RemoteBookingProxy.objects.create(
            appointment_id=appt,
            tenant=tenant,
            bot_user=bot_user,
            start_at=start,
            end_at=start + timedelta(hours=1),
            status=proxy_status,
        )
    return tenant


# --------------------------------------------------------------------------- #
# Acceptance: real bookings reach the global path                              #
# --------------------------------------------------------------------------- #


class TestGlobalBookingLookup:
    def test_lookup_returns_real_bookings(self, mock_send, fake_redis, spy_concierge):
        _make_booking("salon-a", service="УЗ-кавитация", master="Анна")

        _run_global("покажи мои записи", mid="b1")

        text = mock_send[-1]["text"]
        assert "УЗ-кавитация" in text
        assert "Анна" in text
        # The concierge LLM must NOT have answered this turn.
        spy_concierge.assert_not_called()

    def test_multiple_bookings_all_listed(self, mock_send, fake_redis, spy_concierge):
        _make_booking("salon-multi", service="УЗ-кавитация", master="Анна")
        tenant = Tenant.objects.get(slug="salon-multi")
        start = timezone.now() + timedelta(days=3)
        appt = uuid.uuid4()
        with tenant_scope(tenant):
            bot_user = BotUser.objects.get(channel_user_id="222")
            BookingRequest.objects.create(
                tenant=tenant,
                bot_user=bot_user,
                service_name="Массаж",
                master_name="Ольга",
                client_name="Иван",
                client_phone="+70000000000",
                status=BookingRequest.Status.CONFIRMED,
                comment=f"yclients_record_id={appt}",
            )
            RemoteBookingProxy.objects.create(
                appointment_id=appt,
                tenant=tenant,
                bot_user=bot_user,
                start_at=start,
                end_at=start + timedelta(hours=1),
                status=RemoteBookingProxy.Status.CONFIRMED,
            )

        _run_global("когда у меня запись?", mid="b2")

        text = mock_send[-1]["text"]
        # Several bookings must never collapse into an arbitrary single pick.
        assert "УЗ-кавитация" in text
        assert "Массаж" in text

    def test_empty_list_is_a_clear_answer(self, mock_send, fake_redis, spy_concierge):
        _run_global("покажи мои записи", mid="b3")

        assert mock_send[-1]["text"] == "У вас пока нет предстоящих записей."
        spy_concierge.assert_not_called()


# --------------------------------------------------------------------------- #
# Acceptance: §3 — aggregate across tenants, per-salon sections                #
# --------------------------------------------------------------------------- #


class TestMultiTenantAggregation:
    def test_bookings_from_all_salons_shown(self, mock_send, fake_redis, spy_concierge):
        _make_booking("salon-one", service="УЗ-кавитация", master="Анна")
        _make_booking("salon-two", service="Массаж", master="Ольга")

        _run_global("покажи мои записи", mid="a1")

        text = mock_send[-1]["text"]
        assert "УЗ-кавитация" in text
        assert "Массаж" in text
        # Sectioned per salon — the user can see WHERE each booking lives.
        assert "SALON-ONE" in text
        assert "SALON-TWO" in text


# --------------------------------------------------------------------------- #
# Acceptance: isolation + freshness                                            #
# --------------------------------------------------------------------------- #


class TestIsolationAndFreshness:
    def test_other_users_bookings_not_visible(self, mock_send, fake_redis, spy_concierge):
        _make_booking("salon-foreign", user_id=333, service="Чужая услуга")

        _run_global("покажи мои записи", user_id=222, mid="i1")

        text = mock_send[-1]["text"]
        assert "Чужая услуга" not in text
        assert text == "У вас пока нет предстоящих записей."

    def test_cancelled_booking_not_shown(self, mock_send, fake_redis, spy_concierge):
        _make_booking(
            "salon-cancelled",
            service="Отменённая услуга",
            proxy_status=RemoteBookingProxy.Status.CANCELLED,
        )

        _run_global("покажи мои записи", mid="i2")

        assert mock_send[-1]["text"] == "У вас пока нет предстоящих записей."

    def test_rescheduled_booking_not_shown(self, mock_send, fake_redis, spy_concierge):
        _make_booking(
            "salon-rescheduled",
            service="Перенесённая услуга",
            booking_status=BookingRequest.Status.RESCHEDULED,
        )

        _run_global("покажи мои записи", mid="i3")

        assert mock_send[-1]["text"] == "У вас пока нет предстоящих записей."

    def test_past_booking_not_shown(self, mock_send, fake_redis, spy_concierge):
        _make_booking(
            "salon-past",
            service="Прошедшая услуга",
            start_at=timezone.now() - timedelta(days=1),
        )

        _run_global("покажи мои записи", mid="i4")

        assert mock_send[-1]["text"] == "У вас пока нет предстоящих записей."

    def test_lookup_mutates_nothing(self, mock_send, fake_redis, spy_concierge):
        _make_booking("salon-immutable")

        before = (
            BookingRequest.all_tenants.count(),
            BotUser.all_tenants.count(),
            Conversation.all_tenants.count(),
            RemoteBookingProxy.all_tenants.count(),
        )
        _run_global("покажи мои записи", mid="i5")
        after = (
            BookingRequest.all_tenants.count(),
            BotUser.all_tenants.count(),
            Conversation.all_tenants.count(),
            RemoteBookingProxy.all_tenants.count(),
        )

        # The read-only lookup creates no booking rows and no new identities;
        # only the GLOBAL dialog (user + assistant turns) may grow.
        assert after[0] == before[0]
        assert after[1] == before[1] + 1  # the global BotUser, resolved by the handler itself
        assert after[3] == before[3]


# --------------------------------------------------------------------------- #
# Acceptance: mute while an operator drives the dialogs                        #
# --------------------------------------------------------------------------- #


class TestHandoffMute:
    def test_silent_during_active_escalation(self, mock_send, fake_redis, spy_concierge):
        _make_booking("salon-muted")

        _run_global("оператор", mid="m1")
        assert len(mock_send) == 1  # the handoff confirmation

        _run_global("покажи мои записи", mid="m2")
        assert len(mock_send) == 1  # silent — the lookup branch must not fire
        spy_concierge.assert_not_called()

        # DRF-980 close → the lookup works again.
        task = AdminTask.all_tenants.get()
        with tenant_scope(task.tenant):
            resolve_admin_task(task, resolution_note="done")

        _run_global("покажи мои записи", mid="m3")
        assert len(mock_send) == 2
        assert "УЗ-кавитация" in mock_send[-1]["text"]


# --------------------------------------------------------------------------- #
# Acceptance: the detector is NOT bypassed                                     #
# --------------------------------------------------------------------------- #


class TestDetectorBoundaries:
    def test_faq_process_question_not_routed(self, mock_send, fake_redis, spy_concierge):
        _run_global("как записаться?", mid="d1")

        assert mock_send[-1]["text"] == "Какая услуга интересует?"
        spy_concierge.assert_called_once()

    def test_mutation_request_not_routed(self, mock_send, fake_redis, spy_concierge):
        _run_global("перенеси мою запись", mid="d2")

        assert mock_send[-1]["text"] == "Какая услуга интересует?"
        spy_concierge.assert_called_once()

    def test_out_of_domain_zapis_not_routed(self, mock_send, fake_redis, spy_concierge):
        _run_global("покажи запись вебинара", mid="d3")

        assert mock_send[-1]["text"] == "Какая услуга интересует?"
        spy_concierge.assert_called_once()
