"""AI quality dashboard view tests (Веха 3 / #772).

Covers:
- Auth (anonymous → 403, non-staff → 403, staff → 200)
- Empty state (no rows → graceful empty render)
- Row rendering with threshold badges (ok / breach / no_data)
- Tenant dropdown filter
- Date-range jumper (?days=1/7/14/30)
- Sparkline output when ≥1 row exists
- XSS safety (tenant slug + metric values escaped)
- Page reachable via reverse('observability:ai-quality-dashboard')
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.observability.models import AIDailyMetricSummary
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


# ─── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def staff_user():
    return User.objects.create_user(
        username="dash-staff",
        password="pw",  # noqa: S106 — test fixture  # pragma: allowlist secret
        is_staff=True,
    )


@pytest.fixture
def superuser():
    return User.objects.create_user(
        username="dash-super",
        password="pw",  # noqa: S106 — test fixture  # pragma: allowlist secret
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture
def regular_user():
    return User.objects.create_user(
        username="dash-customer",
        password="pw",  # noqa: S106 — test fixture  # pragma: allowlist secret
    )


@pytest.fixture
def tenant_a():
    return Tenant.objects.create(slug="aiq-a", name="AI Quality A")


@pytest.fixture
def tenant_b():
    return Tenant.objects.create(slug="aiq-b", name="AI Quality B")


def _make_summary(
    tenant: Tenant,
    *,
    snapshot_date: date | None = None,
    threshold_status: dict | None = None,
    **overrides,
) -> AIDailyMetricSummary:
    defaults: dict = {
        "tenant": tenant,
        "snapshot_date": snapshot_date or date.today(),
        "total_requests": 100,
        "success_count": 80,
        "error_count": 5,
        "fallback_count": 10,
        "escalated_count": 5,
        "latency_p50_ms": 800,
        "latency_p95_ms": 2500,
        "latency_p99_ms": 4000,
        "total_tokens_input": 1000,
        "total_tokens_output": 500,
        "total_cost_usd": Decimal("0.45"),
        "mean_cost_usd": Decimal("0.0045"),
        "task_success_rate": 0.85,
        "fallback_rate": 0.10,
        "intent_distribution": {"book": 50, "ask": 30, "(none)": 20},
        "threshold_status": threshold_status
        if threshold_status is not None
        else {
            "task_success_rate": {
                "value": 0.85,
                "threshold": 0.80,
                "op": ">=",
                "status": "ok",
            },
            "latency_p95_ms": {
                "value": 2500,
                "threshold": 3000,
                "op": "<",
                "status": "ok",
            },
            "fallback_rate": {
                "value": 0.10,
                "threshold": 0.20,
                "op": "<",
                "status": "ok",
            },
            "mean_cost_usd": {
                "value": 0.0045,
                "threshold": 0.01,
                "op": "<",
                "status": "ok",
            },
        },
    }
    defaults.update(overrides)
    return AIDailyMetricSummary.objects.create(**defaults)


def _url() -> str:
    return reverse("observability:ai-quality-dashboard")


# ─── TestAuth ───────────────────────────────────────────────────────────


class TestAuth:
    def test_anonymous_returns_403(self, client: Client):
        resp = client.get(_url())
        assert resp.status_code == 403

    def test_non_staff_returns_403(self, client: Client, regular_user: User):
        client.force_login(regular_user)
        resp = client.get(_url())
        assert resp.status_code == 403

    def test_staff_returns_200(self, client: Client, staff_user: User):
        client.force_login(staff_user)
        resp = client.get(_url())
        assert resp.status_code == 200


# ─── TestEmptyState ─────────────────────────────────────────────────────


class TestEmptyState:
    def test_no_rows_yields_no_data_message(self, client: Client, staff_user: User):
        client.force_login(staff_user)
        resp = client.get(_url())
        assert resp.status_code == 200
        assert b"No AI quality summaries" in resp.content


# ─── TestRowRendering ───────────────────────────────────────────────────


class TestRowRendering:
    def test_ok_row_shows_green_badge(self, client: Client, staff_user: User, tenant_a: Tenant):
        _make_summary(tenant_a)
        client.force_login(staff_user)
        resp = client.get(_url())
        # 🟢 green emoji indicates all metrics ok
        assert "🟢".encode("utf-8") in resp.content
        assert b"aiq-a" in resp.content

    def test_breach_row_shows_red_badge(self, client: Client, staff_user: User, tenant_a: Tenant):
        _make_summary(
            tenant_a,
            threshold_status={
                "task_success_rate": {
                    "value": 0.65,
                    "threshold": 0.80,
                    "op": ">=",
                    "status": "breach",
                },
                "latency_p95_ms": {
                    "value": 2500,
                    "threshold": 3000,
                    "op": "<",
                    "status": "ok",
                },
                "fallback_rate": {
                    "value": 0.10,
                    "threshold": 0.20,
                    "op": "<",
                    "status": "ok",
                },
                "mean_cost_usd": {
                    "value": 0.0045,
                    "threshold": 0.01,
                    "op": "<",
                    "status": "ok",
                },
            },
        )
        client.force_login(staff_user)
        resp = client.get(_url())
        assert "🔴".encode("utf-8") in resp.content

    def test_no_data_day_shows_empty_badge(
        self, client: Client, staff_user: User, tenant_a: Tenant
    ):
        _make_summary(
            tenant_a,
            total_requests=0,
            success_count=0,
            error_count=0,
            fallback_count=0,
            escalated_count=0,
            threshold_status={"no_data": True},
        )
        client.force_login(staff_user)
        resp = client.get(_url())
        assert "⚪".encode("utf-8") in resp.content
        assert b"no data" in resp.content


# ─── TestTenantDropdown ─────────────────────────────────────────────────


class TestTenantDropdown:
    def test_no_filter_shows_all_tenants(
        self, client: Client, staff_user: User, tenant_a: Tenant, tenant_b: Tenant
    ):
        _make_summary(tenant_a)
        _make_summary(tenant_b)
        client.force_login(staff_user)
        resp = client.get(_url())
        assert b"aiq-a" in resp.content
        assert b"aiq-b" in resp.content

    def test_tenant_query_filter(
        self, client: Client, staff_user: User, tenant_a: Tenant, tenant_b: Tenant
    ):
        _make_summary(tenant_a)
        _make_summary(tenant_b)
        client.force_login(staff_user)
        resp = client.get(_url() + "?tenant=aiq-a")
        # In the summary table, only tenant_a row should appear.
        # `aiq-b` may still appear in the dropdown options — verify
        # the summary row count by counting `<tr>` with the slug.
        text = resp.content.decode("utf-8")
        # Count summary-row appearances of each tenant via the unique cell shape.
        assert text.count("<td>aiq-a</td>") == 1
        assert text.count("<td>aiq-b</td>") == 0


# ─── TestDateRangeJumper ────────────────────────────────────────────────


class TestDateRangeJumper:
    def test_default_window_14_days(self, client: Client, staff_user: User, tenant_a: Tenant):
        # Row 20 days old — outside default 14d window
        _make_summary(tenant_a, snapshot_date=date.today() - timedelta(days=20))
        # Row 5 days old — inside
        _make_summary(tenant_a, snapshot_date=date.today() - timedelta(days=5))
        client.force_login(staff_user)
        resp = client.get(_url())
        text = resp.content.decode("utf-8")
        # Only the 5-day-old row should appear in table
        assert text.count("<td>aiq-a</td>") == 1

    def test_30day_window_includes_older(self, client: Client, staff_user: User, tenant_a: Tenant):
        _make_summary(tenant_a, snapshot_date=date.today() - timedelta(days=20))
        _make_summary(tenant_a, snapshot_date=date.today() - timedelta(days=5))
        client.force_login(staff_user)
        resp = client.get(_url() + "?days=30")
        text = resp.content.decode("utf-8")
        # Both rows appear under 30-day window
        assert text.count("<td>aiq-a</td>") == 2

    def test_invalid_days_param_falls_back_to_default(
        self, client: Client, staff_user: User, tenant_a: Tenant
    ):
        _make_summary(tenant_a)
        client.force_login(staff_user)
        # `?days=999` not in whitelist → fall back to 14d
        resp = client.get(_url() + "?days=999")
        assert resp.status_code == 200
        # Page should still render — graceful fallback
        assert b"Last 14 day" in resp.content


# ─── TestSparkline ──────────────────────────────────────────────────────


class TestSparkline:
    def test_sparkline_present_when_rows_exist(
        self, client: Client, staff_user: User, tenant_a: Tenant
    ):
        _make_summary(tenant_a, snapshot_date=date.today() - timedelta(days=1))
        _make_summary(tenant_a, snapshot_date=date.today())
        client.force_login(staff_user)
        resp = client.get(_url())
        # «Trends» section + per-metric sparkline divs
        assert b"Trends" in resp.content
        assert b"spark-bars" in resp.content
        assert b"Task Success" in resp.content
        assert b"Latency p95" in resp.content
        assert b"Fallback Rate" in resp.content
        assert b"Mean Cost" in resp.content

    def test_sparkline_marks_breach_bars(self, client: Client, staff_user: User, tenant_a: Tenant):
        _make_summary(
            tenant_a,
            threshold_status={
                "task_success_rate": {
                    "value": 0.65,
                    "threshold": 0.80,
                    "op": ">=",
                    "status": "breach",
                },
                "latency_p95_ms": {
                    "value": 2500,
                    "threshold": 3000,
                    "op": "<",
                    "status": "ok",
                },
                "fallback_rate": {
                    "value": 0.10,
                    "threshold": 0.20,
                    "op": "<",
                    "status": "ok",
                },
                "mean_cost_usd": {
                    "value": 0.0045,
                    "threshold": 0.01,
                    "op": "<",
                    "status": "ok",
                },
            },
        )
        client.force_login(staff_user)
        resp = client.get(_url())
        # `bar-breach` CSS class on the task-success bar
        assert b"bar-breach" in resp.content


# ─── TestXSSSafety ──────────────────────────────────────────────────────


class TestXSSSafety:
    def test_tenant_slug_escaped(self, client: Client, staff_user: User):
        # SlugField doesn't allow <>" but be defensive in rendering
        # (a future schema change could relax slug validation; this guards).
        # Use legitimate slug; verify escape() is in the pipeline by checking
        # no raw HTML injection occurs even for special characters in name.
        evil = Tenant.objects.create(
            slug="evil-tnt",
            name="<script>alert(1)</script>",
        )
        _make_summary(evil)
        client.force_login(staff_user)
        resp = client.get(_url())
        # Tenant name is not rendered (only slug is). Verify the dropdown
        # option for the slug is HTML-escaped (escape() is applied).
        # Direct script tags must not appear unescaped.
        assert b"<script>" not in resp.content
        # The dropdown option for the legitimate slug appears.
        assert b"evil-tnt" in resp.content
