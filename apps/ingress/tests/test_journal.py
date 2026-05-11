"""Tests for WebhookJournal + record_webhook (DRF-423 / C1)."""

from __future__ import annotations

import pytest

from apps.audit.models import AuditLog
from apps.events.models import Event
from apps.ingress.models import WebhookJournal
from apps.ingress.services import record_webhook
from apps.tenancy.context import trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _set_token_map(settings, mapping: dict[str, str]) -> None:
    """Helper: configure the Sprint 1 channel-token → tenant-slug map."""

    settings.CHANNEL_TOKEN_TO_TENANT_SLUG = ",".join(f"{k}={v}" for k, v in mapping.items())


class TestRecordWebhook:
    def test_first_call_creates_row(self, settings):
        t = Tenant.objects.create(slug="ch-a", name="A")
        _set_token_map(settings, {"tok-1": "ch-a"})
        row, created = record_webhook(
            channel="max",
            external_event_id="evt-1",
            raw_payload={"text": "hi"},
            channel_token="tok-1",
        )
        assert created is True
        assert row.channel == "max"
        assert row.external_event_id == "evt-1"
        assert row.raw_payload == {"text": "hi"}
        assert row.resolved_tenant_id == t.id

    def test_dedup_on_retry(self, settings):
        Tenant.objects.create(slug="ch-b", name="B")
        _set_token_map(settings, {"tok-2": "ch-b"})
        row1, c1 = record_webhook(
            channel="max",
            external_event_id="evt-2",
            raw_payload={"text": "first"},
            channel_token="tok-2",
        )
        # Retry with same channel+event_id (different payload — that's still
        # the "same event" from the channel's perspective).
        row2, c2 = record_webhook(
            channel="max",
            external_event_id="evt-2",
            raw_payload={"text": "retry"},
            channel_token="tok-2",
        )
        assert c1 is True
        assert c2 is False  # dedup hit
        assert row1.id == row2.id
        assert WebhookJournal.objects.filter(external_event_id="evt-2").count() == 1

    def test_unknown_channel_token_records_with_none_tenant(self, settings):
        _set_token_map(settings, {"valid-tok": "x"})
        Tenant.objects.create(slug="x", name="X")
        row, created = record_webhook(
            channel="max",
            external_event_id="evt-3",
            raw_payload={},
            channel_token="ghost-token",
        )
        assert created is True
        assert row.resolved_tenant is None
        # Audit log captured the miss.
        audit = AuditLog.all_tenants.filter(action="ingress.webhook_unknown_tenant").first()
        assert audit is not None
        assert audit.payload["channel"] == "max"

    def test_empty_channel_token_records_with_none_tenant_no_audit(self, settings):
        # Empty token means "no token supplied" — common for public test
        # webhooks. We don't audit-log every public probe.
        _set_token_map(settings, {})
        row, _ = record_webhook(
            channel="web",
            external_event_id="evt-4",
            raw_payload={},
            channel_token="",
        )
        assert row.resolved_tenant is None
        audit = AuditLog.all_tenants.filter(action="ingress.webhook_unknown_tenant").first()
        assert audit is None  # no audit for empty token

    def test_jsonb_roundtrip_nested(self, settings):
        Tenant.objects.create(slug="ch-c", name="C")
        _set_token_map(settings, {"tok-3": "ch-c"})
        payload = {
            "level1": {"level2": [1, 2, {"deep": True}]},
            "unicode": "Привет 🌟",
        }
        row, _ = record_webhook(
            channel="max",
            external_event_id="evt-5",
            raw_payload=payload,
            channel_token="tok-3",
        )
        row.refresh_from_db()
        assert row.raw_payload == payload

    def test_trace_id_captured(self, settings):
        Tenant.objects.create(slug="ch-d", name="D")
        _set_token_map(settings, {"tok-4": "ch-d"})
        with trace_id_scope("trace-from-ingress"):
            row, _ = record_webhook(
                channel="max",
                external_event_id="evt-6",
                raw_payload={},
                channel_token="tok-4",
            )
        assert row.trace_id == "trace-from-ingress"


class TestEventsEmitted:
    def test_received_event_on_first_call(self, settings):
        _set_token_map(settings, {})
        record_webhook(
            channel="max",
            external_event_id="ev-emit-1",
            raw_payload={},
            channel_token="",
        )
        events = Event.objects.filter(event_type="ingress.webhook_received")
        assert events.count() == 1
        assert events.first().payload["external_event_id"] == "ev-emit-1"

    def test_duplicate_event_on_retry(self, settings):
        _set_token_map(settings, {})
        record_webhook(
            channel="max",
            external_event_id="ev-emit-2",
            raw_payload={},
            channel_token="",
        )
        record_webhook(
            channel="max",
            external_event_id="ev-emit-2",
            raw_payload={},
            channel_token="",
        )
        dup = Event.objects.filter(event_type="ingress.webhook_duplicate")
        assert dup.count() == 1


class TestEventTenantFK:
    """Regression test for fix #1 (post-review): emit() must persist
    the resolved tenant on Event rows. Without tenant_scope() wrapping
    in record_webhook, the (tenant, -created_at) index built in E0 is
    full of NULLs and per-tenant replay timelines break.
    """

    def test_event_has_tenant_fk_when_resolved(self, settings):
        from apps.events.models import Event

        t = Tenant.objects.create(slug="evfk-a", name="A")
        _set_token_map(settings, {"tok-evfk": "evfk-a"})
        record_webhook(
            channel="max",
            external_event_id="evt-fk-1",
            raw_payload={},
            channel_token="tok-evfk",
        )
        received = Event.objects.get(event_type="ingress.webhook_received")
        assert received.tenant_id == t.id

    def test_audit_has_tenant_fk_none_for_unknown_tenant(self, settings):
        # When tenant is None (unknown token), the audit row still has
        # tenant=None — but the wrap in tenant_scope(None) is correct
        # behaviour, just keeps the contract honest.
        _set_token_map(settings, {})
        record_webhook(
            channel="max",
            external_event_id="evt-fk-2",
            raw_payload={},
            channel_token="ghost-token",
        )
        audit = AuditLog.all_tenants.get(action="ingress.webhook_unknown_tenant")
        assert audit.tenant is None


class TestMalformedEnvVar:
    """Regression test for fix #4 (post-review): malformed token map
    entries (typos, missing '=') must NOT crash record_webhook. Log +
    skip the bad pair, keep good pairs working.
    """

    def test_malformed_pair_is_skipped(self, settings, caplog):
        # 'bad-token' has no '='. 'tok-good=ch-good' is valid.
        Tenant.objects.create(slug="ch-good", name="Good")
        settings.CHANNEL_TOKEN_TO_TENANT_SLUG = "bad-token,tok-good=ch-good"
        with caplog.at_level("WARNING", logger="apps.ingress.services"):
            row, _ = record_webhook(
                channel="max",
                external_event_id="evt-mal-1",
                raw_payload={},
                channel_token="tok-good",
            )
        assert row.resolved_tenant is not None
        assert row.resolved_tenant.slug == "ch-good"
        assert any("malformed_pair" in rec.message for rec in caplog.records)

    def test_empty_token_or_slug_is_skipped(self, settings, caplog):
        # Each of these is malformed: empty token, empty slug.
        settings.CHANNEL_TOKEN_TO_TENANT_SLUG = "=ch-broken,tok-empty=,valid=ok"
        Tenant.objects.create(slug="ok", name="OK")
        with caplog.at_level("WARNING", logger="apps.ingress.services"):
            row, _ = record_webhook(
                channel="max",
                external_event_id="evt-mal-2",
                raw_payload={},
                channel_token="valid",
            )
        assert row.resolved_tenant.slug == "ok"
        assert sum("empty_field" in rec.message for rec in caplog.records) >= 1


class TestDedupUniqueConstraint:
    """Different channels with same external_event_id are NOT duplicates."""

    def test_same_event_id_different_channels(self, settings):
        _set_token_map(settings, {})
        row1, c1 = record_webhook(
            channel="max",
            external_event_id="shared-id",
            raw_payload={},
            channel_token="",
        )
        row2, c2 = record_webhook(
            channel="telegram",
            external_event_id="shared-id",
            raw_payload={},
            channel_token="",
        )
        # Both rows created — dedup is per-(channel, event_id).
        assert c1 is True
        assert c2 is True
        assert row1.id != row2.id
        assert WebhookJournal.objects.filter(external_event_id="shared-id").count() == 2
