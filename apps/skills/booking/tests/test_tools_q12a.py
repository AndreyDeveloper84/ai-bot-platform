"""End-to-end Q12-α integration tests for ``execute_reschedule`` (#531).

PR #526 introduced the Q12-α continuation chain (reschedules within
the chain don't bill; chain-break reschedules bill as fresh sales).
The original bug B1 — 100% of LLM-path reschedules silently billed as
``service_swap`` chain-broken — slipped past the friendly review
because friendly tests only exercised the REST service-layer path
(``apps/booking/services/reschedule.py``), not the LLM tool path
(``apps/skills/booking/tools.py::execute_reschedule``). #531 closes
that gap with end-to-end LLM-path coverage using the same fake-YClients
client pattern as :mod:`test_tools_reschedule`.

Three scenarios pinned here (matching #531 acceptance criteria):

1. Same-service reschedule within 90 days →
   ``billable=False`` + ``billing_reason="reschedule_continuation"``
   + ``original_booking_event_id`` linking back to the chain root.
2. Reschedule >90 days from the chain root →
   ``billable=True`` + ``billing_reason`` contains ``over_90d``
   + ``original_booking_event_id IS NULL`` (new chain anchor).
3. Partial-failure path (YClients cancel succeeds, create fails) →
   audit row carries ``q12a_chain_terminator: True``.

LLM-row characteristic — these rows have ``service_id IS NULL`` (the
LLM tool writes via ``BookingRequest.create()`` without the FK). The
B1 fix in ``_normalise_service_id`` ensures the comparator doesn't
falsely flag swap when both sides are ``None``. The fixtures here
deliberately use the LLM-row shape (no ``service_id``) so the B1
fix stays locked in.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone as dj_timezone

from apps.audit.models import AuditLog
from apps.booking.models import BookingRequest
from apps.identity.models import BotUser
from apps.integrations.yclients import (
    AvailableTime,
    BookingRecord,
    YClientsAPIError,
)
from apps.skills.booking.tools import execute_reschedule
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="q12a-llm-e2e", name="Q12-α LLM e2e")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-q12a",
        chat_id="bu-q12a",
        phone="79991234567",
        client_name="Anna",
    )


class FakeYClientsClient:
    """Minimal stub mirroring :class:`apps.skills.booking.tests.test_tools_reschedule.FakeClient`.

    Configured via mutator attributes; raises configured exceptions
    inline to exercise the cancel/create failure modes.

    **SDK drift policy:** the 4 methods below mirror the YClients SDK
    surface that ``execute_reschedule`` calls today
    (``cancel_record`` / ``create_record`` / ``get_user_records`` /
    ``get_available_times``). If the LLM tool grows a 5th call (e.g.
    slot recheck, conflict detection), tests in this file will
    AttributeError at the new call site — fail-loud rather than
    silent false-pass. Extend this stub when that happens.
    """

    def __init__(self) -> None:
        self.cancel_calls: list[int] = []
        self.cancel_exc: Exception | None = None
        self.create_calls: list[dict[str, Any]] = []
        self.create_response: BookingRecord | None = None
        self.create_exc: Exception | None = None
        self.user_records: list[Any] = []
        self.user_records_exc: Exception | None = None
        self.times: list[AvailableTime] = []
        self.times_exc: Exception | None = None

    def cancel_record(self, *, record_id: int) -> bool:
        self.cancel_calls.append(record_id)
        if self.cancel_exc is not None:
            raise self.cancel_exc
        return True

    def create_record(self, **kwargs: Any) -> BookingRecord:
        self.create_calls.append(kwargs)
        if self.create_exc is not None:
            raise self.create_exc
        return self.create_response or BookingRecord(record_id=999, record_hash="h", raw={})

    def get_user_records(self) -> list[Any]:
        if self.user_records_exc is not None:
            raise self.user_records_exc
        return list(self.user_records)

    def get_available_times(self, **_: Any) -> list[AvailableTime]:
        if self.times_exc is not None:
            raise self.times_exc
        return list(self.times)


def _make_llm_root_booking(
    tenant: Tenant,
    bot_user: BotUser,
    *,
    yc_id: int = 555,
    visit_at: Any = None,
) -> BookingRequest:
    """Build a CONFIRMED BookingRequest matching the LLM-row shape:

    * ``service_id IS NULL`` — LLM path writes without the FK
      (B1 adversarial-pass note); chain comparator MUST normalise
      both sides for this to not falsely flag swap.
    * ``visit_at`` SET — required for the 90-day threshold check.
      Without it, ``compute_reschedule_continuation`` short-circuits
      on the ``chain_root_no_visit_at`` legacy guard and the chain
      breaks unconditionally.
    """

    if visit_at is None:
        visit_at = dj_timezone.now() + timedelta(hours=24)
    with tenant_scope(tenant):
        return BookingRequest.objects.create(
            tenant=tenant,
            bot_user=bot_user,
            service_name="Массаж",
            master_name="Ольга",
            client_name="Anna",
            client_phone="79991234567",
            comment=f"Bot booking | yclients_record_id={yc_id}",
            source="bot",
            status=BookingRequest.Status.CONFIRMED,
            visit_at=visit_at,
            duration_min=60,
        )


def _future_iso(*, hours: int = 48) -> str:
    return (dj_timezone.now() + timedelta(hours=hours)).replace(microsecond=0).isoformat()


def _slot_at(new_dt: str) -> AvailableTime:
    return AvailableTime(
        time=new_dt.split("T", 1)[1][:5],
        datetime=new_dt,
        seance_length_s=3600,
    )


def _exec_reschedule(
    *,
    client: FakeYClientsClient,
    new_dt: str,
    tenant: Tenant,
    bot_user: BotUser,
    record_id: int = 555,
):
    """Convenience wrapper around :func:`execute_reschedule` with the
    payload shape every test below uses."""

    with tenant_scope(tenant):
        return execute_reschedule(
            client=client,
            payload={
                "record_id": record_id,
                "new_datetime": new_dt,
                "master_id": 11,
                "service_id": 22,  # LLM payload still carries it; the
                # DB row's FK is None — that's the
                # B1 surface.
                "master_name": "Ольга",
                "service_name": "Массаж",
                "client_phone": "79991234567",
                "client_name": "Anna",
            },
            tenant=tenant,
            bot_user=bot_user,
        )


class TestQ12aContinuationLLMPath:
    """Acceptance #1 + #2 from issue #531: continuation vs chain-break."""

    def test_within_90d_same_service_continuation_chain(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """Acceptance #1: reschedule of a CONFIRMED LLM-row to a slot
        within 90 days produces a continuation-chain row.

        Key invariants pinned here:
        * ``billable is False``
        * ``billing_reason == "reschedule_continuation"``
        * ``original_booking_event_id`` points at the OLD row (the
          chain root) — proves the FK is wired by the LLM path, not
          left at its default NULL.
        * Service-id normalisation (B1 fix) — both sides have
          ``service_id IS NULL`` and the comparator does NOT flag a
          swap.
        """

        root_visit = dj_timezone.now() + timedelta(hours=24)
        old = _make_llm_root_booking(tenant, bot_user, yc_id=555, visit_at=root_visit)

        client = FakeYClientsClient()
        client.create_response = BookingRecord(record_id=888, record_hash="h", raw={})
        # ~5 days later — well within the 90-day threshold.
        new_dt = (root_visit + timedelta(days=5)).replace(microsecond=0).isoformat()
        client.times = [_slot_at(new_dt)]

        result = _exec_reschedule(client=client, new_dt=new_dt, tenant=tenant, bot_user=bot_user)
        assert result.error == ""

        new_row = BookingRequest.all_tenants.get(
            comment__contains="yclients_record_id=888",
        )
        assert new_row.booking_source == "ai_direct"
        assert new_row.billable is False, (
            "Continuation reschedule MUST NOT bill — would silently "
            "double-charge salons under B1 regression."
        )
        assert new_row.billing_reason == "reschedule_continuation"
        assert new_row.original_booking_event_id == old.id

    def test_over_90d_breaks_chain_billable_fresh_sale(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """Acceptance #2: reschedule of a CONFIRMED LLM-row to a slot
        strictly later than 90 days from chain-root visit_at breaks
        the chain. The new row is a fresh billable sale and starts
        its own chain (``original_booking_event_id IS NULL``).
        """

        root_visit = dj_timezone.now() + timedelta(hours=24)
        _make_llm_root_booking(tenant, bot_user, yc_id=555, visit_at=root_visit)

        client = FakeYClientsClient()
        client.create_response = BookingRecord(record_id=888, record_hash="h", raw={})
        # 91 days from root_visit — strictly beyond the 90-day threshold.
        new_dt = (root_visit + timedelta(days=91)).replace(microsecond=0).isoformat()
        client.times = [_slot_at(new_dt)]

        result = _exec_reschedule(client=client, new_dt=new_dt, tenant=tenant, bot_user=bot_user)
        assert result.error == ""

        new_row = BookingRequest.all_tenants.get(
            comment__contains="yclients_record_id=888",
        )
        assert new_row.booking_source == "ai_direct"
        assert new_row.billable is True, (
            "Chain-broken reschedule MUST bill — would silently undercharge salons otherwise."
        )
        assert "over_90d" in (new_row.billing_reason or "")
        assert new_row.original_booking_event_id is None, (
            "Chain-broken row starts a fresh chain — must NOT inherit the old root's FK."
        )

    def test_b1_null_service_id_does_not_falsely_flag_swap(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """B1 regression pin (founder ACK 2026-05-22, PR #526).

        Pre-fix: the LLM tool wrote rows with ``service_id IS NULL``.
        ``compute_reschedule_continuation`` compared
        ``str(None) == "None"`` against the new ``service_id`` string,
        flagging EVERY LLM reschedule as ``service_swap`` chain-broken
        → 100% silent overbilling. The fix normalises ``None`` to
        ``""`` on both sides.

        This test would FAIL with the pre-fix comparator: the test
        above (continuation chain within 90d) would emit
        ``service_swap`` chain-broken, billable=True. The presence
        of ``billable=False`` + ``reschedule_continuation`` here is
        the B1 regression signal.
        """

        root_visit = dj_timezone.now() + timedelta(hours=24)
        old = _make_llm_root_booking(tenant, bot_user, yc_id=555, visit_at=root_visit)
        # Double-check the row really has NULL service_id — if a future
        # refactor changes ``_make_llm_root_booking`` to set the FK,
        # this test must fail-loud rather than silently false-pass.
        assert old.service_id is None, (
            "Fixture broken: LLM-row must have service_id IS NULL to exercise the B1 surface."
        )

        client = FakeYClientsClient()
        client.create_response = BookingRecord(record_id=888, record_hash="h", raw={})
        new_dt = (root_visit + timedelta(days=3)).replace(microsecond=0).isoformat()
        client.times = [_slot_at(new_dt)]

        _exec_reschedule(client=client, new_dt=new_dt, tenant=tenant, bot_user=bot_user)

        new_row = BookingRequest.all_tenants.get(
            comment__contains="yclients_record_id=888",
        )
        # Pre-B1-fix: this would be `True` + `service_swap`.
        # Post-fix: continuation.
        assert new_row.billable is False
        assert new_row.billing_reason == "reschedule_continuation"
        assert "service_swap" not in (new_row.billing_reason or "")


class TestQ12aPartialFailureAuditLLMPath:
    """Acceptance #3 from issue #531: partial-failure path emits an
    audit row with ``q12a_chain_terminator: True`` so the
    Q12aChainTerminatorFilter (issue #530) can surface the ticket for
    manual rebook.
    """

    def test_partial_failure_audit_carries_chain_terminator(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """YClients cancel succeeds, create fails → audit row tagged
        as chain TERMINATOR.

        Also closes Q12-α #533 D5 (partial-failure audit field
        assertion) — the «refactor could silently drop the field»
        regression is pinned by this test's assertions on
        ``q12a_chain_terminator`` + ``q12a_chain_terminator_reason``.

        The exact audit shape is:

        * ``payload["q12a_chain_terminator"] is True``
        * ``payload["q12a_chain_terminator_reason"] == "partial_failure"``
        * ``payload["old_record_id"]`` + ``master_id`` + ``service_id``
          + ``new_datetime`` preserved for manual rebook.

        Without this audit row the operator runbook
        (``docs/runbooks/q12a-partial-failure-triage.md``) has nothing
        to filter on → manual rebook tickets stay invisible.
        """

        root_visit = dj_timezone.now() + timedelta(hours=24)
        _make_llm_root_booking(tenant, bot_user, yc_id=555, visit_at=root_visit)

        client = FakeYClientsClient()
        # Cancel succeeds (default), create raises → partial failure.
        client.create_exc = YClientsAPIError("http_400: slot collision")
        new_dt = (root_visit + timedelta(days=3)).replace(microsecond=0).isoformat()
        client.times = [_slot_at(new_dt)]

        result = _exec_reschedule(client=client, new_dt=new_dt, tenant=tenant, bot_user=bot_user)
        assert result.error == "booking_reschedule_partial_failure"
        # Cancel fired, create attempted exactly once (no retry).
        assert client.cancel_calls == [555]
        assert len(client.create_calls) == 1

        # Audit row with chain-terminator marker.
        terminator_rows = AuditLog.all_tenants.filter(
            payload__has_key="q12a_chain_terminator",
        )
        assert terminator_rows.count() == 1, (
            "Exactly ONE audit row with chain_terminator marker "
            "expected for a single partial-failure reschedule."
        )
        row = terminator_rows.first()
        assert row is not None
        assert row.payload["q12a_chain_terminator"] is True
        assert row.payload["q12a_chain_terminator_reason"] == "partial_failure"
        # Operator-relevant fields preserved (the runbook reads these
        # to drive manual rebook).
        assert row.payload["old_record_id"] == 555
        assert row.payload["new_datetime"] == new_dt
        assert row.payload["master_id"] == 11
        assert row.payload["service_id"] == 22
