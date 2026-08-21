"""Cross-handler safety PARITY + regression (#1053 de-drift, S1-D).

The two live MAX handlers evolved separately — per-tenant `_handle_max_event_inner`
(skill dispatch) and global `_handle_global_max_event_inner` (discovery). S1-B
(pre_check) + S1-C (should_handoff) removed the functional drift by routing BOTH
through the SAME shared helpers (`evaluate_inbound`, `_emit_safety_shortcircuit`,
`_dispatch_skill_handoff`). This module is the guard-rail that keeps them
de-drifted: it drives the SAME inbound phrase through BOTH handlers and asserts
they reach the SAME safety verdict, so a future change to one path that isn't
mirrored in the other fails CI.

It also pins the two INTENTIONAL divergences (documented in the handler), so they
can't silently regress:
  * global (tenant-less) path creates NO AdminTask on a red-flag (Variant A,
    founder 2026-07-03 / #1076) — the per-tenant path's AdminTask/handoff is a
    separate ticket (S1-C, #1047);
  * the per-tenant safety gate has a HUMAN_HANDOFF barge-guard (don't speak over
    an operator); the global path mutes earlier, via ``global_handoff_muted``
    (DRF-1015) — including while the handoff task sits in a salon's queue.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from apps.channels.max import handler as max_handler
from apps.conversations.models import Conversation, Message
from apps.handoff.models import AdminTask
from apps.orchestrator.memory import short_term
from apps.orchestrator.safety.gate import BLOCK_REPLY_TEXT, CRISIS_REPLY_TEXT
from apps.tenancy.context import current_tenant, tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
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
def spy_discovery(monkeypatch):
    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Какая услуга интересует?"))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


@pytest.fixture
def spy_direct_show_masters(monkeypatch):
    # DRF-1102 — the new deterministic branch (looks_like_booking_request →
    # show masters, skipping the concierge LLM). A happy phrase now reaches
    # ONE of {generate_concierge_reply, generate_direct_show_masters_reply}
    # depending on whether it names a service — never neither, and a
    # safety-blocked phrase must reach neither.
    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Вот мастера, которые могут подойти:"))
    monkeypatch.setattr(max_handler, "generate_direct_show_masters_reply", spy)
    return spy


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


def _run_per_tenant(tenant, text, *, mid="pt"):
    trace = uuid.uuid4()
    with tenant_scope(tenant), trace_id_scope(str(trace)):
        max_handler.handle_max_event(_msg(text, user_id=111, chat_id=111, mid=mid), trace_id=trace)


def _run_global(text, *, mid="gl"):
    # Call the inner entrypoint directly (as the other global tests do); the
    # GlobalMaxHandler worker wrapper is thin + covered by test_global_max_handler.
    # It runs at current_tenant()=None via the sentinel services.
    max_handler.handle_global_max_event(
        _msg(text, user_id=222, chat_id=222, mid=mid), trace_id=str(uuid.uuid4())
    )


# --------------------------------------------------------------------------- #
# Parity — same phrase, same safety verdict on BOTH handlers                   #
# --------------------------------------------------------------------------- #
_SAFETY_CASES = [
    ("я думаю о суициде", CRISIS_REPLY_TEXT),
    ("хочу умереть", CRISIS_REPLY_TEXT),
    ("посоветуйте ибупрофен от боли", BLOCK_REPLY_TEXT),
    ("как подать в суд на салон", BLOCK_REPLY_TEXT),
]


class TestSafetyParity:
    @pytest.mark.parametrize("text,expected", _SAFETY_CASES)
    def test_both_handlers_same_safety_reply(
        self, text, expected, mock_send, fake_redis, spy_discovery, spy_direct_show_masters
    ):
        tenant = Tenant.objects.create(slug="parity-a", name="A")

        _run_per_tenant(tenant, text)
        per_tenant_reply = mock_send[-1]["text"]

        mock_send.clear()
        _run_global(text)
        global_reply = mock_send[-1]["text"]

        # De-drift invariant: identical inbound → identical safety reply on both.
        assert per_tenant_reply == expected
        assert global_reply == expected
        assert per_tenant_reply == global_reply
        # Neither path ran discovery on a blocked turn.
        spy_discovery.assert_not_called()
        spy_direct_show_masters.assert_not_called()

    def test_safety_turn_action_type_parity(self, mock_send, fake_redis, spy_discovery):
        # De-drift (CR F1): the safety turn must be tagged action_type=
        # "safety_pre_check" on BOTH paths, else a global crisis turn is invisible
        # to a Message.filter(action_type="safety_pre_check") analytics query.
        from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG

        tenant = Tenant.objects.create(slug="parity-at", name="AT")
        _run_per_tenant(tenant, "я думаю о суициде")
        _run_global("я думаю о суициде")

        pt = (
            Message.all_tenants.filter(tenant=tenant, role="assistant")
            .order_by("-created_at", "-id")
            .first()
        )
        gl = (
            Message.all_tenants.filter(
                role="assistant", conversation__tenant__slug=GLOBAL_BOT_TENANT_SLUG
            )
            .order_by("-created_at", "-id")
            .first()
        )
        assert pt.action_type == "safety_pre_check"
        assert gl.action_type == "safety_pre_check"

    @pytest.mark.parametrize(
        "text",
        [
            "хочу записаться на массаж",
            "маникюр в Пензе",
            "сколько стоит стрижка",
            "почему болит спина после массажа",  # CLARIFY verdict — must proceed, not block
        ],
    )
    def test_happy_phrase_blocked_on_neither(
        self, text, mock_send, fake_redis, spy_discovery, spy_direct_show_masters, monkeypatch
    ):
        # The gate must ALLOW these (handler proceeds past the safety short-circuit
        # on both paths). Stub the per-tenant skill dispatch to a deterministic
        # reply so the assertion isn't coupled to which skill runs (FAQ would make
        # a real LLM call — flaky under the SQLite test DB).
        from apps.skills.base import SkillResult

        monkeypatch.setattr(
            "apps.skills.registry.dispatch", lambda ctx: SkillResult(reply_text="ok-reply")
        )
        tenant = Tenant.objects.create(slug="parity-h", name="H")

        _run_per_tenant(tenant, text)
        # Not short-circuited to a safety reply → the gate allowed it through.
        assert mock_send[-1]["text"] not in (CRISIS_REPLY_TEXT, BLOCK_REPLY_TEXT)
        assert mock_send[-1]["text"] == "ok-reply"

        mock_send.clear()
        _run_global(text)
        assert mock_send[-1]["text"] not in (CRISIS_REPLY_TEXT, BLOCK_REPLY_TEXT)
        # Global happy path reached the normal (non-safety) reply pipeline —
        # DRF-1102 split that pipeline in two: a general booking/service phrase
        # (every case here names one) now short-circuits straight to
        # generate_direct_show_masters_reply instead of the concierge LLM, so
        # exactly one of the two must have run, never neither.
        assert spy_discovery.called != spy_direct_show_masters.called
        assert spy_discovery.call_count + spy_direct_show_masters.call_count == 1


# --------------------------------------------------------------------------- #
# Pinned intentional divergences                                              #
# --------------------------------------------------------------------------- #
class TestIntentionalDivergence:
    def test_global_red_flag_creates_no_admin_task(self, mock_send, fake_redis, spy_discovery):
        # Variant A (#1076): the tenant-less path never creates an AdminTask.
        _run_global("я думаю о суициде")
        # Assert the safety reply DID fire (else "no task" would pass trivially).
        assert mock_send[-1]["text"] == CRISIS_REPLY_TEXT
        assert AdminTask.all_tenants.count() == 0
        assert current_tenant() is None

    def test_per_tenant_red_flag_also_creates_no_admin_task_in_s1b(self, mock_send, fake_redis):
        # S1-B is detection-only on BOTH paths — the per-tenant AdminTask is S1-C
        # and fires on skill should_handoff, NOT on a pre_check red-flag. So a raw
        # red-flag phrase must NOT create a task on either path (parity).
        tenant = Tenant.objects.create(slug="parity-nt", name="NT")
        _run_per_tenant(tenant, "я думаю о суициде")
        assert AdminTask.all_tenants.count() == 0
        conv = Conversation.all_tenants.get(tenant=tenant)
        # Detection reply persisted with the safety action_type.
        last = (
            Message.all_tenants.filter(conversation=conv, role="assistant")
            .order_by("-created_at", "-id")
            .first()
        )
        assert last.action_type == "safety_pre_check"

    def test_per_tenant_gate_does_not_barge_operator(self, mock_send, fake_redis):
        # Per-tenant gate: the HUMAN_HANDOFF guard keeps the gate silent while an
        # operator is driving. (The global path now mutes too — earlier, via
        # global_handoff_muted / DRF-1015 — covered by test_global_human_handoff.)
        tenant = Tenant.objects.create(slug="parity-hh", name="HH")
        _run_per_tenant(tenant, "привет", mid="a")
        conv = Conversation.all_tenants.get(tenant=tenant)
        Conversation.all_tenants.filter(pk=conv.pk).update(state=Conversation.State.HUMAN_HANDOFF)
        mock_send.clear()

        _run_per_tenant(tenant, "я думаю о суициде", mid="b")
        assert mock_send == []  # silent — no crisis barge over the operator
