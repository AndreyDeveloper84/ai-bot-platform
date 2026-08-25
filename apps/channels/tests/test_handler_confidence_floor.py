"""Per-tenant confidence-floor enforcement on the live path (DRF-1209).

Ports the DEPRECATED pipeline's step 10.5
(``apps/orchestrator/pipeline.py::_confidence_floor_reason``) to the live
per-tenant MAX handler: a skill that reports a numeric ``confidence`` below
the configured threshold AND did not ask for handoff itself is escalated to
a human automatically. The AdminTask reason keeps the pipeline's diagnostic
contract — ``pipeline_confidence_floor(confidence=X, threshold=Y)`` —
because ops queries may already match on that slug.

Thresholds come from the same settings the pipeline read:
``SKILL_CONFIDENCE_HANDOFF_THRESHOLD`` (per-skill dict, ``None`` disables)
with fallback to the global ``AI_CONFIDENCE_HANDOFF_THRESHOLD``.

The whole behaviour sits behind ``SKILL_CONFIDENCE_FLOOR_LIVE_ENABLED``
(default OFF): with the flag off the handler is byte-identical to before
(see :class:`TestFlagOffCharacterization`).
"""

from __future__ import annotations

import uuid

import pytest

from apps.channels.max import handler as max_handler
from apps.conversations.models import Conversation
from apps.handoff.models import AdminTask
from apps.orchestrator.memory import short_term
from apps.skills.base import SkillResult
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Fixtures (mirrored from test_handler_safety_parity.py)                       #
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
def floor_enabled(settings):
    settings.SKILL_CONFIDENCE_FLOOR_LIVE_ENABLED = True
    settings.AI_CONFIDENCE_HANDOFF_THRESHOLD = 0.5
    settings.SKILL_CONFIDENCE_HANDOFF_THRESHOLD = {}


@pytest.fixture
def stub_dispatch(monkeypatch):
    """Stub the skill registry; each test sets ``stub_dispatch.result``."""

    class _Stub:
        result: SkillResult | None = SkillResult(reply_text="ok-reply")

    stub = _Stub()
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: stub.result)
    return stub


def _msg(text: str, *, user_id: int, chat_id: int, mid: str = "m-1") -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Иван"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _run_per_tenant(tenant, text, *, mid="cf"):
    trace = uuid.uuid4()
    with tenant_scope(tenant), trace_id_scope(str(trace)):
        max_handler.handle_max_event(_msg(text, user_id=111, chat_id=111, mid=mid), trace_id=trace)


# --------------------------------------------------------------------------- #
# Floor enforcement (flag ON)                                                  #
# --------------------------------------------------------------------------- #
class TestConfidenceFloor:
    def test_below_threshold_escalates_with_pipeline_reason(
        self, mock_send, fake_redis, stub_dispatch, floor_enabled
    ):
        # Skill answered confidently-worded text but reports confidence below
        # the global threshold and did NOT ask for handoff itself → step 10.5.
        stub_dispatch.result = SkillResult(
            reply_text="скорее всего, это поможет",
            confidence=0.32,
            meta={"skill": "faq"},
        )
        tenant = Tenant.objects.create(slug="cf-low", name="CF-LOW")

        _run_per_tenant(tenant, "что делать при боли?")

        tasks = list(AdminTask.all_tenants.all())
        assert len(tasks) == 1
        # Pipeline diagnostic contract — ops queries match on this slug.
        assert tasks[0].reason == "pipeline_confidence_floor(confidence=0.32, threshold=0.50)"
        # The skill's own outgoing text must NOT reach the user — the canned
        # handoff line goes out instead.
        assert mock_send[-1]["text"] == max_handler._HANDOFF_FALLBACK_TEXT
        assert "скорее всего" not in mock_send[-1]["text"]
        conv = Conversation.all_tenants.get(tenant=tenant)
        assert conv.state == Conversation.State.HUMAN_HANDOFF

    def test_above_threshold_normal_path(self, mock_send, fake_redis, stub_dispatch, floor_enabled):
        stub_dispatch.result = SkillResult(
            reply_text="точный ответ", confidence=0.90, meta={"skill": "faq"}
        )
        tenant = Tenant.objects.create(slug="cf-high", name="CF-HIGH")

        _run_per_tenant(tenant, "сколько стоит стрижка?")

        assert AdminTask.all_tenants.count() == 0
        assert mock_send[-1]["text"] == "точный ответ"

    def test_confidence_none_no_enforcement(
        self, mock_send, fake_redis, stub_dispatch, floor_enabled
    ):
        # None = skill didn't compute a score (Sprint 3 deterministic skills)
        # → the floor never engages; the skill owns the handoff decision.
        stub_dispatch.result = SkillResult(reply_text="детерминированный ответ", confidence=None)
        tenant = Tenant.objects.create(slug="cf-none", name="CF-NONE")

        _run_per_tenant(tenant, "привет")

        assert AdminTask.all_tenants.count() == 0
        assert mock_send[-1]["text"] == "детерминированный ответ"

    def test_skill_requested_handoff_not_duplicated(
        self, mock_send, fake_redis, stub_dispatch, floor_enabled
    ):
        # Skill asked for handoff itself AND is below the floor: exactly ONE
        # AdminTask, exactly ONE outbound line. The reason concatenates the
        # skill's own slug with the floor diagnostic — the pipeline's format.
        stub_dispatch.result = SkillResult(
            reply_text="переключаю на менеджера",
            should_handoff=True,
            handoff_reason="faq_low_confidence",
            confidence=0.20,
            meta={"skill": "faq"},
        )
        tenant = Tenant.objects.create(slug="cf-both", name="CF-BOTH")

        _run_per_tenant(tenant, "не понимаю")

        tasks = list(AdminTask.all_tenants.all())
        assert len(tasks) == 1
        assert tasks[0].reason == (
            "faq_low_confidence | pipeline_confidence_floor(confidence=0.20, threshold=0.50)"
        )
        # Skill's own handoff line is honoured (existing S1-C behaviour).
        assert mock_send[-1]["text"] == "переключаю на менеджера"

    def test_per_skill_threshold_overrides_global(
        self, mock_send, fake_redis, stub_dispatch, floor_enabled, settings
    ):
        # Per-skill dict entry beats the global default both ways.
        settings.SKILL_CONFIDENCE_HANDOFF_THRESHOLD = {"faq": 0.30}
        tenant = Tenant.objects.create(slug="cf-perskill", name="CF-PS")

        # 0.40 >= per-skill 0.30 (though < global 0.50) → no enforcement.
        stub_dispatch.result = SkillResult(
            reply_text="ok-faq", confidence=0.40, meta={"skill": "faq"}
        )
        _run_per_tenant(tenant, "вопрос один", mid="a")
        assert AdminTask.all_tenants.count() == 0
        assert mock_send[-1]["text"] == "ok-faq"

        # Same 0.40 from an unlisted skill → global 0.50 applies → floor fires.
        stub_dispatch.result = SkillResult(
            reply_text="ok-other", confidence=0.40, meta={"skill": "other"}
        )
        _run_per_tenant(tenant, "вопрос два", mid="b")
        tasks = list(AdminTask.all_tenants.all())
        assert len(tasks) == 1
        assert tasks[0].reason == "pipeline_confidence_floor(confidence=0.40, threshold=0.50)"
        assert mock_send[-1]["text"] == max_handler._HANDOFF_FALLBACK_TEXT

    def test_per_skill_none_disables_enforcement(
        self, mock_send, fake_redis, stub_dispatch, floor_enabled, settings
    ):
        # Explicit None per skill = enforcement off for that skill; it owns
        # the handoff decision even at near-zero confidence.
        settings.SKILL_CONFIDENCE_HANDOFF_THRESHOLD = {"faq": None}
        stub_dispatch.result = SkillResult(
            reply_text="ответ фака", confidence=0.05, meta={"skill": "faq"}
        )
        tenant = Tenant.objects.create(slug="cf-disabled", name="CF-DIS")

        _run_per_tenant(tenant, "вопрос")

        assert AdminTask.all_tenants.count() == 0
        assert mock_send[-1]["text"] == "ответ фака"

    def test_skill_name_falls_back_to_action_type(
        self, mock_send, fake_redis, stub_dispatch, floor_enabled, settings
    ):
        # meta has no "skill" key → the threshold lookup uses action_type,
        # same as the pipeline (the dispatcher doesn't expose the instance).
        settings.SKILL_CONFIDENCE_HANDOFF_THRESHOLD = {"faq": 0.10}
        stub_dispatch.result = SkillResult(
            reply_text="ответ", action_type="faq", confidence=0.20, meta={}
        )
        tenant = Tenant.objects.create(slug="cf-acttype", name="CF-AT")

        _run_per_tenant(tenant, "вопрос")

        # 0.20 >= per-skill 0.10 → no floor despite global 0.50.
        assert AdminTask.all_tenants.count() == 0
        assert mock_send[-1]["text"] == "ответ"


# --------------------------------------------------------------------------- #
# Flag OFF — byte-identical behaviour (characterization)                       #
# --------------------------------------------------------------------------- #
class TestFlagOffCharacterization:
    def test_flag_off_low_confidence_normal_path(
        self, mock_send, fake_redis, stub_dispatch, settings
    ):
        # Default (flag unset → off): a below-threshold result flows through
        # the ordinary reply path exactly as before DRF-1209 — no AdminTask,
        # no canned line, the skill's own text is delivered.
        settings.SKILL_CONFIDENCE_FLOOR_LIVE_ENABLED = False
        settings.AI_CONFIDENCE_HANDOFF_THRESHOLD = 0.5
        stub_dispatch.result = SkillResult(
            reply_text="неуверенный, но уходит", confidence=0.10, meta={"skill": "faq"}
        )
        tenant = Tenant.objects.create(slug="cf-off", name="CF-OFF")

        _run_per_tenant(tenant, "вопрос")

        assert AdminTask.all_tenants.count() == 0
        assert mock_send[-1]["text"] == "неуверенный, но уходит"
        conv = Conversation.all_tenants.get(tenant=tenant)
        assert conv.state != Conversation.State.HUMAN_HANDOFF

    def test_flag_unset_defaults_to_off(self, mock_send, fake_redis, stub_dispatch, settings):
        # The setting absent entirely (fresh deploy before env is wired) must
        # behave as OFF — getattr-style read, never AttributeError.
        assert not hasattr(settings, "SKILL_CONFIDENCE_FLOOR_LIVE_ENABLED") or (
            settings.SKILL_CONFIDENCE_FLOOR_LIVE_ENABLED is False
        )
        stub_dispatch.result = SkillResult(
            reply_text="обычный ответ", confidence=0.0, meta={"skill": "faq"}
        )
        tenant = Tenant.objects.create(slug="cf-unset", name="CF-UNSET")

        _run_per_tenant(tenant, "вопрос")

        assert AdminTask.all_tenants.count() == 0
        assert mock_send[-1]["text"] == "обычный ответ"
