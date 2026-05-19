"""Tests for the Shiro-Py salon-knowledge webhook receiver (KB-SYNC / GH issue #20 contract).

The handler at ``POST /api/v1/salon-knowledge/webhook/`` consumes
``knowledge.approved`` events from the colleague's ``salon-knowledge``
service. The architectural decision (see
``docs/design/2026-05-18-phase-5-architecture-comparison.md``): we do
**eager sync via webhook**, not query-time HTTP proxy. The webhook
delivers the full markdown body; we upsert a ``KbDocument`` row in the
matching tenant; the K6 Celery ingester picks it up on its next pass
and embeds into ChromaDB. The bot's retrieval surface (``search_kb``)
stays local-only.

Contracts pinned here (every behaviour MUST stay covered by a test —
this is a security + correctness boundary):

    1. Bad / missing / wrong HMAC signature → 200 + audit row, NO write.
       Returning 200 on bad signature is deliberate (matches the YooKassa
       webhook pattern): a prober shouldn't learn signature semantics
       from the HTTP status. Bad-signature audits are how we forensically
       detect probing.
    2. Malformed JSON → 200 + audit row.
    3. Unknown event name → 200 + audit row, no write.
    4. Unknown salon_slug → 200 + audit row, no write. Soft fail: maybe
       the salon was deleted on our side; let the colleague's retry
       chain stop quickly rather than 500-loop.
    5. Happy path CREATE — first delivery of a (tenant, source_uri) →
       new KbDocument at ``version=1``, ``embedded_at=None``.
    6. Happy path SKIP — duplicate delivery (same content checksum) →
       no write; existing row untouched. Idempotency guaranteed even if
       the colleague's Celery retries.
    7. Happy path VERSION BUMP — same source_uri, edited content →
       new KbDocument at ``version=existing.version+1``, old row stays
       as forensic history.
    8. schema_type → doc_type mapping (service/staff/faq/policy →
       SERVICE/MASTER/FAQ/LEGAL).
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from django.test import Client
from django.urls import reverse

from apps.kb.models import KbDocType, KbDocument
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


_SECRET = "test-shared-secret-do-not-use-in-prod"  # pragma: allowlist secret


def _sign(body: bytes, secret: str = _SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post(client: Client, payload: dict, *, signature: str | None = None) -> "object":
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "HTTP_X_SIGNATURE_256": signature if signature is not None else _sign(body),
        "HTTP_X_EVENT": payload.get("event", ""),
        "CONTENT_TYPE": "application/json",
    }
    return client.post(
        reverse("salon_knowledge_webhooks:approved"),
        data=body,
        content_type="application/json",
        **headers,
    )


def _payload(
    *,
    tenant_slug: str = "formula-tela",
    schema_type: str = "service",
    knowledge_doc_id: int = 456,
    version: int = 1,
    content_md: str = "# Маникюр\n\nКлассический маникюр, 60 минут.",
) -> dict:
    source_uri = f"shiro-py://salon/{tenant_slug}/{schema_type}/{knowledge_doc_id}"
    return {
        "event": "knowledge.approved",
        "salon_id": 5,
        "salon_slug": tenant_slug,
        "data": {
            "queue_item_id": 123,
            "knowledge_doc_id": knowledge_doc_id,
            "schema_type": schema_type,
            "version": version,
            "content_md": content_md,
            "source_uri": source_uri,
        },
    }


@pytest.fixture
def tenant(db):
    return Tenant.objects.create(slug="formula-tela", name="Формула тела")


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def _configured(settings):
    """Set the shared webhook secret. Most tests use this; the
    `TestUnconfigured` class explicitly clears it via its own fixture."""
    settings.SALON_KNOWLEDGE_WEBHOOK_SECRET = _SECRET
    yield
    # pytest-django auto-restores; explicit yield here makes the
    # teardown contract obvious to readers.


@pytest.mark.usefixtures("_configured")
class TestSignature:
    def test_bad_signature_returns_200_and_writes_nothing(self, tenant, client):
        response = _post(client, _payload(), signature="sha256=deadbeef")
        assert response.status_code == 200
        assert KbDocument.all_tenants.filter(tenant=tenant).count() == 0

    def test_missing_signature_returns_200_and_writes_nothing(self, tenant, client):
        # Pass empty signature (header present but empty) — should be
        # rejected as "bad signature" path.
        response = _post(client, _payload(), signature="")
        assert response.status_code == 200
        assert KbDocument.all_tenants.filter(tenant=tenant).count() == 0

    def test_wrong_secret_returns_200_and_writes_nothing(self, tenant, client):
        wrong = _sign(b"{}", secret="wrong-secret")  # pragma: allowlist secret
        response = _post(client, _payload(), signature=wrong)
        assert response.status_code == 200
        assert KbDocument.all_tenants.filter(tenant=tenant).count() == 0


@pytest.mark.usefixtures("_configured")
class TestMalformedPayload:
    def test_malformed_json_returns_200(self, client):
        body = b"{not-json"
        response = client.post(
            reverse("salon_knowledge_webhooks:approved"),
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE_256=_sign(body),
            HTTP_X_EVENT="knowledge.approved",
        )
        assert response.status_code == 200

    def test_unknown_event_returns_200_and_writes_nothing(self, tenant, client):
        payload = _payload()
        payload["event"] = "knowledge.something_we_dont_know"
        response = _post(client, payload)
        assert response.status_code == 200
        assert KbDocument.all_tenants.filter(tenant=tenant).count() == 0

    def test_unknown_salon_slug_returns_200_and_writes_nothing(self, client):
        response = _post(client, _payload(tenant_slug="non-existent-salon"))
        assert response.status_code == 200
        # No tenant created; no rows for any tenant.
        assert KbDocument.all_tenants.count() == 0


@pytest.mark.usefixtures("_configured")
class TestHappyPath:
    def test_create_first_delivery(self, tenant, client):
        response = _post(client, _payload())
        assert response.status_code == 200
        row = KbDocument.all_tenants.get(tenant=tenant)
        assert row.doc_type == KbDocType.SERVICE
        assert row.version == 1
        assert row.embedded_at is None
        assert row.content.startswith("# Маникюр")
        assert row.source_uri == "shiro-py://salon/formula-tela/service/456"

    def test_duplicate_delivery_is_skip(self, tenant, client):
        # First delivery: CREATE.
        _post(client, _payload())
        original = KbDocument.all_tenants.get(tenant=tenant)

        # Same payload again (colleague's Celery retry, our 200 didn't
        # land, network blip, etc.). Must not duplicate or version-bump.
        _post(client, _payload())
        rows = list(KbDocument.all_tenants.filter(tenant=tenant))
        assert len(rows) == 1
        assert rows[0].pk == original.pk
        assert rows[0].version == 1

    def test_edit_bumps_version(self, tenant, client):
        # v1.
        _post(client, _payload(content_md="# Маникюр v1"))
        v1 = KbDocument.all_tenants.get(tenant=tenant)
        assert v1.version == 1

        # Same source_uri, different content → new row at v2.
        _post(client, _payload(content_md="# Маникюр v2 (отредактирован)"))
        rows = list(
            KbDocument.all_tenants.filter(
                tenant=tenant,
                source_uri="shiro-py://salon/formula-tela/service/456",
            ).order_by("version")
        )
        assert len(rows) == 2
        assert rows[0].version == 1
        assert rows[1].version == 2
        assert rows[1].embedded_at is None  # K6 will re-embed
        # v1 stays as forensic history — not deleted.
        assert rows[0].content == "# Маникюр v1"


@pytest.mark.usefixtures("_configured")
class TestSchemaTypeMapping:
    @pytest.mark.parametrize(
        "schema_type,expected_doc_type",
        [
            ("service", KbDocType.SERVICE),
            ("staff", KbDocType.MASTER),
            ("faq", KbDocType.FAQ),
            ("policy", KbDocType.LEGAL),
        ],
    )
    def test_schema_type_maps_to_doc_type(self, tenant, client, schema_type, expected_doc_type):
        response = _post(
            client,
            _payload(schema_type=schema_type, knowledge_doc_id=100 + hash(schema_type) % 1000),
        )
        assert response.status_code == 200
        row = KbDocument.all_tenants.get(tenant=tenant)
        assert row.doc_type == expected_doc_type

    def test_unknown_schema_type_returns_200_no_write(self, tenant, client):
        response = _post(client, _payload(schema_type="nonsense"))
        assert response.status_code == 200
        assert KbDocument.all_tenants.filter(tenant=tenant).count() == 0


class TestUnconfigured:
    @pytest.fixture(autouse=True)
    def _clear_secret(self, settings):
        settings.SALON_KNOWLEDGE_WEBHOOK_SECRET = ""

    def test_returns_500_when_secret_unset(self, tenant, client):
        # Distinct from bad-signature path: if the operator forgot to
        # configure the secret on this environment, every request gets
        # 500 (loud) — not 200 (silent). Silent rejection would mask a
        # broken deploy as "consumer silently dropping events".
        response = _post(client, _payload())
        assert response.status_code == 500
        assert KbDocument.all_tenants.filter(tenant=tenant).count() == 0
