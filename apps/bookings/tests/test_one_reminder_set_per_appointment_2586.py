"""DRF-2586: у записи Ayla — один набор напоминаний, откуда бы он ни пришёл.

Запись, сделанная в диалоге, заводила напоминания через фабрику YClients —
UUID записи в ``yclients_record_id``, — а потребитель ``booking.created`` /
``booking.confirmed`` — в ``ayla_appointment_id``. Ключи разные: две пары
на один визит. На пилоте 13 пар, 4 доставлены обеими строками.

Узел «напоминания созданы» прошёл бы и при двойнике, поэтому узлы считают
строки на ``kind``: ровно одна — в обоих порядках прихода (диалог раньше
события и событие раньше диалога) и для старых строк диалога, лежащих в
колонке YClients.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.booking.models import BookingReminder
from apps.bookings.reminders_factory import (
    create_reminders_for_ayla_appointment,
    create_reminders_for_booking,
)
from apps.eventbus.consumers.booking import _schedule_reminders as event_schedules
from apps.identity.models import BotUser
from apps.skills.booking.tools import _reminders_for_record
from apps.skills.booking.tools import _schedule_reminders as dialog_schedules
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

KINDS = (BookingReminder.Kind.DAY_BEFORE, BookingReminder.Kind.TWO_HOURS)


@pytest.fixture(autouse=True)
def _ayla_path(settings):
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="rem-2586", name="Salon 2586")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="bu-2586", chat_id="chat-2586"
    )


@pytest.fixture
def appointment_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def visit_at():
    return timezone.now() + timedelta(days=3)


def _dialog(tenant, bot_user, appointment_id, visit_at) -> None:
    dialog_schedules(
        tenant=tenant,
        bot_user=bot_user,
        yc_id=str(appointment_id),
        visit_at_dt=visit_at,
        master_name="Лера",
        service_name="Массаж",
    )


def _event(tenant, bot_user, appointment_id, visit_at) -> None:
    event_schedules(
        tenant=tenant, bot_user=bot_user, appointment_id=appointment_id, start_at=visit_at
    )


def _rows_per_kind(appointment_id) -> dict[str, int]:
    from apps.booking.reminder_lookup import reminders_for_appointment

    rows = reminders_for_appointment(appointment_id)
    return {kind: rows.filter(kind=kind).count() for kind in KINDS}


def _dialog_direct(tenant, bot_user, appointment_id, visit_at) -> None:
    """Писатель диалога напрямую: ``tools._schedule_reminders`` глотает любое
    исключение, и узел порядка прошёл бы и при упавшем писателе — один набор
    завело бы событие."""
    create_reminders_for_ayla_appointment(
        tenant=tenant,
        bot_user=bot_user,
        appointment_id=appointment_id,
        visit_at=visit_at,
        master_name="Лера",
        service_name="Массаж",
    )


class TestOneSetWhicheverArrivesFirst:
    def test_dialog_then_event_is_one_set(self, tenant, bot_user, appointment_id, visit_at):
        _dialog(tenant, bot_user, appointment_id, visit_at)
        _event(tenant, bot_user, appointment_id, visit_at)

        assert _rows_per_kind(appointment_id) == {kind: 1 for kind in KINDS}

    def test_event_then_dialog_is_one_set(self, tenant, bot_user, appointment_id, visit_at):
        _event(tenant, bot_user, appointment_id, visit_at)
        _dialog(tenant, bot_user, appointment_id, visit_at)

        assert _rows_per_kind(appointment_id) == {kind: 1 for kind in KINDS}
        # Присутствие писателя диалога: имена — его снимок. Без этого узел
        # прошёл бы и при упавшем писателе (``_schedule_reminders`` глотает
        # исключения, а один набор заводит событие).
        names = set(
            BookingReminder.all_tenants.filter(ayla_appointment_id=appointment_id).values_list(
                "master_name", "service_name"
            )
        )
        assert names == {("Лера", "Массаж")}

    def test_the_writers_directly_in_both_orders(self, tenant, bot_user, visit_at):
        first, second = uuid.uuid4(), uuid.uuid4()

        _dialog_direct(tenant, bot_user, first, visit_at)
        _event(tenant, bot_user, first, visit_at)
        _event(tenant, bot_user, second, visit_at)
        _dialog_direct(tenant, bot_user, second, visit_at)

        assert _rows_per_kind(first) == {kind: 1 for kind in KINDS}
        assert _rows_per_kind(second) == {kind: 1 for kind in KINDS}

    def test_the_dialog_now_writes_the_ayla_column(
        self, tenant, bot_user, appointment_id, visit_at
    ):
        _dialog(tenant, bot_user, appointment_id, visit_at)

        rows = BookingReminder.all_tenants.filter(ayla_appointment_id=appointment_id)
        assert rows.count() == 2
        assert not rows.exclude(yclients_record_id=None).exists()

    def test_an_event_does_not_blank_the_dialogs_names(
        self, tenant, bot_user, appointment_id, visit_at
    ):
        _dialog(tenant, bot_user, appointment_id, visit_at)
        _event(tenant, bot_user, appointment_id, visit_at)

        names = set(
            BookingReminder.all_tenants.filter(ayla_appointment_id=appointment_id).values_list(
                "master_name", "service_name"
            )
        )
        assert names == {("Лера", "Массаж")}


class TestOldDialogRowsInTheYclientsColumn:
    def test_an_event_for_an_old_dialog_booking_adds_no_twin(
        self, tenant, bot_user, appointment_id, visit_at
    ):
        """Строки диалога, записанные ДО правки, — в ``yclients_record_id``."""
        create_reminders_for_booking(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id=str(appointment_id),
            visit_at=visit_at,
            master_name="Лера",
            service_name="Массаж",
        )

        _event(tenant, bot_user, appointment_id, visit_at)

        assert _rows_per_kind(appointment_id) == {kind: 1 for kind in KINDS}
        assert not BookingReminder.all_tenants.filter(ayla_appointment_id=appointment_id).exists()


class TestStatusRules:
    def test_the_dialog_rearms_a_cancelled_row_on_reschedule(
        self, tenant, bot_user, appointment_id, visit_at
    ):
        """Перенос гасит PENDING и заводит пару на новое время — тем же ключом."""
        _dialog(tenant, bot_user, appointment_id, visit_at)
        _reminders_for_record(str(appointment_id)).update(status=BookingReminder.Status.CANCELLED)
        later = visit_at + timedelta(days=1)

        _dialog(tenant, bot_user, appointment_id, later)

        rows = BookingReminder.all_tenants.filter(ayla_appointment_id=appointment_id)
        assert set(rows.values_list("status", flat=True)) == {BookingReminder.Status.PENDING}
        assert set(rows.values_list("visit_at", flat=True)) == {later}

    def test_a_late_event_does_not_resurrect_a_sent_reminder(
        self, tenant, bot_user, appointment_id, visit_at
    ):
        _dialog(tenant, bot_user, appointment_id, visit_at)
        BookingReminder.all_tenants.filter(ayla_appointment_id=appointment_id).update(
            status=BookingReminder.Status.SENT
        )

        _event(tenant, bot_user, appointment_id, visit_at)

        statuses = set(
            BookingReminder.all_tenants.filter(ayla_appointment_id=appointment_id).values_list(
                "status", flat=True
            )
        )
        assert statuses == {BookingReminder.Status.SENT}

    def test_a_booking_three_hours_ahead_gets_no_day_before(self, tenant, bot_user, appointment_id):
        """#1146 теперь и для диалога: «завтра ваш визит» за три часа — ложь."""
        soon = timezone.now() + timedelta(hours=3)

        _dialog(tenant, bot_user, appointment_id, soon)

        kinds = set(
            BookingReminder.all_tenants.filter(ayla_appointment_id=appointment_id).values_list(
                "kind", flat=True
            )
        )
        assert kinds == {BookingReminder.Kind.TWO_HOURS}


class TestNoStaleRowAfterReschedule:
    """Ревью DRF-2586: строка про ПРЕЖНЕЕ время, оставшаяся после переноса,
    попадает в эскалацию — менеджеру «клиент не подтвердил визит <старое время>»."""

    def test_an_old_dialog_row_is_rearmed_in_place_not_duplicated(
        self, tenant, bot_user, appointment_id, visit_at
    ):
        create_reminders_for_booking(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id=str(appointment_id),
            visit_at=visit_at,
            master_name="Лера",
            service_name="Массаж",
        )
        BookingReminder.all_tenants.filter(
            yclients_record_id=str(appointment_id), kind=BookingReminder.Kind.DAY_BEFORE
        ).update(status=BookingReminder.Status.SENT_NO_REPLY)
        later = visit_at + timedelta(days=1)

        _dialog(tenant, bot_user, appointment_id, later)

        assert _rows_per_kind(appointment_id) == {kind: 1 for kind in KINDS}
        rows = BookingReminder.all_tenants.filter(yclients_record_id=str(appointment_id))
        assert set(rows.values_list("status", "visit_at")) == {
            (BookingReminder.Status.PENDING, later)
        }

    def test_a_reschedule_into_the_last_day_closes_the_stale_day_before(
        self, tenant, bot_user, appointment_id, visit_at
    ):
        _dialog(tenant, bot_user, appointment_id, visit_at)
        BookingReminder.all_tenants.filter(
            ayla_appointment_id=appointment_id, kind=BookingReminder.Kind.DAY_BEFORE
        ).update(status=BookingReminder.Status.SENT_NO_REPLY)
        soon = timezone.now() + timedelta(hours=5)

        _dialog(tenant, bot_user, appointment_id, soon)

        rows = {
            r.kind: r
            for r in BookingReminder.all_tenants.filter(ayla_appointment_id=appointment_id)
        }
        assert rows[BookingReminder.Kind.DAY_BEFORE].status == (
            BookingReminder.Status.STALE_DROPPED
        )
        assert rows[BookingReminder.Kind.TWO_HOURS].status == BookingReminder.Status.PENDING
        assert rows[BookingReminder.Kind.TWO_HOURS].visit_at == soon

    def test_a_late_event_does_not_move_the_visit_back(
        self, tenant, bot_user, appointment_id, visit_at
    ):
        _dialog(tenant, bot_user, appointment_id, visit_at)
        later = visit_at + timedelta(days=1)
        _dialog(tenant, bot_user, appointment_id, later)

        _event(tenant, bot_user, appointment_id, visit_at)  # опоздавшее, прежнее время

        visits = set(
            BookingReminder.all_tenants.filter(ayla_appointment_id=appointment_id).values_list(
                "visit_at", flat=True
            )
        )
        assert visits == {later}


class TestTheOperatorSeesTheRecord:
    def test_the_reschedule_task_names_the_ayla_record(
        self, tenant, bot_user, appointment_id, visit_at
    ):
        """Было бы «запись None»: у строк диалога ``yclients_record_id`` пуст."""
        from apps.bookings.callbacks import _reschedule_reason

        _dialog(tenant, bot_user, appointment_id, visit_at)
        reminder = BookingReminder.all_tenants.filter(ayla_appointment_id=appointment_id).first()

        text = _reschedule_reason(reminder)

        assert str(appointment_id) in text
        assert "None" not in text


class TestCancelFindsBothColumns:
    def test_cancel_lookup_sees_new_and_old_rows(self, tenant, bot_user, appointment_id, visit_at):
        _dialog(tenant, bot_user, appointment_id, visit_at)
        old_id = uuid.uuid4()
        create_reminders_for_booking(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id=str(old_id),
            visit_at=visit_at,
            master_name="",
            service_name="",
        )

        assert _reminders_for_record(str(appointment_id)).count() == 2
        assert _reminders_for_record(str(old_id)).count() == 2

    def test_without_the_flag_the_yclients_path_is_unchanged(
        self, settings, tenant, bot_user, visit_at
    ):
        settings.BOOKING_VIA_AYLA_REST = False

        dialog_schedules(
            tenant=tenant,
            bot_user=bot_user,
            yc_id="123456",
            visit_at_dt=visit_at,
            master_name="Лера",
            service_name="Массаж",
        )

        rows = BookingReminder.all_tenants.filter(yclients_record_id="123456")
        assert rows.count() == 2
        assert not rows.exclude(ayla_appointment_id=None).exists()
        assert _reminders_for_record("123456").count() == 2
