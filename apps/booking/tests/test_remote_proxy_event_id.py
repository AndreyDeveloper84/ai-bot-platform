"""RemoteBookingProxy.last_synced_event_id width regression (#1066).

Completes #1058 end-to-end. #1067 widened the eventbus dedupe/DLQ/tracker
columns so they hold Ayla's ``uuid4()`` (36 chars) instead of only a bare
ULID (26). This pins the booking consumer's idempotency-tail column: a
real 36-char event_id must round-trip through save/refetch without a
Postgres ``DataError`` — otherwise a booking.* event passes IngestDedupe
but 500s inside the consumer when it writes ``last_synced_event_id``.

NOTE: the ``DataError`` this guards against is Postgres-only — SQLite
ignores ``CharField`` max_length, so the *round-trip* tests pass even
against an un-widened varchar(26) under the local SQLite backend. CI
runs on Postgres (``POSTGRES_HOST`` set) where they are real guards. The
backend-agnostic regression guard is ``test_max_length_36_boundary``
(``full_clean`` + ``deconstruct()``), which catches a model-level 36→26
revert on any backend.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db.models import Field
from django.utils import timezone

from apps.booking.models import RemoteBookingProxy
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant_proxy(db) -> Tenant:
    return Tenant.objects.create(slug="salon-proxy", name="Salon Proxy")


def _make_proxy(tenant: Tenant, **overrides: object) -> RemoteBookingProxy:
    start = timezone.now() + timedelta(days=1)
    defaults: dict[str, object] = {
        "appointment_id": uuid.uuid4(),
        "tenant": tenant,
        "start_at": start,
        "end_at": start + timedelta(hours=1),
        "status": RemoteBookingProxy.Status.CONFIRMED,
    }
    defaults.update(overrides)
    return RemoteBookingProxy.all_tenants.create(**defaults)


class TestLastSyncedEventIdWidth:
    def test_uuid4_event_id_round_trips(self, tenant_proxy: Tenant) -> None:
        """A 36-char Ayla uuid4 survives save/refetch on varchar(36)."""
        uuid4 = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"  # 36 chars
        assert len(uuid4) == 36

        proxy = _make_proxy(tenant_proxy, last_synced_event_id=uuid4)
        proxy.refresh_from_db()
        assert proxy.last_synced_event_id == uuid4

    def test_ulid_event_id_still_round_trips(self, tenant_proxy: Tenant) -> None:
        """Backward-compat: a 26-char ULID still fits the widened column."""
        ulid = "01ARZ3NDEKTSV4RRFFQ69G5FAV"  # 26-char canonical ULID
        assert len(ulid) == 26

        proxy = _make_proxy(tenant_proxy, last_synced_event_id=ulid)
        proxy.refresh_from_db()
        assert proxy.last_synced_event_id == ulid

    def test_max_length_36_boundary(self, tenant_proxy: Tenant) -> None:
        """Boundary (#1066): 36 chars OK, 37 chars fails ``full_clean``."""
        proxy = _make_proxy(tenant_proxy)

        # 36 chars — exactly at the widened limit, must round-trip.
        proxy.last_synced_event_id = "a" * 36
        proxy.full_clean()
        proxy.save(update_fields=["last_synced_event_id"])
        proxy.refresh_from_db()
        assert proxy.last_synced_event_id == "a" * 36

        # 37 chars — over the limit, full_clean must raise.
        proxy.last_synced_event_id = "a" * 37
        with pytest.raises(ValidationError):
            proxy.full_clean()

        # Surface the contract on the Field itself.
        field = RemoteBookingProxy._meta.get_field("last_synced_event_id")
        assert isinstance(field, Field)
        field_attrs = field.deconstruct()[3]
        assert field_attrs.get("max_length") == 36
        assert field_attrs.get("blank") is True
        assert field_attrs.get("default") == ""
