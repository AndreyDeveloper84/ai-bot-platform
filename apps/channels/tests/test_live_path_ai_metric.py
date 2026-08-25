"""AIRequestMetric on the non-concierge live-path turns (DRF-1209, step 2).

Until now the live MAX paths wrote ``AIRequestMetric`` only for concierge
turns (DRF-1211/1266/1283 via ``concierge._record_concierge_metric``). Two
families of terminal outcomes stayed invisible to the pilot thresholds:

* per-tenant skill-dispatch turns (``apps.skills.registry.dispatch`` via
  ``_handle_max_event_inner``) — no metric at all;
* deterministic global-path branches (safety, opt_out, stale_tap, human
  handoff, visits, onboarding) — no metric at all.

The new rows are gated by ``LIVE_PATH_AI_METRIC_ENABLED`` (default OFF):
flag off = zero new rows, byte-identical behaviour
(:class:`TestFlagOffCharacterization`). Field shape mirrors the concierge
writer: uuid5 fallback for non-UUID trace ids, ``llm_pass_index=None`` and
NULL token/cost columns on non-LLM outcomes (the schema's documented
«no LLM call» shape, same as DRF-1283 direct show_masters), the global
path parks rows under the ``global_bot`` sentinel tenant.
"""

from __future__ import annotations

import time
import uuid

import pytest

from apps.channels.max import handler as max_handler
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.observability.models import AIRequestMetric
from apps.orchestrator.memory import short_term
from apps.skills.base import SkillResult
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Fixtures (mirrored from test_handler_confidence_floor.py /                    #
# test_global_safety_precheck.py)                                               #
# --------------------------------------------------------------------------- #
@pytest.fixture
def mock_send(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _no_chat_action(monkeypatch):
    import apps.channels.max.outbound as outbound

    monkeypatch.setattr(outbound, "send_chat_action", lambda **kw: None)


@pytest.fixture(autouse=True)
def _strict(settings):
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.STRICT_TENANT_REFUSE = True


@pytest.fixture
def metric_enabled(settings):
    settings.LIVE_PATH_AI_METRIC_ENABLED = True


@pytest.fixture
def stub_dispatch(monkeypatch):
    """Stub the skill registry; each test sets ``stub_dispatch.result``."""

    class _Stub:
        result: SkillResult | None = SkillResult(reply_text="ok-reply")

    stub = _Stub()
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: stub.result)
    return stub


@pytest.fixture
def mock_concierge(monkeypatch):
    """Stub the concierge LLM turn so global free-text turns don't call out."""
    from unittest.mock import MagicMock

    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Какая услуга интересует?"))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


def _msg(text: str, *, user_id: int = 111, chat_id: int = 111, mid: str = "m-1") -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Иван"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _run_per_tenant(tenant, text, *, trace_id=None, mid="am"):
    trace = trace_id if trace_id is not None else uuid.uuid4()
    with tenant_scope(tenant), trace_id_scope(str(trace)):
        max_handler.handle_max_event(_msg(text, mid=mid), trace_id=trace)
    return trace


def _run_global(text, *, trace_id=None, mid="am"):
    trace = trace_id if trace_id is not None else uuid.uuid4()
    max_handler.handle_global_max_event(
        _msg(text, user_id=7777, chat_id=8888, mid=mid), trace_id=trace
    )
    return trace


# --------------------------------------------------------------------------- #
# Flag OFF — zero new rows (characterization)                                  #
# --------------------------------------------------------------------------- #
class TestFlagOffCharacterization:
    def test_flag_off_per_tenant_skill_turn_writes_nothing(
        self, mock_send, fake_redis, stub_dispatch, settings
    ):
        settings.LIVE_PATH_AI_METRIC_ENABLED = False
        stub_dispatch.result = SkillResult(reply_text="ответ фака", meta={"skill": "faq"})
        tenant = Tenant.objects.create(slug="am-off", name="AM-OFF")

        _run_per_tenant(tenant, "сколько стоит стрижка?")

        assert AIRequestMetric.all_tenants.count() == 0
        assert mock_send[-1]["text"] == "ответ фака"

    def test_flag_off_global_safety_writes_nothing(self, mock_send, fake_redis, settings):
        settings.LIVE_PATH_AI_METRIC_ENABLED = False

        _run_global("я думаю о суициде")

        assert AIRequestMetric.all_tenants.count() == 0
        assert len(mock_send) == 1

    def test_flag_unset_defaults_to_off(self, mock_send, fake_redis, stub_dispatch, settings):
        # Setting absent entirely (fresh deploy before env is wired) behaves
        # as OFF — getattr-style read, never AttributeError.
        assert not hasattr(settings, "LIVE_PATH_AI_METRIC_ENABLED") or (
            settings.LIVE_PATH_AI_METRIC_ENABLED is False
        )
        stub_dispatch.result = SkillResult(reply_text="обычный ответ", meta={"skill": "faq"})
        tenant = Tenant.objects.create(slug="am-unset", name="AM-UNSET")

        _run_per_tenant(tenant, "вопрос")

        assert AIRequestMetric.all_tenants.count() == 0
        assert mock_send[-1]["text"] == "обычный ответ"


# --------------------------------------------------------------------------- #
# Flag ON — per-tenant skill-dispatch turns                                    #
# --------------------------------------------------------------------------- #
class TestPerTenantSkillTurn:
    def test_skill_turn_writes_one_row(self, mock_send, fake_redis, stub_dispatch, metric_enabled):
        stub_dispatch.result = SkillResult(reply_text="точный ответ", meta={"skill": "faq"})
        tenant = Tenant.objects.create(slug="am-faq", name="AM-FAQ")

        trace = _run_per_tenant(tenant, "сколько стоит стрижка?")

        rows = list(AIRequestMetric.all_tenants.all())
        assert len(rows) == 1
        row = rows[0]
        assert row.tenant_id == tenant.id
        assert row.skill_selected == "faq"
        assert row.outcome == AIRequestMetric.OUTCOME_SUCCESS
        # Non-LLM outcome shape (DRF-1283 contract): NULL pass index, NULL
        # tokens/cost — not zeros.
        assert row.llm_pass_index is None
        assert row.llm_tokens_input is None
        assert row.llm_tokens_output is None
        assert row.llm_cost_usd is None
        assert row.request_id == uuid.UUID(str(trace))
        assert row.latency_total_ms >= 0
        assert row.message_text_length == len("сколько стоит стрижка?")
        # The reply itself is unaffected.
        assert mock_send[-1]["text"] == "точный ответ"

    def test_skill_name_falls_back_to_action_type(
        self, mock_send, fake_redis, stub_dispatch, metric_enabled
    ):
        # meta has no "skill" key → action_type, same extraction order as the
        # pipeline / confidence-floor helpers.
        stub_dispatch.result = SkillResult(reply_text="ответ", action_type="echo", meta={})
        tenant = Tenant.objects.create(slug="am-act", name="AM-ACT")

        _run_per_tenant(tenant, "привет")

        row = AIRequestMetric.all_tenants.get()
        assert row.skill_selected == "echo"

    def test_skill_handoff_writes_escalated_row(
        self, mock_send, fake_redis, stub_dispatch, metric_enabled
    ):
        # The skill itself asks for a human (confidence-floor flag stays OFF):
        # the terminal outcome is an escalation.
        stub_dispatch.result = SkillResult(
            reply_text="переключаю на менеджера",
            should_handoff=True,
            handoff_reason="faq_low_confidence",
            meta={"skill": "faq"},
        )
        tenant = Tenant.objects.create(slug="am-handoff", name="AM-HO")

        _run_per_tenant(tenant, "не понимаю")

        rows = list(AIRequestMetric.all_tenants.all())
        assert len(rows) == 1
        assert rows[0].outcome == AIRequestMetric.OUTCOME_ESCALATED
        assert rows[0].skill_selected == "faq"
        assert rows[0].llm_pass_index is None
        assert mock_send[-1]["text"] == "переключаю на менеджера"

    def test_non_uuid_trace_id_uses_deterministic_uuid5(self, metric_enabled):
        # Same fallback as pipeline / concierge: a non-UUID trace id hashes to
        # uuid5(NAMESPACE_DNS, trace_id) so log grep and the metric row stay
        # correlated. Unit-level: the full handler path legitimately refuses a
        # non-UUID trace earlier (record_message parses it), so the fallback
        # is exercised on the helper directly.
        max_handler._record_live_path_metric(
            bot_user=None,
            conversation=None,
            trace_id="trace-not-a-uuid",
            message_text="вопрос",
            t_start=time.monotonic(),
            outcome=AIRequestMetric.OUTCOME_SUCCESS,
            skill_selected="faq",
        )

        row = AIRequestMetric.all_tenants.get()
        assert row.request_id == uuid.uuid5(uuid.NAMESPACE_DNS, "trace-not-a-uuid")


# --------------------------------------------------------------------------- #
# Flag ON — deterministic global branches                                      #
# --------------------------------------------------------------------------- #
class TestGlobalDeterministicBranches:
    def test_safety_shortcircuit_writes_row(self, mock_send, fake_redis, metric_enabled):
        _run_global("я думаю о суициде")

        rows = list(AIRequestMetric.all_tenants.all())
        assert len(rows) == 1
        row = rows[0]
        assert row.tenant.slug == GLOBAL_BOT_TENANT_SLUG
        assert row.skill_selected == "safety_pre_check"
        assert row.outcome == AIRequestMetric.OUTCOME_SUCCESS
        assert row.llm_pass_index is None
        assert row.llm_tokens_input is None
        # The crisis reply still went out.
        assert len(mock_send) == 1

    def test_opt_out_writes_row(self, mock_send, fake_redis, metric_enabled):
        _run_global("не пиши мне")

        rows = list(AIRequestMetric.all_tenants.all())
        assert len(rows) == 1
        row = rows[0]
        assert row.tenant.slug == GLOBAL_BOT_TENANT_SLUG
        assert row.skill_selected == "proactive_opt_out"
        assert row.outcome == AIRequestMetric.OUTCOME_SUCCESS
        assert row.llm_pass_index is None

    def test_stale_tap_writes_fallback_row(self, mock_send, fake_redis, metric_enabled):
        # A «Повторить» tap with no history to resubmit: the turn cannot be
        # resolved to its intended action → fallback row, canned retry reply.
        _run_global("cb:retry:last")

        rows = list(AIRequestMetric.all_tenants.all())
        assert len(rows) == 1
        row = rows[0]
        assert row.skill_selected == "stale_tap"
        assert row.outcome == AIRequestMetric.OUTCOME_FALLBACK
        assert row.fallback_triggered is True
        assert row.llm_pass_index is None

    def test_concierge_turn_not_double_counted(
        self, mock_send, fake_redis, mock_concierge, metric_enabled, monkeypatch
    ):
        # Branches the concierge already meters (its own LLM passes, the
        # deterministic show-masters render) must NOT get a second row from
        # the handler. Spy on the handler's own writer reference: any other
        # writer active this turn (e.g. DRF-1273 intent resolution, which
        # meters the resolver pass itself) is out of scope for this guard.
        from unittest.mock import MagicMock

        handler_writer = MagicMock()
        monkeypatch.setattr(max_handler, "record_ai_request", handler_writer)

        _run_global("привет, как дела?")

        mock_concierge.assert_called_once()
        handler_writer.assert_not_called()
        assert len(mock_send) == 1


# --------------------------------------------------------------------------- #
# Best-effort: a metric failure never breaks the turn                          #
# --------------------------------------------------------------------------- #
class TestMetricFailureIsBestEffort:
    def test_record_failure_still_delivers_per_tenant_reply(
        self, mock_send, fake_redis, stub_dispatch, metric_enabled, monkeypatch
    ):
        def boom(**kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(max_handler, "record_ai_request", boom)
        stub_dispatch.result = SkillResult(
            reply_text="ответ несмотря ни на что", meta={"skill": "faq"}
        )
        tenant = Tenant.objects.create(slug="am-boom", name="AM-BOOM")

        _run_per_tenant(tenant, "вопрос")

        assert mock_send[-1]["text"] == "ответ несмотря ни на что"
        assert AIRequestMetric.all_tenants.count() == 0

    def test_record_failure_still_delivers_global_reply(
        self, mock_send, fake_redis, metric_enabled, monkeypatch
    ):
        def boom(**kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(max_handler, "record_ai_request", boom)

        _run_global("не пиши мне")

        assert len(mock_send) == 1
        assert AIRequestMetric.all_tenants.count() == 0
