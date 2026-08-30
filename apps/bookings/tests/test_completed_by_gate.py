"""Последствия завершения визита гейтятся по `completed_by` (решение владельца 30.08).

### Что произошло на пилоте 26.08

    15:42:50.796   визит закрыт           completed_by = system
    15:42:51.494   уведомление создано    «Оцените мастера»

698 миллисекунд между закрытием по часам и запросом отзыва живому
клиенту. Никто не подтверждал, что человек пришёл. Запрос не дошёл
только потому, что все push падают (DRF-1030) — сломанный push работал
предохранителем.

### Что здесь проверяется

Визит **должен** закрываться по часам: иначе он висит вечно и ломает
расписание. Закрываться он должен **тише** — без действий, которые
требуют подтверждения, что человек пришёл. Гейтим два последствия:

1. **Начисление баллов** (`apps.loyalty.subscribers.LoyaltySubscriber`).
   За ним каскадом идут бонус за возврат, выплата рефереру — третьему
   лицу — и пересчёт тира. Всё это висит на одном `_credit_visit`,
   поэтому и гейт один.
2. **Запрос отзыва** — `bookings.send_post_visit_followups`, «Как прошёл
   вчерашний визит?». Локальный аналог «Оцените мастера»: единственное
   сообщение в этом репозитории, которое уходит клиенту за состоявшийся
   визит. Два пути — через `BookingRequest.completed_at` (закрытие по
   часам, `apps.bookings.tasks.detect_completed_bookings`) и через
   зеркало `RemoteBookingProxy` (закрытие в Ayla).

### Правило контура: отрицательному утверждению нужна положительная стража

Каждое «при системном закрытии последствие не наступило» стоит рядом с
«при закрытии человеком — наступило, на тех же данных». Иначе тест
зелен на коде, который вообще ничего не делает. Пары ниже отличаются
ровно одним полем — значением `completed_by`.

Плюс стража против пере-гейта: :class:`TestVisitStillCloses` — визит с
`completed_by=system` обязан закрыться, статус обязан долететь до
зеркала. Гейт гасит последствия, а не закрытие.

### Дат в этом модуле нет

Все моменты времени — смещения от ``timezone.now()``. Дата-константа
``2026-08-25`` держала ``dev`` красным четверо суток.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.booking.models import BookingReminder, BookingRequest, RemoteBookingProxy
from apps.bookings import followups as followups_mod
from apps.bookings.tasks import detect_completed_bookings
from apps.catalog.models import CatalogService
from apps.consent.models import ConsentRecord
from apps.eventbus.envelope import Actor, Envelope
from apps.eventbus.models import DomainEvent
from apps.eventbus.ulid import new_ulid
from apps.identity.models import BotUser
from apps.loyalty.models import LoyaltyAccount, LoyaltyEvent
from apps.loyalty.subscribers import LoyaltySubscriber
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

MSK = ZoneInfo("Europe/Moscow")

#: Кто закрыл визит, когда закрыл человек. Форма значения не важна —
#: важно, что это не «system»: гейт default-deny, всё неизвестное
#: считается системным.
HUMAN_CLOSER = "master:1f0c8a24-6b3d-4a71-9c2e-5d8f0a1b2c3d"

#: Ровно то, что стояло в логе пилота.
SYSTEM_CLOSER = "system"


# ── фикстуры ────────────────────────────────────────────────────────────────


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="cbgate", name="Completed-by gate")


@pytest.fixture
def customer(tenant: Tenant) -> BotUser:
    """Клиент, прошедший 152-ФЗ: иначе запрос отзыва режет consent-гейт."""
    user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="cbgate-bot",
        chat_id="cbgate-chat",
        context={},
        consent_at=timezone.now() - dt.timedelta(days=30),
        client_name="Анна",
    )
    ConsentRecord.all_tenants.create(
        tenant=tenant,
        bot_user=user,
        consent_type=ConsentRecord.ConsentType.PERSONAL_DATA.value,
        granted=True,
        source="test:cbgate",
    )
    return user


@pytest.fixture
def service(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=77,
        external_updated_at=timezone.now(),
        name="Маникюр",
        price_from=Decimal("1200"),
        duration_min=60,
        is_active=True,
    )


def _yesterday_msk_visit(now_utc: dt.datetime) -> dt.datetime:
    """Вчерашний визит в 14:00 по салонному календарю, от переданного now.

    Окно отбора в ``plan_post_visit_followups`` — полные вчерашние сутки
    МСК. Считаем от того же ``now``, который уходит в планировщик, чтобы
    прогон в полночь не разъехался между двумя вызовами часов.
    """
    yesterday = now_utc.astimezone(MSK).date() - dt.timedelta(days=1)
    return dt.datetime.combine(yesterday, dt.time(14, 0), tzinfo=MSK).astimezone(dt.UTC)


def _make_booking(
    tenant: Tenant,
    customer: BotUser,
    service: CatalogService | None = None,
    *,
    completed_at: dt.datetime | None,
    completed_by: str,
    visit_at: dt.datetime | None = None,
) -> BookingRequest:
    return BookingRequest.all_tenants.create(
        tenant=tenant,
        bot_user=customer,
        service=service,
        service_name="Маникюр",
        master_name="Лера",
        client_name="Анна",
        client_phone="79991234567",
        visit_at=visit_at,
        status=BookingRequest.Status.CONFIRMED,
        completed_at=completed_at,
        completed_by=completed_by,
        source="bot",
        booking_source="external",
    )


def _make_reminder(
    tenant: Tenant,
    customer: BotUser,
    *,
    visit_at: dt.datetime,
    yc_id: str,
    booking: BookingRequest | None = None,
) -> BookingReminder:
    reminder = BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=customer,
        yclients_record_id=yc_id,
        chat_id=customer.chat_id,
        visit_at=visit_at,
        kind=BookingReminder.Kind.DAY_BEFORE,
        status=BookingReminder.Status.SENT,
        scheduled_at=visit_at - dt.timedelta(hours=24),
        master_name="Лера",
        service_name="Маникюр",
    )
    if booking is not None:
        BookingReminder.all_tenants.filter(pk=reminder.pk).update(booking_request=booking)
        reminder.refresh_from_db()
    return reminder


def _make_proxy(
    tenant: Tenant,
    customer: BotUser,
    *,
    start_at: dt.datetime,
    status: str,
    completed_by: str,
) -> RemoteBookingProxy:
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=uuid.uuid4(),
        tenant=tenant,
        bot_user=customer,
        start_at=start_at,
        end_at=start_at + dt.timedelta(hours=1),
        status=status,
        completed_by=completed_by,
    )


def _envelope(data: dict, *, tenant_id) -> Envelope:
    return Envelope(
        event_id=new_ulid(),
        event_name="booking.completed",
        event_version="1.0",
        occurred_at=timezone.now(),
        actor=Actor(type="system"),
        data=data,
        tenant_id=tenant_id,
    )


def _decision_for(now_utc: dt.datetime, bot_user: BotUser):
    """Единственное решение планировщика про этого клиента.

    Планировщик читает БД, составляет текст и возвращает решения, ничего
    не отправляя — то, что нужно, чтобы спросить «ушёл бы запрос отзыва?»
    без моков транспорта.
    """
    decisions = [
        d
        for d in followups_mod.plan_post_visit_followups(now_utc=now_utc)
        if d.bot_user_id == bot_user.pk
    ]
    assert len(decisions) == 1, f"ожидалось одно решение, получено {decisions!r}"
    return decisions[0]


# ── 1. Производитель записывает, кто закрыл ─────────────────────────────────


class TestAutocloseRecordsItsOwnActor:
    """Закрытие по часам обязано назвать себя.

    Без этого гейт ниже нечем питать: `completed_at` сам по себе не
    отличает «часы досчитали» от «мастер подтвердил».
    """

    def test_autoclose_stamps_system_on_the_row(self, tenant: Tenant, customer: BotUser) -> None:
        booking = _make_booking(
            tenant,
            customer,
            completed_at=None,
            completed_by="",
            visit_at=timezone.now() - dt.timedelta(minutes=210),
        )

        detect_completed_bookings()

        booking.refresh_from_db()
        assert booking.completed_at is not None, "визит обязан закрыться"
        assert booking.completed_by == SYSTEM_CLOSER

    def test_autoclose_names_system_in_the_event(self, tenant: Tenant, customer: BotUser) -> None:
        _make_booking(
            tenant,
            customer,
            completed_at=None,
            completed_by="",
            visit_at=timezone.now() - dt.timedelta(minutes=210),
        )

        detect_completed_bookings()

        event = DomainEvent.objects.get(event_name="booking.completed")
        assert event.data["completed_by"] == SYSTEM_CLOSER


# ── 2. Баллы (и всё, что каскадом за ними) ──────────────────────────────────


class TestLoyaltyPoints:
    """Начисление баллов за визит, который никто не подтверждал.

    За `_credit_visit` каскадом идут бонус за долгий возврат, выплата
    рефереру и пересчёт тира — то есть по чужому счёту тоже. Гейт один,
    потому что вход один.
    """

    def test_system_close_credits_nothing(
        self, tenant: Tenant, customer: BotUser, service: CatalogService, settings
    ) -> None:
        settings.STRICT_TENANT_SCOPE = "strict"
        booking = _make_booking(
            tenant, customer, service, completed_at=timezone.now(), completed_by=SYSTEM_CLOSER
        )

        LoyaltySubscriber().handle(
            _envelope(
                {"booking_id": str(booking.pk), "completed_by": SYSTEM_CLOSER},
                tenant_id=tenant.id,
            )
        )

        assert not LoyaltyEvent.all_tenants.filter(
            event_type=LoyaltyEvent.EventType.EARN_VISIT
        ).exists()
        account = LoyaltyAccount.all_tenants.filter(customer=customer).first()
        assert account is None or account.balance == 0

    def test_human_close_credits_points_on_the_same_data(
        self, tenant: Tenant, customer: BotUser, service: CatalogService, settings
    ) -> None:
        """Положительная стража: те же данные, другой закрывающий."""
        settings.STRICT_TENANT_SCOPE = "strict"
        booking = _make_booking(
            tenant, customer, service, completed_at=timezone.now(), completed_by=HUMAN_CLOSER
        )

        LoyaltySubscriber().handle(
            _envelope(
                {"booking_id": str(booking.pk), "completed_by": HUMAN_CLOSER},
                tenant_id=tenant.id,
            )
        )

        account = LoyaltyAccount.all_tenants.get(customer=customer)
        assert account.balance == 12  # 1200 ₽ → floor(1200/100)
        assert (
            LoyaltyEvent.all_tenants.filter(
                account=account, event_type=LoyaltyEvent.EventType.EARN_VISIT
            ).count()
            == 1
        )

    def test_missing_actor_credits_nothing(
        self, tenant: Tenant, customer: BotUser, service: CatalogService, settings
    ) -> None:
        """Default-deny: конверт без имени закрывающего — не подтверждение.

        Отсутствие поля не должно читаться как «закрыл человек»: событие
        без актора приходит ровно оттуда, откуда пришло автозакрытие.
        """
        settings.STRICT_TENANT_SCOPE = "strict"
        booking = _make_booking(
            tenant, customer, service, completed_at=timezone.now(), completed_by=""
        )

        LoyaltySubscriber().handle(_envelope({"booking_id": str(booking.pk)}, tenant_id=tenant.id))

        assert not LoyaltyEvent.all_tenants.filter(
            event_type=LoyaltyEvent.EventType.EARN_VISIT
        ).exists()


# ── 3. Запрос отзыва — путь BookingRequest ──────────────────────────────────


class TestReviewRequestLocalPath:
    """«Как прошёл вчерашний визит?» за визит, закрытый часами.

    До этого коммита `completed_at` работал разрешителем: запрос отзыва
    уходил **потому что и только потому что** сканер закрыл визит по
    часам. Ровно та инверсия, которую владелец закрыл решением 30.08.
    """

    def test_system_close_sends_no_review_request(self, tenant: Tenant, customer: BotUser) -> None:
        now = timezone.now()
        visit_at = _yesterday_msk_visit(now)
        booking = _make_booking(
            tenant,
            customer,
            completed_at=visit_at + dt.timedelta(hours=3),
            completed_by=SYSTEM_CLOSER,
            visit_at=visit_at,
        )
        _make_reminder(tenant, customer, visit_at=visit_at, yc_id="cbgate-yc", booking=booking)

        decision = _decision_for(now, customer)

        assert decision.send is False
        assert decision.reason == "completed_by_system"

    def test_human_close_sends_review_request_on_the_same_data(
        self, tenant: Tenant, customer: BotUser
    ) -> None:
        """Положительная стража: те же данные, закрыл человек."""
        now = timezone.now()
        visit_at = _yesterday_msk_visit(now)
        booking = _make_booking(
            tenant,
            customer,
            completed_at=visit_at + dt.timedelta(hours=3),
            completed_by=HUMAN_CLOSER,
            visit_at=visit_at,
        )
        _make_reminder(tenant, customer, visit_at=visit_at, yc_id="cbgate-yc", booking=booking)

        decision = _decision_for(now, customer)

        assert decision.send is True, decision.reason
        assert "визит" in decision.text


# ── 4. Запрос отзыва — путь зеркала Ayla ────────────────────────────────────


class TestReviewRequestAylaPath:
    """Тот же гейт на пути, которым живёт пилот.

    Пилотное автозакрытие живёт в Ayla и приезжает событием
    ``booking.completed``; зеркало — единственное, что об этом знает на
    нашей стороне.
    """

    def test_system_close_sends_no_review_request(self, tenant: Tenant, customer: BotUser) -> None:
        now = timezone.now()
        visit_at = _yesterday_msk_visit(now)
        proxy = _make_proxy(
            tenant,
            customer,
            start_at=visit_at,
            status=RemoteBookingProxy.Status.COMPLETED,
            completed_by=SYSTEM_CLOSER,
        )
        _make_reminder(tenant, customer, visit_at=visit_at, yc_id=str(proxy.appointment_id))

        decision = _decision_for(now, customer)

        assert decision.send is False
        assert decision.reason == "ayla_completed_by_system"

    def test_human_close_sends_review_request_on_the_same_data(
        self, tenant: Tenant, customer: BotUser
    ) -> None:
        """Положительная стража: то же зеркало, другой закрывающий."""
        now = timezone.now()
        visit_at = _yesterday_msk_visit(now)
        proxy = _make_proxy(
            tenant,
            customer,
            start_at=visit_at,
            status=RemoteBookingProxy.Status.COMPLETED,
            completed_by=HUMAN_CLOSER,
        )
        _make_reminder(tenant, customer, visit_at=visit_at, yc_id=str(proxy.appointment_id))

        decision = _decision_for(now, customer)

        assert decision.send is True, decision.reason


# ── 5. Стража против пере-гейта ─────────────────────────────────────────────


class TestVisitStillCloses:
    """Гейт гасит последствия, а не закрытие.

    «Выключить автозакрытие» — граница, которую легко перейти: визит,
    который не закрылся, висит вечно и ломает расписание. Эти два теста
    падают, если гейт заехал не туда.
    """

    def test_autoclose_still_closes_the_visit(self, tenant: Tenant, customer: BotUser) -> None:
        booking = _make_booking(
            tenant,
            customer,
            completed_at=None,
            completed_by="",
            visit_at=timezone.now() - dt.timedelta(minutes=210),
        )

        result = detect_completed_bookings()

        booking.refresh_from_db()
        assert result["emitted"] == 1
        assert booking.completed_at is not None

    def test_ayla_system_close_still_flips_the_mirror(
        self, tenant: Tenant, customer: BotUser, settings
    ) -> None:
        from apps.eventbus.consumers.booking import handle_booking_completed
        from apps.eventbus.ingest_envelope import IngestEnvelope

        # Тот же путь, которым событие идёт в проде: проверка тенанта
        # включена, тенант и событие пущены через пилотный allowlist.
        settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = False
        settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset({str(tenant.id)})
        settings.EVENT_INGEST_ALLOWED_EVENTS = frozenset({"booking.completed"})

        proxy = _make_proxy(
            tenant,
            customer,
            start_at=timezone.now() - dt.timedelta(hours=4),
            status=RemoteBookingProxy.Status.CONFIRMED,
            completed_by="",
        )
        envelope = IngestEnvelope(
            event_id=new_ulid(),
            event_name="booking.completed",
            event_version=1,
            occurred_at=timezone.now(),
            tenant_id=str(tenant.id),
            # `IngestEnvelope` объявляет `user_id` и `correlation_id`
            # обязательными строками — прод их всегда заполняет. Передавать
            # сюда пустоту значило бы проверять форму, которой продюсер не
            # производит: тест прошёл бы, а живое событие повело бы себя
            # иначе. Тот же класс, что весь разбор 29–30.08.
            user_id=str(customer.pk),
            actor="system",
            correlation_id=new_ulid(),
            causation_id=None,
            data={
                "appointment_id": str(proxy.appointment_id),
                "completed_at": timezone.now().isoformat(),
                "completed_by": SYSTEM_CLOSER,
            },
        )

        handle_booking_completed(envelope)

        proxy.refresh_from_db()
        assert proxy.status == RemoteBookingProxy.Status.COMPLETED
        assert proxy.completed_by == SYSTEM_CLOSER
