"""Migration 0005 (DRF-1963, M1): column → registry row, old type name → new.

The pilot has 0 rows on both sides (readback 15.09 16:28 UTC), so the migration
moves nothing there — which is exactly why its path is tested here: a transfer
that never ran on real data proves nothing by having run.

This file is on the dead-column guard's allowlist: it has to build a person
with the column set.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

migration = importlib.import_module("apps.consent.migrations.0005_food_diary_processing")
DIARY = ConsentRecord.ConsentType.FOOD_DIARY_PROCESSING.value
GRANTED_AT = datetime(2026, 8, 1, 9, 30, tzinfo=timezone.utc)


@pytest.fixture
def historical_apps():
    """The app registry Django hands RunPython — not the live one.

    Live ``ConsentRecord.objects`` is tenant-scoped and answers empty without a
    tenant; the historical model in a migration gets a plain manager. Calling
    the migration functions with the live registry would test a different
    manager than the one the migration actually runs with.
    """
    state = MigrationExecutor(connection).loader.project_state(
        ("consent", "0005_food_diary_processing")
    )
    return state.apps


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="m1-migration", name="M1")


def _person(tenant: Tenant, suffix: str, *, stamped: bool) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"m1-{suffix}",
        food_scanner_consent_at=GRANTED_AT if stamped else None,
    )


def _diary_rows(bot_user: BotUser):
    return ConsentRecord.all_tenants.filter(bot_user=bot_user, consent_type=DIARY)


def test_a_stamped_column_becomes_an_active_row_with_the_original_date(
    tenant, historical_apps
) -> None:
    stamped = _person(tenant, "stamped", stamped=True)
    assert _diary_rows(stamped).count() == 0  # presence of the precondition: nothing yet

    migration.column_to_registry(historical_apps, None)

    rows = list(_diary_rows(stamped))
    assert len(rows) == 1
    row = rows[0]
    assert row.granted is True
    assert row.withdrawn_at is None
    assert row.captured_at == GRANTED_AT  # the grant date, not the migration date
    assert row.document_version == migration.DOCUMENT_VERSION == "food-diary-v0"
    assert row.source == "migration:food_scanner_consent_at"
    assert row.tenant_id == stamped.tenant_id


def test_an_empty_column_moves_nothing(tenant, historical_apps) -> None:
    stamped = _person(tenant, "stamped", stamped=True)
    empty = _person(tenant, "empty", stamped=False)

    migration.column_to_registry(historical_apps, None)

    assert _diary_rows(stamped).count() == 1  # the pass did run
    assert _diary_rows(empty).count() == 0


def test_the_transfer_is_idempotent(tenant, historical_apps) -> None:
    stamped = _person(tenant, "stamped", stamped=True)
    migration.column_to_registry(historical_apps, None)
    migration.column_to_registry(historical_apps, None)
    assert _diary_rows(stamped).count() == 1


def test_the_old_type_name_is_renamed_and_back(tenant, historical_apps) -> None:
    person = _person(tenant, "renamed", stamped=False)
    old = ConsentRecord.all_tenants.create(
        tenant=tenant, bot_user=person, consent_type="nutrition_diary", granted=True, source="t"
    )

    migration.rename_type_forward(historical_apps, None)
    old.refresh_from_db()
    assert old.consent_type == DIARY

    migration.rename_type_backward(historical_apps, None)
    old.refresh_from_db()
    assert old.consent_type == "nutrition_diary"
