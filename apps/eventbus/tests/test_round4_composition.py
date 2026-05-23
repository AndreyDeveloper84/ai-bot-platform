"""Round-4 adversarial-pass regression tests — composition bugs from round-3 fixes.

Per memory ``feedback_h3_waiver_pattern`` N=16 insight: «late-rounds
find COMPOSITION bugs, early-rounds find DESIGN bugs». Each test
exercises a cross-layer interaction that round-3 fixes accidentally
created.

- R3-4 — NEW-1 X-Real-IP gate conflated depth>0 with edge_ack.
- R3-2 — NEW-5 sampler defeated own forensic purpose.
- R3-1 — ALLOWED_ENUM_VALUES contained PII field names.
- R3-3 — locmem GC unbounded between sweep windows.
"""

from __future__ import annotations

import datetime as dt

import pytest


# ─── R3-4 — X-Real-IP MUST ONLY be honored under edge_ack ───────────────


class TestR3_4XRealIPGateEdgeAckOnly:
    def test_x_real_ip_ignored_at_depth_2_without_edge_ack(self, settings) -> None:
        """Bypass: at depth=2 (XFF hop count claim for k8s+nginx),
        edge_ack=False (default), an attacker sends:

            X-Real-IP: spoof.attacker.1.1
            X-Forwarded-For: 10.0.0.1, 1.1.1.1

        Round-3 NEW-1 gate `depth>0 OR edge_ack` honored X-Real-IP
        unconditionally → spoof wins.

        Defense: X-Real-IP gate SOLELY on edge_ack. With edge_ack=False,
        X-Real-IP is IGNORED and the XFF chain (with depth=2 trust) is
        the real source.
        """
        from apps.eventbus.ingest_ip import get_remote_ip

        settings.EVENT_INGEST_TRUSTED_PROXY_DEPTH = 2
        settings.EVENT_INGEST_EDGE_CONFIGURED_ACK = False

        class _Req:
            META = {
                "HTTP_X_REAL_IP": "spoof.attacker.1.1",
                "HTTP_X_FORWARDED_FOR": "10.0.0.1, 1.1.1.1, 2.2.2.2",
                "REMOTE_ADDR": "127.0.0.1",
            }

        ip = get_remote_ip(_Req())
        # XFF chain "10.0.0.1, 1.1.1.1, 2.2.2.2" with depth=2 means
        # drop the trailing 2 hops; client is "10.0.0.1".
        assert ip == "10.0.0.1", (
            f"R3-4 bypass: depth=2 + edge_ack=False honored X-Real-IP; "
            f"resolved to {ip!r} instead of XFF-derived 10.0.0.1."
        )

    def test_x_real_ip_honored_under_edge_ack(self, settings) -> None:
        """Positive complement: edge_ack=True is the documented
        «trusted edge sets X-Real-IP» mode. With that flag, X-Real-IP
        IS the canonical source."""
        from apps.eventbus.ingest_ip import get_remote_ip

        settings.EVENT_INGEST_TRUSTED_PROXY_DEPTH = 0
        settings.EVENT_INGEST_EDGE_CONFIGURED_ACK = True

        class _Req:
            META = {
                "HTTP_X_REAL_IP": "10.0.0.42",
                "REMOTE_ADDR": "127.0.0.1",
            }

        assert get_remote_ip(_Req()) == "10.0.0.42"


# ─── R3-2 — Sampler preserves forensic count ─────────────────────────────


class TestR3_2SamplerForensicCount:
    @pytest.mark.django_db
    def test_burst_of_fail_open_writes_audit_with_count(self, settings) -> None:
        """Bypass: round-3 NEW-5 sampler emits 1/min/user_id — attacker
        with fixed user_id sends 1000 spoofed-tenant events → 999
        leave NO trace. Forensic purpose defeated.

        Defense: every call increments a cache counter; the sampled
        audit row INCLUDES the count for its (user_id, tenant_id)
        window. 1000 events → 1 row with count_in_window=1000.
        """
        from django.core.cache import cache

        from apps.audit.models import AuditLog
        from apps.eventbus.ingest_envelope import IngestEnvelope
        from apps.eventbus.ingest_tenancy import (
            assert_envelope_tenant_authorized,
        )

        cache.clear()
        settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True

        env = IngestEnvelope(
            event_id="01J9HXKM8Z2T4V6R8Q1P3D5F7E",  # pragma: allowlist secret
            event_name="booking.created",
            event_version=1,
            occurred_at=dt.datetime(2026, 5, 21, 14, 32, 11, tzinfo=dt.timezone.utc),
            tenant_id="9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c",
            user_id="f1a2b3c4-d5e6-4789-9abc-def012345678",
            actor="user",
            correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            causation_id=None,
            data={},
        )

        # Burst 50 fall-through calls for the SAME (user_id, tenant_id).
        for _ in range(50):
            assert_envelope_tenant_authorized(env)

        rows = AuditLog.all_tenants.filter(
            action="eventbus.ingest.tenant_verify_fail_open",
        )
        # At least 1 row written (sampler kept the row-write bounded).
        assert rows.count() >= 1
        # The row payload MUST carry a count_in_window field reflecting
        # at least the 50 invocations — without it the forensic count
        # is lost.
        payloads = [r.payload for r in rows]
        total_count = sum(int(p.get("count_in_window", 0) or 0) for p in payloads)
        assert total_count >= 50, (
            f"R3-2 bypass: 50 fall-through calls produced audit rows "
            f"with total count_in_window={total_count}; expected ≥50."
        )

    @pytest.mark.django_db
    def test_distinct_tenant_id_each_get_audit_row(self, settings) -> None:
        """Same user_id, different tenant_id → each composite key gets
        an audit row. Surfaces tenant-spoofing fan-out per attacker.
        """
        from django.core.cache import cache

        from apps.audit.models import AuditLog
        from apps.eventbus.ingest_envelope import IngestEnvelope
        from apps.eventbus.ingest_tenancy import (
            assert_envelope_tenant_authorized,
        )

        cache.clear()
        settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True

        def _env(tenant_id: str) -> IngestEnvelope:
            return IngestEnvelope(
                event_id="01J9HXKM8Z2T4V6R8Q1P3D5F7E",  # pragma: allowlist secret
                event_name="booking.created",
                event_version=1,
                occurred_at=dt.datetime(2026, 5, 21, 14, 32, 11, tzinfo=dt.timezone.utc),
                tenant_id=tenant_id,
                user_id="f1a2b3c4-d5e6-4789-9abc-def012345678",
                actor="user",
                correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                causation_id=None,
                data={},
            )

        for tid in (
            "tenant-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "tenant-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "tenant-cccccccc-cccc-cccc-cccc-cccccccccccc",
        ):
            assert_envelope_tenant_authorized(_env(tid))

        rows = AuditLog.all_tenants.filter(
            action="eventbus.ingest.tenant_verify_fail_open",
        )
        # Three distinct (user_id, tenant_id) pairs → three audit rows.
        assert rows.count() == 3


# ─── R3-1 — PII-field-name strings MUST NOT pass enum allowlist ─────────


class TestR3_1NoPIIFieldNamesInEnumAllowlist:
    def test_field_name_phone_is_redacted(self) -> None:
        """Bypass: ALLOWED_ENUM_VALUES contained "phone" (literal
        contract value for user.profile.updated.changed_fields).
        Attacker sets `customer_name="phone"` → passes redaction.

        Defense: drop "phone"/"email"/"name"/"birthday"/etc. from
        the global allowlist. changed_fields lists redact to
        [REDACTED, ...] — operator sees the change occurred, just
        not which fields.
        """
        from apps.eventbus.ingest_redaction import (
            REDACTED_SENTINEL,
            redact_data_for_dlq,
        )

        data = {"customer_name": "phone"}
        out = redact_data_for_dlq(data)
        assert out["customer_name"] == REDACTED_SENTINEL, (
            "R3-1 bypass: 'phone' passed enum allowlist as free-text value bypass."
        )

    def test_other_pii_field_names_redacted(self) -> None:
        """Same bypass for the other documented PII field names."""
        from apps.eventbus.ingest_redaction import (
            REDACTED_SENTINEL,
            redact_data_for_dlq,
        )

        for field_name in ("email", "name", "birthday", "language", "display_name"):
            out = redact_data_for_dlq({"leak": field_name})
            assert out["leak"] == REDACTED_SENTINEL, (
                f"R3-1 bypass: PII field-name {field_name!r} passed allowlist as value."
            )

    def test_status_enums_still_pass(self) -> None:
        """Round-3 fix MUST NOT lose the legitimate non-PII enums."""
        from apps.eventbus.ingest_redaction import redact_data_for_dlq

        data = {
            "status": "confirmed",
            "source": "mobile_app",
            "reason": "card_declined",
            "provider": "yookassa",
        }
        assert redact_data_for_dlq(data) == data


# ─── R3-3 — locmem oldest-eviction caps unbounded growth ────────────────


class TestR3_3LocmemBoundedGrowth:
    def test_locmem_capped_under_distributed_attack(self) -> None:
        """Bypass: round-3 NEW-2 GC fires once at 10000 entries; an
        attacker rotating IPs at line speed adds 10000+ entries
        between GC sweeps → unbounded process memory until cutoff
        sweep.

        Defense: OrderedDict with bounded capacity + popitem(last=False)
        evicts the OLDEST entry on every insert past the cap.
        """
        from apps.eventbus import ingest_rate_audit_sampler as sampler

        # Reset locmem.
        with sampler._locmem_lock:
            sampler._locmem_seen.clear()

        # Insert MORE than the cap.
        from unittest.mock import patch

        with patch.object(sampler.cache, "add", side_effect=RuntimeError("redis down")):
            for i in range(sampler._LOCMEM_MAX_ENTRIES + 200):
                sampler.should_audit_rate_limited(f"10.0.{i // 256}.{i % 256}")

        with sampler._locmem_lock:
            size = len(sampler._locmem_seen)
        assert size <= sampler._LOCMEM_MAX_ENTRIES, (
            f"R3-3 bypass: locmem grew to {size} entries past the "
            f"cap of {sampler._LOCMEM_MAX_ENTRIES}."
        )
