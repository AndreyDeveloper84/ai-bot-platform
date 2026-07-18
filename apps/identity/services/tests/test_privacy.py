"""C5 privacy tests — export aggregation + delete cascade + the two views.

The Ayla wire is stubbed at the client level. Covers the frozen-contract
obligations: one-JSON export with attachment, idempotent delete cascade
(repeat → 200), per-step honest partials (502), audit rows without
personal values, consent/memory_green interplay.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from urllib.parse import urlencode

import pytest
from django.test import Client as DjangoClient

from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser, MemoryEntry
from apps.identity.services.memory_inferred import (
    InferredGreenFact,
    record_inferred_green_facts,
)
from apps.identity.services.privacy import (
    PrivacyUpstreamError,
    delete_personal_data,
    export_personal_data,
)
from apps.integrations.ayla.personal_context_client import (
    PersonalContextNotFoundError,
    PersonalContextTransportError,
)
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-xyz"
CT = ConsentRecord.ConsentType


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str = "12345") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Мария"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    # miniapp_api auth resolves the request tenant by this slug.
    settings.MAX_BOT_TENANT_SLUG = "priv-test"


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="priv-test", name="Privacy Test")


@pytest.fixture
def ayla_user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def bot_user(tenant, ayla_user_id) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="12345",
        chat_id="12345",
        ayla_user_id=ayla_user_id,
    )


def _grant(bu: BotUser, ctype: str) -> ConsentRecord:
    return ConsentRecord.all_tenants.create(
        tenant=bu.tenant,
        bot_user=bu,
        consent_type=ctype,
        granted=True,
        source="test",
    )


def _seed_memory(bu: BotUser, ayla_user_id: uuid.UUID) -> None:
    _grant(bu, CT.PERSONAL_DATA)
    record_inferred_green_facts(
        bu,
        [InferredGreenFact(kind="diet", content={"key": "diet_type", "value": "vegan"})],
    )


class _StubPCClient:
    """Personal-context client stand-in (export/delete legs)."""

    def __init__(self, *, export_payload=None, delete_exc: Exception | None = None) -> None:
        self.export_payload = export_payload or {
            "profile": {"display_name": "Мария"},
            "context": {"diet_type": "vegan"},
        }
        self.delete_exc = delete_exc
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    def get_personal_data_export(self, *, ayla_user_id: str):
        self.calls.append(("export", ayla_user_id))
        return self.export_payload

    def delete_personal_data(self, *, ayla_user_id: str) -> None:
        self.calls.append(("delete", ayla_user_id))
        if self.delete_exc:
            raise self.delete_exc

    def close(self) -> None:
        self.closed = True


class TestExport:
    def test_aggregates_three_sections(self, bot_user, ayla_user_id) -> None:
        _seed_memory(bot_user, ayla_user_id)
        _grant(bot_user, CT.MEMORY_GREEN)

        payload = export_personal_data(bot_user, client=_StubPCClient())  # type: ignore[arg-type]

        assert payload["subject"]["ayla_user_id"] == str(ayla_user_id)
        assert payload["ayla"]["profile"]["display_name"] == "Мария"
        assert [m["content"]["value"] for m in payload["memory"]] == ["vegan"]
        consent_types = {c["consent_type"] for c in payload["consents"]}
        assert {CT.PERSONAL_DATA, CT.MEMORY_GREEN} <= consent_types
        assert payload["generated_at"]

    def test_audit_row_written(self, bot_user, ayla_user_id) -> None:
        from apps.audit.models import AuditLog

        export_personal_data(bot_user, client=_StubPCClient())  # type: ignore[arg-type]

        log = AuditLog.all_tenants.filter(action="privacy.personal_data_exported").first()
        assert log is not None
        assert "vegan" not in json.dumps(log.payload)

    def test_upstream_failure_raises(self, bot_user) -> None:
        class _Failing(_StubPCClient):
            def get_personal_data_export(self, *, ayla_user_id: str):
                raise PersonalContextTransportError("http_500")

        with pytest.raises(PrivacyUpstreamError):
            export_personal_data(bot_user, client=_Failing())  # type: ignore[arg-type]

    def test_unlinked_user_exports_bot_side_only(self, tenant) -> None:
        bu = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="777", ayla_user_id=None
        )

        payload = export_personal_data(tenant and bu)  # no client needed

        assert payload["subject"]["ayla_user_id"] is None
        assert payload["ayla"] is None
        assert payload["memory"] == []


class TestDelete:
    def test_full_cascade(self, bot_user, ayla_user_id) -> None:
        _seed_memory(bot_user, ayla_user_id)
        _grant(bot_user, CT.MEMORY_GREEN)
        client = _StubPCClient()

        result = delete_personal_data(bot_user, client=client)  # type: ignore[arg-type]

        assert result.all_ok
        assert client.calls == [("delete", str(ayla_user_id))]
        # Memory gone from the read surface.
        assert not MemoryEntry.objects.filter(
            user_id=ayla_user_id, soft_deleted_at__isnull=True
        ).exists()
        # Consents withdrawn (both bases).
        assert not ConsentRecord.all_tenants.filter(
            bot_user=bot_user, withdrawn_at__isnull=True
        ).exists()

    def test_idempotent_repeat(self, bot_user, ayla_user_id) -> None:
        _seed_memory(bot_user, ayla_user_id)
        client = _StubPCClient()

        first = delete_personal_data(bot_user, client=client)  # type: ignore[arg-type]
        second = delete_personal_data(bot_user, client=client)  # type: ignore[arg-type]

        assert first.all_ok and second.all_ok

    def test_upstream_404_counts_as_deleted(self, bot_user) -> None:
        client = _StubPCClient(delete_exc=PersonalContextNotFoundError("gone"))
        result = delete_personal_data(bot_user, client=client)  # type: ignore[arg-type]
        assert result.all_ok
        ayla_step = next(s for s in result.steps if s.step == "ayla_delete")
        assert ayla_step.detail == "already_deleted"

    def test_upstream_5xx_is_honest_partial(self, bot_user, ayla_user_id) -> None:
        _seed_memory(bot_user, ayla_user_id)
        client = _StubPCClient(delete_exc=PersonalContextTransportError("http_500"))

        result = delete_personal_data(bot_user, client=client)  # type: ignore[arg-type]

        assert not result.all_ok
        assert result.failed_steps == ["ayla_delete"]
        # Local steps still ran (user's legal right doesn't wait on upstream).
        assert not MemoryEntry.objects.filter(
            user_id=ayla_user_id, soft_deleted_at__isnull=True
        ).exists()

    def test_audit_has_no_values(self, bot_user, ayla_user_id) -> None:
        from apps.audit.models import AuditLog

        _seed_memory(bot_user, ayla_user_id)
        delete_personal_data(bot_user, client=_StubPCClient())  # type: ignore[arg-type]

        log = AuditLog.all_tenants.filter(action="privacy.personal_data_deleted").first()
        assert log is not None
        assert "vegan" not in json.dumps(log.payload)
        assert set(log.payload["scope"]) == {
            "ayla_delete",
            "memory_delete",
            "consent_withdraw",
        }


class TestViews:
    def test_export_attachment(
        self, client: DjangoClient, bot_user, ayla_user_id, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "apps.identity.services.privacy.PersonalContextHttpClient",
            lambda: _StubPCClient(),
        )
        resp = client.get(
            "/api/v1/customer/me/personal-data/export/",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 200
        assert resp["Content-Disposition"] == 'attachment; filename="personal-data-export.json"'
        body = resp.json()
        assert body["subject"]["ayla_user_id"] == str(ayla_user_id)

    def test_export_upstream_502(self, client: DjangoClient, bot_user, monkeypatch) -> None:
        class _Failing(_StubPCClient):
            def get_personal_data_export(self, *, ayla_user_id: str):
                raise PersonalContextTransportError("http_500")

        monkeypatch.setattr(
            "apps.identity.services.privacy.PersonalContextHttpClient",
            lambda: _Failing(),
        )
        resp = client.get(
            "/api/v1/customer/me/personal-data/export/",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 502
        assert resp.json()["error"] == "upstream_unavailable"

    def test_delete_200_and_repeat(
        self, client: DjangoClient, bot_user, ayla_user_id, monkeypatch
    ) -> None:
        _seed_memory(bot_user, ayla_user_id)
        monkeypatch.setattr(
            "apps.identity.services.privacy.PersonalContextHttpClient",
            lambda: _StubPCClient(),
        )
        for _ in range(2):
            resp = client.delete(
                "/api/v1/customer/me/personal-data/",
                HTTP_AUTHORIZATION=_init_data_header("12345"),
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "deleted"

    def test_delete_partial_502(self, client: DjangoClient, bot_user, monkeypatch) -> None:
        monkeypatch.setattr(
            "apps.identity.services.privacy.PersonalContextHttpClient",
            lambda: _StubPCClient(delete_exc=PersonalContextTransportError("http_500")),
        )
        resp = client.delete(
            "/api/v1/customer/me/personal-data/",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 502
        assert resp.json()["failed_steps"] == ["ayla_delete"]

    def test_auth_required(self, client: DjangoClient) -> None:
        assert client.get("/api/v1/customer/me/personal-data/export/").status_code in (
            400,
            401,
            403,
        )
        assert client.delete("/api/v1/customer/me/personal-data/").status_code in (
            400,
            401,
            403,
        )
