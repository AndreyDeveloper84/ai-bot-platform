"""Tests for :mod:`apps.eventbus.ingest_envelope` (Phase 0 / #432).

Pin each `IngestEnvelopeError.reason` slug — the view layer maps them
to HTTP 400/422 per event-contract.md §8, and the test suite is the
contract for that mapping until the view-level tests land.
"""

from __future__ import annotations

import json

import pytest

from apps.eventbus.ingest_envelope import (
    IngestEnvelopeError,
    parse_envelope,
)


VALID_ENVELOPE: dict = {
    "event_id": "01J9HXKM8Z2T4V6R8Q1P3D5F7E",
    "event_name": "booking.created",
    "event_version": 1,
    "occurred_at": "2026-05-21T14:32:11.482Z",
    "tenant_id": "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c",
    "user_id": "f1a2b3c4-d5e6-4789-9abc-def012345678",
    "actor": "user",
    "correlation_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "causation_id": None,
    "data": {"appointment_id": "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"},
}


class TestHappyPath:
    def test_parses_valid_envelope(self) -> None:
        env = parse_envelope(json.dumps(VALID_ENVELOPE))
        assert env.event_id == VALID_ENVELOPE["event_id"]
        assert env.event_name == "booking.created"
        assert env.event_version == 1
        assert env.actor == "user"
        assert env.tenant_id == VALID_ENVELOPE["tenant_id"]
        assert env.causation_id is None
        assert env.data == VALID_ENVELOPE["data"]

    def test_accepts_bytes_body(self) -> None:
        env = parse_envelope(json.dumps(VALID_ENVELOPE).encode("utf-8"))
        assert env.event_name == "booking.created"

    def test_accepts_dict_body(self) -> None:
        env = parse_envelope(VALID_ENVELOPE)
        assert env.event_name == "booking.created"

    def test_user_profile_updated_allows_null_tenant(self) -> None:
        payload = dict(VALID_ENVELOPE)
        payload["event_name"] = "user.profile.updated"
        payload["tenant_id"] = None
        env = parse_envelope(payload)
        assert env.tenant_id is None

    def test_parses_z_suffix_timestamp(self) -> None:
        env = parse_envelope(VALID_ENVELOPE)
        assert env.occurred_at.tzinfo is not None

    def test_parses_explicit_offset_timestamp(self) -> None:
        payload = dict(VALID_ENVELOPE)
        payload["occurred_at"] = "2026-05-21T14:32:11.482+00:00"
        env = parse_envelope(payload)
        assert env.occurred_at.tzinfo is not None


class TestErrorReasons:
    def test_invalid_json(self) -> None:
        with pytest.raises(IngestEnvelopeError) as excinfo:
            parse_envelope(b"not-json")
        assert excinfo.value.reason == "invalid_json"

    def test_not_object_at_top_level(self) -> None:
        with pytest.raises(IngestEnvelopeError) as excinfo:
            parse_envelope(b"[]")
        assert excinfo.value.reason == "not_object"

    def test_missing_required_field(self) -> None:
        payload = dict(VALID_ENVELOPE)
        del payload["event_name"]
        with pytest.raises(IngestEnvelopeError) as excinfo:
            parse_envelope(payload)
        assert excinfo.value.reason == "missing_field"
        assert "event_name" in excinfo.value.detail

    def test_invalid_event_name_unknown(self) -> None:
        payload = dict(VALID_ENVELOPE)
        payload["event_name"] = "booking.invented"
        with pytest.raises(IngestEnvelopeError) as excinfo:
            parse_envelope(payload)
        assert excinfo.value.reason == "invalid_event_name"

    def test_invalid_event_version_string(self) -> None:
        payload = dict(VALID_ENVELOPE)
        payload["event_version"] = "1"
        with pytest.raises(IngestEnvelopeError) as excinfo:
            parse_envelope(payload)
        assert excinfo.value.reason == "invalid_event_version"

    def test_invalid_event_version_bool_rejected(self) -> None:
        """``True`` is a subclass of int — explicit guard required."""
        payload = dict(VALID_ENVELOPE)
        payload["event_version"] = True
        with pytest.raises(IngestEnvelopeError) as excinfo:
            parse_envelope(payload)
        assert excinfo.value.reason == "invalid_event_version"

    def test_invalid_event_version_zero(self) -> None:
        payload = dict(VALID_ENVELOPE)
        payload["event_version"] = 0
        with pytest.raises(IngestEnvelopeError) as excinfo:
            parse_envelope(payload)
        assert excinfo.value.reason == "invalid_event_version"

    def test_invalid_actor(self) -> None:
        payload = dict(VALID_ENVELOPE)
        payload["actor"] = "robot"
        with pytest.raises(IngestEnvelopeError) as excinfo:
            parse_envelope(payload)
        assert excinfo.value.reason == "invalid_actor"

    def test_invalid_occurred_at_not_iso(self) -> None:
        payload = dict(VALID_ENVELOPE)
        payload["occurred_at"] = "yesterday"
        with pytest.raises(IngestEnvelopeError) as excinfo:
            parse_envelope(payload)
        assert excinfo.value.reason == "invalid_occurred_at"

    def test_invalid_occurred_at_no_timezone(self) -> None:
        payload = dict(VALID_ENVELOPE)
        payload["occurred_at"] = "2026-05-21T14:32:11.482"
        with pytest.raises(IngestEnvelopeError) as excinfo:
            parse_envelope(payload)
        assert excinfo.value.reason == "invalid_occurred_at"

    def test_invalid_tenant_id_null_for_booking(self) -> None:
        payload = dict(VALID_ENVELOPE)
        payload["tenant_id"] = None  # null only allowed for user.profile.updated
        with pytest.raises(IngestEnvelopeError) as excinfo:
            parse_envelope(payload)
        assert excinfo.value.reason == "invalid_tenant_id"

    def test_invalid_data_not_object(self) -> None:
        payload = dict(VALID_ENVELOPE)
        payload["data"] = "string-not-object"
        with pytest.raises(IngestEnvelopeError) as excinfo:
            parse_envelope(payload)
        assert excinfo.value.reason == "invalid_data"
