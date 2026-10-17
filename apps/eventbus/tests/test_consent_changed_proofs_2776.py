"""Что зависит от доставки ``customer.consent.changed``, а что нет — исполнением (DRF-2776).

Решение владельца 05.10 (D) просило доказать цепочку «необработанный
``consent.changed`` → блокировка исходящего ящика → недоставка
напоминаний» и потребовало, чтобы компоненты памяти прекращали обработку
по отозванному согласию. Сверка кода бота (dev f95d9ae4) дала три факта,
и здесь каждый держится исполнением, а не чтением:

1. **Память бота останавливается сразу, без события.** Шлюз
   ``personal_context._gate`` на каждом ходе читает реестр согласий; отзыв
   пишет реестр синхронно. Событие при этом лежит в ящике недоставленным —
   и шлюз всё равно закрыт.
2. **Напоминание о записи не зависит от ящика.** Своя beat-задача
   (``bookings.send_due_reminders``) уходит при забитом ящике и закрытом
   рубильнике диспетчера.
3. **Строка без потребителя не держит соседей.** Подписчик, падающий на
   ``consent.changed``, не мешает остальным строкам пачки уйти в том же
   прогоне; сама строка через ``MAX_ATTEMPTS`` уходит в DLQ, а не висит.

То есть цепочка владельца для бота **опровергнута**: головная блокировка
была в каталожном local-outbox (починена там). А «память-стоп» владельца
уже выполнена устройством шлюза — второй, отложенный подписчик памяти не
нужен.

``transaction=True``: событие согласия испускается в ``on_commit``, а
диспетчер берёт строки ``select_for_update(skip_locked=True)``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.booking.models import BookingReminder, RemoteBookingProxy
from apps.bookings.tasks import send_due_reminders
from apps.consent import services as consent_services
from apps.consent.models import ConsentRecord
from apps.eventbus import dispatcher, services, vocabulary as V
from apps.eventbus.dispatcher import (
    MAX_ATTEMPTS,
    dispatch_pending_events,
    dispatch_pending_events_beat,
)
from apps.eventbus.envelope import Envelope
from apps.eventbus.models import DomainEvent
from apps.identity.models import BotUser
from apps.identity.services.personal_context import _gate
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)

CT = ConsentRecord.ConsentType


@pytest.fixture(autouse=True)
def _switch_closed(settings):
    """Рубильник диспетчера закрыт — как на стенде 04.10."""
    settings.EVENTBUS_DISPATCH_BEAT_ENABLED = False
    settings.EVENTBUS_DISPATCH_BEAT_DRY_RUN = True
    dispatcher.reset_registry_cache()
    yield
    dispatcher.reset_registry_cache()


def _tenant(slug: str) -> Tenant:
    return Tenant.objects.create(slug=slug, name=slug)


def _undelivered(event_name: str) -> int:
    return DomainEvent.objects.filter(
        event_name=event_name, is_dispatched=False, dead_lettered_at__isnull=True
    ).count()


def _emit_booking(i: int) -> None:
    services.emit(
        V.BOOKING_CREATED,
        {
            "booking_id": f"proof-b{i}",
            "customer_id": f"proof-c{i}",
            "service_id": "s",
            "slot_start": "2026-10-05T10:00:00Z",
            "booking_source": "ai_direct",
        },
        actor_type="system",
    )


def _emit_consent(customer_id: str = "proof-consent") -> None:
    services.emit_customer_consent_changed(
        customer_id=customer_id,
        consent_type=CT.MEMORY_GREEN,
        granted=False,
        granted_at="2026-10-05T10:00:00Z",
    )


# ─── 1. память останавливается сразу, событие для этого не нужно ────────────


def test_withdrawn_memory_consent_closes_the_gate_while_the_event_sits_undelivered() -> None:
    tenant = _tenant("proof-memory")
    ayla_id = uuid.uuid4()
    bot_user = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="proof-memory", ayla_user_id=ayla_id
    )
    with tenant_scope(tenant):
        consent_services.grant(bot_user, consent_type=CT.MEMORY_GREEN, source="proof")
    assert _gate(bot_user) == ayla_id, "до отзыва шлюз открыт — иначе узел ничего не доказывает"

    with tenant_scope(tenant):
        consent_services.withdraw(bot_user, consent_type=CT.MEMORY_GREEN, source="proof")

    # Событие отзыва испущено и лежит — доставить его некому: рубильник закрыт.
    assert dispatch_pending_events_beat() == {"mode": "disabled", "pending": None}
    withdrawals = DomainEvent.objects.filter(
        event_name=V.CUSTOMER_CONSENT_CHANGED, data__granted=False, is_dispatched=False
    )
    assert withdrawals.count() == 1
    # ...а память уже закрыта.
    assert _gate(bot_user) is None


# ─── 2. напоминание о записи уходит при забитом ящике ───────────────────────


def test_a_due_booking_reminder_goes_out_with_a_full_outbox_and_the_switch_closed() -> None:
    tenant = _tenant("proof-reminder")
    bot_user = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="proof-reminder", chat_id="chat-proof"
    )
    appointment_id = uuid.uuid4()
    start_at = timezone.now() + timedelta(hours=24)
    RemoteBookingProxy.all_tenants.create(
        appointment_id=appointment_id,
        tenant=tenant,
        bot_user=bot_user,
        start_at=start_at,
        end_at=start_at + timedelta(hours=1),
        status="confirmed",
    )
    reminder = BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        booking_request=None,
        ayla_appointment_id=appointment_id,
        chat_id=bot_user.chat_id,
        visit_at=start_at,
        kind=BookingReminder.Kind.DAY_BEFORE,
        status=BookingReminder.Status.PENDING,
        scheduled_at=timezone.now() - timedelta(minutes=5),
        master_name="Lera",
        service_name="Strizhka",
    )
    # Забитый ящик: неразобранные согласия и записи, как на стенде.
    for i in range(3):
        _emit_consent(f"proof-consent-{i}")
        _emit_booking(i)
    assert _undelivered(V.CUSTOMER_CONSENT_CHANGED) == 3

    with patch("apps.bookings.tasks.send_message") as send:
        result = send_due_reminders()

    reminder.refresh_from_db()
    assert result["sent"] == 1
    assert len(send.call_args_list) == 1
    assert reminder.status == BookingReminder.Status.SENT_NO_REPLY
    # Ящик при этом никто не трогал — напоминание его не читает.
    assert _undelivered(V.CUSTOMER_CONSENT_CHANGED) == 3


# ─── 3. строка без потребителя не держит соседей и не висит вечно ───────────


class _FailsOnConsent:
    """Подписчик, для которого ``consent.changed`` — событие без потребителя."""

    delivered: list[str] = []

    def handle(self, envelope: Envelope) -> None:
        if envelope.event_name == V.CUSTOMER_CONSENT_CHANGED:
            raise RuntimeError("no consumer for consent.changed")
        _FailsOnConsent.delivered.append(envelope.event_name)


def test_a_failing_consent_row_neither_blocks_its_neighbours_nor_lives_forever(settings) -> None:
    settings.DOMAIN_EVENT_SUBSCRIBERS = [f"{__name__}._FailsOnConsent"]
    _FailsOnConsent.delivered = []
    # Строка согласия — ПЕРВАЯ в порядке ``event_id``, то есть в голове пачки.
    _emit_consent()
    _emit_booking(1)
    _emit_booking(2)

    first = dispatch_pending_events()

    assert first["dispatched"] == 2, (
        "соседи головной неудачной строки обязаны уйти в том же прогоне"
    )
    assert _FailsOnConsent.delivered == [V.BOOKING_CREATED, V.BOOKING_CREATED]
    for _ in range(MAX_ATTEMPTS - 1):
        dispatch_pending_events()

    consent_row = DomainEvent.objects.get(event_name=V.CUSTOMER_CONSENT_CHANGED)
    assert consent_row.dead_lettered_at is not None, "строка без потребителя обязана уйти в DLQ"
    assert consent_row.dispatch_attempts == MAX_ATTEMPTS
    assert not consent_row.is_dispatched, "DLQ — не «доставлено»"
    # Уход в DLQ не молчит: диспетчер сам испускает тревогу о деградации.
    assert DomainEvent.objects.filter(event_name=V.SYSTEM_MODULE_HEALTH_DEGRADED).count() == 1
    # После DLQ строку согласия диспетчер больше не берёт: следующий прогон
    # разбирает только тревогу, попыток у согласия не прибавляется.
    dispatch_pending_events()
    consent_row.refresh_from_db()
    assert consent_row.dispatch_attempts == MAX_ATTEMPTS
