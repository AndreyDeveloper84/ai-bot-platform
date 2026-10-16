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


# ─── след запуска: одна агрегатная строка журнала ───────────────────────────


def _audit_rows() -> list[dict[str, Any]]:
    from apps.audit.models import AuditLog

    return [
        dict(row.payload)
        for row in AuditLog.all_tenants.filter(action="consent.resend.catalog").order_by(
            "created_at"
        )
    ]


def test_a1_apply_leaves_one_aggregate_audit_row_without_person_ids(
    tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = timezone.now()
    for person in ("29690", "29691", "29692"):
        _withdrawn_person(tenant, person, now)
    answers = iter(["applied", "duplicate", "applied"])

    def _post(**kwargs: Any) -> ConsentEventReceipt:
        return ConsentEventReceipt(event_id=kwargs["body"]["event_id"], outcome=next(answers))

    monkeypatch.setattr(f"{COMMAND}.post_consent_event", _post)

    printed = _run("--apply", "--pause", "0")

    (row,) = _audit_rows()
    assert row["consent_type"] == "personal_data"
    assert row["people"] == 3
    assert row["outcomes"] == {"applied": 2, "duplicate": 1}
    assert row["completed"] is True
    assert row["started_at"] <= row["finished_at"]
    assert sorted(row) == [
        "completed",
        "consent_type",
        "finished_at",
        "outcomes",
        "people",
        "run_id",
        "started_at",
    ]
    # Ни одного идентификатора человека — ни в строке журнала, ни в выводе.
    flat = repr(row) + printed
    assert row["people"] == 3 and not any(p in flat for p in ("29690", "29691", "29692"))
    assert f"run_id={row['run_id']}" in printed


def test_a2_the_dry_run_leaves_no_audit_row(tenant, monkeypatch: pytest.MonkeyPatch) -> None:
    _withdrawn_person(tenant, "29693", timezone.now())
    monkeypatch.setattr(f"{COMMAND}.post_consent_event", lambda **kw: None)

    printed = _run()

    assert "mode=dry-run people=1" in printed
    assert _audit_rows() == []  # empty-assert-ok: a1 видит здесь одну строку


def test_a3_failures_are_in_the_audit_row_by_class_name(
    tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = timezone.now()
    _withdrawn_person(tenant, "29694", now)
    _withdrawn_person(tenant, "29695", now)
    calls: list[int] = []

    def _post(**kwargs: Any) -> ConsentEventReceipt:
        calls.append(1)
        if len(calls) == 1:
            raise ConsentEventUnavailable("server: HTTP 503")
        return ConsentEventReceipt(event_id=kwargs["body"]["event_id"], outcome="applied")

    monkeypatch.setattr(f"{COMMAND}.post_consent_event", _post)

    _run("--apply", "--pause", "0")

    (row,) = _audit_rows()
    assert row["outcomes"] == {"applied": 1, "failed:ConsentEventUnavailable": 1}
    assert row["completed"] is True


def test_a4_an_interrupted_run_still_leaves_the_row_with_what_went_out(
    tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Прогон оборвался не отказом каталога, а чем-то иным — уже отправленное
    должно остаться в журнале, с пометкой «не доведён»."""
    now = timezone.now()
    _withdrawn_person(tenant, "29696", now)
    _withdrawn_person(tenant, "29697", now)
    calls: list[int] = []

    def _post(**kwargs: Any) -> ConsentEventReceipt:
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("interrupted")
        return ConsentEventReceipt(event_id=kwargs["body"]["event_id"], outcome="applied")

    monkeypatch.setattr(f"{COMMAND}.post_consent_event", _post)

    with pytest.raises(RuntimeError):
        _run("--apply", "--pause", "0")

    (row,) = _audit_rows()
    assert row["completed"] is False
    assert row["outcomes"] == {"applied": 1}
    assert row["people"] == 2


def test_a5_a_missing_audit_row_is_a_loud_failure(tenant, monkeypatch: pytest.MonkeyPatch) -> None:
    """``write_audit`` свои сбои глотает; строка — единственный след отправки,
    поэтому её отсутствие — отказ команды с числами в тексте, а не тишина."""
    from django.core.management.base import CommandError

    _withdrawn_person(tenant, "29698", timezone.now())
    monkeypatch.setattr(
        f"{COMMAND}.post_consent_event",
        lambda **kw: ConsentEventReceipt(event_id=kw["body"]["event_id"], outcome="applied"),
    )
    monkeypatch.setattr(f"{COMMAND}.write_audit", lambda *args, **kwargs: None)

    with pytest.raises(CommandError) as caught:
        _run("--apply", "--pause", "0")

    assert "строка журнала аудита не записана" in str(caught.value)
    assert "'applied': 1" in str(caught.value)
    assert "29698" not in str(caught.value)


def test_the_marker_names_this_resend() -> None:
    assert catalog_resend.GRANTED_VIA == "resend:drf-2967"
