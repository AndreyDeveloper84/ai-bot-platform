"""DRF-1760 — «Не знаю» на канале: тап отвечает сам и закрывает открытый вопрос.

Живое доказательство через ``handle_global_max_event``: после free-вопроса
(клавиатура из одной кнопки) тап ``cb:clarify:dk`` отвечает текстом
``CLARIFY_DONT_KNOW_TEXT``, в модель не идёт, а открытый вопрос DRF-1779
снят и ответ «Не знаю» лежит в ``last_answered`` — чтобы следующая реплика
не читалась как второй ответ на тот же вопрос.

Обвязка — та же, что у ``test_handler_clarification_multiselect``.
"""

from __future__ import annotations

import pytest

from apps.channels.tests import test_handler_clarification_multiselect as _ms
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.orchestrator import discovery, open_question

pytestmark = pytest.mark.django_db

# Та же обвязка, что у multiselect-набора, включая autouse-фикстуры.
wire = _ms.wire
fake_redis = _ms.fake_redis
_no_chat_action = _ms._no_chat_action
_strict = _ms._strict
_QUESTION = _ms._QUESTION
_run = _ms._run
_spy_concierge = _ms._spy_concierge
_tap = _ms._tap
_text_msg = _ms._text_msg


def _conversation():
    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id="333", chat_id="333"
    )
    return resolve_active_global_conversation(bot_user)


def _open_free_question(monkeypatch) -> None:
    """Ход 1 — модель задала free-вопрос; на записи одна кнопка «Не знаю»."""
    _spy_concierge(
        monkeypatch, discovery._render_ask_clarification(_QUESTION, [], offer_dont_know=True)
    )
    _run(_text_msg("хочу что-нибудь для себя", mid="m-open"))
    # Консьерж подменён целиком, так что вопрос открываем так, как открыл бы он.
    open_question.open_question(_conversation(), "ask_clarification", asked_text=_QUESTION)


class TestSelectedCountOnTheWire:
    def test_two_taps_show_one_honest_count(self, wire, fake_redis, monkeypatch):
        """Ход 2 читает вопрос из строки хода 1 — со счётчиком в тексте
        второй тап дал бы «Выбрано: 1 … Выбрано: 2».
        """
        _ms._open_multiselect(monkeypatch, fake_redis)
        _run(_tap("cb:clarify:tg:0:0", mid="m-open"))
        assert wire[-1]["text"] == f"{_QUESTION}\n\nВыбрано: 1"
        _run(_tap("cb:clarify:tg:1:2", mid="m-open"))
        assert wire[-1]["text"] == f"{_QUESTION}\n\nВыбрано: 2"
        assert wire[-1]["text"].count("Выбрано") == 1


class TestDontKnowOnTheWire:
    def test_free_question_shows_the_dont_know_button(self, wire, fake_redis, monkeypatch):
        _open_free_question(monkeypatch)
        assert wire[-1]["kind"] == "send"
        assert wire[-1]["text"] == _QUESTION
        buttons = [b for row in wire[-1]["att"][0]["payload"]["buttons"] for b in row]
        assert [b["text"] for b in buttons] == [discovery.CLARIFY_DONT_KNOW_LABEL]
        assert buttons[0]["payload"] == discovery.CLARIFY_DONT_KNOW_CALLBACK

    def test_tap_answers_without_the_model_and_closes_the_question(
        self, wire, fake_redis, monkeypatch
    ):
        _open_free_question(monkeypatch)
        assert open_question.pending_question(_conversation()) is not None

        spy = _spy_concierge(monkeypatch, discovery.DiscoveryReply(text="не должно вызваться"))
        _run(_tap(discovery.CLARIFY_DONT_KNOW_CALLBACK, mid="m-open"))

        assert wire[-1]["text"] == discovery.CLARIFY_DONT_KNOW_TEXT
        assert wire[-1]["text"] != discovery.CLARIFY_STALE_TEXT
        spy.assert_not_called()

        conversation = _conversation()
        assert open_question.pending_question(conversation) is None
        answered = open_question._state(conversation).get(open_question.ANSWERED_KEY)
        assert isinstance(answered, dict)
        assert answered["question_id"] == "ask_clarification"
        assert answered["answer_text"] == discovery.CLARIFY_DONT_KNOW_LABEL
