"""ReplayTraceAdmin display tests.

Smoke-tests the read-only forensic admin: list_display computed columns
(intent_summary, skill_summary) handle the full range of trace shapes —
populated, empty, malformed, missing keys.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from apps.replay.admin import ReplayTraceAdmin
from apps.replay.models import ReplayTrace
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


def _make_trace(tenant: Tenant, steps: list) -> ReplayTrace:
    return ReplayTrace.all_tenants.create(
        tenant=tenant,
        trace_id=str(uuid4()),
        pipeline_steps=steps,
        redacted=True,
        redaction_method="regex_v1",
        expires_at=timezone.now() + timedelta(days=30),
    )


@pytest.fixture
def tenant():
    return Tenant.objects.create(slug="admin-test", name="Admin test")


@pytest.fixture
def admin_inst():
    """Bare ModelAdmin — we exercise display methods, not Django admin views."""
    from django.contrib.admin.sites import AdminSite

    return ReplayTraceAdmin(ReplayTrace, AdminSite())


class TestIntentSummary:
    def test_populated_intent(self, tenant, admin_inst):
        t = _make_trace(tenant, [{"intent": "delete", "skill_used": "privacy_consent"}])
        assert admin_inst.intent_summary(t) == "delete"

    def test_empty_intent(self, tenant, admin_inst):
        t = _make_trace(tenant, [{"intent": "", "skill_used": "human_handoff"}])
        assert admin_inst.intent_summary(t) == "—"

    def test_missing_key(self, tenant, admin_inst):
        t = _make_trace(tenant, [{"skill_used": "x"}])
        assert admin_inst.intent_summary(t) == "—"

    def test_empty_steps(self, tenant, admin_inst):
        t = _make_trace(tenant, [])
        assert admin_inst.intent_summary(t) == "—"

    def test_malformed_first_step(self, tenant, admin_inst):
        t = _make_trace(tenant, ["not a dict"])
        assert admin_inst.intent_summary(t) == "—"


class TestSkillSummary:
    def test_populated_skill(self, tenant, admin_inst):
        t = _make_trace(tenant, [{"intent": "delete", "skill_used": "privacy_consent"}])
        assert admin_inst.skill_summary(t) == "privacy_consent"

    def test_empty_skill_fallback(self, tenant, admin_inst):
        """Echo dispatch leaves skill_used empty — admin renders dash."""
        t = _make_trace(tenant, [{"intent": "", "skill_used": ""}])
        assert admin_inst.skill_summary(t) == "—"

    def test_missing_key(self, tenant, admin_inst):
        t = _make_trace(tenant, [{"intent": "x"}])
        assert admin_inst.skill_summary(t) == "—"

    def test_empty_steps(self, tenant, admin_inst):
        t = _make_trace(tenant, [])
        assert admin_inst.skill_summary(t) == "—"


class TestListDisplay:
    """Pin the column order so future refactors don't silently drop a column."""

    def test_list_display_includes_intent_and_skill(self, admin_inst):
        assert "intent_summary" in admin_inst.list_display
        assert "skill_summary" in admin_inst.list_display

    def test_list_display_order(self, admin_inst):
        cols = list(admin_inst.list_display)
        assert cols.index("intent_summary") < cols.index("redacted")
        assert cols.index("skill_summary") < cols.index("redacted")
