"""DRF-2967 — «отозвал → согласился снова» по времени, которое видит каталог.

Правило второй линии: известный каталогу отзыв побеждает утверждение бота,
только если отзыв ПОЗЖЕ времени согласия из утверждения. Оно честно, если
верны две вещи, и обе здесь исполняются, а не читаются:

* событие отзыва, которое бот шлёт каталогу, несёт МОМЕНТ ОТЗЫВА (а не время
  исходной выдачи) — тело снимается на выходе настоящего подписчика
  ``CatalogConsentSubscriber``, после настоящего ``withdraw``;
* утверждение после нового согласия несёт время НОВОЙ записи, и оно строго
  позже момента отзыва.

Что здесь не исполняется: диспетчер (конверт берётся у ``emit`` и подаётся
подписчику напрямую) и сам каталог.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from apps.consent import services as consent_services
from apps.consent.models import ConsentRecord
from apps.consent.services import (
    record_person_consent,
    withdraw_personal_data_for_bot_users,
)
from apps.eventbus.subscribers import CatalogConsentSubscriber
from apps.identity.models import BotUser
from apps.integrations.ayla import consent_events_client
from apps.integrations.ayla.consent_events_client import ConsentEventReceipt
from apps.orchestrator.plan_gate import plan_consent_basis
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

PD = ConsentRecord.ConsentType.PERSONAL_DATA.value


@pytest.fixture
def person() -> BotUser:
    tenant = Tenant.objects.create(slug="moment-2967", name="Moment")
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="296790", display_name="Анна"
    )


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Тела, которые подписчик отправил бы в ``consent-events`` каталога.

    Конверты ловятся на выходе настоящего ``emit_customer_consent_changed``
    и подаются настоящему подписчику; подменён только HTTP-вызов.
    """
    bodies: list[dict[str, Any]] = []
    real_emit = consent_services.eventbus_services.emit_customer_consent_changed

    def _post(*, external_user_id: str, body: dict[str, Any]) -> ConsentEventReceipt:
        bodies.append({"external_user_id": external_user_id, **body})
        return ConsentEventReceipt(event_id=str(body["event_id"]), outcome="applied")

    def _emit(**kwargs: Any) -> Any:
        envelope = real_emit(**kwargs)
        CatalogConsentSubscriber().handle(envelope)
        return envelope

    monkeypatch.setattr(consent_events_client, "post_consent_event", _post)
    monkeypatch.setattr(consent_services.eventbus_services, "emit_customer_consent_changed", _emit)
    return bodies


def _moment(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, value  # без пояса каталог считает, что времени нет
    return parsed


def test_t1_the_withdrawal_event_carries_the_moment_of_withdrawal(
    person: BotUser, wire: list[dict[str, Any]], django_capture_on_commit_callbacks
) -> None:
    record_person_consent(person, consent_type=PD, source="chat")
    first = ConsentRecord.all_tenants.get(bot_user=person, consent_type=PD)

    # Событие отзыва уходит после фиксации транзакции — исполняем её вызовы.
    with django_capture_on_commit_callbacks(execute=True):
        withdraw_personal_data_for_bot_users([person], source="chat")

    first.refresh_from_db()
    (sent,) = [body for body in wire if body["consent_type"] == PD]
    assert sent["external_user_id"] == "bot:max:296790"
    assert sent["granted"] is False
    assert sorted(sent) == [
        "consent_type",
        "event_id",
        "external_user_id",
        "granted",
        "granted_at",
        "granted_via",
    ]
    # Момент отзыва — и он позже выдачи той же записи.
    assert _moment(sent["granted_at"]) == first.withdrawn_at
    assert _moment(sent["granted_at"]) > first.captured_at


def test_t2_a_new_grant_is_attested_strictly_later_than_the_withdrawal(
    person: BotUser, wire: list[dict[str, Any]], django_capture_on_commit_callbacks
) -> None:
    """Отозвал → сразу согласился снова: утверждение называет новую запись,
    и её время строго позже момента отзыва, ушедшего каталогу."""
    record_person_consent(person, consent_type=PD, source="chat")
    stale = plan_consent_basis(person)
    assert stale is not None
    # Событие отзыва уходит после фиксации транзакции — исполняем её вызовы.
    with django_capture_on_commit_callbacks(execute=True):
        withdraw_personal_data_for_bot_users([person], source="chat")
    (sent,) = [body for body in wire if body["consent_type"] == PD]
    assert plan_consent_basis(person) is None  # отозвано — утверждать нечего

    record_person_consent(person, consent_type=PD, source="chat")
    fresh = plan_consent_basis(person)

    assert fresh is not None
    assert _moment(fresh["granted_at"]) > _moment(sent["granted_at"])
    # Близнец: утверждение по прежней записи каталог обязан отвергнуть —
    # оно раньше отзыва.
    assert _moment(stale["granted_at"]) < _moment(sent["granted_at"])
    print(f"\nSNAPSHOT withdrawal_event={ {k: v for k, v in sent.items() if k != 'event_id'} }")
    print(f"SNAPSHOT stale_basis={stale}")
    print(f"SNAPSHOT fresh_basis={fresh}")
