"""Integration + unit tests for M7 master notification preferences.

Covers:
  * GET creates with §M7 defaults on first call (idempotent on second).
  * PATCH happy path + partial-update semantics.
  * Validation rejections (urgent_forced_on, time_invalid, unknown field).
  * Overnight quiet-hours window allowed.
  * Audit row written with diff.
  * Cross-tenant isolation.
  * DB CheckConstraint blocks ``urgent=False`` direct writes.
"""

from __future__ import annotations

import json

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.catalog.models import CatalogMaster
from apps.master_api.tests.conftest import init_data_header, make_master
from apps.notifications.models import MasterNotificationPrefs


URL_NAME = "master_api:notification_prefs"


def _get(client: Client, *, header: str | None):
    kwargs: dict = {}
    if header:
        kwargs["HTTP_AUTHORIZATION"] = header
    return client.get(reverse(URL_NAME), **kwargs)


def _patch(client: Client, body: dict, *, header: str | None):
    kwargs: dict = {}
    if header:
        kwargs["HTTP_AUTHORIZATION"] = header
    return client.patch(
        reverse(URL_NAME),
        data=json.dumps(body),
        content_type="application/json",
        **kwargs,
    )


@pytest.mark.django_db
class TestGetPrefs:
    def test_get_creates_defaults(self, client: Client, accepted_master: CatalogMaster) -> None:
        """First GET materialises the row with §M7 defaults."""

        assert MasterNotificationPrefs.all_tenants.filter(master=accepted_master).count() == 0

        resp = _get(client, header=init_data_header("12345"))
        assert resp.status_code == 200, resp.content
        payload = resp.json()["prefs"]

        # §M7 defaults
        assert payload["new_booking"] is True
        assert payload["booking_change"] is True
        assert payload["personal_message"] is True
        assert payload["urgent"] is True
        assert payload["quiet_hours_enabled"] is False
        assert payload["quiet_start"] == "21:00"
        assert payload["quiet_end"] == "09:00"
        assert payload["morning_brief"] is True
        assert payload["evening_summary"] is False

        # Row materialised
        assert MasterNotificationPrefs.all_tenants.filter(master=accepted_master).count() == 1

    def test_get_returns_existing(self, client: Client, accepted_master: CatalogMaster) -> None:
        """Second GET returns same row — no duplicate insert."""

        _get(client, header=init_data_header("12345"))
        _get(client, header=init_data_header("12345"))
        assert MasterNotificationPrefs.all_tenants.filter(master=accepted_master).count() == 1

    def test_unauthenticated_rejected(self, client: Client, db) -> None:
        """No init data → 400 / 401 per existing master_api gating."""

        resp = client.get(reverse(URL_NAME))
        # require_master_init_data returns 400 when no init data and
        # 401 when init data is present but no linked master. Either
        # is an auth rejection here.
        assert resp.status_code in (400, 401, 403), resp.content


@pytest.mark.django_db
class TestPatchPrefs:
    def test_patch_toggle_new_booking_off(
        self, client: Client, accepted_master: CatalogMaster
    ) -> None:
        """Happy path — single bool toggle."""

        resp = _patch(
            client,
            {"new_booking": False},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content
        assert resp.json()["prefs"]["new_booking"] is False

        prefs = MasterNotificationPrefs.all_tenants.get(master=accepted_master)
        assert prefs.new_booking is False
        # Other fields preserved
        assert prefs.booking_change is True
        assert prefs.urgent is True

    def test_patch_urgent_off_rejected_400(
        self, client: Client, accepted_master: CatalogMaster
    ) -> None:
        """Forced-ON urgent: 400 with stable slug."""

        resp = _patch(
            client,
            {"urgent": False},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"] == "urgent_forced_on"

    def test_patch_quiet_hours_enable_with_default_window(
        self, client: Client, accepted_master: CatalogMaster
    ) -> None:
        """Enabling quiet hours without changing the window succeeds."""

        resp = _patch(
            client,
            {"quiet_hours_enabled": True},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 200
        assert resp.json()["prefs"]["quiet_hours_enabled"] is True
        # Defaults preserved
        assert resp.json()["prefs"]["quiet_start"] == "21:00"
        assert resp.json()["prefs"]["quiet_end"] == "09:00"

    def test_patch_quiet_hours_overnight_window(
        self, client: Client, accepted_master: CatalogMaster
    ) -> None:
        """``quiet_start > quiet_end`` is legal — overnight window."""

        resp = _patch(
            client,
            {
                "quiet_hours_enabled": True,
                "quiet_start": "23:00",
                "quiet_end": "07:00",
            },
            header=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content
        prefs = resp.json()["prefs"]
        assert prefs["quiet_start"] == "23:00"
        assert prefs["quiet_end"] == "07:00"

    def test_patch_quiet_hours_same_start_end_rejected(
        self, client: Client, accepted_master: CatalogMaster
    ) -> None:
        """Zero-length quiet window when enabled → 400 time_invalid."""

        resp = _patch(
            client,
            {
                "quiet_hours_enabled": True,
                "quiet_start": "12:00",
                "quiet_end": "12:00",
            },
            header=init_data_header("12345"),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "time_invalid"

    def test_patch_malformed_time_rejected(
        self, client: Client, accepted_master: CatalogMaster
    ) -> None:
        """Non-HH:MM string → 400 time_invalid."""

        resp = _patch(
            client,
            {"quiet_start": "25:99"},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "time_invalid"

    def test_patch_audit_row_written(self, client: Client, accepted_master: CatalogMaster) -> None:
        """Successful PATCH writes one audit row with diff."""

        _patch(
            client,
            {"morning_brief": False, "evening_summary": True},
            header=init_data_header("12345"),
        )

        row = AuditLog.all_tenants.filter(
            action="master.notification_prefs_updated",
        ).first()
        assert row is not None
        changes = row.payload["changes"]
        assert changes["morning_brief"] == {"before": True, "after": False}
        assert changes["evening_summary"] == {"before": False, "after": True}
        # Unchanged fields NOT in diff
        assert "new_booking" not in changes

    def test_patch_noop_writes_no_audit(
        self, client: Client, accepted_master: CatalogMaster
    ) -> None:
        """PATCH with only unchanged values → no audit row."""

        # Materialise defaults
        _get(client, header=init_data_header("12345"))
        AuditLog.all_tenants.all().delete()

        resp = _patch(
            client,
            {"new_booking": True},  # already True (default)
            header=init_data_header("12345"),
        )
        assert resp.status_code == 200
        assert AuditLog.all_tenants.filter(action="master.notification_prefs_updated").count() == 0

    def test_patch_partial_only_updates_supplied_fields(
        self, client: Client, accepted_master: CatalogMaster
    ) -> None:
        """Passing one field doesn't reset others to defaults."""

        # First flip morning_brief
        _patch(
            client,
            {"morning_brief": False},
            header=init_data_header("12345"),
        )
        # Now flip evening_summary — morning_brief should stay False
        resp = _patch(
            client,
            {"evening_summary": True},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 200
        prefs = resp.json()["prefs"]
        assert prefs["morning_brief"] is False  # preserved
        assert prefs["evening_summary"] is True  # newly set

    def test_patch_unknown_field_rejected_400(
        self, client: Client, accepted_master: CatalogMaster
    ) -> None:
        """Extra keys in body → 400 bad_request."""

        resp = _patch(
            client,
            {"sms_enabled": True},  # not a real field
            header=init_data_header("12345"),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"

    def test_patch_non_bool_toggle_rejected(
        self, client: Client, accepted_master: CatalogMaster
    ) -> None:
        """String for a bool field → 400 bad_request."""

        resp = _patch(
            client,
            {"new_booking": "yes"},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"

    def test_patch_creates_row_on_first_call(
        self, client: Client, accepted_master: CatalogMaster
    ) -> None:
        """PATCH on a master who has never opened M7 → row materialised."""

        assert MasterNotificationPrefs.all_tenants.filter(master=accepted_master).count() == 0
        resp = _patch(
            client,
            {"booking_change": False},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 200
        assert MasterNotificationPrefs.all_tenants.filter(master=accepted_master).count() == 1


@pytest.mark.django_db
class TestCrossTenantIsolation:
    def test_cross_tenant_master_cannot_read_other(
        self, client: Client, accepted_master: CatalogMaster, other_tenant
    ) -> None:
        """Tenant A master can't pull tenant B prefs through their session.

        The request resolves the master from the caller's BotUser (HMAC-
        signed init data) — there is no master-id path param to forge.
        We assert by directly checking that the row created by the GET
        belongs to the caller's tenant, NOT the other tenant.
        """

        # Tenant B has its own master + its own row
        from apps.identity.models import BotUser

        bot_b = BotUser.all_tenants.create(
            tenant=other_tenant,
            channel="max",
            channel_user_id="77777",
            display_name="Beta",
            chat_id="77777",
        )
        master_b = make_master(
            other_tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
            linked_bot_user=bot_b,
            external_id=99,
        )
        MasterNotificationPrefs.all_tenants.create(
            tenant=other_tenant,
            master=master_b,
            new_booking=False,
        )

        # Tenant A's session resolves to tenant A's master only.
        resp = _get(client, header=init_data_header("12345"))
        assert resp.status_code == 200
        row = MasterNotificationPrefs.all_tenants.get(master=accepted_master)
        assert row.tenant_id == accepted_master.tenant_id
        # Tenant B's row remains untouched + isolated
        row_b = MasterNotificationPrefs.all_tenants.get(master=master_b)
        assert row_b.tenant_id == other_tenant.id
        assert row_b.new_booking is False


@pytest.mark.django_db
class TestDbConstraint:
    def test_constraint_blocks_direct_db_urgent_false(
        self, tenant, accepted_master: CatalogMaster
    ) -> None:
        """CheckConstraint backstops the service-layer validator.

        Direct ORM bypass (admin shell, data migration) attempting
        ``urgent=False`` must fail at the DB level.
        """

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                MasterNotificationPrefs.all_tenants.create(
                    tenant=tenant,
                    master=accepted_master,
                    urgent=False,
                )
