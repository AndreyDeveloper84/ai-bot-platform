"""Global-path personal booking lookup (DRF-911, re-sourced by DRF-1032).

The deterministic branch that answers «покажи мои записи» before the
concierge LLB gets a chance still exists and still matters — what changed
under DRF-1032 is where its data comes from. Owner decision **OD-H1**: what
the customer is told about their bookings is read from the **Ayla backend**,
not from the local ``RemoteBookingProxy`` mirror. A mirror row can outlive
the booking it mirrors (DRF-1034: the bot showed a booking deleted a day
earlier, with live reminders attached), and history is exactly where that
error accumulates.

So these tests now script the capability
(``apps.booking.services.records``) instead of seeding mirror rows. The
invariants they pin are unchanged and are the reason the file exists:

* a deterministic branch answers BEFORE the concierge LLM;
* the caller sees only their own bookings, and what is not live is not shown;
* the lookup writes nothing;
* the branch stays silent while an operator drives the dialog (DRF-1015);
* FAQ / mutation / out-of-domain phrasings never enter it.

The mirror-side scope helpers (``_booking_lookup_scopes`` and friends) are
NOT removed — their removal is gated on the owner accepting DRF-1033 on the
pilot. The DRF-1033 regression below therefore now exercises that helper
directly, so the fix stays pinned while the code is still in the tree.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.booking.models import BookingRequest, RemoteBookingProxy
from apps.booking.services.records import Visit, VisitsResult
from apps.channels.max import handler as max_handler
from apps.conversations.models import Conversation
from apps.handoff.models import AdminTask
from apps.handoff.services import resolve_admin_task
from apps.identity.models import BotUser
from apps.orchestrator import visits as visits_mod
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


def _visit(
    *,
    appointment_id: str | None = None,
    service: str = "УЗ-кавитация",
    master: str = "Анна",
    start: str = "2026-09-01T12:00:00+00:00",
) -> Visit:
    return Visit(
        appointment_id=appointment_id or str(uuid.uuid4()),
        service_name=service,
        master_name=master,
        start_at=start,
        price=None,
    )


@pytest.fixture(autouse=True)
def _ayla_booking_flag(settings):
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture(autouse=True)
def backend(monkeypatch):
    """Script the Ayla-backed capability — the network is never touched.

    Defaults to "this customer has nothing", so a test that forgets to set
    up data gets the honest empty answer rather than someone else's rows.
    """
    state: dict = {
        "upcoming": VisitsResult(status="empty"),
        "visits": VisitsResult(status="empty"),
        "subjects": [],
    }

    def _capture(result_key: str):
        def _call(*, bot_user, limit=5):
            state["subjects"].append(bot_user.channel_user_id)
            return state[result_key]

        return _call

    monkeypatch.setattr(visits_mod, "list_upcoming", _capture("upcoming"))
    monkeypatch.setattr(visits_mod, "list_visits", _capture("visits"))
    return state


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


def _make_mirror_booking(
    slug: str,
    *,
    user_id: int = 222,
    service: str = "УЗ-кавитация",
    master: str = "Анна",
) -> Tenant:
    """A CONFIRMED billing row + mirror row — used ONLY to prove the mirror
    is no longer the customer-facing source, and by the DRF-1033 regression."""
    tenant = Tenant.objects.create(slug=slug, name=slug.upper())
    start = timezone.now() + timedelta(days=2)
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
    return tenant


# --------------------------------------------------------------------------- #
# Acceptance: real bookings reach the global path, before the LLM              #
# --------------------------------------------------------------------------- #


class TestGlobalBookingLookup:
    def test_lookup_returns_real_bookings(self, mock_send, fake_redis, spy_concierge, backend):
        backend["upcoming"] = VisitsResult(status="ok", visits=(_visit(),))

        _run_global("покажи мои записи", mid="b1")

        text = mock_send[-1]["text"]
        assert "УЗ-кавитация" in text
        assert "Анна" in text
        # The concierge LLM must NOT have answered this turn.
        spy_concierge.assert_not_called()

    def test_multiple_bookings_all_listed(self, mock_send, fake_redis, spy_concierge, backend):
        backend["upcoming"] = VisitsResult(
            status="ok",
            visits=(_visit(service="УЗ-кавитация"), _visit(service="Массаж", master="Ольга")),
        )

        _run_global("когда у меня запись?", mid="b2")

        text = mock_send[-1]["text"]
        # Several bookings must never collapse into an arbitrary single pick.
        assert "УЗ-кавитация" in text
        assert "Массаж" in text

    def test_completed_visits_answer_the_same_question(
        self, mock_send, fake_redis, spy_concierge, backend
    ):
        """H-1: «мои визиты» and «мои записи» hit ONE detector, so one reply
        covers both — from one source, or it would contradict itself."""
        backend["visits"] = VisitsResult(
            status="ok", visits=(_visit(service="Массаж спины", start="2026-08-12T09:30:00+00:00"),)
        )

        _run_global("покажи мои визиты", mid="b4")

        text = mock_send[-1]["text"]
        assert "Ваши последние визиты" in text
        assert "Массаж спины" in text
        spy_concierge.assert_not_called()

    def test_empty_list_is_a_clear_answer(self, mock_send, fake_redis, spy_concierge, backend):
        _run_global("покажи мои записи", mid="b3")

        assert "пока нет завершённых визитов" in mock_send[-1]["text"]
        spy_concierge.assert_not_called()

    def test_backend_outage_is_not_answered_from_the_mirror(
        self, mock_send, fake_redis, spy_concierge, backend
    ):
        """§30 — the stale mirror must not stand in for an unreachable source.

        A mirror row exists and says there IS a booking; the backend is down.
        The customer must be told we could not read it, never shown the row.
        """
        _make_mirror_booking("salon-mirror-only", service="Призрачная услуга")
        backend["upcoming"] = VisitsResult(status="backend_unavailable")

        _run_global("покажи мои записи", mid="b5")

        text = mock_send[-1]["text"]
        assert "Призрачная услуга" not in text
        assert "позже" in text.lower()


# --------------------------------------------------------------------------- #
# Acceptance: cross-tenant aggregation — now performed by the backend          #
# --------------------------------------------------------------------------- #


class TestMultiTenantAggregation:
    def test_bookings_from_all_salons_shown(self, mock_send, fake_redis, spy_concierge, backend):
        """The backend lists a customer's bookings across every tenant they
        ever booked in (``records_api.py:312-323``), so the bot no longer
        walks tenants itself — but the visible guarantee is the same."""
        backend["upcoming"] = VisitsResult(
            status="ok",
            visits=(_visit(service="УЗ-кавитация"), _visit(service="Массаж", master="Ольга")),
        )

        _run_global("покажи мои записи", mid="a1")

        text = mock_send[-1]["text"]
        assert "УЗ-кавитация" in text
        assert "Массаж" in text

    def test_partial_read_is_never_served_as_a_complete_list(
        self, mock_send, fake_redis, spy_concierge, backend
    ):
        """One half read, the other failed — the reply must not look whole.

        Previously a per-salon section could be marked as failed; with a
        single cross-tenant source the honest form is to admit the failure
        instead of showing the half that worked as if it were everything.
        """
        backend["upcoming"] = VisitsResult(status="ok", visits=(_visit(service="УЗ-кавитация"),))
        backend["visits"] = VisitsResult(status="backend_unavailable")

        _run_global("покажи мои записи", mid="a2")

        text = mock_send[-1]["text"]
        assert "УЗ-кавитация" not in text
        assert "позже" in text.lower()


# --------------------------------------------------------------------------- #
# Acceptance: isolation + freshness                                            #
# --------------------------------------------------------------------------- #


class TestIsolationAndFreshness:
    def test_the_subject_is_always_the_caller(self, mock_send, fake_redis, spy_concierge, backend):
        """Identity is taken from the resolved caller, never from the message.

        The capability turns this BotUser into ``bot:max:<id>``; the backend
        then scopes every row to that subject. Nothing in the turn lets a
        caller name somebody else.
        """
        _run_global("покажи мои записи", user_id=222, mid="i1")

        assert backend["subjects"] == ["222", "222"]

    def test_other_users_bookings_not_visible(self, mock_send, fake_redis, spy_concierge, backend):
        _make_mirror_booking("salon-foreign", user_id=333, service="Чужая услуга")

        _run_global("покажи мои записи", user_id=222, mid="i2")

        text = mock_send[-1]["text"]
        assert "Чужая услуга" not in text
        assert "пока нет завершённых визитов" in text

    def test_cancelled_and_past_rows_are_not_shown_as_live(
        self, mock_send, fake_redis, spy_concierge, backend
    ):
        """The backend's ``upcoming`` section is active-status-and-future, and
        the visit list is completed-only (OD-H2) — a cancelled or elapsed row
        reaches neither, so an empty answer is the correct one."""
        _make_mirror_booking("salon-stale", service="Отменённая услуга")

        _run_global("покажи мои записи", mid="i3")

        assert "Отменённая услуга" not in mock_send[-1]["text"]

    def test_lookup_mutates_nothing(self, mock_send, fake_redis, spy_concierge, backend):
        _make_mirror_booking("salon-immutable")
        backend["upcoming"] = VisitsResult(status="ok", visits=(_visit(),))

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
    def test_silent_during_active_escalation(self, mock_send, fake_redis, spy_concierge, backend):
        _make_mirror_booking("salon-muted")
        backend["upcoming"] = VisitsResult(status="ok", visits=(_visit(),))

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
# Acceptance: visit card + repeat callbacks                                    #
# --------------------------------------------------------------------------- #


class TestVisitCallbacks:
    def test_card_tap_reaches_the_card_route(
        self, mock_send, fake_redis, spy_concierge, monkeypatch
    ):
        seen: dict = {}

        def fake_route(*, global_bot_user, callback_text, trace_id=None):
            from apps.orchestrator.discovery import DiscoveryReply

            seen["callback"] = callback_text
            return DiscoveryReply(text="карточка")

        monkeypatch.setattr(max_handler, "route_visit_callback", fake_route)

        _run_global("cb:visit:card:abc", mid="c1")

        assert seen["callback"] == "cb:visit:card:abc"
        assert mock_send[-1]["text"] == "карточка"
        # A callback is a deterministic transition — the LLM never sees it.
        spy_concierge.assert_not_called()

    def test_repeat_tap_reaches_the_same_route(
        self, mock_send, fake_redis, spy_concierge, monkeypatch
    ):
        seen: dict = {}

        def fake_route(*, global_bot_user, callback_text, trace_id=None):
            from apps.orchestrator.discovery import DiscoveryReply

            seen["callback"] = callback_text
            return DiscoveryReply(text="повтор")

        monkeypatch.setattr(max_handler, "route_visit_callback", fake_route)

        _run_global("cb:visit:repeat:abc", mid="c2")

        assert seen["callback"] == "cb:visit:repeat:abc"
        spy_concierge.assert_not_called()


# --------------------------------------------------------------------------- #
# Acceptance: DRF-1033 — one tenant's bookings must yield ONE scope            #
# --------------------------------------------------------------------------- #


class TestDuplicateSectionRegression:
    def test_two_bookings_same_tenant_yield_one_scope(self, mock_send, fake_redis):
        """``_booking_lookup_scopes`` used ``.values_list(...).distinct()`` on a
        queryset carrying ``BookingRequest``'s default ``ordering =
        ["-created_at"]``. Django folds ordering columns into a DISTINCT
        SELECT, so two CONFIRMED bookings in the SAME tenant (different
        ``created_at``) surfaced as two "different" scopes, and the reply
        rendered that salon twice.

        DRF-1032 moved the customer-facing read to the backend, so this helper
        no longer drives the reply — but it is still in the tree (its removal
        is gated on the owner accepting DRF-1033 on the pilot), so the fix is
        pinned here directly instead of through the handler.
        """
        from apps.orchestrator.handoff import _booking_lookup_scopes

        tenant = _make_mirror_booking("salon-dup", service="УЗ-кавитация — 1 зона")
        with tenant_scope(tenant):
            bot_user = BotUser.objects.get(channel_user_id="222")
            second = BookingRequest.objects.create(
                tenant=tenant,
                bot_user=bot_user,
                service_name="Массаж",
                master_name="Ольга",
                client_name="Иван",
                client_phone="+70000000000",
                status=BookingRequest.Status.CONFIRMED,
                comment=f"yclients_record_id={uuid.uuid4()}",
            )
            # Force a distinct created_at from the first row — auto_now_add
            # would otherwise collapse both inserts into the same instant and
            # mask the bug this test pins.
            BookingRequest.objects.filter(pk=second.pk).update(
                created_at=timezone.now() + timedelta(seconds=5)
            )
            global_user = BotUser.objects.get(channel_user_id="222")

        scopes = _booking_lookup_scopes(global_user)

        assert len(scopes) == 1


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
