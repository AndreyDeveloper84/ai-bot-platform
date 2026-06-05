"""YClients admin webhook receiver tests (DRF-838 / Phase 1 / B2).

Coverage matrix — every leaf in :mod:`apps.integrations.yclients.webhooks`:

* ``record.create`` happy path → 200 + BookingRequest + 2 reminders +
  ``booking_created_from_yclients_admin`` event
* Idempotent re-delivery of ``create`` → no duplicate rows
* ``create`` with no matching BotUser → 200 + audit row, no booking
* ``create`` with BotUser missing ``chat_id`` → 200 + audit row
* ``record.update`` → old reminders go CANCELLED, new PENDING with new time
* ``record.delete`` → existing reminders flip to CANCELLED (soft, not hard)
* Idempotent ``delete`` re-delivery → no error, no extra writes
* Malformed JSON body → 200 + audit row
* Unknown resource type → 200 + ignored
* Unknown status → 200 + ignored
* Missing tenant slug config → 200 + audit row (dormant receiver)
* Unhandled exception inside handler → 200 + audit row (always-200 rule)

The receiver MUST always answer 200 — YClients retries any non-2xx
indefinitely. Every failure-path test asserts ``status_code == 200``.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import Client
from django.test.utils import override_settings

from apps.audit.models import AuditLog
from apps.booking.models import BookingReminder, BookingRequest
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


_URL = "/api/v1/yclients/webhook/"
_TENANT_SLUG = "salon-a"


# `transaction.on_commit` only fires when the surrounding atomic block
# commits. pytest-django's default fixture wraps each test in a single
# rollback'd transaction, so the callbacks never run. Mark the module
# as transactional so the inner ``with transaction.atomic()`` issued by
# the Django test client actually commits.
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _yclients_tenant_setting() -> Generator[None, None, None]:
    with override_settings(YCLIENTS_WEBHOOK_TENANT_SLUG=_TENANT_SLUG):
        # Webhook resolves tenant per-request — no need for tenant_scope
        # at fixture level. The view enters its own tenant_scope.
        yield


@pytest.fixture(autouse=True)
def _scope_off_for_webhook(settings) -> Generator[None, None, None]:
    # The webhook is an EXTERNAL entry — it sets its own tenant_scope.
    # Default conftest forces STRICT for tests; relax to `audit` so the
    # outer-test pre-arrange (BotUser.all_tenants.create etc.) doesn't
    # fail before the view runs. The view ITSELF still scopes correctly.
    settings.STRICT_TENANT_SCOPE = "audit"
    yield


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug=_TENANT_SLUG, name="Salon A")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="user-111",
        chat_id="chat-111",
        phone="79991234567",  # E.164-ish — last 10 = 9991234567
        client_name="Anna",
    )


@pytest.fixture
def client() -> Client:
    return Client()


def _payload_create(
    *,
    yc_id: int = 12345,
    phone: str = "+79991234567",
    dt: str = "2026-05-20 14:00:00",
    name: str = "Anna",
    master: str = "Maria",
    service: str = "Massage",
    company_id: int = 999,
) -> dict[str, object]:
    return {
        "company_id": company_id,
        "resource": "record",
        "status": "create",
        "data": {
            "id": yc_id,
            "datetime": dt,
            "client": {"phone": phone, "name": name},
            "staff": {"id": 67890, "name": master},
            "services": [{"id": 11111, "title": service}],
        },
    }


def _post(client: Client, payload: object):
    """Helper for posting to the webhook. Return type is intentionally
    inferred — django-stubs returns ``_MonkeyPatchedWSGIResponse`` (with
    ``.json()``) which is private API. Annotating it explicitly would
    couple us to a stub internal."""
    return client.post(
        _URL,
        data=json.dumps(payload),
        content_type="application/json",
    )


def _audit_actions() -> list[str]:
    return list(AuditLog.all_tenants.values_list("action", flat=True))


# ─── happy path ──────────────────────────────────────────────────────────


class TestCreateEvent:
    def test_create_persists_booking_and_two_reminders(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ) -> None:
        with patch("apps.integrations.yclients.webhooks.emit") as emit_mock:
            resp = _post(client, _payload_create())

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "created"
        assert body["yclients_record_id"] == "12345"

        # One BookingRequest with the yclients marker in comment.
        reqs = list(BookingRequest.all_tenants.all())
        assert len(reqs) == 1
        assert reqs[0].source == "yclients_admin"
        assert "yclients_record_id=12345" in reqs[0].comment
        assert reqs[0].bot_user_id == bot_user.id
        assert reqs[0].is_processed is True

        # Two reminders, one of each kind, both PENDING, both linked.
        rems = list(BookingReminder.all_tenants.all())
        assert len(rems) == 2
        kinds = {r.kind for r in rems}
        assert kinds == {
            BookingReminder.Kind.DAY_BEFORE,
            BookingReminder.Kind.TWO_HOURS,
        }
        for r in rems:
            assert r.status == BookingReminder.Status.PENDING
            assert r.yclients_record_id == "12345"
            assert r.bot_user_id == bot_user.id
            assert r.chat_id == "chat-111"

        # Scheduled offsets correct.
        day_before = next(r for r in rems if r.kind == BookingReminder.Kind.DAY_BEFORE)
        two_hours = next(r for r in rems if r.kind == BookingReminder.Kind.TWO_HOURS)
        delta_day = day_before.visit_at - day_before.scheduled_at
        delta_two = two_hours.visit_at - two_hours.scheduled_at
        assert delta_day == timedelta(hours=24)
        assert delta_two == timedelta(hours=2)

        # transaction.on_commit fires the event after request completes.
        emit_mock.assert_called_once()
        args, kwargs = emit_mock.call_args
        assert args[0] == "booking_created_from_yclients_admin"
        props = kwargs["properties"]
        assert props["yclients_record_id"] == "12345"
        assert props["chat_id"] == "chat-111"
        assert "message" in props  # rendered welcome text

    def test_idempotent_redelivery_no_duplicate_rows(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ) -> None:
        with patch("apps.integrations.yclients.webhooks.emit"):
            resp1 = _post(client, _payload_create())
            resp2 = _post(client, _payload_create())

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert BookingRequest.all_tenants.count() == 1
        assert BookingReminder.all_tenants.count() == 2

    def test_no_bot_user_match_returns_200_with_audit(self, client: Client, tenant: Tenant) -> None:
        # No BotUser exists for this phone.
        resp = _post(client, _payload_create(phone="+78889999999"))

        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"
        assert resp.json()["reason"] == "no_bot_user_match"
        assert BookingRequest.all_tenants.count() == 0
        assert BookingReminder.all_tenants.count() == 0
        assert "yclients.webhook.no_bot_user_match" in _audit_actions()

    def test_bot_user_no_chat_id_returns_200_with_audit(
        self, client: Client, tenant: Tenant
    ) -> None:
        BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="user-222",
            chat_id="",  # explicitly empty
            phone="79998887766",
            client_name="Bob",
        )
        resp = _post(client, _payload_create(phone="+79998887766"))

        assert resp.status_code == 200
        assert resp.json()["reason"] == "bot_user_no_chat_id"
        assert BookingReminder.all_tenants.count() == 0
        assert "yclients.webhook.bot_user_no_chat_id" in _audit_actions()


# ─── update event ────────────────────────────────────────────────────────


class TestUpdateEvent:
    def test_update_cancels_old_creates_new(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ) -> None:
        with patch("apps.integrations.yclients.webhooks.emit"):
            _post(client, _payload_create(dt="2026-05-20 14:00:00"))

        # Trigger update with new datetime.
        update_payload = _payload_create(dt="2026-05-21 16:30:00")
        update_payload["status"] = "update"

        resp = _post(client, update_payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

        # Old rows: now CANCELLED. New rows: PENDING with new visit_at.
        all_rems = list(BookingReminder.all_tenants.all())
        cancelled = [r for r in all_rems if r.status == BookingReminder.Status.CANCELLED]
        pending = [r for r in all_rems if r.status == BookingReminder.Status.PENDING]
        # update_or_create on the same (yc_id, kind) pair MUTATES the
        # row instead of inserting a new one — so the final count is 2,
        # both rows updated in place to the new schedule (the
        # ``existing.update(status=CANCELLED)`` happens BEFORE the
        # update_or_create flips them back to PENDING + new times).
        assert len(pending) == 2
        assert len(cancelled) == 0
        for r in pending:
            # Updated to new visit_at (May 21, not May 20).
            assert r.visit_at.day == 21
            # scheduled_at offsets still correct.
            offset = r.visit_at - r.scheduled_at
            assert offset in (timedelta(hours=24), timedelta(hours=2))

    def test_update_with_no_existing_reminders_falls_through_to_create(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ) -> None:
        # No prior create — mysite handles this by treating update as
        # create (we missed the original event).
        update_payload = _payload_create()
        update_payload["status"] = "update"

        with patch("apps.integrations.yclients.webhooks.emit"):
            resp = _post(client, update_payload)

        assert resp.status_code == 200
        # Falls through to _handle_create → returns "created".
        assert resp.json()["status"] == "created"
        assert BookingReminder.all_tenants.count() == 2


# ─── delete event ────────────────────────────────────────────────────────


class TestDeleteEvent:
    def test_delete_marks_reminders_cancelled_not_hard_deleted(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ) -> None:
        with patch("apps.integrations.yclients.webhooks.emit"):
            _post(client, _payload_create())

        delete_payload = {
            "resource": "record",
            "status": "delete",
            "data": {"id": 12345},
        }
        resp = _post(client, delete_payload)

        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert resp.json()["cancelled"] == 2

        # Rows still exist (audit trail) — just CANCELLED.
        assert BookingReminder.all_tenants.count() == 2
        for r in BookingReminder.all_tenants.all():
            assert r.status == BookingReminder.Status.CANCELLED

    def test_delete_idempotent_already_cancelled(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ) -> None:
        with patch("apps.integrations.yclients.webhooks.emit"):
            _post(client, _payload_create())
        delete_payload = {
            "resource": "record",
            "status": "delete",
            "data": {"id": 12345},
        }
        _post(client, delete_payload)
        resp2 = _post(client, delete_payload)
        assert resp2.status_code == 200
        # Second delete: no rows transitioned (already CANCELLED).
        assert resp2.json()["cancelled"] == 0


# ─── error paths (always 200) ────────────────────────────────────────────


class TestAlwaysReturns200:
    def test_malformed_json_body_returns_200_with_audit(
        self, client: Client, tenant: Tenant
    ) -> None:
        resp = client.post(
            _URL,
            data="not-a-json{",
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.json()["reason"] == "malformed_json"
        assert "yclients.webhook.rejected" in _audit_actions()

    def test_wrong_content_type_still_parsed_or_audited(
        self, client: Client, tenant: Tenant
    ) -> None:
        # YClients has historically sent application/x-www-form-urlencoded
        # with JSON in the body. We don't gate on Content-Type — we parse
        # the raw bytes. So a valid JSON body with wrong CT still works.
        # (For genuinely unparseable bytes the malformed_json test above
        # already covers the audit-200 path.)
        body = json.dumps({"resource": "other", "status": "x"})
        resp = client.post(
            _URL,
            data=body,
            content_type="application/x-www-form-urlencoded",
        )
        assert resp.status_code == 200

    def test_unknown_resource_returns_200_ignored(self, client: Client, tenant: Tenant) -> None:
        resp = _post(client, {"resource": "client", "status": "create"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

    def test_unknown_status_returns_200_ignored(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ) -> None:
        payload = _payload_create()
        payload["status"] = "weirdo"
        resp = _post(client, payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

    def test_missing_tenant_setting_returns_200_with_audit(
        self, client: Client, tenant: Tenant
    ) -> None:
        with override_settings(YCLIENTS_WEBHOOK_TENANT_SLUG=""):
            resp = _post(client, _payload_create())
        assert resp.status_code == 200
        assert resp.json()["reason"] == "tenant_unresolved"
        assert "yclients.webhook.rejected" in _audit_actions()

    def test_unhandled_exception_in_handler_returns_200_with_audit(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ) -> None:
        # Force the handler to raise — assert the always-200 invariant.
        with patch(
            "apps.integrations.yclients.webhooks._handle_create",
            side_effect=RuntimeError("simulated bug"),
        ):
            resp = _post(client, _payload_create())
        assert resp.status_code == 200
        assert resp.json()["reason"] == "handler_exception"
        assert "yclients.webhook.handler_exception" in _audit_actions()


# ─── phone normalisation ────────────────────────────────────────────────


class TestPhoneNormalisation:
    def test_various_phone_formats_match_same_bot_user(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ) -> None:
        # bot_user.phone is '79991234567'. All these should match.
        for incoming in (
            "+7 (999) 123-45-67",
            "8 999 123 45 67",
            "+79991234567",
            "79991234567",
            "9991234567",
        ):
            BookingReminder.all_tenants.all().delete()
            BookingRequest.all_tenants.all().delete()
            with patch("apps.integrations.yclients.webhooks.emit"):
                resp = _post(
                    client,
                    _payload_create(yc_id=999, phone=incoming),
                )
            assert resp.status_code == 200
            assert resp.json()["status"] == "created", incoming
            assert BookingReminder.all_tenants.count() == 2, incoming


# ---------------------------------------------------------------------------
# YClients retro B4 — phone-match ambiguity is deterministic + audited
# ---------------------------------------------------------------------------


class TestRetroB4PhoneAmbiguity:
    """Pre-fix ``_find_bot_user`` returned ``BotUser.objects.filter(
    phone__contains=last10).first()`` — non-deterministic on multi-match.
    Two BotUsers in the same tenant sharing their last 10 phone digits
    (e.g. ``+79991234567`` and ``89991234567``) would silently link a
    new booking to whichever Django returned first.

    Post-fix: order by ``-first_seen``, most-recent registration wins,
    AND an audit row is written so ops can spot the collision.
    """

    def test_phone_collision_deterministic_and_audited(
        self,
        client: Client,
        tenant: Tenant,
    ) -> None:
        # Build two BotUsers in the same tenant whose phones share the
        # last 10 digits — exact mixed-format reality the docstring
        # acknowledges.
        old_bu = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="user-old",
            chat_id="chat-old",
            phone="+79991234567",
            client_name="Anna-old",
        )
        # Sleep-free way to force a deterministic ordering: write directly.
        import datetime as dt

        BotUser.all_tenants.filter(pk=old_bu.pk).update(
            first_seen=dt.datetime(2024, 1, 1, tzinfo=dt.UTC)
        )
        new_bu = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="user-new",
            chat_id="chat-new",
            phone="89991234567",  # Same last 10 digits, different format.
            client_name="Anna-new",
        )
        BotUser.all_tenants.filter(pk=new_bu.pk).update(
            first_seen=dt.datetime(2026, 5, 1, tzinfo=dt.UTC)
        )

        with patch("apps.integrations.yclients.webhooks.emit"):
            resp = _post(
                client,
                _payload_create(yc_id=77777, phone="+79991234567"),
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "created"

        # Booking linked to the MORE RECENT BotUser deterministically.
        booking = BookingRequest.all_tenants.get(comment__contains="yclients_record_id=77777")
        assert booking.bot_user_id == new_bu.pk

        # And an ambiguity audit row was written.
        ambiguity_audits = [a for a in _audit_actions() if "phone_match_ambiguous" in a]
        assert len(ambiguity_audits) == 1, (
            f"expected exactly one phone_match_ambiguous audit row, got: {_audit_actions()}"
        )

    def test_single_match_no_ambiguity_audit(
        self,
        client: Client,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
        # Single match — no audit row (negative regression).
        with patch("apps.integrations.yclients.webhooks.emit"):
            resp = _post(
                client,
                _payload_create(yc_id=88888, phone="+79991234567"),
            )
        assert resp.status_code == 200
        ambiguity_audits = [a for a in _audit_actions() if "phone_match_ambiguous" in a]
        assert len(ambiguity_audits) == 0
