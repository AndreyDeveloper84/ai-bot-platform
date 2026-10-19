"""DRF-2919 п.2 — ``deletion_eligibility``: обязательства, а не роль.

Каждый блок проверяется парой: тот же человек без обязательства проходит,
с ним — нет. Деньги Ayla (C2/C3) подменены на границе сервиса биллинга,
всё остальное — настоящие строки.

Узлы, которые ловят подмены, названные в задаче:
* ложный ELIGIBLE при недоступности данных — ``TestUnavailableIsNeverEligible``;
* блок по запрещённому сигналу — ``TestForbiddenSignalsDoNotBlock``;
* хранение как блок — ``TestRetentionNeverBlocks``;
* обязательство кабинета как личное — ``TestCabinetObligationsAreNotPersonal``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone as dj_tz

from apps.booking.models import BookingRequest, PaymentMirror, RemoteBookingProxy
from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.identity.services import deletion_eligibility as de
from apps.identity.services.deletion_eligibility import (
    Action,
    ObligationOwner,
    Operation,
    Outcome,
    Reason,
    deletion_eligibility,
)
from apps.identity.services.role_resolver import resolve_role
from apps.identity.services.solo_onboarding import create_solo_provider, is_solo_provider
from apps.master_api.services.billing import BillingProxyResult, ProxyStatus
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

UID = "2919300"
AYLA_ID = uuid.UUID("2c3d4e5f-6a7b-4c8d-9e0f-a1b2c3d42919")
BILLING = "apps.master_api.services.billing"


def _status(*, sub="active", fees="0.00") -> BillingProxyResult:
    return BillingProxyResult(
        status=ProxyStatus.OK,
        payload={"subscription": {"status": sub}, "fees": {"pending_total": fees}},
    )


def _payout(amount="0.00") -> BillingProxyResult:
    return BillingProxyResult(status=ProxyStatus.OK, payload={"pending_amount": amount})


@pytest.fixture
def billing():
    """Ayla C2/C3: по умолчанию долгов и выплат нет."""

    class _Billing:
        status = _status()
        payout = _payout()
        calls = 0

    state = _Billing()

    def _c2(card, **_k):
        state.calls += 1
        if isinstance(state.status, Exception):
            raise state.status
        return state.status

    def _c3(card, **_k):
        if isinstance(state.payout, Exception):
            raise state.payout
        return state.payout

    with (
        patch(f"{BILLING}.billing_status_for_master", _c2),
        patch(f"{BILLING}.payout_preview_for_master", _c3),
    ):
        yield state


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="elig-2919", name="Студия", timezone="Europe/Moscow")


@pytest.fixture
def person(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=UID, chat_id="c-elig", display_name="Анна"
    )


def _card(tenant, row=None, *, ayla_user_id=None, external_id=2919) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=external_id,
        external_updated_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        name="Анна",
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        mode=CatalogMaster.Mode.INVITE,
        linked_bot_user=row,
        ayla_user_id=ayla_user_id,
    )


def _visit(tenant, *, specialist_id=None, bot_user=None, days=3, status="confirmed"):
    start = dj_tz.now() + timedelta(days=days)
    return RemoteBookingProxy.all_tenants.create(
        tenant=tenant,
        appointment_id=uuid.uuid4(),
        bot_user=bot_user,
        specialist_id=specialist_id,
        start_at=start,
        end_at=start + timedelta(hours=1),
        status=status,
    )


def _op(result, op: Operation):
    return result.operations[op]


# --- свои обязательства блокируют ---------------------------------------------


class TestOwnBookingsBlock:
    def test_future_mirror_booking_blocks_erasure_and_exit(self, tenant, person, billing):
        card = _card(tenant, person, ayla_user_id=AYLA_ID)
        assert deletion_eligibility(person).erasure_may_start

        _visit(tenant, specialist_id=card.id)
        result = deletion_eligibility(person)

        erasure = result.erasure
        assert erasure.outcome is Outcome.SETTLE_FIRST
        assert Reason.ACTIVE_BOOKINGS in erasure.reasons
        assert Action.SETTLE_BOOKINGS in erasure.next_actions
        assert erasure.obligation_owner is ObligationOwner.SELF
        assert _op(result, Operation.STAFF_EXIT).outcome is Outcome.SETTLE_FIRST
        assert _op(result, Operation.ACCOUNT_DEACTIVATION).outcome is Outcome.SETTLE_FIRST
        assert not result.erasure_may_start

    def test_local_booking_counts_when_the_mirror_says_nothing(self, tenant, person, billing):
        card = _card(tenant, person, ayla_user_id=AYLA_ID)
        BookingRequest.all_tenants.create(
            tenant=tenant,
            master=card,
            service_name="Стрижка",
            client_name="Клиент",
            client_phone="+71112223344",
            status=BookingRequest.Status.CONFIRMED,
            visit_at=dj_tz.now() + timedelta(days=2),
        )
        assert RemoteBookingProxy.all_tenants.count() == 0
        assert Reason.ACTIVE_BOOKINGS in deletion_eligibility(person).erasure.reasons

    def test_past_and_cancelled_visits_are_not_obligations(self, tenant, person, billing):
        card = _card(tenant, person, ayla_user_id=AYLA_ID)
        _visit(tenant, specialist_id=card.id, days=-3, status="completed")
        _visit(tenant, specialist_id=card.id, days=3, status="cancelled")
        result = deletion_eligibility(person)
        assert result.erasure_may_start
        assert Reason.ACTIVE_BOOKINGS not in result.erasure.reasons

    def test_card_found_by_the_catalog_key_alone(self, tenant, person, billing):
        """Второй факт идентичности: карточка без ``linked_bot_user``, но с ключом Ayla человека."""
        BotUser.all_tenants.filter(pk=person.pk).update(ayla_user_id=AYLA_ID)
        card = _card(tenant, None, ayla_user_id=AYLA_ID)
        _visit(tenant, specialist_id=card.id)
        assert Reason.ACTIVE_BOOKINGS in deletion_eligibility(person).erasure.reasons

    def test_card_on_another_shell_of_the_same_person(self, tenant, person, billing):
        other = Tenant.objects.create(slug="elig-2919-b", name="Другая", timezone="Europe/Moscow")
        row = BotUser.all_tenants.create(
            tenant=other, channel="max", channel_user_id=UID, chat_id="c-elig-b"
        )
        card = _card(other, row, ayla_user_id=AYLA_ID, external_id=2920)
        _visit(other, specialist_id=card.id)
        assert not deletion_eligibility(person).erasure_may_start

    def test_archived_card_is_not_a_working_role(self, tenant, person, billing):
        card = _card(tenant, person, ayla_user_id=AYLA_ID)
        _visit(tenant, specialist_id=card.id)
        assert not deletion_eligibility(person).erasure_may_start
        CatalogMaster.all_tenants.filter(pk=card.pk).update(archived_at=dj_tz.now())
        result = deletion_eligibility(person)
        assert _op(result, Operation.STAFF_EXIT).outcome is Outcome.NOT_APPLICABLE


class TestOwnDebtBlocks:
    @pytest.mark.parametrize(
        "status",
        [_status(sub="past_due"), _status(fees="270.00")],
        ids=["past_due", "pending_fees"],
    )
    def test_own_debt_is_self_and_blocks(self, tenant, person, billing, status):
        _card(tenant, person, ayla_user_id=AYLA_ID)
        assert deletion_eligibility(person).erasure_may_start

        billing.status = status
        erasure = deletion_eligibility(person).erasure
        assert erasure.outcome is Outcome.SETTLE_FIRST
        assert erasure.reasons == (Reason.FINANCIAL_OBLIGATIONS,)
        assert erasure.next_actions == (Action.SETTLE_FINANCES,)
        assert erasure.obligation_owner is ObligationOwner.SELF


# --- недоступность — никогда не ELIGIBLE --------------------------------------


class TestUnavailableIsNeverEligible:
    @pytest.mark.parametrize(
        "failure",
        [
            BillingProxyResult(status=ProxyStatus.UPSTREAM_ERROR),
            BillingProxyResult(status=ProxyStatus.CLIENT_ERROR),
            RuntimeError("billing client not configured"),
        ],
        ids=["upstream", "client_error", "raises"],
    )
    def test_billing_unavailable_is_verification_required(self, tenant, person, billing, failure):
        _card(tenant, person, ayla_user_id=AYLA_ID)
        assert deletion_eligibility(person).erasure.outcome is Outcome.IMMEDIATE

        billing.status = failure
        erasure = deletion_eligibility(person).erasure
        assert erasure.outcome is Outcome.REFUSED_IN_PART
        assert erasure.reasons == (Reason.VERIFICATION_REQUIRED,)
        # Не тупик: путь есть.
        assert erasure.next_actions == (Action.CONTACT_SUPPORT,)
        assert Reason.ELIGIBLE not in erasure.reasons

    def test_unlinked_master_with_a_trace_is_verification_required(self, tenant, person, billing):
        card = _card(tenant, person, ayla_user_id=None)
        assert deletion_eligibility(person).erasure_may_start

        _visit(tenant, specialist_id=card.id, days=-10, status="completed")
        erasure = deletion_eligibility(person).erasure
        assert erasure.reasons == (Reason.VERIFICATION_REQUIRED,)
        assert not deletion_eligibility(person).erasure_may_start
        # Без ключа Ayla звать биллинг не по чему.
        assert billing.calls == 0

    def test_unlinked_master_with_a_local_booking_trace(self, tenant, person, billing):
        card = _card(tenant, person, ayla_user_id=None)
        BookingRequest.all_tenants.create(
            tenant=tenant,
            master=card,
            service_name="Стрижка",
            client_name="Клиент",
            client_phone="+71112223344",
            status=BookingRequest.Status.CONFIRMED,
            visit_at=dj_tz.now() + timedelta(days=5),
        )
        erasure = deletion_eligibility(person).erasure
        assert erasure.reasons == (Reason.ACTIVE_BOOKINGS, Reason.VERIFICATION_REQUIRED)
        assert erasure.next_actions == (Action.SETTLE_BOOKINGS, Action.CONTACT_SUPPORT)

    def test_identity_conflict_is_verification_required(self, tenant, person, billing):
        other = Tenant.objects.create(slug="elig-2919-c", name="Ещё", timezone="Europe/Moscow")
        BotUser.all_tenants.filter(pk=person.pk).update(ayla_user_id=AYLA_ID)
        BotUser.all_tenants.create(
            tenant=other,
            channel="max",
            channel_user_id=UID,
            chat_id="c-elig-c",
            ayla_user_id=uuid.uuid4(),
        )
        erasure = deletion_eligibility(person).erasure
        assert erasure.reasons == (Reason.VERIFICATION_REQUIRED,)
        assert erasure.obligation_owner is ObligationOwner.UNKNOWN


# --- запрещённые сигналы не блокируют -----------------------------------------


class TestForbiddenSignalsDoNotBlock:
    def test_abandoned_solo_deletes_itself(self, db, billing):
        seed = create_solo_provider(channel="max", channel_user_id="2919400", display_name="Соло")
        row = seed.bot_user
        # Все четыре запрещённых сигнала говорят «рабочий кабинет».
        assert resolve_role(row).primary_role != "customer"
        assert is_solo_provider(seed.tenant)
        assert seed.master.ayla_user_id is None

        for is_active, synced in ((True, None), (False, None), (True, dj_tz.now())):
            Tenant.objects.filter(pk=seed.tenant.pk).update(
                is_active=is_active, last_catalog_sync_ok_at=synced
            )
            result = deletion_eligibility(row)
            assert result.erasure.outcome is Outcome.IMMEDIATE, (is_active, synced)
            assert result.erasure.reasons == (Reason.ELIGIBLE,)
            closure = _op(result, Operation.CABINET_CLOSURE)
            assert closure.outcome is Outcome.IMMEDIATE
            assert closure.next_actions == (Action.CLOSE_EMPTY_CABINET,)
            assert _op(result, Operation.STAFF_EXIT).outcome is Outcome.IMMEDIATE

        # Эвристика «1 человек = соло» не участвует: финансы без ключа Ayla
        # и без следа деятельности — не вопрос, и биллинг не звали.
        assert billing.calls == 0

    def test_the_same_solo_with_a_booking_is_blocked(self, db, billing):
        seed = create_solo_provider(channel="max", channel_user_id="2919401", display_name="Соло")
        _visit(seed.tenant, specialist_id=seed.master.id)
        result = deletion_eligibility(seed.bot_user)
        assert Reason.ACTIVE_BOOKINGS in result.erasure.reasons
        assert _op(result, Operation.CABINET_CLOSURE).outcome is Outcome.SETTLE_FIRST

    def test_plain_customer_is_eligible_and_has_no_cabinet(self, person, billing):
        result = deletion_eligibility(person)
        assert result.request_accepted is True
        assert result.erasure.outcome is Outcome.IMMEDIATE
        assert _op(result, Operation.STAFF_EXIT).outcome is Outcome.NOT_APPLICABLE
        assert _op(result, Operation.CABINET_CLOSURE).outcome is Outcome.NOT_APPLICABLE


# --- хранение — никогда не блок -----------------------------------------------


class TestRetentionNeverBlocks:
    def test_client_history_is_partial_with_retention(self, tenant, person, billing):
        assert deletion_eligibility(person).erasure.outcome is Outcome.IMMEDIATE

        _visit(tenant, bot_user=person, days=-30, status="completed")
        result = deletion_eligibility(person)
        assert result.erasure.outcome is Outcome.PARTIAL_WITH_RETENTION
        assert result.erasure.reasons == (Reason.RETENTION_REQUIRED,)
        assert Action.EXPLAIN_RETAINED_DATA in result.erasure.next_actions
        assert result.erasure_may_start
        # Деактивация аккаунта хранением не затронута.
        assert _op(result, Operation.ACCOUNT_DEACTIVATION).outcome is Outcome.IMMEDIATE


# --- обязательства кабинета и организации — не личные -------------------------


class TestCabinetObligationsAreNotPersonal:
    @pytest.fixture
    def owner_with_staff(self, tenant, person):
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=person, role=TenantStaff.Role.OWNER)
        colleague = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="2919500", chat_id="c-col"
        )
        TenantStaff.all_tenants.create(
            tenant=tenant, bot_user=colleague, role=TenantStaff.Role.ADMIN
        )
        return colleague

    def test_owner_with_staff_may_erase_but_not_exit(
        self, tenant, person, billing, owner_with_staff
    ):
        result = deletion_eligibility(person)

        exit_ = _op(result, Operation.STAFF_EXIT)
        assert exit_.outcome is Outcome.SETTLE_FIRST
        assert exit_.reasons == (Reason.STAFF_TRANSFER_REQUIRED,)
        assert exit_.next_actions == (Action.TRANSFER_MANAGEMENT,)
        assert exit_.obligation_owner is ObligationOwner.CABINET_OWNER

        closure = _op(result, Operation.CABINET_CLOSURE)
        assert closure.outcome is Outcome.SETTLE_FIRST
        assert closure.obligation_owner is ObligationOwner.ORGANIZATION

        assert result.erasure_may_start
        assert result.erasure.outcome is Outcome.PARTIAL_WITH_RETENTION
        assert Reason.STAFF_TRANSFER_REQUIRED in result.erasure.reasons
        assert _op(result, Operation.ACCOUNT_DEACTIVATION).outcome is Outcome.IMMEDIATE

    def test_colleague_gone_means_no_transfer(self, tenant, person, billing, owner_with_staff):
        TenantStaff.all_tenants.filter(bot_user=owner_with_staff).update(deactivated_at=dj_tz.now())
        result = deletion_eligibility(person)
        assert _op(result, Operation.STAFF_EXIT).outcome is Outcome.IMMEDIATE
        assert _op(result, Operation.CABINET_CLOSURE).next_actions == (Action.CLOSE_EMPTY_CABINET,)

    def test_enrolled_master_counts_as_staff(self, tenant, person, billing):
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=person, role=TenantStaff.Role.OWNER)
        colleague = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="2919501", chat_id="c-m"
        )
        _card(tenant, colleague, external_id=2930)
        reasons = _op(deletion_eligibility(person), Operation.STAFF_EXIT).reasons
        assert reasons == (Reason.STAFF_TRANSFER_REQUIRED,)

    def test_colleagues_bookings_are_the_cabinets_not_the_owners(
        self, tenant, person, billing, owner_with_staff
    ):
        _visit(tenant, specialist_id=uuid.uuid4())
        result = deletion_eligibility(person)
        assert result.erasure_may_start
        assert Reason.ACTIVE_BOOKINGS in _op(result, Operation.CABINET_CLOSURE).reasons
        assert _op(result, Operation.ACCOUNT_DEACTIVATION).outcome is Outcome.IMMEDIATE

    def test_admin_leaving_a_staffed_cabinet_transfers_nothing(self, tenant, person, billing):
        owner = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="2919502", chat_id="c-o"
        )
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=owner, role=TenantStaff.Role.OWNER)
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=person, role=TenantStaff.Role.ADMIN)
        result = deletion_eligibility(person)
        assert _op(result, Operation.STAFF_EXIT).outcome is Outcome.IMMEDIATE
        assert _op(result, Operation.CABINET_CLOSURE).outcome is Outcome.NOT_APPLICABLE

    def test_policy_flip_is_one_switch(self, tenant, person, billing, owner_with_staff):
        assert deletion_eligibility(person).erasure_may_start
        with patch.object(de, "BLOCK_ERASURE_ON_CABINET_TRANSFER", True):
            erasure = deletion_eligibility(person).erasure
        assert erasure.outcome is Outcome.SETTLE_FIRST
        assert erasure.reasons == (Reason.STAFF_TRANSFER_REQUIRED,)


class TestMoneyOwedToThePerson:
    def test_payout_owed_to_master_is_organizations_and_kept(self, tenant, person, billing):
        _card(tenant, person, ayla_user_id=AYLA_ID)
        billing.payout = _payout("5730.00")
        result = deletion_eligibility(person)
        assert result.erasure_may_start
        assert result.erasure.outcome is Outcome.PARTIAL_WITH_RETENTION
        assert Reason.FINANCIAL_OBLIGATIONS in result.erasure.reasons
        assert result.erasure.obligation_owner is ObligationOwner.ORGANIZATION
        # Уходу из кабинета выплата не мешает.
        assert _op(result, Operation.STAFF_EXIT).outcome is Outcome.IMMEDIATE

        with patch.object(de, "BLOCK_ERASURE_ON_MONEY_OWED_TO_PERSON", True):
            assert not deletion_eligibility(person).erasure_may_start

    def test_client_prepayment_on_a_live_visit(self, tenant, person, billing):
        visit = _visit(tenant, bot_user=person)
        assert deletion_eligibility(person).erasure.reasons == (Reason.RETENTION_REQUIRED,)

        PaymentMirror.all_tenants.create(
            tenant=tenant,
            appointment_id=visit.appointment_id,
            capture_state=PaymentMirror.CaptureState.AUTHORIZED,
            amount=Decimal("1500.00"),
        )
        result = deletion_eligibility(person)
        assert result.erasure_may_start
        assert Reason.FINANCIAL_OBLIGATIONS in result.erasure.reasons
        assert result.erasure.obligation_owner is ObligationOwner.ORGANIZATION

        with patch.object(de, "BLOCK_ERASURE_ON_MONEY_OWED_TO_PERSON", True):
            erasure = deletion_eligibility(person).erasure
        assert erasure.outcome is Outcome.SETTLE_FIRST
        assert erasure.next_actions == (Action.SETTLE_FINANCES,)

    def test_refunded_payment_is_settled(self, tenant, person, billing):
        visit = _visit(tenant, bot_user=person)
        PaymentMirror.all_tenants.create(
            tenant=tenant,
            appointment_id=visit.appointment_id,
            capture_state=PaymentMirror.CaptureState.REFUNDED,
            amount=Decimal("1500.00"),
        )
        assert Reason.FINANCIAL_OBLIGATIONS not in deletion_eligibility(person).erasure.reasons

    def test_payout_unavailable_follows_the_same_policy(self, tenant, person, billing):
        _card(tenant, person, ayla_user_id=AYLA_ID)
        billing.payout = BillingProxyResult(status=ProxyStatus.UPSTREAM_ERROR)
        assert deletion_eligibility(person).erasure_may_start
        with patch.object(de, "BLOCK_ERASURE_ON_MONEY_OWED_TO_PERSON", True):
            erasure = deletion_eligibility(person).erasure
        assert erasure.reasons == (Reason.VERIFICATION_REQUIRED,)


def test_as_dict_is_closed_vocabulary(tenant, person, billing):
    body = deletion_eligibility(person).as_dict()
    assert body["request_accepted"] is True
    assert set(body["operations"]) == {op.value for op in Operation}
    assert body["operations"]["PERSONAL_DATA_ERASURE"] == {
        "outcome": "IMMEDIATE",
        "reasons": ["ELIGIBLE"],
        "next_actions": ["PROCEED"],
        "obligation_owner": "SELF",
    }
