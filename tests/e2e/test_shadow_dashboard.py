"""Shadow-mode dashboard e2e (DRF-723 / Sprint 8 / D3)."""

from __future__ import annotations

import datetime as _dt

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from apps.observability.models import ShadowDeltaSnapshot
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_user() -> object:
    User = get_user_model()
    return User.objects.create_user(
        username="d3-staff",
        password="d3-staff-pw-test",  # noqa: S106 — test fixture only
        is_staff=True,
    )


@pytest.fixture
def regular_user() -> object:
    User = get_user_model()
    return User.objects.create_user(
        username="d3-regular",
        password="d3-regular-pw-test",  # noqa: S106 — test fixture only
        is_staff=False,
    )


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="d3-tenant", name="D3", shadow_mode=True)


def _seed_seven_snapshots(tenant: Tenant) -> None:
    today = _dt.date.today()
    for i in range(7):
        ShadowDeltaSnapshot.objects.create(
            tenant=tenant,
            snapshot_date=today - _dt.timedelta(days=i),
            intent_agreement=0.97 - i * 0.01,
            action_type_agreement=0.95 - i * 0.005,
            sample_count=200 - i * 10,
            payload={
                "intent_agreement": 0.97 - i * 0.01,
                "latency_p50_delta_ms": 120 + i * 5,
                "latency_p95_delta_ms": 380 + i * 10,
                "error_delta_pct": 0.001 * i,
            },
        )


class TestStaffAccess:
    def test_staff_can_view_dashboard(self, staff_user, tenant: Tenant, client: Client) -> None:
        _seed_seven_snapshots(tenant)
        client.force_login(staff_user)
        response = client.get("/admin/observability/shadow/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        # All seven rows rendered.
        assert content.count("<tr>") == 8  # 1 header + 7 data rows
        # Agreement badge class threading.
        assert "agreement-green" in content
        # Inline CSS marker (D1 ships dependency-free; no external CDN).
        assert "<style>" in content
        # Phase 1 migration note.
        assert "Prometheus" in content


class TestNonStaffForbidden:
    def test_anonymous_gets_403(self, client: Client) -> None:
        response = client.get("/admin/observability/shadow/")
        assert response.status_code == 403

    def test_regular_user_gets_403(self, regular_user, client: Client) -> None:
        client.force_login(regular_user)
        response = client.get("/admin/observability/shadow/")
        assert response.status_code == 403


class TestEmptyState:
    def test_no_rows_renders_friendly_message(self, staff_user, client: Client) -> None:
        client.force_login(staff_user)
        response = client.get("/admin/observability/shadow/")

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "No ShadowDeltaSnapshot rows" in content
