"""DRF-2984 PR-B1 — зеркало подписки каталога на тенанте бота.

Узлы идут через настоящий диспетчер (``dispatch_envelope``), а не через
функцию зеркала напрямую: то, что событие доходит до строки тенанта, — и
есть предмет.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from apps.eventbus.ingest_dispatcher import DispatchOutcome, dispatch_envelope
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.tenancy.models import SubscriptionStatus, Tenant


pytestmark = pytest.mark.django_db

AYLA_USER_ID = "f1a2b3c4-d5e6-4789-9abc-def012345678"
SPECIALIST_ID = "7c2d8e1f-0a5c-4c3a-9e1b-4d52f8eb3a17"
T0 = dt.datetime(2026, 10, 20, 12, 0, tzinfo=dt.UTC)


@pytest.fixture(autouse=True)
def _fail_open_tenant_verify(settings) -> None:
    # Pre-#246 transition bridge — same pattern as test_billing_consumer.
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="billing-mirror", name="Студия Зеркало")


def _send(event_name: str, data: dict[str, Any], *, tenant_id: str | None, at: dt.datetime = T0):
    return dispatch_envelope(
        IngestEnvelope(
            event_id=str(uuid.uuid4()),
            event_name=event_name,
            event_version=1,
            occurred_at=at,
            tenant_id=tenant_id,
            user_id=AYLA_USER_ID,
            actor="system",
            correlation_id="c-2984",
            causation_id=None,
            data=data,
        )
    )


def _mirror(tenant: Tenant) -> tuple:
    t = Tenant.all_objects.get(pk=tenant.pk)
    return (t.subscription_status, t.plan, t.trial_ends_at, t.current_period_end)


def test_m1_a_new_tenant_mirrors_nothing():
    t = Tenant.objects.create(slug="billing-fresh", name="Новая")

    assert _mirror(t) == ("none", "free", None, None)


@pytest.mark.parametrize(
    ("event_name", "data", "expected"),
    [
        (
            "subscription.trial_started",
            {"tariff": "salon", "trial_ends_at": "2026-11-20"},
            ("trial", "salon", dt.date(2026, 11, 20), None),
        ),
        (
            "subscription.activated",
            {"specialist_id": SPECIALIST_ID, "tariff": "solo", "period_end": "2026-11-30"},
            ("active", "solo", None, dt.date(2026, 11, 30)),
        ),
        (
            "subscription.renewed",
            {"period_end": "2026-12-31", "amount": "1140.00", "masters_count": 4},
            ("active", "free", None, dt.date(2026, 12, 31)),
        ),
        (
            "subscription.past_due",
            {"specialist_id": SPECIALIST_ID, "debt_amount": "990.00", "failed_attempts": 3},
            ("past_due", "free", None, None),
        ),
        (
            "subscription.frozen",
            {"reason": "trial_ended_no_card"},
            ("frozen", "free", None, None),
        ),
        (
            "subscription.canceled",
            {"effective_at": "2026-11-30"},
            ("canceled", "free", None, None),
        ),
    ],
)
def test_m2_each_subscription_event_writes_its_status(tenant, event_name, data, expected):
    result = _send(event_name, data, tenant_id=str(tenant.pk))

    assert result.outcome == DispatchOutcome.OK
    assert _mirror(tenant) == expected


def test_m3_an_older_event_never_overwrites_a_newer_one(tenant):
    _send("subscription.frozen", {"reason": "past_due_timeout"}, tenant_id=str(tenant.pk))
    # Пришло позже, но случилось раньше (outbox не обещает порядок).
    late = _send(
        "subscription.renewed",
        {"period_end": "2026-11-30"},
        tenant_id=str(tenant.pk),
        at=T0 - dt.timedelta(hours=1),
    )

    assert late.outcome == DispatchOutcome.OK
    assert _mirror(tenant) == ("frozen", "free", None, None)


def test_m4_a_newer_event_does_overwrite(tenant):
    _send(
        "subscription.past_due",
        {"specialist_id": SPECIALIST_ID, "debt_amount": "1", "failed_attempts": 3},
        tenant_id=str(tenant.pk),
    )
    _send(
        "subscription.activated",
        {"specialist_id": SPECIALIST_ID, "tariff": "salon", "period_end": "2026-12-31"},
        tenant_id=str(tenant.pk),
        at=T0 + dt.timedelta(days=1),
    )

    assert _mirror(tenant) == ("active", "salon", None, dt.date(2026, 12, 31))


@pytest.mark.parametrize(
    ("event_name", "data"),
    [
        ("subscription.trial_started", {"tariff": "enterprise", "trial_ends_at": "2026-11-20"}),
        ("subscription.trial_started", {"tariff": "salon", "trial_ends_at": "через месяц"}),
        (
            "subscription.activated",
            {"specialist_id": SPECIALIST_ID, "tariff": "solo", "period_end": 20261130},
        ),
        ("subscription.frozen", {}),  # нет обязательного ключа
    ],
)
def test_m5_a_value_outside_the_vocabulary_is_acked_and_writes_nothing(tenant, event_name, data):
    result = _send(event_name, data, tenant_id=str(tenant.pk))

    assert result.outcome == DispatchOutcome.OK  # постоянная ошибка производителя: ack, не повтор
    assert _mirror(tenant) == ("none", "free", None, None)


def test_m6_a_legacy_solo_event_without_tenant_writes_no_tenant(tenant):
    result = _send(
        "subscription.activated",
        {"specialist_id": SPECIALIST_ID, "tariff": "solo", "period_end": "2026-11-30"},
        tenant_id=None,
    )

    assert result.outcome == DispatchOutcome.OK
    assert _mirror(tenant) == ("none", "free", None, None)


def test_m7_fee_charged_does_not_touch_the_mirror(tenant):
    _send(
        "billing.fee_charged",
        {
            "specialist_id": SPECIALIST_ID,
            "appointment_id": str(uuid.uuid4()),
            "amount": "90.00",
            "period": "2026-10",
        },
        tenant_id=str(tenant.pk),
    )

    assert _mirror(tenant) == ("none", "free", None, None)


def test_m8_an_inactive_tenant_is_mirrored_too(tenant):
    Tenant.all_objects.filter(pk=tenant.pk).update(is_active=False)

    _send("subscription.frozen", {"reason": "past_due_timeout"}, tenant_id=str(tenant.pk))

    assert _mirror(tenant)[0] == "frozen"


def test_m9_the_mirror_vocabulary_is_exactly_the_catalog_statuses_plus_none():
    assert [s.value for s in SubscriptionStatus] == [
        "none",
        "trial",
        "active",
        "past_due",
        "frozen",
        "canceled",
    ]
