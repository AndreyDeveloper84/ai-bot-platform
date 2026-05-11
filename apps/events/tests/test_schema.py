"""EventEnvelope + validate() tests (DRF-460 / Sprint 3 / B2)."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from dataclasses import FrozenInstanceError

from apps.events.schema import EventEnvelope, validate


class TestEnvelopeShape:
    def test_minimal_construction(self):
        env = EventEnvelope(event_name="consent_granted")
        assert env.event_name == "consent_granted"
        assert env.distinct_id == ""
        assert env.tenant_id is None
        assert env.dialog_id is None
        assert env.properties == {}
        assert env.trace_id == ""
        assert isinstance(env.ts, dt.datetime)

    def test_full_construction(self):
        tenant = uuid.uuid4()
        dialog = uuid.uuid4()
        env = EventEnvelope(
            event_name="message_sent",
            distinct_id="bot-user-uuid",
            tenant_id=tenant,
            dialog_id=dialog,
            properties={"text": "hi"},
            trace_id="trace-abc",
        )
        assert env.tenant_id == tenant
        assert env.dialog_id == dialog
        assert env.properties == {"text": "hi"}

    def test_envelope_is_frozen(self):
        env = EventEnvelope(event_name="consent_granted")
        with pytest.raises(FrozenInstanceError):
            env.event_name = "tampered"  # type: ignore[misc]

    def test_default_properties_not_shared_between_instances(self):
        """field(default_factory=dict) — no shared-mutable-default footgun."""

        a = EventEnvelope(event_name="consent_granted")
        b = EventEnvelope(event_name="consent_withdrawn")
        a.properties["x"] = 1
        assert "x" not in b.properties


class TestValidate:
    def test_valid_envelope_returns_empty_list(self):
        env = EventEnvelope(
            event_name="consent_granted",
            distinct_id="x",
            tenant_id=uuid.uuid4(),
            properties={"consent_type": "marketing"},
            trace_id="trace-1",
        )
        assert validate(env) == []

    def test_unknown_event_name_rejected(self):
        env = EventEnvelope(event_name="totally_made_up")
        errors = validate(env)
        assert len(errors) == 1
        assert "unknown event_name" in errors[0]
        assert "totally_made_up" in errors[0]

    def test_dotted_legacy_name_rejected_as_unknown(self):
        """Infra-tier names (worker.*, ingress.*) are NOT canonical."""

        env = EventEnvelope(event_name="worker.handler_started")
        errors = validate(env)
        assert any("unknown event_name" in e for e in errors)

    def test_non_dict_properties_rejected(self):
        env = EventEnvelope(
            event_name="consent_granted",
            properties="not a dict",  # type: ignore[arg-type]
        )
        errors = validate(env)
        assert any("properties must be a dict" in e for e in errors)

    def test_non_json_serialisable_properties_rejected(self):
        """A set is not JSON-serialisable — must be caught."""

        env = EventEnvelope(
            event_name="consent_granted",
            properties={"weird": {1, 2, 3}},  # type: ignore[dict-item]
        )
        errors = validate(env)
        assert any("not JSON-serialisable" in e for e in errors)

    def test_wrong_tenant_id_type_rejected(self):
        env = EventEnvelope(
            event_name="consent_granted",
            tenant_id="not-a-uuid",  # type: ignore[arg-type]
        )
        errors = validate(env)
        assert any("tenant_id must be UUID" in e for e in errors)

    def test_wrong_dialog_id_type_rejected(self):
        env = EventEnvelope(
            event_name="consent_granted",
            dialog_id=12345,  # type: ignore[arg-type]
        )
        errors = validate(env)
        assert any("dialog_id must be UUID" in e for e in errors)

    def test_multiple_errors_collected_at_once(self):
        """validate() does not bail on first error — surfaces them all."""

        env = EventEnvelope(
            event_name="bogus",
            tenant_id="not-uuid",  # type: ignore[arg-type]
            properties="also wrong",  # type: ignore[arg-type]
        )
        errors = validate(env)
        # 3 problems: event_name + tenant_id + properties (non-dict)
        assert len(errors) >= 3
