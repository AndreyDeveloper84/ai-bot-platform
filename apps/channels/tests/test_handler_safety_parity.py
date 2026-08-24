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

### DRF-1300 — Telegram joins the parity

Telegram (DRF-848) shipped after #1053 and was left out of the gate entirely:
a crisis phrase reached the skill registry and came back as an ordinary model
answer. It is now a THIRD path in this file, not a separate test island — the
whole point of a parity guard is that adding a channel without adding it here
is exactly the failure that produced DRF-1300. :class:`TestEveryLiveHandlerIsGated`
is the structural backstop for the channel after next: it fails if any live
channel handler module stops routing through ``evaluate_inbound``.

### DRF-1210 — the same backstop, facing the other way

The inbound rule caught the channel that forgot to read the PERSON. Nothing
caught the channel that never read the ASSISTANT: ``evaluate_outbound``
shipped with DRF-1061, was wired to four surfaces where the bot speaks
first, and was on none of the three where a client is waiting for an
answer. :class:`TestEveryLiveHandlerChecksItsOwnReply` is the mirror rule —
same scan, same brain-caller set, the other direction — so the channel
after next cannot ship half-gated either.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from apps.channels.max import handler as max_handler
from apps.channels.telegram import handler as tg_handler
from apps.channels.telegram import outbound as tg_outbound
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
def tg_tenant():
    return Tenant.objects.create(
        slug="parity-tg",
        name="TG",
        telegram_bot_token="bot-token-parity",  # pragma: allowlist secret
        telegram_webhook_secret="secret-parity",  # pragma: allowlist secret
    )


@pytest.fixture
def mock_tg_post():
    """Stub only ``requests.post`` — the real outbound module still runs, so a
    reply that never reaches the wire fails the parity assert like any other
    drift."""
    from types import SimpleNamespace
    from unittest.mock import patch

    with patch.object(
        tg_outbound.requests,
        "post",
        return_value=SimpleNamespace(ok=True, status_code=200, text='{"ok":true}'),
    ) as m:
        yield m


def _tg_sent_texts(mock_tg_post) -> list[str]:
    return [
        call.kwargs["json"]["text"]
        for call in mock_tg_post.call_args_list
        if "sendMessage" in call.args[0]
    ]


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


def _tg_payload(text: str, *, user_id=333, chat_id=333, message_id=1) -> dict:
    return {
        "update_id": 900 + message_id,
        "message": {
            "message_id": message_id,
            "date": 1731320000,
            "from": {"id": user_id, "is_bot": False, "first_name": "Иван"},
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


def _run_telegram(tenant, text, *, message_id=1):
    # The webhook view enters tenant_scope and passes the tenant explicitly;
    # this mirrors that contract exactly (see apps.channels.telegram.webhook).
    with tenant_scope(tenant):
        tg_handler.handle_inbound(_tg_payload(text, message_id=message_id), tenant=tenant)


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
    def test_all_live_handlers_same_safety_reply(
        self,
        text,
        expected,
        mock_send,
        fake_redis,
        spy_discovery,
        spy_direct_show_masters,
        tg_tenant,
        mock_tg_post,
    ):
        tenant = Tenant.objects.create(slug="parity-a", name="A")

        _run_per_tenant(tenant, text)
        per_tenant_reply = mock_send[-1]["text"]

        mock_send.clear()
        _run_global(text)
        global_reply = mock_send[-1]["text"]

        # DRF-1300 — Telegram is the third live client surface.
        _run_telegram(tg_tenant, text)
        telegram_reply = _tg_sent_texts(mock_tg_post)[-1]

        # De-drift invariant: identical inbound → identical safety reply on all
        # three. A channel that answers a crisis phrase differently is a channel
        # that answers it wrongly — the copy is founder-signed for ALL of them.
        assert per_tenant_reply == expected
        assert global_reply == expected
        assert telegram_reply == expected
        assert per_tenant_reply == global_reply == telegram_reply
        # No path ran discovery on a blocked turn.
        spy_discovery.assert_not_called()
        spy_direct_show_masters.assert_not_called()

    def test_safety_turn_action_type_parity(
        self, mock_send, fake_redis, spy_discovery, tg_tenant, mock_tg_post
    ):
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

        # DRF-1300 — the Telegram crisis turn must be visible to the SAME
        # analytics query, otherwise a crisis on Telegram is invisible to the
        # people whose job is to notice it.
        _run_telegram(tg_tenant, "я думаю о суициде")
        tg = (
            Message.all_tenants.filter(tenant=tg_tenant, role="assistant")
            .order_by("-created_at", "-id")
            .first()
        )
        assert tg.action_type == "safety_pre_check"

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


# --------------------------------------------------------------------------- #
# Structural backstop — the channel after next                                #
# --------------------------------------------------------------------------- #
class TestEveryLiveHandlerIsGated:
    """DRF-1300 was not a bug in a line of code — it was a channel that shipped
    without anyone noticing the gate was missing. The behavioural tests above
    only cover the channels somebody remembered to add. This one covers the
    channel after next.

    The rule it encodes is the lesson itself: **if a module calls the brain, it
    gates first.** ``orchestrate_turn`` is the single normalized entry into the
    live brains (skill registry + concierge), and the seam is contractually
    side-effect-free — it cannot gate on the caller's behalf, so every caller
    must. A new adapter that wires up ``orchestrate_turn`` and forgets
    ``evaluate_inbound`` reproduces DRF-1300 exactly, and fails here.

    Why a source-level rule rather than another behavioural case: a channel
    nobody has written yet has no fixtures here to drive. The only thing that
    can be asserted about it on the day it lands is that it consults the gate.

    What this deliberately does NOT flag: ``apps/channels/max/salon_handler.py``
    — the salon/staff bot dispatches no skills and calls no seam. Its one LLM
    branch delegates to ``apps.master_api.services.assistant.answer_master_question``,
    which runs BOTH ``evaluate_inbound`` and ``evaluate_outbound`` itself. The
    rule tracks the brain, not the filename, so that path is correctly silent
    here instead of needing a hand-maintained exemption.
    """

    def test_every_brain_caller_gates_first(self):
        import pathlib

        apps_root = pathlib.Path(max_handler.__file__).resolve().parents[3]
        brain_callers = []
        for path in sorted(apps_root.rglob("*.py")):
            if "tests" in path.parts or path.name.startswith("test_"):
                continue
            body = path.read_text(encoding="utf-8")
            if "orchestrate_turn(" in body and "def orchestrate_turn" not in body:
                brain_callers.append((path, body))

        # Guard the guard: an empty scan would make this pass vacuously.
        assert len(brain_callers) >= 2, (
            f"expected the live brain callers, found {[str(p) for p, _ in brain_callers]}"
        )

        ungated = [
            str(path.relative_to(apps_root))
            for path, body in brain_callers
            if "evaluate_inbound" not in body
        ]
        assert ungated == [], (
            f"module(s) calling orchestrate_turn with no inbound safety gate: {ungated}. "
            "Every surface where the bot talks to a person must run "
            "apps.orchestrator.safety.gate.evaluate_inbound before the brain (DRF-1300)."
        )


class TestEveryLiveHandlerChecksItsOwnReply:
    """DRF-1210, the mirror of the class above.

    ``evaluate_inbound`` reads what the person said; nothing on the client
    path read what the bot was about to say. That is not a second copy of
    the DRF-1300 bug — it is the same bug with the arrow reversed, and it
    lasted longer precisely because the class above only ever looked one
    way.

    The rule: **if a module calls the brain, it checks what came back.**
    The brain-caller set is deliberately the SAME one — ``orchestrate_turn``
    is the single normalized entry into the live brains, the seam is
    contractually side-effect-free and so cannot check on the caller's
    behalf, and a module that calls it is by definition a module that hands
    model output to a person.

    Why the inbound gate does not already cover it, and why tightening it
    would be the wrong fix: ``gate.py`` short-circuits ``HANDOFF`` and
    ``BLOCK`` and lets ``CLARIFY`` through on purpose (gate.py:16-20). The
    outbound class — a confident medical claim, a promise made on the
    salon's behalf — arrives as an innocuous ``CLARIFY`` question. Making
    the inbound gate catch it means sending beauty queries to crisis
    screening.

    Same source-level posture as its sibling, for the same reason: a
    channel nobody has written yet has no fixtures here to drive. The only
    thing assertable about it on the day it lands is that it consults both
    halves of the gate.
    """

    def test_every_brain_caller_checks_its_reply(self):
        import pathlib

        apps_root = pathlib.Path(max_handler.__file__).resolve().parents[3]
        brain_callers = []
        for path in sorted(apps_root.rglob("*.py")):
            if "tests" in path.parts or path.name.startswith("test_"):
                continue
            body = path.read_text(encoding="utf-8")
            if "orchestrate_turn(" in body and "def orchestrate_turn" not in body:
                brain_callers.append((path, body))

        # Guard the guard: an empty scan would make this pass vacuously.
        assert len(brain_callers) >= 2, (
            f"expected the live brain callers, found {[str(p) for p, _ in brain_callers]}"
        )

        # ``guard_outbound`` is the channel-facing wrapper (it adds the single
        # PII-safe emit); ``evaluate_outbound`` is the check itself. Either
        # name satisfies the rule — what matters is that the module consults
        # the outbound half at all, not which door it comes in by.
        unchecked = [
            str(path.relative_to(apps_root))
            for path, body in brain_callers
            if "guard_outbound" not in body and "evaluate_outbound" not in body
        ]
        assert unchecked == [], (
            f"module(s) calling orchestrate_turn without checking the reply: {unchecked}. "
            "Every surface where the bot talks to a person must run "
            "apps.orchestrator.safety.gate.guard_outbound over the drafted reply "
            "before it is sent (DRF-1210)."
        )
