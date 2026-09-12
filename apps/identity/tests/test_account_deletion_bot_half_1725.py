"""Бот-половина удаления аккаунта (§7 D3, DRF-1725) —
``POST /api/v1/internal/privacy/account-deletion/`` и её сервис.

Каталог ставит COMPLETED только по нашему ``all_ok``; здесь проверяется,
что ``all_ok`` — правда о сделанном, а не о попытке.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from unittest.mock import patch

import pytest
from django.test import Client

from apps.identity.models import BotUser, UserPersonalContext
from apps.identity.services.account_deletion import execute_bot_half, shells_for
from apps.identity.services.deletion_gate import (
    deletion_gate,
    mark_deletion_requested,
)
from apps.identity.services.privacy import DeleteCascadeResult, DeleteStep
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

AYLA_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
REQUEST_ID = str(uuid.uuid4())
URL = "/api/v1/internal/privacy/account-deletion/"
SECRET = "test-hmac-secret-1725"  # pragma: allowlist secret


@pytest.fixture
def tenant(db):
    return Tenant.objects.create(slug="del-1725", name="Del")


@pytest.fixture
def bot_user(tenant):
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="1725001",
        chat_id="chat-1725001",
        display_name="Анна",
        ayla_user_id=AYLA_ID,
    )


@pytest.fixture
def flagged(bot_user):
    mark_deletion_requested(AYLA_ID, request_id=REQUEST_ID)
    assert deletion_gate(AYLA_ID).blocked
    return bot_user


def _ok_cascade(*_a, **_k):
    return DeleteCascadeResult(
        steps=(DeleteStep("ayla_delete", True), DeleteStep("memory_delete", True))
    )


def _failed_cascade(*_a, **_k):
    return DeleteCascadeResult(
        steps=(DeleteStep("ayla_delete", False, "not_linked"), DeleteStep("memory_delete", True))
    )


class TestService:
    def test_shells_found_by_ayla_id_and_by_external_id(self, bot_user, tenant):
        other = BotUser.all_tenants.create(
            tenant=Tenant.objects.create(slug="del-1725-b", name="B"),
            channel="max",
            channel_user_id="1725001",
            chat_id="c2",
        )
        found = shells_for(AYLA_ID, ["bot:max:1725001"])
        assert {b.id for b in found} == {bot_user.id, other.id}
        # Положительная стража рядом: те же два id по своим ключам находятся —
        # пустой ответ ниже значит «чужой ключ», а не «поиск не работает».
        assert len(shells_for(AYLA_ID, [])) == 1
        assert len(shells_for(uuid.uuid4(), ["bot:max:1725001"])) == 2
        assert shells_for(uuid.uuid4(), ["bot:max:nobody", "garbage"]) == []

    def test_cascade_ok_clears_the_flag_and_reports_all_ok(self, flagged):
        with patch(
            "apps.identity.services.account_deletion.delete_personal_data", _ok_cascade
        ) as p:
            out = execute_bot_half(
                ayla_user_id=AYLA_ID, external_user_ids=["bot:max:1725001"], request_id=REQUEST_ID
            )
        assert out.all_ok and out.shells == 1 and out.flag_cleared
        assert out.failed_steps == []
        assert [s["step"] for s in out.steps] == ["ayla_delete", "memory_delete"]
        assert not deletion_gate(AYLA_ID).blocked
        _ = p

    def test_failed_step_keeps_the_flag_and_is_not_all_ok(self, flagged):
        with patch("apps.identity.services.account_deletion.delete_personal_data", _failed_cascade):
            out = execute_bot_half(
                ayla_user_id=AYLA_ID, external_user_ids=[], request_id=REQUEST_ID
            )
        assert not out.all_ok and out.failed_steps == ["ayla_delete"]
        assert not out.flag_cleared
        # Флаг D2 стоит: каталог не получил подтверждения — гейт закрыт.
        assert deletion_gate(AYLA_ID).blocked

    def test_no_shells_is_all_ok_with_no_steps(self, db):
        upc = UserPersonalContext.objects.create(user_id=AYLA_ID)
        mark_deletion_requested(AYLA_ID, request_id=REQUEST_ID)
        with patch("apps.identity.services.account_deletion.delete_personal_data") as cascade:
            out = execute_bot_half(
                ayla_user_id=AYLA_ID, external_user_ids=[], request_id=REQUEST_ID
            )
        cascade.assert_not_called()
        assert out.all_ok and out.shells == 0 and out.steps == []
        assert out.flag_cleared
        upc.refresh_from_db()
        assert upc.deletion_requested_at is None


def _signed(body: bytes, secret: str = SECRET, ts_ms: int | None = None) -> dict:
    ts = str(ts_ms if ts_ms is not None else int(time.time() * 1000))
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"HTTP_X_AYLA_EVENT_SIGNATURE": sig, "HTTP_X_AYLA_EVENT_TIMESTAMP": ts}


def _body(**over) -> bytes:
    data = {
        "request_id": REQUEST_ID,
        "ayla_user_id": str(AYLA_ID),
        "external_user_ids": ["bot:max:1725001"],
    }
    data.update(over)
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


class TestView:
    @pytest.fixture(autouse=True)
    def _secret(self, settings):
        settings.EVENT_INGEST_HMAC_SECRET = SECRET

    def test_signed_request_runs_the_bot_half_and_returns_all_ok(self, flagged):
        body = _body()
        with patch("apps.identity.services.account_deletion.delete_personal_data", _ok_cascade):
            resp = Client().post(URL, data=body, content_type="application/json", **_signed(body))
        assert resp.status_code == 200, resp.content
        data = resp.json()["data"]
        assert data["all_ok"] is True and data["shells"] == 1 and data["flag_cleared"] is True
        assert data["failed_steps"] == []
        assert not deletion_gate(AYLA_ID).blocked

    def test_unsigned_or_wrong_secret_is_401_and_nothing_runs(self, flagged):
        body = _body()
        with patch("apps.identity.services.account_deletion.delete_personal_data") as cascade:
            unsigned = Client().post(URL, data=body, content_type="application/json")
            wrong = Client().post(
                URL,
                data=body,
                content_type="application/json",
                **_signed(body, secret="other"),  # pragma: allowlist secret
            )
            stale = Client().post(
                URL,
                data=body,
                content_type="application/json",
                **_signed(body, ts_ms=int(time.time() * 1000) - 10 * 60 * 1000),
            )
        assert (unsigned.status_code, wrong.status_code, stale.status_code) == (401, 401, 401)
        assert unsigned.json()["reason"] == "missing_signature"
        assert wrong.json()["reason"] == "hmac_mismatch"
        assert stale.json()["reason"] == "timestamp_stale"
        cascade.assert_not_called()
        assert deletion_gate(AYLA_ID).blocked

    def test_malformed_body_is_400(self, flagged):
        body = b'{"request_id": "r"}'
        resp = Client().post(URL, data=body, content_type="application/json", **_signed(body))
        assert resp.status_code == 400
        body = _body(ayla_user_id="not-a-uuid")
        resp = Client().post(URL, data=body, content_type="application/json", **_signed(body))
        assert resp.status_code == 400

    def test_failed_step_is_200_with_all_ok_false(self, flagged):
        body = _body()
        with patch("apps.identity.services.account_deletion.delete_personal_data", _failed_cascade):
            resp = Client().post(URL, data=body, content_type="application/json", **_signed(body))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["all_ok"] is False and data["failed_steps"] == ["ayla_delete"]
        assert deletion_gate(AYLA_ID).blocked

    def test_get_is_not_served(self):
        assert Client().get(URL).status_code == 405

    def test_strict_tenant_mode_does_not_preempt_the_signature_check(self, flagged, settings):
        """Каталог X-Tenant не шлёт; в strict-режиме до подписи должен доходить
        сам запрос, а не 400 TENANT_REQUIRED от middleware."""
        settings.STRICT_TENANT_SCOPE = "strict"
        body = _body()
        with patch("apps.identity.services.account_deletion.delete_personal_data", _ok_cascade):
            resp = Client().post(URL, data=body, content_type="application/json", **_signed(body))
        assert resp.status_code == 200, resp.content
        unsigned = Client().post(URL, data=body, content_type="application/json")
        assert unsigned.status_code == 401 and b"TENANT_REQUIRED" not in unsigned.content


class TestRealCascade:
    """Без подмены каскада — только каталог за фальшивым клиентом: телефон
    на оболочке стёрт, флаг снят, шаг ``ayla_delete`` сходил в каталог."""

    def test_end_to_end_with_a_fake_catalog(self, flagged):
        calls: list[dict] = []

        class _Catalog:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return None

            def close(self):
                return None

            def delete_personal_data(self, **kw):
                calls.append(kw)

        flagged.phone = "+79990001122"
        flagged.save(update_fields=["phone"])
        # Имя подменяется там, где каскад его читает (импорт на уровне
        # модуля privacy), — иначе подмена вернёт своё имя, но не потомство.
        with patch(
            "apps.identity.services.privacy.PersonalContextHttpClient",
            lambda *a, **k: _Catalog(),
        ):
            out = execute_bot_half(
                ayla_user_id=AYLA_ID, external_user_ids=["bot:max:1725001"], request_id=REQUEST_ID
            )
        assert out.all_ok, out.failed_steps
        assert calls == [{"ayla_user_id": str(AYLA_ID), "external_user_id": "bot:max:1725001"}]
        flagged.refresh_from_db()
        assert flagged.phone == "" and flagged.display_name == ""
        assert not deletion_gate(AYLA_ID).blocked
