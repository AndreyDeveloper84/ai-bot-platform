"""Живой режим не помечает ящик, пока нет настоящего подписчика (DRF-2434).

Диспетчер считает строку доставленной, если ни один подписчик не бросил
исключения. `NoopSubscriber` их не бросает, пустой реестр — тем более, так
что живой прогон при таком реестре помечает `is_dispatched=True` всё, до
чего дотянется, не доставив ничего никому, — и обратного хода нет.

На стенде 04.10: 65 неотправленных, старейшему 53 суток, 21 из них — смена
согласия; рубильник закрыт, реестр — только Noop. Порядок «подписчик раньше
рубильника» держался прозой. Здесь стережётся, что его держит код:

* живой режим при реестре только из Noop или пустом → `refused_noop_only`,
  ни одна строка не помечена и не тронута, WARNING в журнале;
* тот же живой режим с настоящим подписчиком доставляет — отказ не
  срабатывает на всё подряд;
* сухой прогон и прямой операторский вызов отказ не затрагивает.

`transaction=True` — как у соседей из `test_dispatch_beat.py`: claim идёт
через `select_for_update(skip_locked=True)` внутри `atomic`.
"""

from __future__ import annotations

import logging

import pytest

from apps.eventbus import dispatcher, services, vocabulary as V
from apps.eventbus.dispatcher import (
    dispatch_pending_events,
    dispatch_pending_events_beat,
    registry_is_noop_only,
)
from apps.eventbus.envelope import Envelope
from apps.eventbus.models import DomainEvent

pytestmark = pytest.mark.django_db(transaction=True)

NOOP = "apps.eventbus.dispatcher.NoopSubscriber"
RECORDING = f"{__name__}.RecordingSubscriber"


class RecordingSubscriber:
    """Настоящий подписчик в миниатюре: запоминает, что ему доставили.

    Объявляет себя доставщиком смены согласия (DRF-2776): иначе живой режим
    с согласием в ящике получил бы `refused_undelivered`, а этот файл про
    другой отказ — про реестр из одних заглушек.
    """

    delivers = frozenset({"customer.consent.changed"})
    received: list[str] = []

    def handle(self, envelope: Envelope) -> None:
        RecordingSubscriber.received.append(envelope.event_name)


@pytest.fixture(autouse=True)
def _clean_registry():
    RecordingSubscriber.received = []
    dispatcher.reset_registry_cache()
    yield
    dispatcher.reset_registry_cache()


@pytest.fixture
def live(settings):
    settings.EVENTBUS_DISPATCH_BEAT_ENABLED = True
    settings.EVENTBUS_DISPATCH_BEAT_DRY_RUN = False
    return settings


def _emit_consent_and_booking() -> None:
    services.emit(
        V.BOOKING_CREATED,
        {
            "booking_id": "noop-b1",
            "customer_id": "noop-c1",
            "service_id": "s",
            "slot_start": "2026-10-04T10:00:00Z",
            "booking_source": "ai_direct",
        },
        actor_type="system",
    )
    services.emit_customer_consent_changed(
        customer_id="noop-c2",
        consent_type="marketing",
        granted=False,
        granted_at="2026-10-04T10:00:00Z",
    )


def _untouched() -> int:
    return DomainEvent.objects.filter(
        is_dispatched=False, dead_lettered_at__isnull=True, dispatch_attempts=0
    ).count()


# ─── отказ ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "registry", [[NOOP], [NOOP, NOOP], []], ids=["noop", "noop-twice", "empty"]
)
def test_live_mode_with_no_real_subscriber_marks_nothing(live, registry, caplog) -> None:
    live.DOMAIN_EVENT_SUBSCRIBERS = registry
    _emit_consent_and_booking()
    assert _untouched() == 2

    with caplog.at_level(logging.WARNING, logger="apps.eventbus.dispatcher"):
        result = dispatch_pending_events_beat()

    assert result == {"mode": "refused_noop_only", "pending": 2}
    assert _untouched() == 2, "отказ пометил или тронул строки"
    assert DomainEvent.objects.filter(is_dispatched=True).count() == 0
    assert any("refused_noop_only pending=2" in r.getMessage() for r in caplog.records)


def test_registry_is_noop_only_reads_the_configured_subscribers(settings) -> None:
    settings.DOMAIN_EVENT_SUBSCRIBERS = [NOOP]
    assert registry_is_noop_only() is True

    settings.DOMAIN_EVENT_SUBSCRIBERS = [NOOP, RECORDING]
    assert registry_is_noop_only() is False


# ─── отказ не срабатывает на всё подряд ─────────────────────────────────────


def test_live_mode_with_a_real_subscriber_delivers(live) -> None:
    live.DOMAIN_EVENT_SUBSCRIBERS = [NOOP, RECORDING]
    _emit_consent_and_booking()

    result = dispatch_pending_events_beat()

    assert result["mode"] == "live"
    assert result["dispatched"] == 2
    assert sorted(RecordingSubscriber.received) == sorted(
        [V.BOOKING_CREATED, V.CUSTOMER_CONSENT_CHANGED]
    )
    assert DomainEvent.objects.filter(is_dispatched=True).count() == 2


def test_dry_run_is_untouched_by_the_refusal(settings) -> None:
    settings.EVENTBUS_DISPATCH_BEAT_ENABLED = True
    settings.EVENTBUS_DISPATCH_BEAT_DRY_RUN = True
    settings.DOMAIN_EVENT_SUBSCRIBERS = [NOOP]
    _emit_consent_and_booking()

    assert dispatch_pending_events_beat() == {"mode": "dry_run", "pending": 2}


def test_disabled_stays_disabled(settings) -> None:
    settings.EVENTBUS_DISPATCH_BEAT_ENABLED = False
    settings.DOMAIN_EVENT_SUBSCRIBERS = [NOOP]

    assert dispatch_pending_events_beat() == {"mode": "disabled", "pending": None}


def test_the_direct_operator_call_is_not_guarded(settings) -> None:
    """Прямой вызов — для replay и тестов; рубильник и отказ заведены для beat."""
    settings.DOMAIN_EVENT_SUBSCRIBERS = [NOOP]
    _emit_consent_and_booking()

    counters = dispatch_pending_events()

    assert counters["dispatched"] == 2
