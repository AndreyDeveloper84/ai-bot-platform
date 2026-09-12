"""``dialog_transcript`` — след у каждой реплики бота (DRF-1754).

Приёмочный материал — три реплики владельца 12.09 03:01–03:09 UTC. На пилоте
все четыре ответа бота легли с ``action_type=health_screening`` и
``outcome=success``, хотя на экране были «Не разобрала» и «Сейчас проверю»:
модель выбирала инструмент, инструмент отказывал, ответом уходила проза
рядом с ним — и ни одна строка базы об этом не говорила.

Здесь тот же ход прогоняется через НАСТОЯЩИЙ канал (обвязка — как в
``test_degraded_lines_1489``): подменена только модель (каждый раз выбирает
``health_screening``) и сам инструмент (отказывает). Проверяется не текст
ответа, а то, что расшифровка после этого ГОВОРИТ, почему ответ был таким.
"""

from __future__ import annotations

import uuid
from io import StringIO
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.channels.max import handler as max_handler
from apps.conversations.management.commands.dialog_transcript import parse_since, short_hash
from apps.conversations.models import Message
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge
from apps.orchestrator.llm import templates
from apps.orchestrator.memory import short_term
from apps.replay.models import ReplayTrace
from apps.tenancy.context import trace_id_scope

# ``transaction=True`` — как в test_degraded_lines_1489: ход консьержа пишет в
# БД из другого потока (``asyncio.run``), сессия в одной транзакции его не видит.
pytestmark = pytest.mark.django_db(transaction=True)

OWNER_REPLIES = (
    "1. Спина, 2. После работы",
    "ну я назвал тебе конкретное место - спина",
    "и?",
)


@pytest.fixture(autouse=True)
def _channel_harness(settings, monkeypatch):
    settings.GLOBAL_BOT_ONBOARDING = True
    settings.REPLAY_LIVE_CAPTURE_ENABLED = True
    settings.REPLAY_SAMPLE_RATE_TEST = 1.0
    monkeypatch.setattr(
        "apps.channels.max.outbound.send_chat_action", lambda **kwargs: {"ok": True}
    )
    monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock())
    monkeypatch.setattr(max_handler, "maybe_weave_question", lambda _c, _b, reply: reply)
    monkeypatch.setattr(max_handler, "send_message", lambda **kwargs: {"ok": True})
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    monkeypatch.setattr(short_term, "_redis_client", lambda: _FakeRedis())


def _health_screening_call() -> CompletionResult:
    return CompletionResult(
        text="",
        tool_calls=[ToolCall(id="c1", name="health_screening", arguments={"text": "спина"})],
        prompt_tokens=30,
        completion_tokens=6,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="tool_calls",
    )


def _model_picks_health_screening(monkeypatch) -> None:
    provider = AsyncMock()
    provider.complete.return_value = _health_screening_call()
    router = Mock()
    router.get_provider.return_value = provider
    monkeypatch.setattr(concierge, "get_router", lambda: router)
    monkeypatch.setattr(concierge, "execute_nutrition_tool", lambda *a, **kw: None)


def _welcomed_user(user_id: int):
    from django.utils import timezone

    from apps.consent.services import record_global_consent

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), chat_id="8899"
    )
    bot_user.welcomed_at = timezone.now()
    bot_user.save(update_fields=["welcomed_at"])
    record_global_consent(
        bot_user,
        consent_type="personal_data",
        source="test:drf1754",
        document_version="welcome-s2-v1",
    )
    bot_user.refresh_from_db()
    return bot_user, resolve_active_global_conversation(bot_user)


def _msg(*, text: str, user_id: int, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Андрей"},
            "recipient": {"chat_id": 8899, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _run(*args: str) -> str:
    out = StringIO()
    call_command("dialog_transcript", *args, stdout=out, stderr=StringIO())
    return out.getvalue()


def _owner_dialog(monkeypatch, *, user_id: int = 71754):
    """Три реплики владельца через настоящий канал — каждая в своём trace_id.

    На пилоте trace_id ставит воркер (``apps/workers/base.py``): входит в
    ``trace_id_scope`` (его читает ``record_message``) и передаёт тот же id
    хендлеру явно (его читают метрика и replay). Здесь — то же самое руками;
    без этого у реплик нет trace_id, и следу не к чему привязаться (команда
    так и печатает).
    """
    _model_picks_health_screening(monkeypatch)
    _bot_user, conversation = _welcomed_user(user_id)
    for index, text in enumerate(OWNER_REPLIES):
        trace = uuid.uuid4()
        with trace_id_scope(str(trace)):
            max_handler.handle_global_max_event(
                _msg(text=text, user_id=user_id, mid=f"m{index}"), trace_id=trace
            )
    return conversation


class TestOwnerRepliesLeaveATrace:
    def test_every_bot_reply_says_which_tool_declined(self, monkeypatch):
        conversation = _owner_dialog(monkeypatch)
        replies = Message.all_tenants.filter(conversation=conversation, role="assistant")
        assert replies.count() == len(OWNER_REPLIES)
        # Экран пилота 12.09 воспроизведён: инструмент выбран, ответ — «Не разобрала».
        assert {m.content for m in replies} == {templates.NOT_PARSED_RU}
        assert {m.action_type for m in replies} == {"health_screening"}
        assert ReplayTrace.all_tenants.count() == len(OWNER_REPLIES)

        text = _run("--conv", str(conversation.id))

        trace_lines = [line for line in text.splitlines() if line.strip().startswith("след:")]
        assert len(trace_lines) == len(OWNER_REPLIES), text
        for line in trace_lines:
            assert "action=health_screening" in line
            assert "llm: concierge#1 gpt-4o-mini success" in line
            # То, чего на пилоте не было видно: инструмент ОТКАЗАЛ, а не «success».
            assert "tools=health_screening→declined_not_parsed" in line
            assert "pre=" in line and "post=" in line
            # Отсутствие доезжает отсутствием — по имени.
            assert "safety_state: нет" in line
            assert "dr_verdict: нет" in line

    def test_replies_are_printed_with_their_trace_right_under_them(self, monkeypatch):
        conversation = _owner_dialog(monkeypatch)
        lines = _run("--conv", str(conversation.id)).splitlines()
        for reply in OWNER_REPLIES:
            user_idx = next(i for i, line in enumerate(lines) if reply in line and "user" in line)
            assert "assistant" in lines[user_idx + 1]
            assert lines[user_idx + 2].strip().startswith("след:")

    def test_flag_off_names_the_absence(self, monkeypatch, settings):
        settings.REPLAY_LIVE_CAPTURE_ENABLED = False
        conversation = _owner_dialog(monkeypatch)
        text = _run("--conv", str(conversation.id))
        assert "replay: нет — REPLAY_LIVE_CAPTURE_ENABLED выключен" in text
        assert "tools=" not in text


class TestMasksAndListing:
    def test_phone_and_email_are_masked_and_ids_hashed(self, monkeypatch):
        conversation = _owner_dialog(monkeypatch)
        from apps.conversations.services import record_global_message

        record_global_message(
            conversation,
            role="user",
            content="мой номер +7 927 123-45-67, почта ivan@example.com",
        )
        text = _run("--conv", str(conversation.id))
        assert "123-45-67" not in text
        assert "ivan@example.com" not in text
        assert str(conversation.bot_user_id) not in text
        assert short_hash(conversation.bot_user_id) in text

    def test_list_shows_the_dialog_with_counts(self, monkeypatch):
        conversation = _owner_dialog(monkeypatch)
        text = _run("--list", "--since", "1h")
        row = next(line for line in text.splitlines() if str(conversation.id) in line)
        assert f"| {2 * len(OWNER_REPLIES)} |" in row
        assert short_hash(conversation.id) in row

    def test_out_writes_a_file_outside_the_repo_tree(self, monkeypatch, tmp_path):
        conversation = _owner_dialog(monkeypatch)
        _run("--conv", str(conversation.id), "--out", str(tmp_path / "dialogs"))
        files = list((tmp_path / "dialogs").glob(f"{short_hash(conversation.id)}_*.txt"))
        assert len(files) == 1
        assert "след:" in files[0].read_text(encoding="utf-8")

    def test_command_never_writes_to_the_database(self, monkeypatch):
        conversation = _owner_dialog(monkeypatch)
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            _run("--conv", str(conversation.id))
        writes = [q["sql"] for q in ctx.captured_queries if not q["sql"].lstrip().upper().startswith("SELECT")]
        assert writes == [], writes


class TestArguments:
    @pytest.mark.parametrize("value,seconds", [("24h", 86400), ("30m", 1800), ("2d", 172800)])
    def test_since_units(self, value, seconds):
        assert parse_since(value).total_seconds() == seconds

    def test_since_rejects_garbage(self):
        with pytest.raises(CommandError):
            parse_since("вчера")

    def test_unknown_conversation_is_an_error_not_an_empty_transcript(self):
        with pytest.raises(CommandError):
            _run("--conv", "00000000-0000-0000-0000-000000000000")

    def test_no_arguments_is_an_error(self):
        with pytest.raises(CommandError):
            _run()
