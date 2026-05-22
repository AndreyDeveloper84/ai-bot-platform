"""Round-2 adversarial-pass regression tests — A3+A8 + AS10 health check.

Each test IS the bypass attempt per memory ``feedback_h3_waiver_pattern``
N=10 discipline. Cluster mapping to the round-1 verdict:

- AS8 — assert_envelope_tenant_authorized fail-CLOSED by default.
- AS4 — DLQ raw_body redaction strips free-text PII.
- AS10 — cleanup_beat_health detects stale beat.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.eventbus.cleanup_tasks import (
    cleanup_beat_health,
    cleanup_ingest_dedupe,
    cleanup_ingest_dlq,
)
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.ingest_redaction import (
    REDACTED_SENTINEL,
    redact_data_for_dlq,
)
from apps.eventbus.ingest_tenancy import (
    TenantAuthorizationError,
    assert_envelope_tenant_authorized,
)


# ─── AS8 — Fail-closed stub by default ──────────────────────────────────


def _envelope(
    *,
    event_name: str = "booking.created",
    tenant_id: str | None = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c",
) -> IngestEnvelope:
    return IngestEnvelope(
        event_id="01J9HXKM8Z2T4V6R8Q1P3D5F7E",
        event_name=event_name,
        event_version=1,
        occurred_at=dt.datetime(2026, 5, 21, 14, 32, 11, tzinfo=dt.timezone.utc),
        tenant_id=tenant_id,
        user_id="f1a2b3c4-d5e6-4789-9abc-def012345678",
        actor="user",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        causation_id=None,
        data={"appointment_id": "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"},
    )


class TestAS8FailClosedStub:
    def test_default_settings_fail_closed_with_no_canonical_model(self, settings) -> None:
        """The Round-1 fail-OPEN bypass MUST NOT survive.

        Default settings = EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN is
        absent or False. Without the canonical TenantUserRelationship
        model (Sprint 1 #246 not yet shipped), any tenant_id-set
        envelope MUST raise TenantAuthorizationError.

        Round-1 bypass: stub silently logged + returned. An attacker
        with HMAC + arbitrary tenant_id would write to victim_tenant.
        """
        # Ensure flag is unset (the round-1 default).
        if hasattr(settings, "EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN"):
            del settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN

        with pytest.raises(TenantAuthorizationError) as excinfo:
            assert_envelope_tenant_authorized(_envelope())

        assert (
            "fail" in str(excinfo.value).lower() or "not available" in str(excinfo.value).lower()
        ), (
            f"AS8 bypass: helper did not raise on tenant_id with no model + "
            f"no flag. Got: {excinfo.value!r}"
        )

    def test_explicit_fail_open_flag_allows_pre246_dev(self, settings) -> None:
        """Documented opt-in path: ops sets FAIL_OPEN=True for the
        pre-#246 transition. Helper logs + returns instead of raising.
        """
        settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True
        # Should NOT raise.
        assert_envelope_tenant_authorized(_envelope())

    def test_null_tenant_for_nullable_event_passes_regardless(self, settings) -> None:
        """user.profile.updated is §2-nullable — passes even with flag False."""
        settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = False
        env = _envelope(event_name="user.profile.updated", tenant_id=None)
        # Should NOT raise.
        assert_envelope_tenant_authorized(env)

    def test_null_tenant_for_non_nullable_event_raises(self, settings) -> None:
        settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True  # flag doesn't help here
        env = _envelope(event_name="booking.created", tenant_id=None)
        with pytest.raises(TenantAuthorizationError):
            assert_envelope_tenant_authorized(env)


# ─── AS4 — DLQ redaction strips free-text PII ───────────────────────────


class TestAS4DLQRedaction:
    def test_strips_free_text_name_field(self) -> None:
        """Bypass: a v2 event with a `customer_name` field would
        leak names into 90d DLQ storage. Defense: free-text strings
        not matching ID/timestamp/enum shape are redacted.
        """
        data = {"customer_name": "Анна Иванова"}
        out = redact_data_for_dlq(data)
        assert out["customer_name"] == REDACTED_SENTINEL

    def test_strips_email_phone_address_shapes(self) -> None:
        """Common PII shapes all collapse to the sentinel."""
        data = {
            "email": "user@example.com",
            "phone": "+7 999 123-45-67",
            "address": "ул. Ленина 1, кв 2",
        }
        out = redact_data_for_dlq(data)
        assert out["email"] == REDACTED_SENTINEL
        assert out["phone"] == REDACTED_SENTINEL
        assert out["address"] == REDACTED_SENTINEL

    def test_preserves_uuid_ids(self) -> None:
        data = {
            "appointment_id": "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8",
            "master_id": "7c2d8e1f-0a5c-9c3a-7e1b-4d52f8eb3a17",
        }
        out = redact_data_for_dlq(data)
        assert out == data

    def test_preserves_ulid(self) -> None:
        # pragma: allowlist secret — ULID-shaped test fixture, not a secret.
        data = {"event_ref": "01J9HXKM8Z2T4V6R8Q1P3D5F7E"}  # pragma: allowlist secret
        assert redact_data_for_dlq(data) == data

    def test_preserves_iso_timestamp(self) -> None:
        data = {
            "start_at": "2026-05-22T15:00:00+03:00",
            "captured_at": "2026-05-21T14:32:11.482Z",
        }
        assert redact_data_for_dlq(data) == data

    def test_preserves_enums_numerics_booleans(self) -> None:
        data = {
            "status": "confirmed",
            "source": "mobile_app",
            "price_total": "1800.00",
            "rating": 5,
            "is_anonymous": False,
        }
        assert redact_data_for_dlq(data) == data

    def test_recursive_nested_dict(self) -> None:
        data = {
            "outer": {
                "name": "Anna",  # PII
                "id": "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8",  # safe
            }
        }
        out = redact_data_for_dlq(data)
        assert out["outer"]["name"] == REDACTED_SENTINEL
        assert out["outer"]["id"] == data["outer"]["id"]

    def test_recursive_list_of_dicts(self) -> None:
        data = {"items": [{"name": "Anna", "qty": 2}, {"name": "Bob", "qty": 3}]}
        out = redact_data_for_dlq(data)
        for item in out["items"]:
            assert item["name"] == REDACTED_SENTINEL
            assert item["qty"] in (2, 3)

    def test_empty_or_non_dict_input(self) -> None:
        assert redact_data_for_dlq({}) == {}
        assert redact_data_for_dlq(None) == {}  # type: ignore[arg-type]


# ─── AS10 — Beat health check ───────────────────────────────────────────


pytestmark_for_health = pytest.mark.django_db


class TestAS10CleanupBeatHealth:
    """The beat health check must distinguish:

    * never-ran (cache miss) → unhealthy + reason=no_marker
    * ran recently (<25h ago) → healthy
    * stale (>25h ago) → unhealthy + reason=stale
    """

    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        cache.clear()
        yield
        cache.clear()

    @pytest.mark.django_db
    def test_unhealthy_when_no_marker_set(self) -> None:
        result = cleanup_beat_health()
        assert result["cleanup_ingest_dlq"]["healthy"] is False
        assert result["cleanup_ingest_dlq"]["reason"] == "no_marker"
        assert result["cleanup_ingest_dedupe"]["healthy"] is False

    @pytest.mark.django_db
    def test_healthy_after_task_runs(self) -> None:
        cleanup_ingest_dlq()
        cleanup_ingest_dedupe()

        result = cleanup_beat_health()
        assert result["cleanup_ingest_dlq"]["healthy"] is True
        assert result["cleanup_ingest_dedupe"]["healthy"] is True

    @pytest.mark.django_db
    def test_unhealthy_when_marker_stale_beyond_25h(self) -> None:
        """Bypass attempt: leave a marker from 26h ago and assert
        the check still reports unhealthy. Defense: 25h cutoff."""
        # Set the marker manually to a stale value.
        from apps.eventbus.cleanup_tasks import (
            _DLQ_BEAT_LAST_RUN_CACHE_KEY,
            _DEDUPE_BEAT_LAST_RUN_CACHE_KEY,
        )

        stale = (timezone.now() - dt.timedelta(hours=26)).isoformat()
        cache.set(_DLQ_BEAT_LAST_RUN_CACHE_KEY, stale, timeout=30 * 24 * 3600)
        cache.set(_DEDUPE_BEAT_LAST_RUN_CACHE_KEY, stale, timeout=30 * 24 * 3600)

        result = cleanup_beat_health()
        assert result["cleanup_ingest_dlq"]["healthy"] is False
        assert result["cleanup_ingest_dlq"]["reason"] == "stale"
        assert result["cleanup_ingest_dedupe"]["healthy"] is False
