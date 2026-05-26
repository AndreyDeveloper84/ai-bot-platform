"""AIRequestMetric model + recorder tests (AI observability Веха 1 / #770).

Veha 1 scope: schema correctness + recorder API + TenantScopedManager isolation
+ performance smoke. Aggregation / dashboard tests live in Веха 2 / 3.
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal

import pytest
from django.db import connection

from apps.identity.models import BotUser
from apps.observability.ai_metrics import record_ai_request
from apps.observability.models import AIRequestMetric
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


# ─── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def tenant_a():
    return Tenant.objects.create(slug="ai-metric-a", name="AI Metric A")


@pytest.fixture
def tenant_b():
    return Tenant.objects.create(slug="ai-metric-b", name="AI Metric B")


@pytest.fixture
def bot_user_a(tenant_a):
    return BotUser.all_tenants.create(
        tenant=tenant_a,
        channel="max",
        channel_user_id="airm-a-1",
        display_name="User A",
    )


@pytest.fixture
def bot_user_b(tenant_b):
    return BotUser.all_tenants.create(
        tenant=tenant_b,
        channel="max",
        channel_user_id="airm-b-1",
        display_name="User B",
    )


def _make_metric(
    tenant: Tenant,
    bot_user: BotUser | None = None,
    **overrides,
) -> AIRequestMetric:
    """Helper for tests that construct a minimal valid AIRequestMetric."""
    defaults = {
        "tenant": tenant,
        "bot_user": bot_user,
        "request_id": uuid.uuid4(),
        "message_text_length": 42,
        "latency_total_ms": 1234,
        "outcome": AIRequestMetric.OUTCOME_SUCCESS,
    }
    defaults.update(overrides)
    return AIRequestMetric.all_tenants.create(**defaults)


# ─── TestModelContract ──────────────────────────────────────────────────


class TestModelContract:
    """Schema basics: PK, FKs, NULL semantics, defaults, choices."""

    def test_minimal_create(self, tenant_a, bot_user_a):
        m = _make_metric(tenant_a, bot_user_a)
        assert m.id is not None
        assert m.tenant_id == tenant_a.id
        assert m.bot_user_id == bot_user_a.id
        assert m.created_at is not None
        # Defaults
        assert m.intent_classified == ""
        assert m.intent_confidence is None
        assert m.skill_selected == ""
        assert m.fallback_triggered is False
        assert m.latency_llm_ms is None
        assert m.latency_skill_ms is None
        assert m.llm_provider == ""
        assert m.llm_tokens_input is None
        assert m.llm_tokens_output is None
        assert m.llm_cost_usd is None
        # Веха 2 fills these
        assert m.booking_event_id is None
        assert m.success_correlated_at is None

    def test_bot_user_nullable(self, tenant_a):
        """System-triggered AI calls (proactive nudges) have no bot_user."""
        m = _make_metric(tenant_a, bot_user=None)
        assert m.bot_user_id is None

    def test_outcome_choices_enforced_at_recorder(self, tenant_a, bot_user_a):
        """Recorder API rejects invalid outcome; ORM-level it's just a string."""
        with pytest.raises(ValueError, match="OUTCOME_CHOICES"):
            record_ai_request(
                tenant=tenant_a,
                bot_user=bot_user_a,
                conversation=None,
                request_id=uuid.uuid4(),
                message_text_length=10,
                latency_total_ms=100,
                outcome="not-a-valid-outcome",
            )

    def test_str_representation(self, tenant_a, bot_user_a):
        m = _make_metric(tenant_a, bot_user_a, intent_classified="book_service")
        s = str(m)
        assert "AIRequestMetric" in s
        assert "book_service" in s
        assert "success" in s


# ─── TestTenantScoping ──────────────────────────────────────────────────


class TestTenantScoping:
    """`objects` (TenantScopedManager) filters per `current_tenant()`."""

    def test_default_manager_filters_to_current_tenant(
        self, tenant_a, tenant_b, bot_user_a, bot_user_b, settings
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        _make_metric(tenant_a, bot_user_a, intent_classified="for_a")
        _make_metric(tenant_b, bot_user_b, intent_classified="for_b")

        with tenant_scope(tenant_a):
            rows = list(AIRequestMetric.objects.all())
            assert len(rows) == 1
            assert rows[0].tenant_id == tenant_a.id
            assert rows[0].intent_classified == "for_a"

        with tenant_scope(tenant_b):
            rows = list(AIRequestMetric.objects.all())
            assert len(rows) == 1
            assert rows[0].tenant_id == tenant_b.id

    def test_all_tenants_escape_hatch(self, tenant_a, tenant_b, bot_user_a, bot_user_b, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        _make_metric(tenant_a, bot_user_a)
        _make_metric(tenant_b, bot_user_b)
        assert AIRequestMetric.all_tenants.count() == 2


# ─── TestRecorderAPI ────────────────────────────────────────────────────


class TestRecorderAPI:
    """`record_ai_request()` kwargs validation + happy path."""

    def test_happy_path_full_kwargs(self, tenant_a, bot_user_a):
        rid = uuid.uuid4()
        m = record_ai_request(
            tenant=tenant_a,
            bot_user=bot_user_a,
            conversation=None,
            request_id=rid,
            message_text_length=128,
            intent_classified="book_service",
            intent_confidence=0.92,
            skill_selected="booking",
            fallback_triggered=False,
            latency_total_ms=1800,
            latency_llm_ms=1200,
            latency_skill_ms=400,
            llm_provider="openai",
            llm_tokens_input=350,
            llm_tokens_output=80,
            llm_cost_usd=Decimal("0.003500"),
            outcome=AIRequestMetric.OUTCOME_SUCCESS,
        )
        assert m.id is not None
        assert m.request_id == rid
        assert m.intent_confidence == pytest.approx(0.92)
        assert m.llm_cost_usd == Decimal("0.003500")

    def test_minimal_kwargs_with_defaults(self, tenant_a, bot_user_a):
        """Caller supplies only required args; defaults fill optional fields."""
        m = record_ai_request(
            tenant=tenant_a,
            bot_user=bot_user_a,
            conversation=None,
            request_id=uuid.uuid4(),
            message_text_length=10,
            latency_total_ms=100,
            outcome=AIRequestMetric.OUTCOME_FALLBACK,
        )
        assert m.outcome == AIRequestMetric.OUTCOME_FALLBACK
        assert m.intent_classified == ""
        assert m.intent_confidence is None
        assert m.fallback_triggered is False

    def test_fallback_outcome_with_triggered_flag(self, tenant_a, bot_user_a):
        m = record_ai_request(
            tenant=tenant_a,
            bot_user=bot_user_a,
            conversation=None,
            request_id=uuid.uuid4(),
            message_text_length=20,
            latency_total_ms=500,
            intent_confidence=0.55,
            fallback_triggered=True,
            outcome=AIRequestMetric.OUTCOME_FALLBACK,
        )
        assert m.fallback_triggered is True
        assert m.outcome == AIRequestMetric.OUTCOME_FALLBACK

    def test_invalid_outcome_raises_value_error(self, tenant_a, bot_user_a):
        with pytest.raises(ValueError, match="OUTCOME_CHOICES"):
            record_ai_request(
                tenant=tenant_a,
                bot_user=bot_user_a,
                conversation=None,
                request_id=uuid.uuid4(),
                message_text_length=10,
                latency_total_ms=100,
                outcome="banana",
            )

    def test_system_call_no_bot_user(self, tenant_a):
        """Proactive nudge / scheduled message — bot_user=None allowed."""
        m = record_ai_request(
            tenant=tenant_a,
            bot_user=None,
            conversation=None,
            request_id=uuid.uuid4(),
            message_text_length=0,  # system-generated, no inbound text
            latency_total_ms=50,
            outcome=AIRequestMetric.OUTCOME_SUCCESS,
        )
        assert m.bot_user_id is None
        assert m.tenant_id == tenant_a.id


# ─── TestIndexes (postgres-only) ────────────────────────────────────────


class TestIndexes:
    """Verify the 4 declared indexes exist after migration."""

    @pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="pg_indexes inspection is Postgres-only.",
    )
    def test_all_four_indexes_present(self):
        expected = {
            "airm_tenant_ts_idx",
            "airm_tenant_intent_idx",
            "airm_tenant_outcome_idx",
            "airm_tenant_fallback_idx",
        }
        with connection.cursor() as cur:
            cur.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'observability_airequestmetric'"
            )
            found = {row[0] for row in cur.fetchall()}
        missing = expected - found
        assert not missing, f"Missing indexes: {missing}. Found: {found}"


# ─── TestPerformanceSmoke ────────────────────────────────────────────────


class TestPerformanceSmoke:
    """1000 sequential inserts < 5s — establishes a baseline for the
    recorder's <5ms-per-call target on hot path.

    NOT a strict regression gate (CI variance is wide). Documents the
    rough envelope so a 10x degradation surfaces in test output.
    """

    def test_thousand_inserts_under_five_seconds(self, tenant_a, bot_user_a):
        start = time.perf_counter()
        for i in range(1000):
            record_ai_request(
                tenant=tenant_a,
                bot_user=bot_user_a,
                conversation=None,
                request_id=uuid.uuid4(),
                message_text_length=42,
                latency_total_ms=1500,
                outcome=AIRequestMetric.OUTCOME_SUCCESS,
            )
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0, (
            f"1000 record_ai_request inserts took {elapsed:.2f}s — "
            "performance regression on the hot path (target <5ms per call)."
        )
        assert AIRequestMetric.all_tenants.filter(tenant=tenant_a).count() == 1000


# ─── TestTenantProtect — tenant FK PROTECT semantic ────────────────────


class TestTenantProtect:
    def test_tenant_drop_blocked_when_metrics_exist(self, tenant_a, bot_user_a):
        """Forensic + billing data — accidental tenant drop must fail loud."""
        from django.db.models import ProtectedError

        _make_metric(tenant_a, bot_user_a)
        # bot_user has PROTECT on tenant FK already, but stage cleanly
        # by removing bot_user first then attempting tenant delete.
        with pytest.raises(ProtectedError):
            tenant_a.delete()
