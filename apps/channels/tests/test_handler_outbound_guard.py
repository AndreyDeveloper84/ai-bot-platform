"""The client path checks what it is about to SAY (DRF-1210).

``evaluate_inbound`` reads the person. Until this ticket nothing on the
client path read the assistant. ``evaluate_outbound`` had existed since
DRF-1061 and was wired to four surfaces — the master assistant, both
proactive senders, master deactivation — every one of them a place where
the bot speaks first and nobody is waiting. The one surface it was NOT on
is the one with a client sitting at the other end.

### Why the inbound gate was never going to cover this

``gate.py`` short-circuits ``HANDOFF`` and ``BLOCK`` only; ``CLARIFY``
passes on purpose (``gate.py:16-20`` — on a beauty marketplace «почему
болит спина» is more likely a massage query than a safety event). A
confident medical claim in the ANSWER arrives as exactly that innocuous
``CLARIFY`` question. The class the outbound check exists for is the class
the inbound check is deliberately built not to catch, so the two are not
substitutes and tightening one is not a fix for the other.

### What these tests pin

1. a live turn where the model says something forbidden — the person gets
   the replacement, and the bus says which SHAPE fired;
2. the transcript ends up holding what the person read, not what the model
   drafted, because the transcript is the next turn's prompt;
3. the founder-approved crisis reply is exempt, and only it;
4. an ordinary turn is byte-identical — including its keyboard.

The structural backstop and the measured false-positive budget live in
``test_handler_safety_parity.py`` and ``test_outbound_guard_budget.py``.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from apps.channels.max import handler as max_handler
from apps.channels.telegram import handler as tg_handler
from apps.channels.telegram import outbound as tg_outbound
from apps.conversations.models import Message
from apps.events.models import Event
from apps.orchestrator.discovery import DiscoveryReply
from apps.orchestrator.memory import short_term
from apps.orchestrator.safety.gate import CRISIS_REPLY_TEXT, OUTBOUND_ACTION_TYPE
from apps.orchestrator.safety.outbound import REPLACEMENT_TEXT
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

_BLOCK_EVENT = "safety.outbound_blocked"

#: One sentence per shape ``evaluate_outbound`` looks for. Written the way a
#: model actually drifts — a confident claim, a promise made on the salon's
#: behalf, a phone number nobody asked it to hand out (DRF-1039).
_FORBIDDEN = {
    "medical": "У вас аллергия на этот материал, это точно.",
    "promise": "Я гарантирую результат уже после первого сеанса.",
    "contact": "Позвоните мастеру напрямую: +7 999 123-45-67.",
}


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture
def mock_send(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text, "attachments": attachments})
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


def _msg(text: str, *, user_id: int, chat_id: int, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Иван"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _run_global(text: str, *, mid: str) -> None:
    max_handler.handle_global_max_event(
        _msg(text, user_id=222, chat_id=222, mid=mid), trace_id=str(uuid.uuid4())
    )


def _spy_concierge(monkeypatch, reply: DiscoveryReply) -> MagicMock:
    """Make the model say ``reply``.

    Patched at ``apps.orchestrator.concierge.generate_concierge_reply`` — the
    symbol ``turn_seam`` imports — so the whole live chain below the brain
    runs for real: the seam, the handler's persistence, the send.
    """

    spy = MagicMock(return_value=reply)
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


# --------------------------------------------------------------------------- #
# 1. The live turn                                                             #
# --------------------------------------------------------------------------- #
class TestForbiddenReplyNeverReachesTheClient:
    @pytest.mark.parametrize("category,text", sorted(_FORBIDDEN.items()))
    def test_blocked_and_audited(self, category, text, mock_send, fake_redis, monkeypatch):
        _spy_concierge(monkeypatch, DiscoveryReply(text=text))

        _run_global("расскажи про массаж", mid=f"ob-{category}")

        # The person read the replacement, not the draft.
        assert len(mock_send) == 1
        assert mock_send[0]["text"] == REPLACEMENT_TEXT

        # The bus says WHICH shape fired — and never the sentence that fired
        # it, which is by definition the part we decided nobody should read.
        events = list(Event.objects.filter(event_type=_BLOCK_EVENT))
        assert len(events) == 1, "a block must be visible in the bus exactly once"
        payload = events[0].payload
        assert category in payload["categories"]
        assert payload["surface"] == "max"
        assert payload["text_len"] == len(text)
        assert text not in str(payload)

    def test_transcript_holds_what_the_person_read(self, mock_send, fake_redis, monkeypatch):
        """The transcript is the next turn's prompt (DRF-1354).

        A row written from the blocked draft would hand the model its own
        medical claim back as an established fact of the conversation, and
        the guard would then have to win again every turn to keep it off the
        screen. Stopping the sentence reaching the person while letting it
        reach the prompt is not stopping it.
        """

        _spy_concierge(monkeypatch, DiscoveryReply(text=_FORBIDDEN["medical"]))

        _run_global("почему чешется после процедуры", mid="ob-transcript")

        rows = list(Message.all_tenants.filter(role="assistant").order_by("created_at"))
        assert rows, "the assistant turn must be on record"
        assert all(_FORBIDDEN["medical"] not in (r.content or "") for r in rows)
        assert rows[-1].content == REPLACEMENT_TEXT
        assert rows[-1].action_type == OUTBOUND_ACTION_TYPE

    def test_keyboard_goes_with_the_text(self, mock_send, fake_redis, monkeypatch):
        """A replaced reply is REPLACED, not edited (``outbound.py``).

        Master cards left hanging under «тут нужен человек» would be an
        edited reply by another name — and the cards are what the blocked
        sentence was selling.
        """

        _spy_concierge(
            monkeypatch,
            DiscoveryReply(
                text=_FORBIDDEN["promise"],
                action_data={
                    "buttons": [{"label": "Записаться", "callback": "cb:discover:book:1"}]
                },
            ),
        )

        _run_global("хочу записаться", mid="ob-kb")

        assert mock_send[0]["text"] == REPLACEMENT_TEXT
        assert not mock_send[0]["attachments"]


# --------------------------------------------------------------------------- #
# 2. The negative — an ordinary turn does not notice the guard                 #
# --------------------------------------------------------------------------- #
class TestOrdinaryTurnIsUnchanged:
    def test_clean_reply_passes_byte_identical(self, mock_send, fake_redis, monkeypatch):
        clean = "Массаж спины у Дениса стоит 3 500 ₽ за час. Записать вас на завтра в 18:00?"
        buttons = {"buttons": [{"label": "Завтра 18:00", "callback": "cb:discover:book:7"}]}
        _spy_concierge(monkeypatch, DiscoveryReply(text=clean, action_data=buttons))

        _run_global("сколько стоит массаж спины", mid="ob-clean")

        assert mock_send[0]["text"] == clean
        assert mock_send[0]["attachments"], "a clean reply keeps its keyboard"
        assert not Event.objects.filter(event_type=_BLOCK_EVENT).exists()


# --------------------------------------------------------------------------- #
# 3. The one exemption                                                         #
# --------------------------------------------------------------------------- #
class TestCrisisReplyIsExempt:
    def test_crisis_reply_is_delivered_verbatim(self, mock_send, fake_redis):
        """The inbound gate's canned crisis copy is founder-approved and is
        the sole live self-harm response (``gate.py``: «change it only via a
        new founder sign-off»). A regex deciding on its own to swap a
        helpline number for «спросите администратора салона» is the one
        failure here that could cost more than it saves.
        """

        _run_global("я думаю о суициде", mid="ob-crisis")

        assert mock_send[0]["text"] == CRISIS_REPLY_TEXT
        assert not Event.objects.filter(event_type=_BLOCK_EVENT).exists()

    def test_the_crisis_copy_would_survive_the_guard_anyway(self):
        """Belt and braces: the exemption exists so the copy cannot be
        silently overridden, not because the copy is dirty. If a future edit
        to that text starts tripping the guard, this says so here rather than
        in a production trace nobody reads.
        """

        from apps.orchestrator.safety.outbound import evaluate_outbound

        assert evaluate_outbound(CRISIS_REPLY_TEXT).allowed


# --------------------------------------------------------------------------- #
# 4. The other two live client surfaces                                        #
# --------------------------------------------------------------------------- #
class TestEveryClientSurface:
    def test_per_tenant_max_path_is_guarded(self, mock_send, fake_redis, monkeypatch):
        from apps.skills.base import SkillResult

        tenant = Tenant.objects.create(slug="ob-pt", name="PT")
        monkeypatch.setattr(
            "apps.skills.registry.dispatch",
            lambda ctx: SkillResult(reply_text=_FORBIDDEN["promise"], action_type="faq"),
        )
        trace = uuid.uuid4()
        with tenant_scope(tenant), trace_id_scope(str(trace)):
            max_handler.handle_max_event(
                _msg("верните деньги", user_id=111, chat_id=111, mid="ob-pt"), trace_id=trace
            )

        assert mock_send[0]["text"] == REPLACEMENT_TEXT
        assert Event.objects.filter(event_type=_BLOCK_EVENT).exists()

    def test_telegram_path_is_guarded(self, fake_redis, monkeypatch):
        """DRF-1300 was a channel that shipped without the INBOUND gate.
        Leaving Telegram out of the outbound one would be the same shape a
        second time — and the structural backstop would say so.
        """

        from types import SimpleNamespace
        from unittest.mock import patch

        from apps.skills.base import SkillResult

        tenant = Tenant.objects.create(
            slug="ob-tg",
            name="TG",
            telegram_bot_token="bot-token-ob",  # pragma: allowlist secret
            telegram_webhook_secret="secret-ob",  # pragma: allowlist secret
        )
        monkeypatch.setattr(
            "apps.skills.registry.dispatch",
            lambda ctx: SkillResult(reply_text=_FORBIDDEN["contact"], action_type="faq"),
        )
        payload = {
            "update_id": 901,
            "message": {
                "message_id": 1,
                "date": 1731320000,
                "from": {"id": 333, "is_bot": False, "first_name": "Иван"},
                "chat": {"id": 333, "type": "private"},
                "text": "дайте телефон мастера",
            },
        }
        with patch.object(
            tg_outbound.requests,
            "post",
            return_value=SimpleNamespace(ok=True, status_code=200, text='{"ok":true}'),
        ) as post:
            with tenant_scope(tenant):
                tg_handler.handle_inbound(payload, tenant=tenant)

        sent = [
            call.kwargs["json"]["text"]
            for call in post.call_args_list
            if "sendMessage" in call.args[0]
        ]
        assert sent == [REPLACEMENT_TEXT]
        assert Event.objects.filter(event_type=_BLOCK_EVENT, payload__surface="telegram").exists()
