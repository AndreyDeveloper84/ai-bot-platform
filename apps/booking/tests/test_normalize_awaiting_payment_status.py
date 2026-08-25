"""Migration data-step tests for 0019 (DRF-1145).

The pilot mirror holds rows with the raw wire status ``awaiting_payment``,
which is NOT in ``RemoteBookingProxy.Status`` (the enum carries
``pending_payment``). Django never validated ``choices`` at the database
level, so the value landed silently and every read that filters by the
enum misses the row — DRF-1085 and ``miniapp_api`` already work around
this with defensive raw-string lists.

The writers were audited on current ``dev``: both write enum members
(the eventbus consumer maps ``awaiting_payment`` → ``PENDING_PAYMENT``
at ingest, the tools upsert writes ``CONFIRMED``). What remains is the
stored lie — healed here by the same mapping the consumer applies.
"""

import uuid
from importlib import import_module

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.booking.models import RemoteBookingProxy
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

MIGRATION_NAME = "0019_remotebookingproxy_normalize_awaiting_payment"

TENANT_ID = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(
        id=TENANT_ID,
        slug="normalize-status",
        name="Формула тела",
        timezone="Europe/Moscow",
    )


def _proxy(tenant: Tenant, appointment_id: str, status: str) -> RemoteBookingProxy:
    import datetime as dt

    return RemoteBookingProxy.all_tenants.create(
        appointment_id=uuid.UUID(appointment_id),
        tenant=tenant,
        start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
        end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
        # Written as a raw string on purpose: the defect under test is
        # precisely that Django stores any string here without validating
        # ``choices``, so the ORM happily reproduces the pilot's row.
        status=status,
        source=RemoteBookingProxy.Source.AUTOMATION,
    )


class TestNormalizeAwaitingPayment:
    def test_wire_status_rows_are_healed_by_the_consumers_mapping(self, tenant: Tenant) -> None:
        """``awaiting_payment`` rows become ``pending_payment`` — the exact
        mapping the ingest consumer applies to new events (booking.py)."""

        sick = _proxy(tenant, "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8", "awaiting_payment")
        healthy = _proxy(
            tenant,
            "c9e4f5a6-2d3e-4f7a-9b0c-d4e5f6a7b8c9",
            RemoteBookingProxy.Status.CONFIRMED,
        )

        migration = import_module(f"apps.booking.migrations.{MIGRATION_NAME}")
        historical_apps = (
            MigrationExecutor(connection).loader.project_state(("booking", MIGRATION_NAME)).apps
        )
        migration.normalize_awaiting_payment_rows(historical_apps, connection.schema_editor)

        sick.refresh_from_db()
        healthy.refresh_from_db()
        assert sick.status == RemoteBookingProxy.Status.PENDING_PAYMENT
        assert healthy.status == RemoteBookingProxy.Status.CONFIRMED

    def test_every_stored_status_is_an_enum_member_after_the_step(self, tenant: Tenant) -> None:
        """The point of the ticket: nothing outside ``Status`` survives."""

        _proxy(tenant, "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8", "awaiting_payment")

        migration = import_module(f"apps.booking.migrations.{MIGRATION_NAME}")
        historical_apps = (
            MigrationExecutor(connection).loader.project_state(("booking", MIGRATION_NAME)).apps
        )
        migration.normalize_awaiting_payment_rows(historical_apps, connection.schema_editor)

        stored = set(RemoteBookingProxy.all_tenants.values_list("status", flat=True))
        assert stored <= set(RemoteBookingProxy.Status.values)
