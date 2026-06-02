"""E0#1 Variant A — `_load_tenant_master_roster` tests (founder verdict
2026-06-02).

Covers the DB-touching half of pre-injection: loading active
``CatalogMaster`` rows for one tenant + capping + tenant isolation +
graceful failure. The pure rendering half is tested in
`test_prompts.py::TestKnownMastersBlock`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from apps.catalog.models import CatalogMaster
from apps.skills.booking.skill import (
    _KNOWN_MASTERS_ROSTER_CAP,
    _load_tenant_master_roster,
)
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="km-roster", name="KM Roster")


@pytest.fixture
def tenant_other() -> Tenant:
    return Tenant.objects.create(slug="km-roster-other", name="KM Other")


_next_external_id = [1]


def _master(
    tenant: Tenant,
    *,
    name: str,
    specialization: str = "",
    is_active: bool = True,
) -> CatalogMaster:
    # external_id is part of CatalogMaster's unique constraint
    # (tenant, external_id) — auto-increment for test convenience.
    _next_external_id[0] += 1
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=_next_external_id[0],
        external_updated_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
        name=name,
        specialization=specialization,
        is_active=is_active,
    )


class TestLoadRoster:
    def test_empty_tenant_returns_empty_list(self, tenant: Tenant) -> None:
        # New tenant с no synced masters yet — block must be skipped
        # gracefully (show_masters tool fallback remains).
        assert _load_tenant_master_roster(tenant) == []

    def test_returns_active_masters_with_name_and_specialization(self, tenant: Tenant) -> None:
        _master(tenant, name="Ольга", specialization="массаж")
        _master(tenant, name="Анна", specialization="маникюр")

        roster = _load_tenant_master_roster(tenant)
        names = {row["name"] for row in roster}
        assert names == {"Ольга", "Анна"}
        specs = {row["specialization"] for row in roster}
        assert specs == {"массаж", "маникюр"}

    def test_inactive_masters_excluded(self, tenant: Tenant) -> None:
        _master(tenant, name="Активный", specialization="x", is_active=True)
        _master(tenant, name="Архивный", specialization="y", is_active=False)

        roster = _load_tenant_master_roster(tenant)
        names = {row["name"] for row in roster}
        assert names == {"Активный"}
        assert "Архивный" not in names

    def test_sorted_alphabetically_for_stability(self, tenant: Tenant) -> None:
        _master(tenant, name="Светлана")
        _master(tenant, name="Анна")
        _master(tenant, name="Ольга")

        roster = _load_tenant_master_roster(tenant)
        names = [row["name"] for row in roster]
        # Order matters — the prompt token shape must be deterministic
        # so prompt-cache hits stay high across turns.
        assert names == sorted(names)

    def test_caps_at_roster_limit(self, tenant: Tenant) -> None:
        # Create cap+5 active masters; loader returns exactly cap, top-N
        # alphabetical.
        for i in range(_KNOWN_MASTERS_ROSTER_CAP + 5):
            # Pad zero so the lex order matches the index order (10 < 9
            # would otherwise sort wrong as plain strings).
            _master(tenant, name=f"Master-{i:03d}", specialization="x")

        roster = _load_tenant_master_roster(tenant)
        assert len(roster) == _KNOWN_MASTERS_ROSTER_CAP
        # First should be Master-000, last should be the cap-1.
        assert roster[0]["name"] == "Master-000"
        assert roster[-1]["name"] == f"Master-{_KNOWN_MASTERS_ROSTER_CAP - 1:03d}"

    def test_cross_tenant_isolation(self, tenant: Tenant, tenant_other: Tenant) -> None:
        _master(tenant, name="Тенант-А", specialization="x")
        _master(tenant_other, name="Тенант-Б", specialization="y")

        roster_a = _load_tenant_master_roster(tenant)
        names_a = {row["name"] for row in roster_a}
        assert names_a == {"Тенант-А"}
        # Critical adversarial concern — tenant boundary must hold.
        assert "Тенант-Б" not in names_a

    def test_db_failure_returns_empty_list_does_not_raise(self, tenant: Tenant) -> None:
        # Loader MUST NOT propagate DB exceptions — observability /
        # anti-hallucination block is defence-in-depth, not load-bearing.
        # A turn must complete even when the catalog mirror is briefly
        # unavailable.
        with patch(
            "apps.catalog.models.CatalogMaster.all_tenants",
        ) as mock_mgr:
            mock_mgr.filter.side_effect = RuntimeError("synthetic db outage")
            roster = _load_tenant_master_roster(tenant)
            assert roster == []

    def test_empty_string_specialization_preserved(self, tenant: Tenant) -> None:
        # Legacy mysite-synced rows may have specialization="" — loader
        # surfaces empty string (renderer drops the «— spec» suffix).
        _master(tenant, name="Без специализации", specialization="")
        roster = _load_tenant_master_roster(tenant)
        assert len(roster) == 1
        assert roster[0]["specialization"] == ""
