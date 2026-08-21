"""Step 3.5 — canonical write compatibility (stop new schema drift).

After the Step-3 backfills (0016/0018) every NEW explicit persistent write
must carry the canonical §3.1 fields at creation time. The stamping lives
in the single sanctioned write path (``memory_writer.write_entry``), so
all explicit callers are covered. inferred/signal rows are deliberately
NOT stamped — user_confirmed_inference may only come from the proposal
flow (Step 4+).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.consent.services import record_global_consent
from apps.identity.models import MemoryEntry, UserPersonalContext
from apps.identity.services import resolve_or_create_global_bot_user
from apps.identity.services.memory_writer import write_entry
from apps.orchestrator.memory.personal_context import record_explicit_green_facts

pytestmark = pytest.mark.django_db


def _upc() -> UserPersonalContext:
    return UserPersonalContext.objects.create(user_id=uuid.uuid4())


def _write(upc, *, source=MemoryEntry.SOURCE_EXPLICIT, ttl_days=None):
    return write_entry(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=source,
        kind="lifestyle",
        content={"key": "diet", "value": "vegan"},
        request_id=uuid.uuid4(),
        purpose="test:step35",
        # CHECK 1: last_inferred_at required for inferred/signal, NULL for explicit.
        last_inferred_at=None if source == MemoryEntry.SOURCE_EXPLICIT else timezone.now(),
        ttl_days=ttl_days,
    )


class TestExplicitWriteStampsCanonical:
    def test_status_active_provenance_user_stated(self):
        """1./2. status=active, provenance=user_stated on an explicit write."""
        entry = _write(_upc())
        entry.refresh_from_db()
        assert entry.status == MemoryEntry.STATUS_ACTIVE
        assert entry.provenance == MemoryEntry.PROVENANCE_USER_STATED

    def test_effective_from_and_updated_at_single_timestamp(self):
        """3./4. effective_from and updated_at set, from ONE write timestamp."""
        entry = _write(_upc())
        entry.refresh_from_db()
        assert entry.effective_from is not None
        assert entry.updated_at is not None
        assert entry.effective_from == entry.updated_at

    def test_ttl_days_gives_exact_expires_at(self):
        """5. expires_at = write timestamp + ttl_days (same base time)."""
        entry = _write(_upc(), ttl_days=90)
        entry.refresh_from_db()
        assert entry.expires_at == entry.effective_from + timedelta(days=90)
        assert entry.ttl_days == 90  # legacy relative field untouched

    def test_ttl_null_gives_null_expires_at(self):
        """6. No ttl_days → nothing invented for expires_at."""
        entry = _write(_upc(), ttl_days=None)
        entry.refresh_from_db()
        assert entry.expires_at is None

    def test_legacy_source_stays_explicit(self):
        """7. The legacy `source` column keeps its value."""
        entry = _write(_upc())
        entry.refresh_from_db()
        assert entry.source == MemoryEntry.SOURCE_EXPLICIT

    def test_nothing_fabricated(self):
        """8./9. consent_scope stays NULL, evidence_refs stays []."""
        entry = _write(_upc())
        entry.refresh_from_db()
        assert entry.consent_scope is None
        assert entry.source_event_id is None
        assert entry.derivation_method is None
        assert entry.evidence_refs == []
        assert entry.purpose_tags == []

    def test_no_confidence_field(self):
        """10. confidence remains forbidden in canonical MemoryEntry."""
        assert "confidence" not in {f.name for f in MemoryEntry._meta.fields}


class TestInferredSignalNeverPromoted:
    @pytest.mark.parametrize("source", [MemoryEntry.SOURCE_INFERRED, MemoryEntry.SOURCE_SIGNAL])
    def test_no_canonical_stamping(self, source):
        """11. inferred/signal writes never become user_confirmed_inference
        automatically — canonical fields stay NULL (proposal flow only)."""
        entry = _write(_upc(), source=source)
        entry.refresh_from_db()
        assert entry.provenance is None
        assert entry.status is None
        assert entry.source == source  # legacy metadata unchanged


@pytest.mark.django_db(transaction=True)
class TestNoNewSchemaDrift:
    """Regression gate: the REAL production explicit writer must never
    create a row with status=NULL or provenance=NULL."""

    def test_prod_explicit_write_is_canonical(self):
        bu = resolve_or_create_global_bot_user(
            channel="max", channel_user_id="s35-drift", ayla_user_id=uuid.uuid4()
        )
        record_global_consent(bu, source="welcome")

        assert record_explicit_green_facts(bu, "я веган") == 1

        entry = MemoryEntry.objects.get(user_id=bu.ayla_user_id)
        assert entry.status == MemoryEntry.STATUS_ACTIVE
        assert entry.provenance == MemoryEntry.PROVENANCE_USER_STATED
        assert entry.effective_from is not None
        assert entry.updated_at is not None
