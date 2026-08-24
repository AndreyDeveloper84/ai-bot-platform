"""DRF-1362 live: two taps update ONE message (C02 / C03 §multi-select).

The defect this closes is not that multi-select looked wrong — it did not
exist. ``ask_clarification`` rendered one row of buttons where a tap was a
final answer, so a question with several true answers had no shape on this
channel at all, and the prior estimate said it could not have one because
"MAX cannot edit a message". It can, and has since Phase 3.2A in
``legacy_maxbot``.

What is pinned here is the whole live chain, at the handler, through the real
parser, the real ladder and the real persistence:

1. two taps accumulate and the SAME message is rewritten, not followed;
2. a refused edit does not cost the tap — it lands as a new message;
3. «Продолжить» re-enters the turn as text the concierge resolves;
4. «Ни один вариант» closes without choosing;
5. every turn that is not a multi-select tap is byte-identical.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from apps.channels.max import handler as max_handler
from apps.channels.max.outbound import MaxAPIError
from apps.conversations.models import Message
from apps.orchestrator import discovery
from apps.orchestrator.discovery import DiscoveryReply
from apps.orchestrator.memory import short_term

pytestmark = pytest.mark.django_db

_QUESTION = "Что именно нужно?"
_OPTIONS = ["Маникюр", "Педикюр", "Стрижка"]


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture
def wire(monkeypatch):
    """Record every outbound, distinguishing an EDIT from a SEND."""

    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0, bot=None):
        calls.append({"kind": "send", "chat_id": chat_id, "text": text, "att": attachments})
        return {"ok": True}

    def fake_edit_or_send(*, chat_id, message_id, text, attachments=None, timeout=10.0, bot=None):
        calls.append(
            {
                "kind": "edit",
                "chat_id": chat_id,
                "mid": message_id,
                "text": text,
                "att": attachments,
            }
        )
        return True

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    monkeypatch.setattr(max_handler, "edit_message_or_send", fake_edit_or_send)
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


def _text_msg(text: str, *, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": 333, "name": "Иван"},
            "recipient": {"chat_id": 333, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _tap(payload: str, *, mid: str) -> dict:
    """A MAX callback whose keyboard hung under message ``mid``."""
    return {
        "update_type": "message_callback",
        "timestamp": 1731320000000,
        "callback": {
            "timestamp": 1731320000500,
            "callback_id": f"cb-{uuid.uuid4()}",
            "payload": payload,
            "user": {"user_id": 333, "name": "Иван", "lang": "ru"},
        },
        "message": {
            "recipient": {"chat_id": 333, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": _QUESTION, "attachments": []},
        },
    }


def _run(payload: dict) -> None:
    max_handler.handle_global_max_event(payload, trace_id=str(uuid.uuid4()))


def _spy_concierge(monkeypatch, reply: DiscoveryReply) -> MagicMock:
    spy = MagicMock(return_value=reply)
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


def _open_multiselect(monkeypatch, fake_redis) -> None:
    """Turn 1 — the model asks a choose_many question. Leaves it on record."""
    _spy_concierge(
        monkeypatch,
        discovery.render_multiselect_clarification(_QUESTION, _OPTIONS, mask=0),
    )
    _run(_text_msg("хочу что-нибудь для себя", mid="m-open"))


def _labels(call: dict) -> list[str]:
    return [b["text"] for row in call["att"][0]["payload"]["buttons"] for b in row]


# --------------------------------------------------------------------------- #
# 1. The live proof                                                            #
# --------------------------------------------------------------------------- #
class TestTwoTapsUpdateOneMessage:
    def test_the_offer_survives_the_message_that_made_it(self, wire, fake_redis, monkeypatch):
        """Without this row the next tap has a mask and no labels to index."""
        _open_multiselect(monkeypatch, fake_redis)
        row = Message.all_tenants.filter(role="assistant").order_by("-created_at").first()
        assert row is not None
        assert row.action_data["clarification"]["options"] == _OPTIONS

    def test_two_taps_accumulate_and_rewrite_the_same_message(self, wire, fake_redis, monkeypatch):
        _open_multiselect(monkeypatch, fake_redis)
        assert wire[-1]["kind"] == "send"  # the question itself is a new message

        _run(_tap("cb:clarify:tg:0:0", mid="m-open"))  # tap «Маникюр»
        first = wire[-1]
        assert first["kind"] == "edit", "a tap must rewrite the screen, not follow it"
        assert first["mid"] == "m-open"
        assert _labels(first)[0].startswith(discovery.CLARIFY_MARK_ON)
        assert _labels(first)[2].startswith(discovery.CLARIFY_MARK_OFF)

        # tap «Стрижка»; the payload carries the mask «Маникюр» left behind
        _run(_tap("cb:clarify:tg:1:2", mid="m-open"))
        second = wire[-1]
        assert second["kind"] == "edit"
        assert second["mid"] == "m-open"
        # BOTH are ticked — the second tap did not forget the first.
        marks = _labels(second)
        assert marks[0].startswith(discovery.CLARIFY_MARK_ON)
        assert marks[1].startswith(discovery.CLARIFY_MARK_OFF)
        assert marks[2].startswith(discovery.CLARIFY_MARK_ON)

        # Three outbounds total: one question, two rewrites of it. Not three
        # messages — that is the entire point of the ticket.
        assert [c["kind"] for c in wire] == ["send", "edit", "edit"]

    def test_the_original_question_is_kept_across_redraws(self, wire, fake_redis, monkeypatch):
        """A redraw that reworded the question would read as the bot changing
        its mind. The wording is the model's and is carried, not paraphrased.
        """
        _open_multiselect(monkeypatch, fake_redis)
        _run(_tap("cb:clarify:tg:0:1", mid="m-open"))
        assert wire[-1]["text"] == _QUESTION

    def test_a_raw_payload_never_enters_the_dialog_history(self, wire, fake_redis, monkeypatch):
        """DRF-988: a `cb:` string in history is what the model happily
        interprets. A redraw tap is not something a person said.
        """
        _open_multiselect(monkeypatch, fake_redis)
        _run(_tap("cb:clarify:tg:0:0", mid="m-open"))
        contents = [m.content for m in Message.all_tenants.filter(role="user")]
        assert not any(c.startswith("cb:clarify") for c in contents)


# --------------------------------------------------------------------------- #
# 2. The negative proof — a refused edit must not cost the tap                 #
# --------------------------------------------------------------------------- #
class TestRefusedEditKeepsTheTurn:
    def test_a_refused_edit_lands_as_a_new_message(self, fake_redis, monkeypatch):
        """MAX refuses edits routinely — and refuses them with HTTP 200.

        Here the real ``edit_message_or_send`` runs against a refusing
        transport, so what is pinned is the live fallback, not a mock of it.
        """
        sends: list[dict] = []
        edits: list[str] = []

        def refuse_edit(*, message_id, text=None, attachments=None, timeout=10.0, bot=None):
            edits.append(message_id)
            raise MaxAPIError(200, "edit refused (success=false)")

        def fake_send(*, chat_id, text, attachments=None, timeout=10.0, bot=None):
            sends.append({"text": text, "att": attachments})
            return {"ok": True}

        import apps.channels.max.outbound as outbound

        monkeypatch.setattr(outbound, "edit_message", refuse_edit)
        monkeypatch.setattr(outbound, "send_message", fake_send)
        monkeypatch.setattr(max_handler, "send_message", fake_send)

        _open_multiselect(monkeypatch, fake_redis)
        _run(_tap("cb:clarify:tg:0:0", mid="m-open"))

        assert edits == ["m-open"], "the edit was attempted"
        # Two sends: the question, then the redraw that could not be an edit.
        assert len(sends) == 2
        # The tap is ANSWERED — the ticked keyboard is there, just in a new
        # message. Losing the keyboard here would answer a tap with a dead end.
        labels = [b["text"] for row in sends[-1]["att"][0]["payload"]["buttons"] for b in row]
        assert labels[0].startswith(discovery.CLARIFY_MARK_ON)


# --------------------------------------------------------------------------- #
# 3. Submit — re-resolution, not a second parallel path                        #
# --------------------------------------------------------------------------- #
class TestSubmitReEntersTheTurn:
    def test_continue_sends_the_accumulated_answer_to_the_concierge(
        self, wire, fake_redis, monkeypatch
    ):
        _open_multiselect(monkeypatch, fake_redis)

        spy = _spy_concierge(monkeypatch, DiscoveryReply(text="Подобрала мастеров."))
        _run(_tap("cb:clarify:ok:5", mid="m-open"))  # bits 0 and 2

        assert spy.called, "submit must re-enter the normal turn, not shortcut it"
        passed = " ".join(str(a) for a in spy.call_args.args) + str(spy.call_args.kwargs)
        assert "Маникюр" in passed and "Стрижка" in passed
        assert "Педикюр" not in passed
        # It followed the question as a new message: an answer is a new turn.
        assert wire[-1]["kind"] == "send"

    def test_the_submitted_answer_is_recorded_as_the_user_turn(self, wire, fake_redis, monkeypatch):
        """It IS what the person said — the buttons were the keyboard."""
        _open_multiselect(monkeypatch, fake_redis)
        _spy_concierge(monkeypatch, DiscoveryReply(text="ok"))
        _run(_tap("cb:clarify:ok:5", mid="m-open"))

        contents = [m.content for m in Message.all_tenants.filter(role="user")]
        assert "Маникюр, Стрижка" in contents

    def test_continue_with_nothing_ticked_is_the_same_as_none(self, wire, fake_redis, monkeypatch):
        _open_multiselect(monkeypatch, fake_redis)
        _run(_tap("cb:clarify:ok:0", mid="m-open"))
        assert wire[-1]["text"] == discovery.CLARIFY_NONE_TEXT

    def test_none_closes_without_choosing(self, wire, fake_redis, monkeypatch):
        _open_multiselect(monkeypatch, fake_redis)
        _run(_tap("cb:clarify:no", mid="m-open"))
        assert wire[-1]["kind"] == "edit"
        assert wire[-1]["text"] == discovery.CLARIFY_NONE_TEXT
        # It invites the answer in the person's own words rather than
        # dead-ending — nothing was asked for yet.
        assert wire[-1]["att"] is None


# --------------------------------------------------------------------------- #
# 4. Stale + malformed taps                                                    #
# --------------------------------------------------------------------------- #
class TestTapWithNoQuestionBehindIt:
    def test_a_tap_with_no_offer_on_record_reads_as_stale(self, wire, fake_redis, monkeypatch):
        _spy_concierge(monkeypatch, DiscoveryReply(text="Привет!"))
        _run(_text_msg("привет", mid="m-plain"))
        _run(_tap("cb:clarify:tg:0:0", mid="m-plain"))
        assert wire[-1]["text"] == discovery.CLARIFY_STALE_TEXT

    def test_a_malformed_clarify_payload_never_reaches_the_model(
        self, wire, fake_redis, monkeypatch
    ):
        """The DRF-988 defect: a raw `cb:` string answered as if it were
        something a person said.
        """
        _open_multiselect(monkeypatch, fake_redis)
        spy = _spy_concierge(monkeypatch, DiscoveryReply(text="не должно вызваться"))
        _run(_tap("cb:clarify:tg:garbage", mid="m-open"))
        assert not spy.called
        assert wire[-1]["text"] == discovery.CLARIFY_STALE_TEXT


# --------------------------------------------------------------------------- #
# 5. Nothing else moved                                                        #
# --------------------------------------------------------------------------- #
class TestEverythingElseIsUnchanged:
    def test_an_ordinary_turn_is_still_a_plain_send(self, wire, fake_redis, monkeypatch):
        _spy_concierge(monkeypatch, DiscoveryReply(text="Вот что есть."))
        _run(_text_msg("что у вас есть", mid="m-1"))
        assert [c["kind"] for c in wire] == ["send"]

    def test_an_ordinary_clarification_is_still_a_plain_send(self, wire, fake_redis, monkeypatch):
        """confirm_one did not become a multi-select by gaining a mode."""
        _spy_concierge(
            monkeypatch,
            discovery._render_ask_clarification(
                "Какой город?", ["Москва", "Казань"], "confirm_one"
            ),
        )
        _run(_text_msg("хочу записаться", mid="m-2"))
        assert [c["kind"] for c in wire] == ["send"]
        assert _labels(wire[-1]) == ["Москва", "Казань"], "no marks, no Продолжить"


# --------------------------------------------------------------------------- #
# 6. The outbound guard (DRF-1210) still gets the last word                    #
# --------------------------------------------------------------------------- #
class TestOutboundGuardOutranksTheRedraw:
    """DRF-1210 landed the outbound check ABOVE the transcript write, because
    the transcript is the next turn's prompt. This ticket adds a send mode
    below both. The ordering must survive: a blocked reply is REPLACED, and a
    replacement is never an in-place edit.

    Editing the multi-select message would leave «тут нужен человек» sitting
    where the question was, keyboard stripped — an edited reply by another
    name, and the one form in which a stopped turn reads as a changed question.
    """

    def test_a_blocked_redraw_becomes_a_new_message_not_an_edit(
        self, wire, fake_redis, monkeypatch
    ):
        from apps.orchestrator.safety.outbound import REPLACEMENT_TEXT

        _open_multiselect(monkeypatch, fake_redis)

        # Force the guard to fire on the redraw, whatever the redraw says.
        monkeypatch.setattr(
            max_handler,
            "guard_outbound",
            lambda text, **kw: SimpleNamespace(
                blocked=True, text=REPLACEMENT_TEXT, categories=["medical"]
            ),
        )
        _run(_tap("cb:clarify:tg:0:0", mid="m-open"))

        assert wire[-1]["kind"] == "send", "a blocked reply is replaced, never edited"
        assert wire[-1]["text"] == REPLACEMENT_TEXT
        assert wire[-1]["att"] is None, "the keyboard goes with the text"

    def test_an_unblocked_redraw_is_untouched_by_the_guard(self, wire, fake_redis, monkeypatch):
        _open_multiselect(monkeypatch, fake_redis)
        _run(_tap("cb:clarify:tg:0:0", mid="m-open"))
        assert wire[-1]["kind"] == "edit"
        assert wire[-1]["text"] == _QUESTION
