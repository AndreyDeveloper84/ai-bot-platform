"""DRF-2974 — согласие, выданное на глобальном пути, доходит до каталога.

Отзыв на глобальной оболочке уходил в шину всегда (:func:`withdraw` в
``tenant_scope`` оболочки), а выдача — нет: ``record_global_consent`` писал
строку без события. Каталог узнавал «отозвано» и не узнавал «согласился
снова». Замер на двух живых сервисах (бот ``a0f746d8``, каталог ``e8a5927``):
отзыв дневника → повторная выдача в чате → запись в дневник каталога всё
равно ``422 CONSENT_REQUIRED withdrawn``; та же выдача, опубликованная, —
``201``.

Тела снимаются на выходе настоящего ``CatalogConsentSubscriber`` после
настоящего ``emit``; подменён только HTTP-вызов. Диспетчер шины не
исполняется (конверт подаётся подписчику напрямую). Публикация идёт через
``transaction.on_commit`` — действия исполняются под
``django_capture_on_commit_callbacks(execute=True)``, иначе перехват пуст и
читается как «события нет».
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from apps.consent import services as consent_services
from apps.consent.models import ConsentRecord
from apps.consent.nutrition import (
    FOOD_DIARY_CONSENT_DOCUMENT_VERSION,
    grant_diary,
    withdraw_diary,
)
from apps.consent.services import record_global_consent, record_person_consent
from apps.eventbus.models import DomainEvent
from apps.eventbus.subscribers import CatalogConsentSubscriber
from apps.identity.models import BotUser
from apps.identity.services import resolve_or_create_global_bot_user
from apps.integrations.ayla import consent_events_client
from apps.integrations.ayla.consent_events_client import ConsentEventReceipt

pytestmark = pytest.mark.django_db

DIARY = "food_diary_processing"
PD = ConsentRecord.ConsentType.PERSONAL_DATA.value


@pytest.fixture
def person() -> BotUser:
    return resolve_or_create_global_bot_user(channel="max", channel_user_id="297401")


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Тела, которые подписчик отправил бы в ``consent-events`` каталога."""
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


@pytest.mark.parametrize(
    "consent_type",
    [PD, "personal_calculation", DIARY, ConsentRecord.ConsentType.HEALTH.value],
)
def test_a_global_grant_reaches_the_catalog_with_the_moment_of_the_grant(
    person: BotUser,
    wire: list[dict[str, Any]],
    consent_type: str,
    django_capture_on_commit_callbacks,
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        record_person_consent(
            person, consent_type=consent_type, source="chat", document_version="v1"
        )

    record = ConsentRecord.all_tenants.get(bot_user=person, consent_type=consent_type)
    (sent,) = wire
    assert sent["external_user_id"] == "bot:max:297401"
    assert sent["consent_type"] == consent_type
    assert sent["granted"] is True
    assert sent["granted_via"] == "chat"
    assert _moment(sent["granted_at"]) == record.captured_at
    # Строка шины — под тенантом самой оболочки (сентинел глобального бота).
    event = DomainEvent.objects.get(event_name="customer.consent.changed")
    assert event.tenant_id == person.tenant_id
    assert event.data["customer_id"] == str(person.id)


def test_withdraw_then_agree_again_reaches_the_catalog_in_order(
    person: BotUser, wire: list[dict[str, Any]], django_capture_on_commit_callbacks
) -> None:
    """Замеренный сценарий: дневник отозван и выдан снова в чате."""
    with django_capture_on_commit_callbacks(execute=True):
        grant_diary(person, document_version=FOOD_DIARY_CONSENT_DOCUMENT_VERSION)
    with django_capture_on_commit_callbacks(execute=True):
        withdraw_diary(person)
    with django_capture_on_commit_callbacks(execute=True):
        grant_diary(person, document_version=FOOD_DIARY_CONSENT_DOCUMENT_VERSION)

    assert [(b["consent_type"], b["granted"]) for b in wire] == [
        (DIARY, True),
        (DIARY, False),
        (DIARY, True),
    ]
    _, withdrawal, regrant = wire
    # Каталог упорядочивает состояние по granted_at: новая выдача обязана быть
    # строго позже отзыва, иначе она придёт как stale и отзыв останется.
    assert _moment(regrant["granted_at"]) > _moment(withdrawal["granted_at"])
    assert len({b["event_id"] for b in wire}) == 3


def test_a_repeated_tap_publishes_nothing(
    person: BotUser, wire: list[dict[str, Any]], django_capture_on_commit_callbacks
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        record_global_consent(person, consent_type=DIARY, source="chat")
    with django_capture_on_commit_callbacks(execute=True):
        record_global_consent(person, consent_type=DIARY, source="chat")

    assert len(wire) == 1


def test_a_bus_failure_does_not_break_the_grant(
    person: BotUser, monkeypatch: pytest.MonkeyPatch, django_capture_on_commit_callbacks
) -> None:
    def _boom(**_: Any) -> Any:
        raise RuntimeError("bus down")

    monkeypatch.setattr(consent_services.eventbus_services, "emit_customer_consent_changed", _boom)
    with django_capture_on_commit_callbacks(execute=True):
        record = record_global_consent(person, consent_type=DIARY, source="chat")

    assert ConsentRecord.all_tenants.filter(pk=record.pk, granted=True, withdrawn_at=None).exists()


def test_a_rolled_back_grant_publishes_nothing(
    person: BotUser, wire: list[dict[str, Any]], django_capture_on_commit_callbacks
) -> None:
    """Событие — только о строке, которая есть в базе (как у :func:`grant`)."""
    from django.db import transaction

    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                record_global_consent(person, consent_type=DIARY, source="chat")
                raise RuntimeError("rollback")

    assert wire == []
    assert not ConsentRecord.all_tenants.filter(bot_user=person, consent_type=DIARY).exists()
