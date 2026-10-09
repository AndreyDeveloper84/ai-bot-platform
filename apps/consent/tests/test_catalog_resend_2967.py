"""DRF-2967 — досылка каталогу отзывов согласия на хранение.

Каталог раньше принимал события ``personal_data`` и не хранил их: об
отзывах до выкладки его правки он не знает. Досылается текущее «отозвано» —
одно событие на человека, со временем последнего отзыва.
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.consent import catalog_resend
from apps.consent.catalog_resend import withdrawn_people
from apps.consent.models import ConsentRecord
from apps.consent.services import record_global_consent
from apps.identity.models import BotUser
from apps.integrations.ayla.consent_events_client import (
    ConsentEventReceipt,
    ConsentEventUnavailable,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

PD = ConsentRecord.ConsentType.PERSONAL_DATA.value
COMMAND = "apps.consent.management.commands.resend_withdrawn_personal_data"


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="resend-2967", name="Resend")


def _shell(tenant: Tenant, channel_user_id: str) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=channel_user_id, display_name="Анна"
    )


def _withdraw(shell: BotUser, at: Any) -> None:
    ConsentRecord.all_tenants.filter(
        bot_user=shell, consent_type=PD, withdrawn_at__isnull=True
    ).update(withdrawn_at=at)


def _withdrawn_person(tenant: Tenant, channel_user_id: str, at: Any) -> BotUser:
    shell = _shell(tenant, channel_user_id)
    record_global_consent(shell, source="test")
    _withdraw(shell, at)
    return shell


# ─── кого досылаем ───────────────────────────────────────────────────────────


def test_r1_a_withdrawn_person_is_resent_with_the_moment_of_withdrawal(tenant) -> None:
    at = timezone.now() - timedelta(days=3)
    _withdrawn_person(tenant, "29671", at)

    (person,) = list(withdrawn_people())

    assert person.external_user_id == "bot:max:29671"
    assert person.body() == {
        "event_id": person.event_id,
        "consent_type": "personal_data",
        "granted": False,
        "granted_at": at.isoformat(),
        "granted_via": "resend:drf-2967",
    }


def test_r2_only_the_currently_withdrawn_are_resent(tenant) -> None:
    now = timezone.now()
    _withdrawn_person(tenant, "29672", now - timedelta(days=5))  # отозвано — досылаем
    record_global_consent(_shell(tenant, "29673"), source="test")  # действует
    _shell(tenant, "29674")  # не соглашался
    regranted = _withdrawn_person(tenant, "29675", now - timedelta(days=5))
    record_global_consent(regranted, source="test")  # отозвал и согласился снова

    resent = [person.external_user_id for person in withdrawn_people()]

    assert resent == ["bot:max:29672"]


def test_r3_one_event_per_person_with_the_latest_withdrawal(tenant) -> None:
    """Две оболочки одного человека — один субъект каталога, одно событие."""
    now = timezone.now()
    other = Tenant.objects.create(slug="resend-2967-other", name="Other")
    _withdrawn_person(tenant, "29676", now - timedelta(days=9))
    second = _shell(other, "29676")
    record_global_consent(second, source="test")
    _withdraw(second, now - timedelta(days=2))

    (person,) = list(withdrawn_people())

    assert person.external_user_id == "bot:max:29676"
    assert person.withdrawn_at == now - timedelta(days=2)


def test_r4_the_event_id_is_stable_and_follows_the_withdrawal(tenant) -> None:
    """Повторный прогон каталог узнаёт по ``event_id``; новый отзыв — новое событие."""
    now = timezone.now()
    shell = _withdrawn_person(tenant, "29677", now - timedelta(days=4))

    (first,) = list(withdrawn_people())
    (again,) = list(withdrawn_people())
    assert first.event_id == again.event_id

    record_global_consent(shell, source="test")
    _withdraw(shell, now - timedelta(days=1))
    (later,) = list(withdrawn_people())

    assert later.event_id != first.event_id


# ─── команда ─────────────────────────────────────────────────────────────────


def _run(*args: str) -> str:
    out = StringIO()
    call_command("resend_withdrawn_personal_data", *args, stdout=out)
    return out.getvalue()


def test_c1_the_dry_run_counts_and_sends_nothing(tenant, monkeypatch: pytest.MonkeyPatch) -> None:
    _withdrawn_person(tenant, "29678", timezone.now())
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(f"{COMMAND}.post_consent_event", lambda **kw: sent.append(kw))

    printed = _run()

    assert "mode=dry-run people=1" in printed
    assert sent == []  # empty-assert-ok: c2 видит здесь одно событие


def test_c2_apply_sends_one_event_per_person_and_prints_outcomes(
    tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = timezone.now() - timedelta(days=1)
    _withdrawn_person(tenant, "29679", at)
    sent: list[dict[str, Any]] = []

    def _post(**kwargs: Any) -> ConsentEventReceipt:
        sent.append(kwargs)
        return ConsentEventReceipt(event_id=kwargs["body"]["event_id"], outcome="applied")

    monkeypatch.setattr(f"{COMMAND}.post_consent_event", _post)

    printed = _run("--apply", "--pause", "0")

    assert "mode=apply people=1" in printed
    assert "applied=1" in printed
    (call,) = sent
    assert call["external_user_id"] == "bot:max:29679"
    assert call["body"]["granted"] is False
    assert call["body"]["granted_at"] == at.isoformat()
    # Только числа: идентификатор человека в вывод не попадает.
    assert "mode=apply" in printed and "29679" not in printed


def test_c3_a_failed_delivery_is_counted_and_the_run_goes_on(
    tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = timezone.now()
    _withdrawn_person(tenant, "29680", now)
    _withdrawn_person(tenant, "29681", now)
    calls: list[str] = []

    def _post(**kwargs: Any) -> ConsentEventReceipt:
        calls.append(kwargs["external_user_id"])
        if len(calls) == 1:
            raise ConsentEventUnavailable("server: HTTP 503")
        return ConsentEventReceipt(event_id=kwargs["body"]["event_id"], outcome="applied")

    monkeypatch.setattr(f"{COMMAND}.post_consent_event", _post)

    printed = _run("--apply", "--pause", "0")

    assert len(calls) == 2
    assert "failed:ConsentEventUnavailable=1" in printed
    assert "applied=1" in printed


def test_the_marker_names_this_resend() -> None:
    assert catalog_resend.GRANTED_VIA == "resend:drf-2967"
