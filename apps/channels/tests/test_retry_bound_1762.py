"""DRF-1762 — «Повторить» привязано к ходу: один раз и только пока он последний.

До этого кнопка несла ``cb:retry:last`` и повторяла *последнюю* реплику
человека, сколько бы раз её ни нажали и что бы человек ни написал после
сбоя: второй тап после удавшегося повтора отправлял тот же ход третий раз,
а тап по старой кнопке после новой реплики повторял не тот ход, под которым
нарисован.

Теперь payload несёт id строки человека, чей ход не состоялся. Повтор сам
ложится новой строкой, поэтому «один раз» и «только пока последний» — одно
и то же условие: строка с этим id всё ещё последняя у человека.

Обвязка — та же, что у ``test_first_contact_c01`` (там живут прежние
сторожа «AI недоступна»; они не тронуты).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from apps.channels.max import quick_actions
from apps.channels.max.quick_actions import (
    RETRY_CALLBACK,
    RETRY_LABEL,
    STALE_TAP_TEXT,
    is_retry_callback,
    retry_callback,
    retry_turn_id,
)
from apps.channels.tests import test_first_contact_c01 as _c01
from apps.orchestrator.discovery import DiscoveryReply

pytestmark = pytest.mark.django_db

_onboarding_on = _c01._onboarding_on
_no_chat_actions = _c01._no_chat_actions
sent = _c01.sent
fake_redis = _c01.fake_redis
concierge = _c01.concierge
_msg = _c01._msg
_tap = _c01._tap
_welcomed_user = _c01._welcomed_user
_buttons = _c01._buttons
_user_messages = _c01._user_messages

_OUTAGE = DiscoveryReply(
    text="Извини, у меня сейчас короткий технический сбой — отвечу через минуту.",
    outage=True,
)


def _model(monkeypatch, reply: DiscoveryReply) -> MagicMock:
    spy = MagicMock(return_value=reply)
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


def _run(payload: dict) -> None:
    _c01.max_handler.handle_global_max_event(payload)


def _last_user_row_hex(conversation) -> str:
    from apps.conversations.models import Message

    row = (
        Message.all_tenants.filter(conversation_id=conversation.id, role="user")
        .order_by("-created_at")
        .first()
    )
    assert row is not None
    return row.id.hex


class TestPayload:
    def test_bound_and_legacy_forms_both_read_as_retry(self):
        bound = retry_callback("0123456789abcdef0123456789abcdef")
        assert bound == "cb:retry:0123456789abcdef0123456789abcdef"
        assert is_retry_callback(bound)
        assert is_retry_callback(RETRY_CALLBACK)
        assert retry_turn_id(bound) == "0123456789abcdef0123456789abcdef"
        assert retry_turn_id(RETRY_CALLBACK) is None

    def test_without_turn_id_the_legacy_form_is_kept(self):
        assert retry_callback(None) == RETRY_CALLBACK
        assert (
            quick_actions.ai_unavailable_action_data()["buttons"][0]["callback"] == RETRY_CALLBACK
        )

    def test_foreign_text_is_not_a_retry(self):
        assert not is_retry_callback("cb:retry:")
        assert not is_retry_callback("cb:retry:not-a-uuid")
        assert not is_retry_callback("повторить")


class TestBoundRetryOnTheWire:
    def test_outage_button_carries_the_failed_turns_row(self, sent, fake_redis, monkeypatch):
        _model(monkeypatch, _OUTAGE)
        _, conversation = _welcomed_user(76001)
        _run(_msg(text="Хочу снять напряжение", user_id=76001, mid="r-1"))

        buttons = _buttons(sent[-1]["attachments"])
        assert [b["text"] for b in buttons] == [RETRY_LABEL]
        assert buttons[0]["payload"] == retry_callback(_last_user_row_hex(conversation))

    def test_retry_replays_once_and_the_second_tap_is_stale(self, sent, fake_redis, monkeypatch):
        _model(monkeypatch, _OUTAGE)
        _, conversation = _welcomed_user(76002)
        _run(_msg(text="Беспокоят отёки", user_id=76002, mid="r-2"))
        payload = _buttons(sent[-1]["attachments"])[0]["payload"]

        recovered = _model(monkeypatch, DiscoveryReply(text="Расскажи чуть подробнее?"))
        _run(_tap(payload=payload, user_id=76002, callback_id="retry-a"))
        assert recovered.call_args.args[0] == "Беспокоят отёки"
        assert sent[-1]["text"] == "Расскажи чуть подробнее?"
        # Повтор — это «отправь то же ещё раз»: он лёг новой строкой.
        assert _user_messages(conversation) == ["Беспокоят отёки", "Беспокоят отёки"]

        # Второй тап по той же кнопке — ход уже не последний: честный отказ,
        # модель не вызвана, третьей строки нет.
        recovered.reset_mock()
        _run(_tap(payload=payload, user_id=76002, callback_id="retry-b"))
        assert sent[-1]["text"] == STALE_TAP_TEXT
        recovered.assert_not_called()
        assert _user_messages(conversation) == ["Беспокоят отёки", "Беспокоят отёки"]

    def test_old_button_after_a_new_message_does_not_replay_the_new_one(
        self, sent, fake_redis, monkeypatch
    ):
        """Кнопка повторяет ход, под которым нарисована, — не «что угодно последнее»."""
        _model(monkeypatch, _OUTAGE)
        _, conversation = _welcomed_user(76003)
        _run(_msg(text="Хочу выглядеть свежее", user_id=76003, mid="r-3"))
        stale_payload = _buttons(sent[-1]["attachments"])[0]["payload"]

        recovered = _model(monkeypatch, DiscoveryReply(text="Поняла."))
        _run(_msg(text="А ещё болит спина", user_id=76003, mid="r-4"))
        recovered.reset_mock()

        _run(_tap(payload=stale_payload, user_id=76003, callback_id="retry-c"))
        assert sent[-1]["text"] == STALE_TAP_TEXT
        recovered.assert_not_called()
        assert _user_messages(conversation) == ["Хочу выглядеть свежее", "А ещё болит спина"]

    def test_a_second_outage_rebinds_the_button_to_the_new_row(self, sent, fake_redis, monkeypatch):
        """Сбой на самом повторе — новая кнопка, привязанная к новой строке, работает."""
        _model(monkeypatch, _OUTAGE)
        _, conversation = _welcomed_user(76004)
        _run(_msg(text="Нужен совет по уходу", user_id=76004, mid="r-5"))
        first = _buttons(sent[-1]["attachments"])[0]["payload"]
        _run(_tap(payload=first, user_id=76004, callback_id="retry-d"))
        second = _buttons(sent[-1]["attachments"])[0]["payload"]
        assert second != first
        assert second == retry_callback(_last_user_row_hex(conversation))

        recovered = _model(monkeypatch, DiscoveryReply(text="Вот что можно."))
        _run(_tap(payload=second, user_id=76004, callback_id="retry-e"))
        assert recovered.call_args.args[0] == "Нужен совет по уходу"

    def test_legacy_last_form_still_replays(self, sent, fake_redis, concierge):
        """Клавиатуры, нарисованные до привязки, продолжают работать как раньше."""
        from apps.conversations.services import record_global_message

        _, conversation = _welcomed_user(76005)
        record_global_message(conversation, role="user", content="Хочу выглядеть свежее")
        _run(_tap(payload=RETRY_CALLBACK, user_id=76005, callback_id="retry-f"))
        assert concierge.call_args.args[0] == "Хочу выглядеть свежее"
