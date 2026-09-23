"""Отмена из напоминания доходит до Ayla, а не только до нашей строки (DRF-2337).

Перепись DRF-2327, место № 2 из пяти — самое тяжёлое, потому что расхождение
выходит за экран: мастер ждёт, слот занят, человек не придёт.

Замер: строки записей, пришедшие событием из Ayla, создаются с
``yclients_record_id=None`` и заполненным ``ayla_appointment_id``.
``_handle_cancel`` смотрел ТОЛЬКО на ``yclients_record_id``, на пустом
коротко замыкался — и отвечал человеку «Запись отменена». По ADR-0009
состояние Ayla меняется только её REST, а REST здесь не звали вовсе. То есть
это не «поставщик отказал», а «не спросили».

Правило:

* у строки из Ayla отмена идёт REST-вызовом по ``ayla_appointment_id``;
* **успех сообщается только по факту**: сервис не ответил или салон отказал —
  человеку не говорят «отменена», и наша строка остаётся открытой, чтобы
  повтор был возможен;
* «записи уже нет» — это то, чего человек хотел, и говорится честно;
* строка из YClients идёт прежним путём: там отмена «по возможности» была
  осмысленным решением, и узел на неё остаётся — только сузился до
  настоящего сбоя поставщика.

Слов своих не сочиняем: исходы отмены уже названы в
:mod:`apps.orchestrator.visits` (карточка визита, DRF-1547) — тот же набор
исходов, те же слова.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.booking.models import BookingReminder
from apps.bookings.callbacks import (
    REPLY_ALREADY_HANDLED,
    REPLY_CANCELLED,
    BookingReminderCallbackSkill,
)
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.orchestrator.visits import (
    _CANCEL_GONE_TEXT,
    _CANCEL_REFUSED_TEXT,
    _CANCEL_UNAVAILABLE_TEXT,
)
from apps.skills.base import SkillContext, claims_done_of
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

_CANCEL = "apps.booking.services.records.cancel_booking"


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="rem-2337", name="Salon 2337")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-2337",
        chat_id="chat-2337",
        phone="79991112233",
        client_name="Ольга",
    )


@pytest.fixture
def conversation(tenant: Tenant, bot_user: BotUser) -> Conversation:
    return Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)


def _ayla_reminder(tenant: Tenant, bot_user: BotUser, appointment_id=None) -> BookingReminder:
    """Строка ровно такая, какую кладёт консьюмер событий Ayla."""
    visit_at = timezone.now() + timedelta(hours=24)
    return BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        yclients_record_id=None,
        ayla_appointment_id=appointment_id or uuid.uuid4(),
        chat_id=bot_user.chat_id,
        visit_at=visit_at,
        kind=BookingReminder.Kind.DAY_BEFORE,
        status=BookingReminder.Status.SENT_NO_REPLY,
        scheduled_at=visit_at - timedelta(hours=24),
        master_name="Лера",
        service_name="Массаж",
    )


def _yclients_reminder(tenant: Tenant, bot_user: BotUser) -> BookingReminder:
    visit_at = timezone.now() + timedelta(hours=24)
    return BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        yclients_record_id="123456",
        chat_id=bot_user.chat_id,
        visit_at=visit_at,
        kind=BookingReminder.Kind.DAY_BEFORE,
        status=BookingReminder.Status.SENT_NO_REPLY,
        scheduled_at=visit_at - timedelta(hours=24),
        master_name="Лера",
        service_name="Массаж",
    )


def _tap(reminder, bot_user, conversation):
    ctx = SkillContext(
        bot_user=bot_user,
        conversation=conversation,
        message_text=f"cb:rem:cancel:{reminder.pk}",
        has_attachments=False,
    )
    return BookingReminderCallbackSkill().handle(ctx)


class TestAnAylaBookingIsCancelledInAyla:
    def test_the_rest_call_is_made_by_appointment_id(
        self,
        tenant,
        bot_user,
        conversation,
    ) -> None:
        appointment_id = uuid.uuid4()
        reminder = _ayla_reminder(tenant, bot_user, appointment_id)

        with patch(_CANCEL, return_value="ok") as cancel:
            _tap(reminder, bot_user, conversation)

        assert cancel.call_count == 1
        assert cancel.call_args.kwargs["appointment_id"] == str(appointment_id)

    def test_a_successful_cancel_says_so_and_closes_the_row(
        self,
        tenant,
        bot_user,
        conversation,
    ) -> None:
        reminder = _ayla_reminder(tenant, bot_user)

        with patch(_CANCEL, return_value="ok"):
            result = _tap(reminder, bot_user, conversation)

        assert result.reply_text == REPLY_CANCELLED
        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.CANCELLED

    def test_an_appointment_already_gone_is_said_honestly(
        self,
        tenant,
        bot_user,
        conversation,
    ) -> None:
        """Человек хотел, чтобы записи не было, — её нет. Но не «я отменила»."""
        reminder = _ayla_reminder(tenant, bot_user)

        with patch(_CANCEL, return_value="already_gone"):
            result = _tap(reminder, bot_user, conversation)

        assert result.reply_text == _CANCEL_GONE_TEXT
        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.CANCELLED


class TestAFailedCancelIsNeverReportedAsSuccess:
    """Главное правило листа: успех — только по факту."""

    def test_an_unreachable_service_does_not_say_cancelled(
        self,
        tenant,
        bot_user,
        conversation,
    ) -> None:
        reminder = _ayla_reminder(tenant, bot_user)

        with patch(_CANCEL, return_value="backend_unavailable"):
            result = _tap(reminder, bot_user, conversation)

        assert result.reply_text != REPLY_CANCELLED
        assert result.reply_text == _CANCEL_UNAVAILABLE_TEXT

    def test_a_refusal_by_the_salon_does_not_say_cancelled(
        self,
        tenant,
        bot_user,
        conversation,
    ) -> None:
        reminder = _ayla_reminder(tenant, bot_user)

        with patch(_CANCEL, return_value="refused"):
            result = _tap(reminder, bot_user, conversation)

        assert result.reply_text != REPLY_CANCELLED
        assert result.reply_text == _CANCEL_REFUSED_TEXT

    def test_a_failed_cancel_leaves_the_row_open(
        self,
        tenant,
        bot_user,
        conversation,
    ) -> None:
        """Иначе повтор упрётся в «эта запись уже обработана» — тупик."""
        reminder = _ayla_reminder(tenant, bot_user)

        with patch(_CANCEL, return_value="backend_unavailable"):
            _tap(reminder, bot_user, conversation)

        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.SENT_NO_REPLY

    def test_a_second_tap_after_a_failure_tries_again(
        self,
        tenant,
        bot_user,
        conversation,
    ) -> None:
        reminder = _ayla_reminder(tenant, bot_user)

        with patch(_CANCEL, return_value="backend_unavailable"):
            _tap(reminder, bot_user, conversation)
        with patch(_CANCEL, return_value="ok") as second:
            result = _tap(reminder, bot_user, conversation)

        assert second.call_count == 1
        assert result.reply_text == REPLY_CANCELLED

    def test_a_replay_after_a_successful_cancel_does_not_call_twice(
        self,
        tenant,
        bot_user,
        conversation,
    ) -> None:
        """Контроль: защита от двойного тапа не потеряна."""
        reminder = _ayla_reminder(tenant, bot_user)

        with patch(_CANCEL, return_value="ok"):
            _tap(reminder, bot_user, conversation)
        with patch(_CANCEL, return_value="ok") as second:
            result = _tap(reminder, bot_user, conversation)

        assert second.call_count == 0
        assert result.reply_text == REPLY_ALREADY_HANDLED


class TestTheYClientsPathIsUnchanged:
    """Сужение, а не снос: прежнее решение осталось при своём случае."""

    def test_a_yclients_row_does_not_call_ayla(
        self,
        tenant,
        bot_user,
        conversation,
    ) -> None:
        reminder = _yclients_reminder(tenant, bot_user)

        with (
            patch(_CANCEL) as cancel,
            patch("apps.bookings.callbacks._try_yclients_cancel", return_value=True),
        ):
            result = _tap(reminder, bot_user, conversation)

        assert cancel.call_count == 0
        assert result.reply_text == REPLY_CANCELLED

    def test_a_real_supplier_outage_still_does_not_block_the_local_cancel(
        self,
        tenant,
        bot_user,
        conversation,
    ) -> None:
        """Тот самый прежний узел — теперь только про настоящий сбой поставщика."""
        reminder = _yclients_reminder(tenant, bot_user)

        with patch("apps.bookings.callbacks._try_yclients_cancel", return_value=False):
            result = _tap(reminder, bot_user, conversation)

        assert result.reply_text == REPLY_CANCELLED
        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.CANCELLED


class TestTheBranchDeclaresWhatItClaims:
    """DRF-2341: ветка, утверждающая выполненное, объявляет это признаком.

    По тексту такие ветки не различить — «Запись отменена» и «Не удалось
    отменить» отличаются одним словом, и формулировки правит владелец.
    Признак ставит тот, кто знает, что сделал; ветка без признака сторожу
    класса невидима.
    """

    def test_a_confirmed_cancel_declares_it(self, tenant, bot_user, conversation) -> None:
        reminder = _ayla_reminder(tenant, bot_user)

        with patch(_CANCEL, return_value="ok"):
            result = _tap(reminder, bot_user, conversation)

        assert result.claims_done is True

    def test_a_failed_cancel_claims_nothing(self, tenant, bot_user, conversation) -> None:
        reminder = _ayla_reminder(tenant, bot_user)

        with patch(_CANCEL, return_value="backend_unavailable"):
            result = _tap(reminder, bot_user, conversation)

        assert result.claims_done is False

    def test_an_appointment_already_gone_claims_nothing(
        self,
        tenant,
        bot_user,
        conversation,
    ) -> None:
        """Записи не стало не от нашего действия — приписывать себе нечего."""
        reminder = _ayla_reminder(tenant, bot_user)

        with patch(_CANCEL, return_value="already_gone"):
            result = _tap(reminder, bot_user, conversation)

        assert result.claims_done is False


class TestNoAnswerIsADeadEnd:
    """§72: «не получилось» без следующего шага — тупик."""

    @pytest.mark.parametrize("status", ["backend_unavailable", "refused"])
    def test_a_refusal_still_offers_a_next_step(
        self,
        tenant,
        bot_user,
        conversation,
        status,
    ) -> None:
        reminder = _ayla_reminder(tenant, bot_user)

        with patch(_CANCEL, return_value=status):
            result = _tap(reminder, bot_user, conversation)

        assert result.action_data, "человеку некуда нажать после отказа"


class TestTheEvidenceIsReadThroughOneDoor:
    """DRF-2341: признак и подтверждение читает один читатель, не два.

    Носитель у ответов разный — поле у ``SkillResult``, ключ ``meta`` там,
    где объекта-ответа нет. Имена общие, и расходиться в чтении нельзя:
    разойдясь, сторож класса молча перестанет видеть часть веток.
    """

    def test_a_confirmed_cancel_carries_evidence_from_the_source(
        self,
        tenant,
        bot_user,
        conversation,
    ) -> None:
        reminder = _ayla_reminder(tenant, bot_user)

        with patch(_CANCEL, return_value="ok"):
            result = _tap(reminder, bot_user, conversation)

        claims, evidence = claims_done_of(result)
        assert claims is True
        assert evidence == "ayla.appointments.cancel:2xx"

    def test_a_failed_cancel_carries_neither(
        self,
        tenant,
        bot_user,
        conversation,
    ) -> None:
        reminder = _ayla_reminder(tenant, bot_user)

        with patch(_CANCEL, return_value="refused"):
            result = _tap(reminder, bot_user, conversation)

        assert claims_done_of(result) == (False, "")

    def test_the_yclients_branch_claims_without_evidence(
        self,
        tenant,
        bot_user,
        conversation,
    ) -> None:
        """Честное состояние, а не недосмотр: подтверждать там нечем."""
        reminder = _yclients_reminder(tenant, bot_user)

        with patch("apps.bookings.callbacks._try_yclients_cancel", return_value=True):
            result = _tap(reminder, bot_user, conversation)

        assert claims_done_of(result) == (True, "")

    def test_the_reader_sees_the_meta_carrier_too(self) -> None:
        """Обработчики без ``SkillResult`` кладут признак в ``meta``."""
        assert claims_done_of({"claims_done": True, "claims_done_evidence": "handoff.task:id"}) == (
            True,
            "handoff.task:id",
        )
