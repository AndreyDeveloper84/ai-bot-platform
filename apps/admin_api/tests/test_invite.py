"""Integration tests for POST /api/v1/admin/masters/invite (PR 3 / MM2).

Covers:

* Auth matrix — Owner allowed, Admin allowed, Receptionist 403,
  Customer 403.
* Validation — missing name, bad contact_method, oversized fields,
  cross-tenant service UUID, role!=master.
* Side effects — CatalogMaster created with token + TTL, WorkingHours
  seeded, MasterService rows seeded, audit rows for
  ``master.invited`` + ``master.invite_dispatched``.
* Idempotency — same (name, contact_value) returns existing row +
  ``X-Idempotent`` header; expired invite creates fresh; different
  contact_value creates fresh.
* MAX DM dispatch — success path = queued; raise = failed + audit
  reflects failure.
* Atomic — WorkingHours seed failure rolls back master row.
* Modes — ``catalog_only`` skips token + dispatch.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import init_data_header
from apps.audit.models import AuditLog
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.channels.max.outbound import MaxAPIError
from apps.identity.models import BotUser
from apps.scheduling.models import WorkingHours
from apps.tenancy.models import Tenant


# --- URL helper -----------------------------------------------------------


def _invite_url() -> str:
    return reverse("admin_api:master_invite_create")


# --- shared body builder --------------------------------------------------


def _valid_body(
    *,
    name: str = "Анна Петрова",
    contact_method: str = "max_username",
    contact_value: str = "@anna_styl",
    services: list[str] | None = None,
    schedule_preset: str = "default_mon_fri_10_19",
    mode: str = "invite",
    role: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": name,
        "contact_method": contact_method,
        "contact_value": contact_value,
        "services": services or [],
        "schedule_preset": schedule_preset,
        "mode": mode,
    }
    if role is not None:
        out["role"] = role
    return out


@pytest.fixture
def patched_send_message():
    """Default — MAX send_message returns OK (response ignored)."""

    with patch("apps.admin_api.views_invite.max_outbound.send_message") as mock:
        mock.return_value = {"ok": True}
        yield mock


@pytest.fixture
def patched_send_message_fails():
    """MAX send_message raises a MaxAPIError → delivery=failed."""

    with patch("apps.admin_api.views_invite.max_outbound.send_message") as mock:
        mock.side_effect = MaxAPIError(503, "service unavailable")
        yield mock


# =========================================================================
# AUTH MATRIX
# =========================================================================


class TestAuth:
    def test_missing_auth_header_400(self, client: Client, tenant: Tenant) -> None:
        resp = client.post(_invite_url(), data=_valid_body(), content_type="application/json")
        assert resp.status_code == 400

    def test_customer_403(
        self,
        client: Client,
        customer_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5005"),
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"

    def test_receptionist_403(
        self,
        client: Client,
        receptionist_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5003"),
        )
        assert resp.status_code == 403

    def test_admin_allowed(
        self,
        client: Client,
        admin_bot_user: BotUser,
        tenant: Tenant,
        patched_send_message,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(name="Admin Issued"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5002"),
        )
        assert resp.status_code == 201, resp.content

    def test_owner_allowed(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        patched_send_message,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(name="Owner Issued"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201, resp.content


# =========================================================================
# HAPPY PATH — full side-effect verification
# =========================================================================


class TestHappyPath:
    def test_creates_master_with_token_and_ttl(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        patched_send_message,
        settings,
    ) -> None:
        # DRF-1079 — the assertion below says «uses configured
        # SITE_DOMAIN», and until this line nothing configured it: the
        # test passed against the repository default, i.e. against the
        # localhost link that reached real invited masters on the pilot.
        settings.SITE_DOMAIN = "https://api-dev.gobeauty.site"
        before = datetime.now(tz=timezone.utc)
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        after = datetime.now(tz=timezone.utc)
        assert resp.status_code == 201, resp.content
        body = resp.json()

        master = CatalogMaster.all_tenants.get(id=body["master_id"])
        assert master.tenant_id == tenant.id
        assert master.name == "Анна Петрова"
        assert master.invite_status == CatalogMaster.InviteStatus.PENDING
        assert master.is_active is False
        assert master.mode == CatalogMaster.Mode.INVITE
        assert master.max_handle == "@anna_styl"
        assert master.invite_token is not None
        assert str(master.invite_token) == body["invite_token"]

        # TTL ≈ now + 7 days (allow a generous 60s slack for slow CI).
        assert master.invite_expires_at is not None
        assert master.invite_expires_at > before + timedelta(days=7) - timedelta(minutes=1)
        assert master.invite_expires_at < after + timedelta(days=7) + timedelta(minutes=1)

        # fallback_link includes the token + uses configured SITE_DOMAIN.
        assert body["fallback_link"].endswith(f"?token={master.invite_token}")
        assert "/onboarding/master" in body["fallback_link"]

    def test_default_preset_no_longer_seeds_working_hours(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        patched_send_message,
    ) -> None:
        """DRF-1062: the invite must not manufacture a schedule.

        This branch used to bulk-create Mon-Fri 10:00-19:00 — the stub all
        four pilot masters now carry, indistinguishable from a schedule the
        salon actually set. It also shaped nothing: with
        BOOKING_VIA_AYLA_REST the backend serves slots, not
        `apps.scheduling`.

        `schedule_preset` stays in the request contract and is still echoed
        back; it simply has no side effect now.
        """

        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )

        # Accepted with the preset still in the request contract...
        assert resp.status_code == 201
        # ...and no schedule manufactured behind it.
        master_id = uuid.UUID(resp.json()["master_id"])
        assert not WorkingHours.all_tenants.filter(master_id=master_id).exists()

    def test_schedule_preset_none_seeds_zero_working_hours(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        patched_send_message,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(schedule_preset="none"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201
        master_id = uuid.UUID(resp.json()["master_id"])
        assert WorkingHours.all_tenants.filter(master_id=master_id).count() == 0

    def test_services_array_creates_master_services(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        service: CatalogService,
        patched_send_message,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(services=[str(service.id)]),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201
        master_id = uuid.UUID(resp.json()["master_id"])
        rows = list(MasterService.all_tenants.filter(master_id=master_id))
        assert len(rows) == 1
        assert rows[0].service_id == service.id

    def test_two_audit_rows_emitted(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        patched_send_message,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201
        master_id = uuid.UUID(resp.json()["master_id"])
        actions = sorted(
            AuditLog.all_tenants.filter(target_id=master_id).values_list("action", flat=True)
        )
        assert actions == ["master.invite_dispatched", "master.invited"]

    def test_audit_payload_shape_invited(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        service: CatalogService,
        patched_send_message,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(services=[str(service.id)]),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        master_id = uuid.UUID(resp.json()["master_id"])
        row = AuditLog.all_tenants.get(target_id=master_id, action="master.invited")
        payload = row.payload
        assert payload["master_id"] == str(master_id)
        assert payload["actor_id"] == str(owner_bot_user.id)
        assert payload["actor_role"] == "owner"
        assert payload["role"] == "master"
        assert payload["contact_method"] == "max_username"
        assert payload["mode"] == "invite"
        assert payload["services_count"] == 1


# =========================================================================
# VALIDATION
# =========================================================================


class TestValidation:
    def test_missing_name_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        body = _valid_body()
        del body["name"]
        resp = client.post(
            _invite_url(),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400
        assert "name" in resp.json()["detail"]

    def test_blank_name_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(name="   "),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_bad_contact_method_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(contact_method="email"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400
        # Verify the error mentions email is deferred.
        assert "email" in resp.json()["detail"].lower()

    def test_max_phone_contact_stored_in_raw(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        patched_send_message,
    ) -> None:
        """CatalogMaster has no phone column; we stash phone in raw["invite_phone"]."""

        resp = client.post(
            _invite_url(),
            data=_valid_body(contact_method="max_phone", contact_value="+79161234567"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201, resp.content
        master = CatalogMaster.all_tenants.get(id=resp.json()["master_id"])
        assert master.raw.get("invite_phone") == "+79161234567"
        assert master.max_handle == ""  # max_phone does NOT populate max_handle

    def test_max_phone_dm_delivery_skipped(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        patched_send_message,
    ) -> None:
        """max_phone has no phone→chat lookup yet — dispatch is skipped."""

        resp = client.post(
            _invite_url(),
            data=_valid_body(contact_method="max_phone", contact_value="+79161234567"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201
        assert resp.json()["max_dm_delivery"] == "skipped"
        # send_message must not have been called.
        patched_send_message.assert_not_called()

    def test_cross_tenant_service_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        other_tenant: Tenant,
    ) -> None:
        now = datetime.now(tz=timezone.utc)
        foreign_service = CatalogService.all_tenants.create(
            tenant=other_tenant,
            external_id=999,
            external_updated_at=now,
            slug="foreign",
            name="Foreign",
            duration_min=30,
            is_active=True,
        )
        resp = client.post(
            _invite_url(),
            data=_valid_body(services=[str(foreign_service.id)]),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400
        assert "service not in tenant" in resp.json()["detail"]

    def test_role_admin_rejected(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        """PR 3 scope: admin/receptionist invite writes TenantStaff, not CatalogMaster."""

        resp = client.post(
            _invite_url(),
            data=_valid_body(role="admin"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400
        assert "master" in resp.json()["detail"].lower()

    def test_bad_schedule_preset_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(schedule_preset="custom"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_bad_mode_400(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(mode="anonymous"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_catalog_only_mode_no_token_no_dispatch(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        patched_send_message,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(mode="catalog_only"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["invite_token"] is None
        assert body["invite_expires_at"] is None
        assert body["max_dm_delivery"] == "skipped"
        assert body["fallback_link"] == ""

        master = CatalogMaster.all_tenants.get(id=body["master_id"])
        assert master.mode == CatalogMaster.Mode.CATALOG_ONLY
        assert master.invite_token is None
        assert master.invite_status == CatalogMaster.InviteStatus.ACCEPTED
        assert master.is_active is False
        # send_message NOT called for catalog_only.
        patched_send_message.assert_not_called()


# =========================================================================
# IDEMPOTENCY
# =========================================================================


class TestIdempotency:
    def test_same_name_and_contact_returns_existing_200(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        patched_send_message,
    ) -> None:
        # First call — 201
        first = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert first.status_code == 201
        first_master_id = first.json()["master_id"]
        first_token = first.json()["invite_token"]

        # Second call — same body → 200 with X-Idempotent header.
        second = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert second.status_code == 200, second.content
        assert second["X-Idempotent"] == "true"
        body = second.json()
        assert body["master_id"] == first_master_id
        assert body["invite_token"] == first_token

        # Only one CatalogMaster row.
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 1

    def test_expired_invite_creates_new_row(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        patched_send_message,
    ) -> None:
        # Fabricate an EXPIRED pending invite directly.
        now = datetime.now(tz=timezone.utc)
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=11111,
            external_updated_at=now,
            name="Анна Петрова",
            invite_status=CatalogMaster.InviteStatus.PENDING,
            invite_token=uuid.uuid4(),
            invite_expires_at=now - timedelta(hours=1),
            invited_at=now - timedelta(days=8),
            max_handle="@anna_styl",
            mode=CatalogMaster.Mode.INVITE,
            is_active=False,
        )

        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        # Fresh PENDING row issued — 201, not 200.
        assert resp.status_code == 201
        assert "X-Idempotent" not in resp
        # Now 2 rows in catalog (1 expired, 1 fresh).
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 2

    def test_different_contact_value_same_name_creates_new(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        patched_send_message,
    ) -> None:
        resp1 = client.post(
            _invite_url(),
            data=_valid_body(contact_value="@anna_styl"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp1.status_code == 201

        resp2 = client.post(
            _invite_url(),
            data=_valid_body(contact_value="@anna_other"),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp2.status_code == 201
        assert resp1.json()["master_id"] != resp2.json()["master_id"]


# =========================================================================
# MAX DM DISPATCH
# =========================================================================


class TestDispatch:
    def test_dispatch_success_queued(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        patched_send_message,
        settings,
    ) -> None:
        settings.SITE_DOMAIN = "https://api-dev.gobeauty.site"  # DRF-1079
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201
        assert resp.json()["max_dm_delivery"] == "queued"
        patched_send_message.assert_called_once()
        # The dispatch text should mention the master name + carry the
        # web URL fallback (defence-in-depth message-shape check).
        kwargs = patched_send_message.call_args.kwargs
        assert "Анна" in kwargs["text"]
        assert "/onboarding/master?token=" in kwargs["text"]
        assert kwargs["chat_id"] == "anna_styl"  # leading @ stripped

    def test_dispatch_failure_marks_failed(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        patched_send_message_fails,
    ) -> None:
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201
        assert resp.json()["max_dm_delivery"] == "failed"
        # Master row still created (dispatch is observable, not blocking).
        master = CatalogMaster.all_tenants.get(id=resp.json()["master_id"])
        assert master.invite_status == CatalogMaster.InviteStatus.PENDING

        # Audit row reflects the failure.
        audit = AuditLog.all_tenants.get(target_id=master.id, action="master.invite_dispatched")
        assert audit.payload["delivery"] == "failed"
        assert "error" in audit.payload


# =========================================================================
# ATOMICITY
# =========================================================================


class TestAtomicity:
    def test_service_seeding_failure_rolls_back_master(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        service: CatalogService,
        patched_send_message,
    ) -> None:
        """A failure inside the transaction must not leave a half-made master.

        Was written against WorkingHours seeding, which DRF-1062 removed.
        Re-pointed at MasterService — the remaining bulk write in the same
        atomic block — so the rollback guarantee stays covered rather than
        quietly disappearing with the branch it happened to test.
        """

        before = CatalogMaster.all_tenants.filter(tenant=tenant).count()
        with patch("apps.admin_api.views_invite.MasterService.all_tenants") as mock_ms:
            mock_ms.bulk_create.side_effect = RuntimeError("forced failure")
            resp = client.post(
                _invite_url(),
                # Non-empty services: _seed_services short-circuits on an
                # empty list, so an empty body would never reach bulk_create
                # and the test would pass without exercising anything.
                data=_valid_body(services=[str(service.id)]),
                content_type="application/json",
                HTTP_AUTHORIZATION=init_data_header("5001"),
            )

        assert resp.status_code == 500
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == before


# --- DRF-1079: the web fallback must never point at localhost -------------


@pytest.mark.django_db
class TestSiteDomainFallback:
    """The invite link is the one artefact of this endpoint a human uses.

    On the pilot ``SITE_DOMAIN`` was never set, so every invite carried
    ``http://localhost:5173/onboarding/master?token=...`` — a link that
    opens nothing on the phone it arrives at, with no error anywhere on
    our side. These tests pin the two halves of the fix: a real domain
    produces a real link, an unset one produces no link at all rather
    than a broken one.
    """

    def test_configured_domain_is_used(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        patched_send_message,
        settings,
    ) -> None:
        settings.SITE_DOMAIN = "https://api-dev.gobeauty.site"
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201, resp.content
        link = resp.json()["fallback_link"]
        assert link.startswith("https://api-dev.gobeauty.site/onboarding/master?token=")
        assert link in patched_send_message.call_args.kwargs["text"]

    def test_bare_host_gets_https(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        patched_send_message,
        settings,
    ) -> None:
        """A value written without a scheme must not become a relative URL."""

        settings.SITE_DOMAIN = "api-dev.gobeauty.site"
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201, resp.content
        assert resp.json()["fallback_link"].startswith(
            "https://api-dev.gobeauty.site/onboarding/master?token="
        )

    def test_loopback_domain_suppresses_the_link(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        patched_send_message,
        settings,
    ) -> None:
        """The pilot's actual state: SITE_DOMAIN unset, DEBUG off."""

        settings.SITE_DOMAIN = ""
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201, resp.content
        assert resp.json()["fallback_link"] == ""

        text = patched_send_message.call_args.kwargs["text"]
        assert "localhost" not in text
        # The deeplink still goes — the invite is degraded, not cancelled.
        assert "max://bot/" in text
        # And no orphan heading left behind by the removed block.
        assert "веб-версию" not in text

    def test_debug_keeps_the_localhost_link(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        patched_send_message,
        settings,
    ) -> None:
        """Local dev is the one place the Vite URL is the right answer."""

        settings.DEBUG = True
        settings.SITE_DOMAIN = ""
        resp = client.post(
            _invite_url(),
            data=_valid_body(),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 201, resp.content
        assert resp.json()["fallback_link"].startswith(
            "http://localhost:5173/onboarding/master?token="
        )

    def test_system_check_flags_the_unset_domain(self, settings) -> None:
        """`manage.py check` / `migrate` is where the deploy sees it."""

        from apps.admin_api.checks import check_site_domain

        settings.SITE_DOMAIN = ""
        warnings = check_site_domain(None)
        assert [w.id for w in warnings] == ["admin_api.W001"]

        settings.SITE_DOMAIN = "https://api-dev.gobeauty.site"
        assert check_site_domain(None) == []
