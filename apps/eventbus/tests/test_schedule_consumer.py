"""Tests for the schedule event consumer (#445 master.schedule.updated).

Covers per event-contract.md §3.11:

* Cache version bumps on every event (additive).
* Tenant-scoped lookup — cross-tenant pre-claim impossible.
* Graceful no-op when mirror row absent (sync race).
* Malformed payload defence (bad master_id, non-string change_type,
  non-list affected_dates).
* Tenant-verify mandate (A3).
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID

import pytest

from apps.catalog.models import CatalogMaster
from apps.eventbus.consumers.schedule import handle_master_schedule_updated
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


TENANT_ID = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"
AYLA_MASTER_ID = "8d4e5f6a-7b8c-4d9e-0f1a-2b3c4d5e6f7a"


# ─── helpers ───────────────────────────────────────────────────────────────


def _envelope(
    *,
    data: dict[str, Any],
    event_id: str = "01J9SCHED0000000000000000Z",  # pragma: allowlist secret
    tenant_id: str | None = TENANT_ID,
) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=event_id,
        event_name="master.schedule.updated",
        event_version=1,
        occurred_at=dt.datetime(2026, 5, 22, 13, 24, 50, tzinfo=dt.timezone.utc),
        tenant_id=tenant_id,
        user_id=AYLA_MASTER_ID,
        # Contract §3.11 example uses actor="master", but the
        # envelope enum in code is system|user|admin. Drift filed
        # as FOLLOW_UP. master IS a user in Ayla → "user" is the
        # correct enum value here.
        actor="user",
        correlation_id="e5f6a7b8-c9d0-1234-ef01-456789012345",
        causation_id=None,
        data=data,
    )


def _schedule_data(
    *,
    master_id: str = AYLA_MASTER_ID,
    change_type: str = "vacation_set",
    affected_all_dates: bool = False,
    affected_dates: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "master_id": master_id,
        "change_type": change_type,
        "affected_all_dates": affected_all_dates,
        "affected_dates": affected_dates
        if affected_dates is not None
        else ["2026-06-15", "2026-06-16"],
    }


@pytest.fixture(autouse=True)
def _enable_tenant_verify_fail_open(settings) -> None:
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(
        id=TENANT_ID,
        slug="t-schedule",
        name="Schedule test tenant",
    )


@pytest.fixture
def master_mirror(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=42,
        ayla_user_id=UUID(AYLA_MASTER_ID),
        external_updated_at=dt.datetime(2026, 5, 20, 10, 0, tzinfo=dt.timezone.utc),
        name="Анна",
        cache_version=0,
    )


# ─── Happy path ────────────────────────────────────────────────────────────


class TestScheduleHappyPath:
    def test_bumps_cache_version_on_vacation_set(
        self, tenant: Tenant, master_mirror: CatalogMaster
    ) -> None:
        env = _envelope(data=_schedule_data(change_type="vacation_set"))
        handle_master_schedule_updated(env)

        master_mirror.refresh_from_db()
        assert master_mirror.cache_version == 1

    def test_bumps_cache_version_on_affected_all_dates(
        self, tenant: Tenant, master_mirror: CatalogMaster
    ) -> None:
        env = _envelope(
            data=_schedule_data(
                affected_all_dates=True,
                affected_dates=[],
            )
        )
        handle_master_schedule_updated(env)

        master_mirror.refresh_from_db()
        assert master_mirror.cache_version == 1

    def test_canonical_fields_untouched(self, tenant: Tenant, master_mirror: CatalogMaster) -> None:
        env = _envelope(data=_schedule_data())
        handle_master_schedule_updated(env)

        master_mirror.refresh_from_db()
        # ADR-0009 Rule #5 — canonical fields not touched by handler.
        assert master_mirror.name == "Анна"
        assert master_mirror.is_active is True


# ─── No mirror row ─────────────────────────────────────────────────────────


class TestNoMirrorRow:
    def test_no_mirror_row_no_op(self, tenant: Tenant) -> None:
        """No mirror row for this Ayla master yet (sync race).
        Handler logs + no-op. No stub created."""
        env = _envelope(data=_schedule_data())
        handle_master_schedule_updated(env)

        assert CatalogMaster.all_tenants.filter(ayla_user_id=UUID(AYLA_MASTER_ID)).count() == 0


# ─── Cross-tenant isolation ────────────────────────────────────────────────


class TestCrossTenantIsolation:
    def test_event_for_other_tenant_does_not_touch_this_tenants_mirror(
        self, tenant: Tenant, master_mirror: CatalogMaster
    ) -> None:
        """Two mirrors with the same ayla_user_id under different
        tenants — event tagged with other tenant MUST bump only the
        other tenant's mirror."""
        other_tenant = Tenant.objects.create(
            id="11111111-2222-3333-4444-555555555555",
            slug="t-other",
            name="Other tenant",
        )
        other_mirror = CatalogMaster.all_tenants.create(
            tenant=other_tenant,
            external_id=42,
            ayla_user_id=UUID(AYLA_MASTER_ID),
            external_updated_at=dt.datetime(2026, 5, 20, 10, 0, tzinfo=dt.timezone.utc),
            name="Other Анна",
            cache_version=0,
        )

        env = _envelope(data=_schedule_data(), tenant_id=str(other_tenant.id))
        handle_master_schedule_updated(env)

        master_mirror.refresh_from_db()
        other_mirror.refresh_from_db()
        assert master_mirror.cache_version == 0  # this tenant untouched
        assert other_mirror.cache_version == 1  # other tenant bumped


# ─── Replay 3× ─────────────────────────────────────────────────────────────


class TestReplay3x:
    def test_direct_3x_calls_increment_3_times(
        self, tenant: Tenant, master_mirror: CatalogMaster
    ) -> None:
        """Direct handler invocation 3× bumps 3× (additive cache_version
        is OK — still a valid invalidation signal). Dispatcher-level
        idempotency via IngestDedupe is tested separately in
        tests/contracts/test_event_idempotency.py."""
        env = _envelope(data=_schedule_data())
        for _ in range(3):
            handle_master_schedule_updated(env)

        master_mirror.refresh_from_db()
        assert master_mirror.cache_version == 3


# ─── Malformed payload defence ─────────────────────────────────────────────


class TestMalformedPayload:
    def test_missing_master_id_no_op(self, tenant: Tenant) -> None:
        env = _envelope(data={"change_type": "vacation_set"})
        handle_master_schedule_updated(env)

    def test_non_uuid_master_id_no_op(self, tenant: Tenant) -> None:
        env = _envelope(data=_schedule_data(master_id="not-a-uuid"))
        handle_master_schedule_updated(env)

    def test_non_string_change_type_still_bumps(
        self, tenant: Tenant, master_mirror: CatalogMaster
    ) -> None:
        """change_type is only used for observability log. Defensive
        handling: non-string treated as empty; bump still fires."""
        env = _envelope(
            data={
                "master_id": AYLA_MASTER_ID,
                "change_type": 42,
                "affected_all_dates": False,
                "affected_dates": [],
            }
        )
        handle_master_schedule_updated(env)

        master_mirror.refresh_from_db()
        assert master_mirror.cache_version == 1

    def test_non_list_affected_dates_still_bumps(
        self, tenant: Tenant, master_mirror: CatalogMaster
    ) -> None:
        env = _envelope(
            data={
                "master_id": AYLA_MASTER_ID,
                "change_type": "exception_added",
                "affected_all_dates": False,
                "affected_dates": "not-a-list",
            }
        )
        handle_master_schedule_updated(env)

        master_mirror.refresh_from_db()
        assert master_mirror.cache_version == 1


# ─── Tenant-verify mandate (A3) ────────────────────────────────────────────


class TestTenantVerifyMandate:
    def test_raises_when_tenant_id_null(self) -> None:
        from apps.eventbus.ingest_tenancy import TenantAuthorizationError

        env = _envelope(data=_schedule_data(), tenant_id=None)
        with pytest.raises(TenantAuthorizationError):
            handle_master_schedule_updated(env)
