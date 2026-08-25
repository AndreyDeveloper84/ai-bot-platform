"""ReplayTrace capture on the LIVE MAX paths (DRF-1209, step 18).

Until now ``apps.replay.recorder`` was called only by the DEPRECATED
``apps.orchestrator.pipeline.turn`` (zero callers outside docstrings and
tests) and the offline replay runner — the path that actually answers
people (``apps/channels/max/handler.py``) wrote no traces, so live
behaviour could not be replayed or diffed even though the CI replay gate
reads traces.

This module pins the live-path capture:

* gated by ``REPLAY_LIVE_CAPTURE_ENABLED`` (default OFF): flag off = zero
  new rows, byte-identical behaviour (:class:`TestFlagOffCharacterization`);
* the SAME recorder the pipeline point uses — same sampling
  (``REPLAY_SAMPLE_RATE_*``), same ``regex_v2`` redaction BEFORE persist,
  same swallow-everything contract;
* global rows park under the ``global_bot`` sentinel tenant (the global
  path runs at ``current_tenant()=None``, which the recorder skips);
* live rows are distinguishable from pipeline rows by the
  ``source: "live_path"`` marker on the inbound step payload;
* a recorder failure never breaks the user-facing reply
  (:class:`TestRecorderFailureIsBestEffort`).
"""

from __future__ import annotations

import uuid

import pytest

from apps.channels.max import handler as max_handler
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.orchestrator.memory import short_term
from apps.replay.models import ReplayTrace
from apps.replay.redactor import REDACTION_METHOD
from apps.skills.base import SkillResult
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Fixtures (mirrored from test_live_path_ai_metric.py)                          #
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
def capture_enabled(settings):
    """Flag ON + deterministic sampling (the recorder's own gate)."""

    settings.REPLAY_LIVE_CAPTURE_ENABLED = True
    settings.REPLAY_SAMPLE_RATE_TEST = 1.0


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


def _steps_by_name(row) -> dict:
    return {s["step"]: s["payload"] for s in row.pipeline_steps}


# --------------------------------------------------------------------------- #
# Flag OFF — zero new rows (characterization)                                  #
# --------------------------------------------------------------------------- #
class TestFlagOffCharacterization:
    def test_flag_off_per_tenant_skill_turn_writes_nothing(
        self, mock_send, fake_redis, stub_dispatch, settings
    ):
        settings.REPLAY_LIVE_CAPTURE_ENABLED = False
        settings.REPLAY_SAMPLE_RATE_TEST = 1.0
        stub_dispatch.result = SkillResult(reply_text="ответ фака", meta={"skill": "faq"})
        tenant = Tenant.objects.create(slug="rp-off", name="RP-OFF")

        _run_per_tenant(tenant, "сколько стоит стрижка?")

        assert ReplayTrace.all_tenants.count() == 0
        assert mock_send[-1]["text"] == "ответ фака"

    def test_flag_off_global_concierge_turn_writes_nothing(
        self, mock_send, fake_redis, mock_concierge, settings
    ):
        settings.REPLAY_LIVE_CAPTURE_ENABLED = False
        settings.REPLAY_SAMPLE_RATE_TEST = 1.0

        _run_global("привет, как дела?")

        mock_concierge.assert_called_once()
        assert ReplayTrace.all_tenants.count() == 0
        assert mock_send[-1]["text"] == "Какая услуга интересует?"

    def test_flag_unset_defaults_to_off(self, mock_send, fake_redis, stub_dispatch, settings):
        # Setting absent entirely (fresh deploy before env is wired) behaves
        # as OFF — getattr-style read, never AttributeError.
        assert not hasattr(settings, "REPLAY_LIVE_CAPTURE_ENABLED") or (
            settings.REPLAY_LIVE_CAPTURE_ENABLED is False
        )
        settings.REPLAY_SAMPLE_RATE_TEST = 1.0
        stub_dispatch.result = SkillResult(reply_text="обычный ответ", meta={"skill": "faq"})
        tenant = Tenant.objects.create(slug="rp-unset", name="RP-UNSET")

        _run_per_tenant(tenant, "вопрос")

        assert ReplayTrace.all_tenants.count() == 0
        assert mock_send[-1]["text"] == "обычный ответ"


# --------------------------------------------------------------------------- #
# Flag ON — global concierge turn                                              #
# --------------------------------------------------------------------------- #
class TestGlobalConciergeTurn:
    def test_concierge_turn_writes_trace_with_live_marker(
        self, mock_send, fake_redis, mock_concierge, capture_enabled
    ):
        trace = _run_global("привет, как дела?")

        rows = list(ReplayTrace.all_tenants.all())
        assert len(rows) == 1
        row = rows[0]
        # Global path runs tenant-less; the row parks under the sentinel,
        # same as the concierge AIRequestMetric rows.
        assert row.tenant.slug == GLOBAL_BOT_TENANT_SLUG
        assert row.trace_id == str(trace)
        assert row.redacted is True
        assert row.redaction_method == REDACTION_METHOD

        steps = _steps_by_name(row)
        # Live-path marker — distinguishable from pipeline-captured rows.
        assert steps["inbound"]["source"] == "live_path"
        assert steps["inbound"]["surface"] == "max_global"
        assert steps["inbound"]["text"] == "привет, как дела?"
        # What the bot actually decided + what the person actually read.
        assert steps["routing"]["branch"]
        assert steps["pre_check"]["verdict"] == "allow"
        assert steps["post_check"]["verdict"] == "allow"
        assert steps["composer"]["final_text"] == "Какая услуга интересует?"
        # The reply itself is unaffected.
        assert mock_send[-1]["text"] == "Какая услуга интересует?"

    def test_concierge_turn_redacts_pii_before_persist(
        self, mock_send, fake_redis, mock_concierge, capture_enabled
    ):
        # Same regex_v2 redaction the pipeline point applies — the raw user
        # text NEVER reaches the row.
        _run_global("мой телефон +74951234567, запишите на массаж")

        row = ReplayTrace.all_tenants.get()
        steps = _steps_by_name(row)
        assert "[PHONE]" in steps["inbound"]["text"]
        assert "+74951234567" not in steps["inbound"]["text"]
        # …while the person still got the concierge reply.
        assert mock_send[-1]["text"] == "Какая услуга интересует?"

    def test_deterministic_global_branch_captured_by_same_point(
        self, mock_send, fake_redis, capture_enabled
    ):
        # The safety short-circuit is one of the deterministic branches the
        # single end-of-turn capture point covers.
        _run_global("я думаю о суициде")

        row = ReplayTrace.all_tenants.get()
        steps = _steps_by_name(row)
        assert steps["inbound"]["source"] == "live_path"
        assert steps["routing"]["branch"] == "safety_pre_check"
        assert steps["pre_check"]["verdict"] != "allow"
        assert steps["composer"]["final_text"]
        # The crisis reply still went out.
        assert len(mock_send) == 1


# --------------------------------------------------------------------------- #
# Flag ON — per-tenant skill-dispatch turn                                     #
# --------------------------------------------------------------------------- #
class TestPerTenantSkillTurn:
    def test_skill_turn_writes_trace_with_live_marker(
        self, mock_send, fake_redis, stub_dispatch, capture_enabled
    ):
        stub_dispatch.result = SkillResult(reply_text="точный ответ", meta={"skill": "faq"})
        tenant = Tenant.objects.create(slug="rp-faq", name="RP-FAQ")

        trace = _run_per_tenant(tenant, "сколько стоит стрижка?")

        rows = list(ReplayTrace.all_tenants.all())
        assert len(rows) == 1
        row = rows[0]
        assert row.tenant_id == tenant.id
        assert row.trace_id == str(trace)
        assert row.redaction_method == REDACTION_METHOD

        steps = _steps_by_name(row)
        assert steps["inbound"]["source"] == "live_path"
        assert steps["inbound"]["surface"] == "max_per_tenant"
        assert steps["inbound"]["text"] == "сколько стоит стрижка?"
        assert steps["routing"]["skill"] == "faq"
        assert steps["pre_check"]["verdict"] == "allow"
        assert steps["post_check"]["verdict"] == "allow"
        assert steps["composer"]["final_text"] == "точный ответ"
        assert mock_send[-1]["text"] == "точный ответ"

    def test_skill_handoff_turn_captured(
        self, mock_send, fake_redis, stub_dispatch, capture_enabled
    ):
        stub_dispatch.result = SkillResult(
            reply_text="переключаю на менеджера",
            should_handoff=True,
            handoff_reason="faq_low_confidence",
            meta={"skill": "faq"},
        )
        tenant = Tenant.objects.create(slug="rp-ho", name="RP-HO")

        _run_per_tenant(tenant, "не понимаю")

        row = ReplayTrace.all_tenants.get()
        steps = _steps_by_name(row)
        assert steps["inbound"]["source"] == "live_path"
        assert steps["routing"]["skill"] == "faq"
        assert steps["composer"]["final_text"] == "переключаю на менеджера"
        assert mock_send[-1]["text"] == "переключаю на менеджера"


# --------------------------------------------------------------------------- #
# Sampling — the recorder's own gate, reused unchanged                         #
# --------------------------------------------------------------------------- #
class TestSampling:
    def test_sample_miss_writes_nothing(self, mock_send, fake_redis, stub_dispatch, settings):
        settings.REPLAY_LIVE_CAPTURE_ENABLED = True
        # Same gate the pipeline point honours: rate 0 → no row.
        settings.REPLAY_SAMPLE_RATE_TEST = 0.0
        stub_dispatch.result = SkillResult(reply_text="ответ фака", meta={"skill": "faq"})
        tenant = Tenant.objects.create(slug="rp-skip", name="RP-SKIP")

        _run_per_tenant(tenant, "вопрос")

        assert ReplayTrace.all_tenants.count() == 0
        assert mock_send[-1]["text"] == "ответ фака"


# --------------------------------------------------------------------------- #
# Best-effort: a recorder failure never breaks the turn                        #
# --------------------------------------------------------------------------- #
class TestRecorderFailureIsBestEffort:
    def test_recorder_failure_still_delivers_per_tenant_reply(
        self, mock_send, fake_redis, stub_dispatch, capture_enabled, monkeypatch
    ):
        def boom(trace_id, steps):
            raise RuntimeError("db down")

        monkeypatch.setattr("apps.replay.recorder.capture", boom)
        stub_dispatch.result = SkillResult(
            reply_text="ответ несмотря ни на что", meta={"skill": "faq"}
        )
        tenant = Tenant.objects.create(slug="rp-boom", name="RP-BOOM")

        _run_per_tenant(tenant, "вопрос")

        assert mock_send[-1]["text"] == "ответ несмотря ни на что"
        assert ReplayTrace.all_tenants.count() == 0

    def test_recorder_failure_still_delivers_global_reply(
        self, mock_send, fake_redis, mock_concierge, capture_enabled, monkeypatch
    ):
        def boom(trace_id, steps):
            raise RuntimeError("db down")

        monkeypatch.setattr("apps.replay.recorder.capture", boom)

        _run_global("привет, как дела?")

        assert mock_send[-1]["text"] == "Какая услуга интересует?"
        assert ReplayTrace.all_tenants.count() == 0
