"""Админка записей: действия вместо правки полей (DRF-1498).

Проверяет четыре требования задачи:

1. Поле состояния механически недоступно для прямой правки в админке.
2. Отмена через админку порождает те же события и побочные эффекты, что
   и отмена через API (путь Mini App: ``request_cancel`` + ``commit_cancel``
   — именно их зовёт ``apps.miniapp_api.views.booking_cancel_*``).
3. Отмена без причины не проходит.
4. Автор действия и причина попадают в журнал.

Каждой отрицательной проверке здесь стоит парная положительная на тех
же данных (negative_assert_guard, DRF-1411).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone as dj_timezone

from apps.adminconsole.accounts import PASSWORD_ENV_VAR, grant_admin_account
from apps.audit.models import AuditLog
from apps.booking.models import BookingReminder, BookingRequest
from apps.booking.services.admin_cancel import AUDIT_ADMIN_CANCEL
from apps.booking.services.transitions import (
    AUDIT_CANCEL_REQUESTED,
    AUDIT_CANCELLED,
    EVENT_CANCEL_REQUESTED,
    EVENT_CANCELLED,
    commit_cancel,
    request_cancel,
)
from apps.catalog.models import CatalogMaster, CatalogService
from apps.events.models import Event
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

MSK = ZoneInfo("Europe/Moscow")

_CANCEL_EVENTS = (EVENT_CANCEL_REQUESTED, EVENT_CANCELLED)
_CANCEL_AUDITS = (AUDIT_CANCEL_REQUESTED, AUDIT_CANCELLED)


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="adm", name="Adm", timezone="Europe/Moscow")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="adm-1",
        chat_id="adm-1",
    )


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=7,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        name="Анна",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
    )


@pytest.fixture
def service(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=77,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        slug="manicure",
        name="Маникюр",
        duration_min=60,
        is_active=True,
    )


def _make_booking(
    *,
    tenant: Tenant,
    bot_user: BotUser,
    master: CatalogMaster,
    service: CatalogService,
) -> BookingRequest:
    target = dj_timezone.now().astimezone(MSK) + timedelta(days=14)
    visit_at = target.replace(hour=12, minute=0, second=0, microsecond=0)
    return BookingRequest.objects.create(
        tenant=tenant,
        bot_user=bot_user,
        service=service,
        master=master,
        service_name=service.name,
        master_name=master.name,
        client_name="Test",
        client_phone="+7-000",
        visit_at=visit_at,
        duration_min=60,
        status=BookingRequest.Status.CONFIRMED,
        source="bot",
        booking_source="ai_direct",
        billable=True,
        billing_reason="ai_direct + confirmed",
        attribution_metadata={"actor_type": "customer", "created_by": "execute_confirm"},
    )


def _make_pending_reminder(booking: BookingRequest) -> BookingReminder:
    return BookingReminder.all_tenants.create(
        tenant=booking.tenant,
        bot_user=booking.bot_user,
        booking_request=booking,
        yclients_record_id=f"adm-{booking.pk}",
        chat_id="adm-1",
        visit_at=booking.visit_at,
        kind=BookingReminder.Kind.DAY_BEFORE,
        scheduled_at=booking.visit_at - timedelta(days=1),
        status=BookingReminder.Status.PENDING,
    )


def _account(monkeypatch: pytest.MonkeyPatch, username: str, role: str):
    password = secrets.token_urlsafe(24)
    monkeypatch.setenv(PASSWORD_ENV_VAR, password)
    user, _ = grant_admin_account(username=username, role=role)
    monkeypatch.delenv(PASSWORD_ENV_VAR, raising=False)
    return user


def _cancel_url(booking: BookingRequest) -> str:
    return reverse("admin:booking_bookingrequest_cancel", args=[booking.pk])


def _cancel_event_names(booking: BookingRequest) -> list[str]:
    return sorted(
        Event.objects.filter(
            event_name__in=_CANCEL_EVENTS,
            properties__booking_id=str(booking.pk),
        ).values_list("event_name", flat=True)
    )


def _cancel_audit_actions(booking: BookingRequest) -> list[str]:
    return sorted(
        AuditLog.all_tenants.filter(
            action__in=_CANCEL_AUDITS,
            target_id=booking.pk,
        ).values_list("action", flat=True)
    )


def _post_cancel(client: Client, booking: BookingRequest, reason: str = ""):
    return client.post(_cancel_url(booking), {"reason": reason})


@pytest.mark.django_db
class TestStatusFieldNotEditable:
    """Прямая правка состояния запрещена механически, а не соглашением."""

    def test_change_form_has_no_editable_status(
        self, monkeypatch, tenant, bot_user, master, service
    ) -> None:
        booking = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        editor = _account(monkeypatch, "adm.editor", "editor")
        client = Client()
        client.force_login(editor)

        url = reverse("admin:booking_bookingrequest_change", args=[booking.pk])
        response = client.get(url)
        assert response.status_code == 200

        content = response.content.decode()
        # Отрицательная: поля статуса как элемента ввода на форме нет —
        # править его руками нельзя. Ни checkbox, ни select, ни input.
        assert 'name="status"' not in content
        # Парная положительная: на тех же данных действие доступно —
        # экран отмены открывается, и форма в нём есть.
        form_response = client.get(_cancel_url(booking))
        assert form_response.status_code == 200
        assert 'name="reason"' in form_response.content.decode()


@pytest.mark.django_db
class TestAdminCancelParityWithApi:
    """Отмена через админку = отмена через API по факту побочных эффектов."""

    def test_same_events_audit_and_reminder_effects(
        self, monkeypatch, tenant, bot_user, master, service
    ) -> None:
        # Запись A отменяется путём API: ровно те вызовы, что делает
        # miniapp_api.views.booking_cancel_request + booking_cancel_confirm.
        api_booking = _make_booking(
            tenant=tenant, bot_user=bot_user, master=master, service=service
        )
        api_reminder = _make_pending_reminder(api_booking)
        row = request_cancel(api_booking, actor=bot_user, reason_text="передумал")
        commit_cancel(row, actor=bot_user)

        # Запись B отменяется через админку правящим с причиной.
        admin_booking = _make_booking(
            tenant=tenant, bot_user=bot_user, master=master, service=service
        )
        admin_reminder = _make_pending_reminder(admin_booking)
        editor = _account(monkeypatch, "adm.parity", "editor")
        client = Client()
        client.force_login(editor)
        response = _post_cancel(client, admin_booking, reason="клиент позвонил")
        assert response.status_code == 302

        # Состояние — то же.
        api_booking.refresh_from_db()
        admin_booking.refresh_from_db()
        assert admin_booking.status == BookingRequest.Status.CANCELLED
        assert api_booking.status == BookingRequest.Status.CANCELLED

        # События шины — те же, по факту строк в events.
        assert _cancel_event_names(admin_booking) == sorted(_CANCEL_EVENTS)
        assert _cancel_event_names(admin_booking) == _cancel_event_names(api_booking)

        # Строки аудита переходов — те же.
        assert _cancel_audit_actions(admin_booking) == sorted(_CANCEL_AUDITS)
        assert _cancel_audit_actions(admin_booking) == _cancel_audit_actions(api_booking)

        # Ожидающие напоминания сняты в обоих путях.
        api_reminder.refresh_from_db()
        admin_reminder.refresh_from_db()
        assert admin_reminder.status == BookingReminder.Status.CANCELLED
        assert api_reminder.status == BookingReminder.Status.CANCELLED

    def test_admin_cancel_journal_carries_author_and_reason(
        self, monkeypatch, tenant, bot_user, master, service
    ) -> None:
        booking = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        editor = _account(monkeypatch, "adm.journal", "editor")
        client = Client()
        client.force_login(editor)

        response = _post_cancel(client, booking, reason="клиент позвонил и отменил")
        assert response.status_code == 302

        journal = AuditLog.all_tenants.get(action=AUDIT_ADMIN_CANCEL, target_id=booking.pk)
        assert journal.payload["actor_username"] == "adm.journal"
        assert journal.payload["reason"] == "клиент позвонил и отменил"


@pytest.mark.django_db
class TestCancelRequiresReason:
    def test_empty_reason_rejected_then_reasoned_cancel_works(
        self, monkeypatch, tenant, bot_user, master, service
    ) -> None:
        booking = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        editor = _account(monkeypatch, "adm.reason", "editor")
        client = Client()
        client.force_login(editor)

        # Отрицательная: отмена без причины не проходит.
        response = _post_cancel(client, booking, reason="   ")
        assert response.status_code == 302  # назад в список, с сообщением об ошибке
        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.CONFIRMED
        assert _cancel_event_names(booking) == []
        assert not AuditLog.all_tenants.filter(
            action=AUDIT_ADMIN_CANCEL, target_id=booking.pk
        ).exists()

        # Парная положительная на тех же данных: с причиной отмена проходит.
        response = _post_cancel(client, booking, reason="клиент попросил")
        assert response.status_code == 302
        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.CANCELLED
        assert _cancel_event_names(booking) == sorted(_CANCEL_EVENTS)

        # Парная положительная «нетронутая запись»: соседняя запись того
        # же салона состояния не сменила.
        untouched = _make_booking(
            tenant=tenant, bot_user=bot_user, master=master, service=service
        )
        untouched.refresh_from_db()
        assert untouched.status == BookingRequest.Status.CONFIRMED


@pytest.mark.django_db
class TestViewerCannotCancel:
    def test_viewer_denied_then_editor_allowed(
        self, monkeypatch, tenant, bot_user, master, service
    ) -> None:
        booking = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        viewer = _account(monkeypatch, "adm.viewer", "viewer")
        client = Client()
        client.force_login(viewer)

        # Присутствие: смотрящий действительно попадает на экран чтения.
        changelist = client.get(reverse("admin:booking_bookingrequest_changelist"))
        assert changelist.status_code == 200

        # Отрицательная: действие смотрящему не достаётся.
        response = _post_cancel(client, booking, reason="попытка без прав")
        assert response.status_code == 403
        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.CONFIRMED

        # Парная положительная на тех же данных: правящий ту же отмену
        # проводит.
        editor = _account(monkeypatch, "adm.editor2", "editor")
        client.force_login(editor)
        response = _post_cancel(client, booking, reason="клиент попросил")
        assert response.status_code == 302
        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.CANCELLED


@pytest.mark.django_db
class TestCancelLinkVisibility:
    def test_link_only_where_machine_allows(
        self, monkeypatch, tenant, bot_user, master, service
    ) -> None:
        confirmed = _make_booking(
            tenant=tenant, bot_user=bot_user, master=master, service=service
        )
        cancelled = _make_booking(
            tenant=tenant, bot_user=bot_user, master=master, service=service
        )
        BookingRequest.all_tenants.filter(pk=cancelled.pk).update(
            status=BookingRequest.Status.CANCELLED
        )
        editor = _account(monkeypatch, "adm.links", "editor")
        client = Client()
        client.force_login(editor)

        content = client.get(reverse("admin:booking_bookingrequest_changelist")).content.decode()

        # Положительная: у записи, которую машина разрешает отменить,
        # ссылка «Отменить…» есть.
        assert _cancel_url(confirmed) in content
        # Отрицательная: у уже отменённой записи ссылки нет — отменить
        # её ещё раз машина не даст.
        assert _cancel_url(cancelled) not in content
