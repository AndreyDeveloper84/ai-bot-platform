"""Integration tests for /api/v1/master/onboarding/{claim,accept,reject}."""

from __future__ import annotations

import json
import uuid

from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.master_api.auth import decode_master_session_token
from apps.master_api.tests.conftest import init_data_header, make_master
from apps.tenancy.models import Tenant


def _post_json(client: Client, name: str, *, body: dict, header: str | None):
    if header:
        return client.post(
            reverse(name),
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )
    return client.post(
        reverse(name),
        data=json.dumps(body),
        content_type="application/json",
    )


# --- /onboarding/claim ----------------------------------------------------


class TestOnboardingClaim:
    def test_happy_path_returns_profile(
        self,
        client: Client,
        bot_user: BotUser,
        pending_master: CatalogMaster,
    ) -> None:
        resp = _post_json(
            client,
            "master_api:onboarding_claim",
            body={"token": str(pending_master.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content
        data = resp.json()
        assert data["master"]["id"] == str(pending_master.id)
        assert data["master"]["name"] == pending_master.name
        assert data["salon"]["tenant_id"] == str(pending_master.tenant_id)
        assert data["max_user"]["phone_masked"].endswith("67")  # last two digits of fixture phone

    def test_idempotent(
        self,
        client: Client,
        bot_user: BotUser,
        pending_master: CatalogMaster,
    ) -> None:
        body = {"token": str(pending_master.invite_token)}
        resp1 = _post_json(
            client, "master_api:onboarding_claim", body=body, header=init_data_header("12345")
        )
        resp2 = _post_json(
            client, "master_api:onboarding_claim", body=body, header=init_data_header("12345")
        )
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        pending_master.refresh_from_db()
        # State unchanged — claim is read-only.
        assert pending_master.invite_status == CatalogMaster.InviteStatus.PENDING
        assert pending_master.invite_token is not None

    def test_invalid_token(self, client: Client, bot_user: BotUser) -> None:
        resp = _post_json(
            client,
            "master_api:onboarding_claim",
            body={"token": str(uuid.uuid4())},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "invalid_invite_token"

    def test_expired_token(self, client: Client, bot_user: BotUser, tenant: Tenant) -> None:
        master = make_master(tenant, expires_in_days=-1)
        resp = _post_json(
            client,
            "master_api:onboarding_claim",
            body={"token": str(master.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 410
        assert resp.json()["error"] == "invite_expired"

    def test_already_accepted(self, client: Client, bot_user: BotUser, tenant: Tenant) -> None:
        master = make_master(
            tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=uuid.uuid4(),
        )
        resp = _post_json(
            client,
            "master_api:onboarding_claim",
            body={"token": str(master.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "invite_already_used"

    def test_cross_tenant_404(
        self,
        client: Client,
        bot_user: BotUser,
        other_tenant: Tenant,
    ) -> None:
        """Token belongs to a different tenant — must NOT leak existence."""

        foreign = make_master(other_tenant)
        resp = _post_json(
            client,
            "master_api:onboarding_claim",
            body={"token": str(foreign.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "invalid_invite_token"

    def test_missing_token_field(self, client: Client, bot_user: BotUser) -> None:
        resp = _post_json(
            client,
            "master_api:onboarding_claim",
            body={},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 400

    def test_audit_row_emitted(
        self,
        client: Client,
        bot_user: BotUser,
        pending_master: CatalogMaster,
    ) -> None:
        _post_json(
            client,
            "master_api:onboarding_claim",
            body={"token": str(pending_master.invite_token)},
            header=init_data_header("12345"),
        )
        row = AuditLog.all_tenants.filter(
            action="master.onboarding_started",
            target_id=pending_master.id,
        ).first()
        assert row is not None
        assert row.payload["bot_user_id"] == str(bot_user.id)


# --- /onboarding/accept ---------------------------------------------------


class TestOnboardingAccept:
    def test_happy_path_links_bot_user_and_returns_session(
        self,
        client: Client,
        bot_user: BotUser,
        pending_master: CatalogMaster,
    ) -> None:
        resp = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(pending_master.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content
        data = resp.json()
        assert data["master_id"] == str(pending_master.id)
        assert data["session_token"]
        assert data["expires_at"]

        # State mutated.
        pending_master.refresh_from_db()
        assert pending_master.invite_status == CatalogMaster.InviteStatus.ACCEPTED
        assert pending_master.linked_bot_user_id == bot_user.id
        assert pending_master.invite_token is None
        assert pending_master.mode == CatalogMaster.Mode.INVITE

        # Session token round-trips.
        payload = decode_master_session_token(data["session_token"])
        assert payload.master_id == pending_master.id
        assert payload.bot_user_id == bot_user.id

    def test_audit_row_emitted(
        self,
        client: Client,
        bot_user: BotUser,
        pending_master: CatalogMaster,
    ) -> None:
        _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(pending_master.invite_token)},
            header=init_data_header("12345"),
        )
        row = AuditLog.all_tenants.filter(
            action="master.onboarding_accepted",
            target_id=pending_master.id,
        ).first()
        assert row is not None

    def test_idempotent_second_accept(
        self,
        client: Client,
        bot_user: BotUser,
        pending_master: CatalogMaster,
    ) -> None:
        """Spec: second accept from the SAME BotUser returns same shape."""

        token = str(pending_master.invite_token)
        resp1 = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": token},
            header=init_data_header("12345"),
        )
        # Same token won't validate anymore (cleared), but bot_user is
        # linked → idempotency path returns 200.
        resp2 = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": token},
            header=init_data_header("12345"),
        )
        assert resp1.status_code == 200
        assert resp2.status_code == 200, resp2.content
        assert resp1.json()["master_id"] == resp2.json()["master_id"]

    def test_wrong_recipient_blocked(
        self,
        client: Client,
        tenant: Tenant,
        bot_user: BotUser,
        other_bot_user: BotUser,
    ) -> None:
        """Master already linked to OTHER bot_user → 403 wrong_recipient."""

        master = make_master(
            tenant,
            linked_bot_user=other_bot_user,
            invite_status=CatalogMaster.InviteStatus.PENDING,
        )
        resp = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(master.invite_token)},
            header=init_data_header("12345"),  # bot_user (not other_bot_user)
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "wrong_recipient"

    def test_expired_token(self, client: Client, bot_user: BotUser, tenant: Tenant) -> None:
        master = make_master(tenant, expires_in_days=-1)
        resp = _post_json(
            client,
            "master_api:onboarding_accept",
            body={"token": str(master.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 410


# --- /onboarding/reject ---------------------------------------------------


class TestOnboardingReject:
    def test_happy_path_cancels(
        self,
        client: Client,
        bot_user: BotUser,
        pending_master: CatalogMaster,
    ) -> None:
        resp = _post_json(
            client,
            "master_api:onboarding_reject",
            body={"token": str(pending_master.invite_token)},
            header=init_data_header("12345"),
        )
        assert resp.status_code == 204
        # No body — spec says no master leak.
        assert resp.content == b""

        pending_master.refresh_from_db()
        assert pending_master.invite_status == CatalogMaster.InviteStatus.CANCELLED
        # No BotUser linkage on reject.
        assert pending_master.linked_bot_user_id is None

    def test_audit_row_emitted(
        self,
        client: Client,
        bot_user: BotUser,
        pending_master: CatalogMaster,
    ) -> None:
        _post_json(
            client,
            "master_api:onboarding_reject",
            body={"token": str(pending_master.invite_token)},
            header=init_data_header("12345"),
        )
        row = AuditLog.all_tenants.filter(
            action="master.onboarding_rejected",
            target_id=pending_master.id,
        ).first()
        assert row is not None
        assert row.payload["bot_user_id"] == str(bot_user.id)

    def test_second_reject_blocked(
        self,
        client: Client,
        bot_user: BotUser,
        pending_master: CatalogMaster,
    ) -> None:
        body = {"token": str(pending_master.invite_token)}
        first = _post_json(
            client, "master_api:onboarding_reject", body=body, header=init_data_header("12345")
        )
        second = _post_json(
            client, "master_api:onboarding_reject", body=body, header=init_data_header("12345")
        )
        assert first.status_code == 204
        assert second.status_code == 409
        assert second.json()["error"] == "invite_already_used"
