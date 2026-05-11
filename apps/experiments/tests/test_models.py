"""Experiment / UserAssignment / Holdout model tests (DRF-484 / Sprint 4 / B1)."""

from __future__ import annotations

import pytest
from django.db.utils import IntegrityError

from apps.experiments.models import Experiment, Holdout, UserAssignment
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="ex-a", name="EX-A")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="ex-bot")


class TestExperiment:
    def test_create_defaults(self, tenant, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            exp = Experiment.objects.create(
                tenant=tenant,
                name="router-v2",
                hypothesis="new router lifts conv",
                primary_kpi="booking_conversion_rate",
                variants=[
                    {"name": "control", "weight": 50},
                    {"name": "v2", "weight": 50},
                ],
            )
        exp.refresh_from_db()
        assert exp.status == "draft"
        assert exp.started_at is None
        assert exp.guardrails == []

    def test_unique_name_per_tenant(self, tenant, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            Experiment.objects.create(
                tenant=tenant, name="exp1", hypothesis="", primary_kpi="k", variants=[]
            )
            with pytest.raises(IntegrityError):
                Experiment.objects.create(
                    tenant=tenant,
                    name="exp1",
                    hypothesis="",
                    primary_kpi="k",
                    variants=[],
                )

    def test_status_enum(self):
        statuses = {s[0] for s in Experiment.Status.choices}
        assert statuses == {"draft", "running", "paused", "completed"}


class TestUserAssignment:
    def test_unique_per_user_experiment(self, tenant, bot_user, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            exp = Experiment.objects.create(
                tenant=tenant,
                name="e",
                hypothesis="",
                primary_kpi="k",
                variants=[{"name": "v", "weight": 100}],
            )
            UserAssignment.objects.create(
                tenant=tenant, bot_user=bot_user, experiment=exp, variant="v"
            )
            with pytest.raises(IntegrityError):
                UserAssignment.objects.create(
                    tenant=tenant, bot_user=bot_user, experiment=exp, variant="v"
                )

    def test_cascade_on_bot_user_delete(self, tenant, bot_user, settings):
        """BotUser hard-delete cascades to UserAssignment."""

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            exp = Experiment.objects.create(
                tenant=tenant,
                name="e",
                hypothesis="",
                primary_kpi="k",
                variants=[{"name": "v", "weight": 100}],
            )
            UserAssignment.objects.create(
                tenant=tenant, bot_user=bot_user, experiment=exp, variant="v"
            )
        assert UserAssignment.all_tenants.count() == 1
        bot_user.delete()
        assert UserAssignment.all_tenants.count() == 0


class TestHoldout:
    def test_one_to_one_bot_user(self, tenant, bot_user, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            Holdout.objects.create(tenant=tenant, bot_user=bot_user)
            with pytest.raises(IntegrityError):
                Holdout.objects.create(tenant=tenant, bot_user=bot_user)

    def test_cascade_on_bot_user_delete(self, tenant, bot_user, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            Holdout.objects.create(tenant=tenant, bot_user=bot_user)
        bot_user.delete()
        assert Holdout.all_tenants.count() == 0
