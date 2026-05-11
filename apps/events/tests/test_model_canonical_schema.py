"""Event canonical-envelope schema tests (DRF-459 / Sprint 3 / B1).

Schema-only — emit() upgrade lives in B3 (DRF-461). The tests below
prove the new fields are storable + queryable + indexed. They do NOT
exercise emit() yet — that's intentional.
"""

from __future__ import annotations

import uuid

import pytest

from apps.events.models import Event
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


class TestCanonicalSchema:
    def test_new_fields_default_to_empty(self):
        tenant = Tenant.objects.create(slug="ev-a", name="A")
        event = Event.objects.create(
            tenant=tenant,
            event_type="legacy.something",  # legacy field still required by callers
        )
        assert event.event_name == ""
        assert event.distinct_id == ""
        assert event.dialog_id is None
        assert event.properties == {}

    def test_canonical_fields_persist(self):
        tenant = Tenant.objects.create(slug="ev-b", name="B")
        dialog = uuid.uuid4()
        event = Event.objects.create(
            tenant=tenant,
            event_type="consent_granted",
            event_name="consent_granted",
            distinct_id="bot-user-uuid-stringified",
            dialog_id=dialog,
            properties={"consent_type": "marketing"},
        )
        event.refresh_from_db()
        assert event.event_name == "consent_granted"
        assert event.distinct_id == "bot-user-uuid-stringified"
        assert event.dialog_id == dialog
        assert event.properties == {"consent_type": "marketing"}

    def test_str_prefers_event_name(self):
        tenant = Tenant.objects.create(slug="ev-c", name="C")
        event = Event(
            tenant=tenant,
            event_type="legacy.foo",
            event_name="canonical.foo",
            trace_id="trace-xyz",
        )
        assert "canonical.foo" in str(event)
        assert "trace-xyz" in str(event)

    def test_str_falls_back_to_event_type(self):
        tenant = Tenant.objects.create(slug="ev-d", name="D")
        event = Event(tenant=tenant, event_type="only.legacy")
        assert "only.legacy" in str(event)

    def test_canonical_index_declared(self):
        """The (tenant, event_name, -created_at) index must exist for B3 reads."""

        index_fields = {tuple(idx.fields) for idx in Event._meta.indexes}
        assert ("tenant", "event_name", "-created_at") in index_fields

    def test_query_by_distinct_id(self):
        """db_index on distinct_id means we can pull a user's full stream."""

        tenant = Tenant.objects.create(slug="ev-e", name="E")
        Event.objects.create(tenant=tenant, event_type="x", distinct_id="user-1", event_name="x")
        Event.objects.create(tenant=tenant, event_type="y", distinct_id="user-1", event_name="y")
        Event.objects.create(tenant=tenant, event_type="z", distinct_id="user-2", event_name="z")
        assert Event.objects.filter(distinct_id="user-1").count() == 2
