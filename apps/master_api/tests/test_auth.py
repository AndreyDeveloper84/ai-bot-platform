"""Tests for master_api.auth — decorator + invite-token + session token."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import cast

import pytest
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.test import RequestFactory

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.master_api.auth import (
    InvalidInviteToken,
    InviteAlreadyUsed,
    InviteExpired,
    decode_master_session_token,
    generate_invite_token,
    issue_master_session_token,
    require_init_data_only,
    require_master_init_data,
    validate_invite_token,
)
from apps.master_api.tests.conftest import init_data_header, make_master
from apps.tenancy.models import Tenant


def _resp_json(resp: HttpResponse) -> dict:
    """Decode the JSON body off a raw HttpResponse from a directly-invoked view."""

    return json.loads(resp.content)


# --- generate_invite_token + session-token primitives --------------------


class TestGenerateInviteToken:
    def test_returns_uuid(self) -> None:
        t = generate_invite_token()
        assert isinstance(t, uuid.UUID)
        # Each call returns a new value.
        assert t != generate_invite_token()


class TestSessionToken:
    def test_roundtrip(self) -> None:
        master_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        bot_user_id = uuid.uuid4()
        token, exp = issue_master_session_token(
            master_id=master_id,
            tenant_id=tenant_id,
            bot_user_id=bot_user_id,
        )
        payload = decode_master_session_token(token)
        assert payload.master_id == master_id
        assert payload.tenant_id == tenant_id
        assert payload.bot_user_id == bot_user_id
        assert payload.exp == exp

    def test_tampered_token_rejected(self) -> None:
        from django.core.signing import BadSignature

        token, _ = issue_master_session_token(
            master_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            bot_user_id=uuid.uuid4(),
        )
        # Flip a character mid-token.
        tampered = token[:-3] + ("0" if token[-1] != "0" else "1") + token[-2:]
        with pytest.raises(BadSignature):
            decode_master_session_token(tampered)


# --- validate_invite_token -----------------------------------------------


class TestValidateInviteToken:
    def test_happy_path(self, tenant: Tenant, pending_master: CatalogMaster) -> None:
        assert pending_master.invite_token is not None
        with transaction.atomic():
            row = validate_invite_token(pending_master.invite_token, tenant)
        assert row.id == pending_master.id

    def test_invalid_uuid(self, tenant: Tenant) -> None:
        with transaction.atomic(), pytest.raises(InvalidInviteToken):
            validate_invite_token("not-a-uuid", tenant)

    def test_unknown_token(self, tenant: Tenant) -> None:
        with transaction.atomic(), pytest.raises(InvalidInviteToken):
            validate_invite_token(uuid.uuid4(), tenant)

    def test_wrong_tenant(
        self,
        tenant: Tenant,
        other_tenant: Tenant,
        pending_master: CatalogMaster,
    ) -> None:
        """Token exists but in a different tenant → InvalidInviteToken (404 class).

        Spec: avoid leaking existence by mapping cross-tenant to the
        same error slug as missing-token.
        """

        assert pending_master.invite_token is not None
        with transaction.atomic(), pytest.raises(InvalidInviteToken):
            validate_invite_token(pending_master.invite_token, other_tenant)

    def test_already_used(self, tenant: Tenant) -> None:
        master = make_master(
            tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=uuid.uuid4(),
        )
        assert master.invite_token is not None
        with transaction.atomic(), pytest.raises(InviteAlreadyUsed):
            validate_invite_token(master.invite_token, tenant)

    def test_expired(self, tenant: Tenant) -> None:
        master = make_master(tenant, expires_in_days=-1)
        assert master.invite_token is not None
        with transaction.atomic(), pytest.raises(InviteExpired):
            validate_invite_token(master.invite_token, tenant)


# --- require_master_init_data + require_init_data_only -------------------


class TestRequireMasterInitData:
    def test_attaches_master_to_request(
        self,
        accepted_master: CatalogMaster,
        bot_user: BotUser,
    ) -> None:
        """Decorator resolves linked master + active state + tenant scope."""

        captured: dict[str, object] = {}

        @require_master_init_data
        def view(request: HttpRequest) -> JsonResponse:
            captured["master"] = request.master  # type: ignore[attr-defined]
            captured["bot_user"] = request.bot_user  # type: ignore[attr-defined]
            captured["tenant"] = request.tenant  # type: ignore[attr-defined]
            return JsonResponse({"ok": True})

        rf = RequestFactory()
        request = rf.get("/", HTTP_AUTHORIZATION=init_data_header("12345"))
        resp = view(request)
        assert resp.status_code == 200
        assert cast(CatalogMaster, captured["master"]).id == accepted_master.id
        assert cast(BotUser, captured["bot_user"]).id == bot_user.id
        assert cast(Tenant, captured["tenant"]).id == accepted_master.tenant_id

    def test_missing_init_data(self, db) -> None:
        @require_master_init_data
        def view(request: HttpRequest) -> JsonResponse:
            return JsonResponse({"ok": True})

        rf = RequestFactory()
        resp = view(rf.get("/"))
        assert resp.status_code == 400
        assert _resp_json(resp)["error"] == "malformed"

    def test_no_bot_user(self, db) -> None:
        """initData verifies but no BotUser registered → 404."""

        @require_master_init_data
        def view(request: HttpRequest) -> JsonResponse:
            return JsonResponse({"ok": True})

        rf = RequestFactory()
        resp = view(rf.get("/", HTTP_AUTHORIZATION=init_data_header("88888")))
        assert resp.status_code == 404
        assert _resp_json(resp)["error"] == "user_not_registered"

    def test_not_linked_to_master(self, bot_user: BotUser) -> None:
        """BotUser exists but no master record points at it → 401 not_a_master."""

        @require_master_init_data
        def view(request: HttpRequest) -> JsonResponse:
            return JsonResponse({"ok": True})

        rf = RequestFactory()
        resp = view(rf.get("/", HTTP_AUTHORIZATION=init_data_header("12345")))
        assert resp.status_code == 401
        assert _resp_json(resp)["error"] == "not_a_master"

    def test_inactive_master(self, tenant: Tenant, bot_user: BotUser) -> None:
        make_master(
            tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            linked_bot_user=bot_user,
            is_active=False,
            invite_token=None,
            expires_in_days=None,
        )

        @require_master_init_data
        def view(request: HttpRequest) -> JsonResponse:
            return JsonResponse({"ok": True})

        rf = RequestFactory()
        resp = view(rf.get("/", HTTP_AUTHORIZATION=init_data_header("12345")))
        assert resp.status_code == 403
        assert _resp_json(resp)["error"] == "master_inactive"

    def test_archived_master(self, tenant: Tenant, bot_user: BotUser) -> None:
        make_master(
            tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            linked_bot_user=bot_user,
            archived_at=datetime.now(tz=timezone.utc),
            invite_token=None,
            expires_in_days=None,
        )

        @require_master_init_data
        def view(request: HttpRequest) -> JsonResponse:
            return JsonResponse({"ok": True})

        rf = RequestFactory()
        resp = view(rf.get("/", HTTP_AUTHORIZATION=init_data_header("12345")))
        assert resp.status_code == 403
        assert _resp_json(resp)["error"] == "master_inactive"


class TestRequireInitDataOnly:
    def test_no_master_required(self, bot_user: BotUser) -> None:
        """``require_init_data_only`` succeeds even without a linked master.

        Used by the onboarding/* endpoints — at claim/accept time the
        master isn't linked yet.
        """

        @require_init_data_only
        def view(request: HttpRequest) -> JsonResponse:
            return JsonResponse({"bot_user_id": str(request.bot_user.id)})  # type: ignore[attr-defined]

        rf = RequestFactory()
        resp = view(rf.get("/", HTTP_AUTHORIZATION=init_data_header("12345")))
        assert resp.status_code == 200
        assert _resp_json(resp)["bot_user_id"] == str(bot_user.id)
