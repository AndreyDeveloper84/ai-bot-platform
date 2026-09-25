"""DRF-2462: перепись поставленных ложных штампов — три корзины и очередь.

Производство ложных штампов остановлено (DRF-2454, #2072). Остались поставленные,
и они **видны мастеру сегодня**: список клиентов фильтрует по ``completed_at`` без
гейта ``completed_by``, то есть счётчик визитов и «последняя услуга» уже считают
отменённые визиты.

Узлы держат ровно то, чем можно ошибиться в обе стороны:

* ложный штамп по канону **попадает** в корзину уборки;
* законный **не попадает** — ни закрытый человеком, ни подтверждённый зеркалом;
* недоказуемый (нет зеркала / два зеркала / нет человека в строке) **не объявлен
  ложным**: обнулить его — та же ошибка, что поставить;
* очередь названа числом, и неразобранные события отделены от разобранных;
* ноль **доказан охватом**: на пустой выборке команда говорит, что нули ничего не
  доказывают;
* команда **ничего не меняет** — ни штампов, ни статусов, ни очереди.

``event_id`` строится тем же ``new_ulid()``, что и продакшн: колонка — 26 символов,
и Postgres это требование держит, а локальный SQLite молчит. Первая версия писала
``uuid4`` (36 символов) и была зелёной локально, красной в CI — предмет проверки
здесь очередь, и фикстура обязана быть такой же длины, как живая строка.
"""

from __future__ import annotations

import datetime as dt
import uuid
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.booking.models import BookingRequest, RemoteBookingProxy
from apps.eventbus.models import DomainEvent
from apps.eventbus.ulid import new_ulid
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="census-2462", name="Salon 2462")


def _customer(tenant, suffix: str) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=f"census-2462-{suffix}"
    )


def _stamped(
    tenant,
    customer,
    *,
    mirror_status: str | None,
    completed_by: str = "system",
    minutes: int = -300,
) -> BookingRequest:
    visit_at = timezone.now() + dt.timedelta(minutes=minutes)
    if mirror_status is not None:
        RemoteBookingProxy.all_tenants.create(
            appointment_id=uuid.uuid4(),
            tenant=tenant,
            bot_user=customer,
            start_at=visit_at,
            end_at=visit_at + dt.timedelta(minutes=60),
            status=mirror_status,
            source=RemoteBookingProxy.Source.MOBILE_APP,
        )
    return BookingRequest.objects.create(
        tenant=tenant,
        bot_user=customer,
        service_name="Маникюр",
        client_name="Customer",
        client_phone="snapshot",
        visit_at=visit_at,
        duration_min=60,
        status=BookingRequest.Status.CONFIRMED,
        completed_at=timezone.now(),
        completed_by=completed_by,
        source="bot",
        booking_source="external",
    )


def _run(*args: str) -> str:
    out = StringIO()
    call_command("audit_false_completions", *args, stdout=out)
    return out.getvalue()


# ─── три корзины ───────────────────────────────────────────────────────────


class TestTheThreeBuckets:
    @pytest.mark.parametrize(
        "mirror_status", ("cancelled", "no_show", "pending_payment", "tentative")
    )
    def test_a_stamp_the_canon_contradicts_is_false(self, tenant, mirror_status: str) -> None:
        _stamped(tenant, _customer(tenant, mirror_status), mirror_status=mirror_status)

        text = _run()

        assert "охват (строк со штампом): 1" in text
        assert "ложных по канону (false_by_canon): 1" in text
        assert f"false_by_canon:mirror_{mirror_status}" in text

    def test_a_human_closer_is_legitimate_even_if_the_mirror_says_cancelled(self, tenant) -> None:
        """Зеркало отстаёт, человек — нет: его подпись сильнее."""
        _stamped(
            tenant,
            _customer(tenant, "human"),
            mirror_status="cancelled",
            completed_by="master:42",
        )

        text = _run()

        assert "законных (legitimate):             1" in text
        assert "legitimate:human_closer" in text
        assert "ложных по канону (false_by_canon): 0" in text

    def test_a_mirror_that_says_completed_is_legitimate(self, tenant) -> None:
        _stamped(tenant, _customer(tenant, "done"), mirror_status="completed")

        text = _run()

        assert "законных (legitimate):             1" in text
        assert "legitimate:mirror_completed" in text

    def test_no_mirror_is_unprovable_not_false(self, tenant) -> None:
        """Нет доказательства ни в одну сторону — обнулять нельзя (DRF-2461)."""
        _stamped(tenant, _customer(tenant, "nomirror"), mirror_status=None)

        text = _run()

        assert "недоказуемых (unprovable):         1" in text
        assert "unprovable:no_mirror" in text
        assert "ложных по канону (false_by_canon): 0" in text

    def test_two_mirrors_on_one_key_are_unprovable(self, tenant) -> None:
        customer = _customer(tenant, "ambig")
        booking = _stamped(tenant, customer, mirror_status="cancelled")
        visit_at = booking.visit_at
        assert visit_at is not None  # ключ сопоставления без времени не существует
        RemoteBookingProxy.all_tenants.create(
            appointment_id=uuid.uuid4(),
            tenant=tenant,
            bot_user=customer,
            start_at=visit_at,
            end_at=visit_at + dt.timedelta(minutes=60),
            status="confirmed",
            source=RemoteBookingProxy.Source.MOBILE_APP,
        )

        text = _run()

        assert "unprovable:ambiguous" in text
        assert "ложных по канону (false_by_canon): 0" in text

    def test_an_unknown_canon_state_is_unprovable(self, tenant) -> None:
        _stamped(tenant, _customer(tenant, "future"), mirror_status="some_future_state")

        text = _run()

        assert "unprovable:mirror_some_future_state" in text


# ─── очередь ───────────────────────────────────────────────────────────────


class TestTheQueueIsNamed:
    def test_pending_events_for_false_rows_are_counted_and_warned_about(self, tenant) -> None:
        booking = _stamped(tenant, _customer(tenant, "queued"), mirror_status="cancelled")
        for dispatched in (False, True):
            DomainEvent.objects.create(
                event_id=new_ulid(),
                event_name="booking.completed",
                event_version="1.0.0",
                occurred_at=timezone.now(),
                tenant=tenant,
                actor={"type": "system"},
                data={"booking_id": str(booking.pk)},
                is_dispatched=dispatched,
            )

        text = _run()

        assert "событий booking.completed:  2" in text
        assert "из них НЕ разобрано:        1" in text
        assert "необратимо" in text  # предупреждение названо

    def test_events_of_legitimate_rows_are_not_counted(self, tenant) -> None:
        booking = _stamped(tenant, _customer(tenant, "ok"), mirror_status="completed")
        DomainEvent.objects.create(
            event_id=new_ulid(),
            event_name="booking.completed",
            event_version="1.0.0",
            occurred_at=timezone.now(),
            tenant=tenant,
            actor={"type": "system"},
            data={"booking_id": str(booking.pk)},
            is_dispatched=False,
        )

        text = _run()

        assert "событий booking.completed:  0" in text  # предмет — только ложные строки


# ─── ноль, охват и неприкосновенность ──────────────────────────────────────


class TestTheZeroAndTheReadOnlyPromise:
    def test_an_empty_scope_says_the_zeros_prove_nothing(self, tenant) -> None:
        text = _run()

        assert "охват (строк со штампом): 0" in text
        assert "нули ниже ничего не доказывают" in text

    def test_the_census_changes_nothing(self, tenant) -> None:
        booking = _stamped(tenant, _customer(tenant, "untouched"), mirror_status="cancelled")
        before = (booking.completed_at, booking.completed_by, booking.status)
        # Строки фикстур эмитят свои события (сигналы моделей), поэтому предмет
        # проверки — НЕ «событий ноль», а «их число не изменилось этим прогоном».
        events_before = DomainEvent.objects.count()
        assert events_before >= 1  # положительно: счётчик живой, ему есть что мерить

        _run("--ids")

        booking.refresh_from_db()
        assert (booking.completed_at, booking.completed_by, booking.status) == before
        assert DomainEvent.objects.count() == events_before  # команда не эмитит
        assert not DomainEvent.objects.filter(
            event_name="booking.completed", data__booking_id=str(booking.pk)
        ).exists()

    def test_ids_are_printed_only_for_the_cleanup_bucket(self, tenant) -> None:
        false_row = _stamped(tenant, _customer(tenant, "f"), mirror_status="cancelled")
        legit_row = _stamped(tenant, _customer(tenant, "l"), mirror_status="completed")

        text = _run("--ids")

        assert str(false_row.pk) in text
        assert str(legit_row.pk) not in text
