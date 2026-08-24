"""The concierge checks its own reply before writing it down (DRF-1210).

The channel guards the send — that is what covers the deterministic
branches this function never sees. But only the concierge side can keep
the invariant :func:`generate_concierge_reply` exists for, which DRF-1354
states plainly: *what the transcript says the bot said is what the bot
said*.

The row it writes is the LLM history of the NEXT turn
(:meth:`GlobalConversationStore.load_recent_history` reads the Message
table). Written from a blocked draft, it hands the model back its own
medical claim as an established fact of the conversation, and the channel
guard then has to win again on every subsequent turn to keep it off the
screen. Stopping a sentence reaching the person while letting it reach the
prompt is not stopping it.

Running the check on both sides is free: the replacement line passes, so
the channel's call on an already-guarded reply is a no-op.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from apps.llm.protocol import CompletionResult
from apps.orchestrator import concierge
from apps.orchestrator.concierge import generate_concierge_reply
from apps.orchestrator.safety.outbound import REPLACEMENT_TEXT

_FORBIDDEN = "Я гарантирую результат уже после первого сеанса."


def _router_returning(provider: AsyncMock) -> Mock:
    router = Mock()
    router.get_provider.return_value = provider
    return router


@pytest.mark.django_db(transaction=True)
class TestConciergeGuardsItsOwnReply:
    def _bot_user_and_conversation(self, uid: str):
        from apps.conversations.services import resolve_active_global_conversation
        from apps.identity.services import resolve_or_create_global_bot_user

        bot_user = resolve_or_create_global_bot_user(
            channel="max", channel_user_id=uid, chat_id=uid
        )
        return bot_user, resolve_active_global_conversation(bot_user)

    def _answer(self, monkeypatch, text: str) -> AsyncMock:
        provider = AsyncMock()
        provider.complete.return_value = CompletionResult(text=text)
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        return provider

    def test_forbidden_draft_is_replaced_before_it_is_written_down(self, monkeypatch):
        from apps.conversations.models import Message

        self._answer(monkeypatch, _FORBIDDEN)
        bot_user, conversation = self._bot_user_and_conversation("ob-concierge-1")

        reply = generate_concierge_reply(
            "поможет ли массаж", bot_user=bot_user, conversation=conversation
        )

        assert reply.text == REPLACEMENT_TEXT
        rows = list(Message.all_tenants.filter(conversation=conversation, role="assistant"))
        assert [r.content for r in rows] == [REPLACEMENT_TEXT], (
            "the transcript — which is the next turn's prompt — must hold what was sent"
        )

    def test_action_data_goes_with_the_replaced_text(self, monkeypatch):
        self._answer(monkeypatch, _FORBIDDEN)
        bot_user, conversation = self._bot_user_and_conversation("ob-concierge-2")

        reply = generate_concierge_reply(
            "поможет ли массаж", bot_user=bot_user, conversation=conversation
        )

        assert reply.action_data in (None, {})

    def test_a_clean_answer_is_untouched(self, monkeypatch):
        from apps.conversations.models import Message

        clean = "Массаж помогает при усталости мышц. Записать вас к мастеру?"
        self._answer(monkeypatch, clean)
        bot_user, conversation = self._bot_user_and_conversation("ob-concierge-3")

        reply = generate_concierge_reply(
            "поможет ли массаж", bot_user=bot_user, conversation=conversation
        )

        assert reply.text == clean
        assert reply.persisted is True
        rows = list(Message.all_tenants.filter(conversation=conversation, role="assistant"))
        assert [r.content for r in rows] == [clean]

    def test_the_replacement_survives_a_second_pass(self):
        """The channel runs the same check again on the way out.

        That is only free if the replacement line itself passes — otherwise
        the two guards would fight and the person would read whatever the
        last one produced. Pinned here rather than assumed.
        """

        from apps.orchestrator.safety.outbound import evaluate_outbound

        assert evaluate_outbound(REPLACEMENT_TEXT).allowed
