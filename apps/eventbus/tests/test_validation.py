"""Envelope §2, payload §3, PII §6 validators."""

from __future__ import annotations

import datetime as dt
import uuid

from apps.eventbus import vocabulary as V
from apps.eventbus.envelope import Actor, Envelope
from apps.eventbus.ulid import new_ulid
from apps.eventbus.validation import (
    lint_pii,
    validate_envelope,
    validate_payload,
)


def _envelope(**overrides) -> Envelope:
    defaults = dict(
        event_id=new_ulid(),
        event_name=V.BOOKING_CREATED,
        event_version="1.0",
        occurred_at=dt.datetime.now(dt.UTC),
        actor=Actor(type="system"),
        data={},
    )
    defaults.update(overrides)
    return Envelope(**defaults)


class TestValidateEnvelope:
    def test_well_formed_is_clean(self):
        assert validate_envelope(_envelope()) == []

    def test_rejects_bad_ulid(self):
        errs = validate_envelope(_envelope(event_id="not-a-ulid"))
        assert any("ULID" in e for e in errs)

    def test_rejects_unknown_event_name(self):
        errs = validate_envelope(_envelope(event_name="bogus.name"))
        assert any("unknown event_name" in e for e in errs)

    def test_rejects_naive_datetime(self):
        naive = dt.datetime(2026, 5, 19, 10, 0, 0)
        errs = validate_envelope(_envelope(occurred_at=naive))
        assert any("timezone-aware" in e for e in errs)

    def test_rejects_bad_actor_type(self):
        errs = validate_envelope(_envelope(actor=Actor(type="alien")))  # type: ignore[arg-type]
        assert any("actor.type" in e for e in errs)

    def test_rejects_bad_actor_role(self):
        errs = validate_envelope(
            _envelope(actor=Actor(type="human", role="janitor"))  # type: ignore[arg-type]
        )
        assert any("actor.role" in e for e in errs)

    def test_accepts_optional_actor_role_none(self):
        assert validate_envelope(_envelope(actor=Actor(type="system"))) == []

    def test_rejects_bad_semver(self):
        errs = validate_envelope(_envelope(event_version="v1"))
        assert any("SemVer" in e for e in errs)

    def test_accepts_minor_and_patch_semver(self):
        assert validate_envelope(_envelope(event_version="1.0")) == []
        assert validate_envelope(_envelope(event_version="1.2.3")) == []


class TestValidatePayload:
    def test_well_formed_booking_created(self):
        data = {
            "booking_id": "b1",
            "customer_id": "c1",
            "service_id": "s1",
            "slot_start": "2026-05-19T10:00:00Z",
            "booking_source": "ai_direct",
        }
        assert validate_payload(V.BOOKING_CREATED, data) == []

    def test_missing_required_key_flagged(self):
        data = {"booking_id": "b1"}
        errs = validate_payload(V.BOOKING_CREATED, data)
        assert any("missing required keys" in e for e in errs)

    def test_unknown_event_returns_empty(self):
        # Envelope validator surfaces unknown name; payload validator
        # silent (catalog has nothing to check against).
        assert validate_payload("nope.nope", {"a": 1}) == []


class TestLintPii:
    def test_clean_payload(self):
        data = {"booking_id": "b1", "customer_id": "c1", "amount": 100}
        assert lint_pii(data) == []

    def test_forbidden_key_phone(self):
        violations = lint_pii({"customer_phone": "+7-911-1234567"})
        assert any("phone" in v.lower() for v in violations)

    def test_forbidden_key_email(self):
        violations = lint_pii({"contact_email": "a@b.co"})
        assert any("email" in v.lower() for v in violations)

    def test_forbidden_key_raw_text(self):
        violations = lint_pii({"raw_text": "anything"})
        assert any("raw_text" in v.lower() for v in violations)

    def test_phone_shape_in_value(self):
        violations = lint_pii({"note": "call me at +7 911 555 7777"})
        assert any("phone" in v.lower() for v in violations)

    def test_email_shape_in_value(self):
        violations = lint_pii({"note": "ping at user@example.com"})
        assert any("email" in v.lower() for v in violations)

    def test_walks_nested_dicts(self):
        violations = lint_pii({"meta": {"deep": {"customer_phone": "+71234567890"}}})
        assert violations  # caught the nested phone key

    def test_walks_lists(self):
        violations = lint_pii({"items": [{"customer_email": "x@y.z"}]})
        assert violations

    def test_id_strings_dont_trip_phone_heuristic(self):
        # UUIDs and short numeric ids must not trip the phone regex.
        assert lint_pii({"booking_id": str(uuid.uuid4())}) == []
        assert lint_pii({"score": 0.85}) == []
