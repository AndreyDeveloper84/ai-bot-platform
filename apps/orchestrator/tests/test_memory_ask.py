"""W5 task 3 — memory-ask flow (S3.5): weave → pending → answer/skip → PATCH.

Pins the frozen-contract discipline: mark-asked / skip fire EXACTLY ONCE
per real user action (non-idempotent, never retried); the consent gate
lives inside the mocked W3 services.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.orchestrator import memory_ask
from apps.orchestrator.discovery import DiscoveryReply
from apps.orchestrator.memory_ask import (
    maybe_weave_question,
    read_pending,
    try_handle_answer,
)


class _FakeRedis:
    """Minimal dict-backed Redis stub (get/setex/delete only)."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str):
        return self._data.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


@pytest.fixture(autouse=True)
def _redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(
        "apps.orchestrator.memory.short_term._redis_client",
        lambda: fake,
    )
    return fake


@pytest.fixture
def conversation():
    return SimpleNamespace(id="conv-1")


@pytest.fixture
def bot_user():
    return SimpleNamespace(id="bu-1")


def _eligibility(*, should_ask: bool, field: str | None = None, hint: str | None = None):
    return SimpleNamespace(
        status=memory_ask.GateStatus.OK,
        eligibility=SimpleNamespace(
            should_ask=should_ask,
            field=field,
            prompt_hint=hint,
        ),
    )


class TestWeave:
    def test_no_eligibility_reply_unchanged(self, monkeypatch, conversation, bot_user) -> None:
        mark = Mock()
        monkeypatch.setattr(
            memory_ask, "get_ask_eligibility", lambda bu: _eligibility(should_ask=False)
        )
        monkeypatch.setattr(memory_ask, "mark_asked", mark)
        reply = DiscoveryReply(text="ответ")
        out = maybe_weave_question(conversation, bot_user, reply)
        assert out is reply
        mark.assert_not_called()

    def test_should_ask_appends_question_and_stamps_once(
        self, monkeypatch, conversation, bot_user
    ) -> None:
        mark = Mock(return_value=SimpleNamespace(status=memory_ask.GateStatus.OK))
        monkeypatch.setattr(
            memory_ask,
            "get_ask_eligibility",
            lambda bu: _eligibility(
                should_ask=True,
                field="preferred_time_slots",
                hint="Тебе удобнее записываться утром, днём или вечером?",
            ),
        )
        monkeypatch.setattr(memory_ask, "mark_asked", mark)
        reply = DiscoveryReply(text="Вот мастера:", persisted=True)
        out = maybe_weave_question(conversation, bot_user, reply)

        assert "Вот мастера:" in out.text
        assert "тебе удобнее записываться утром, днём или вечером?" in out.text
        assert out.persisted is True
        mark.assert_called_once_with(bot_user, "preferred_time_slots")
        pending = read_pending(conversation.id)
        assert pending is not None
        assert pending["field"] == "preferred_time_slots"

    def test_favorite_masters_guarded_not_asked(self, monkeypatch, conversation, bot_user) -> None:
        """favorite_masters answers can't be resolved to UUIDs cross-tenant —
        the bot does not ask the field in the pilot (documented scope guard)."""
        mark = Mock()
        monkeypatch.setattr(
            memory_ask,
            "get_ask_eligibility",
            lambda bu: _eligibility(
                should_ask=True, field="favorite_masters", hint="Есть любимый мастер?"
            ),
        )
        monkeypatch.setattr(memory_ask, "mark_asked", mark)
        reply = DiscoveryReply(text="ответ")
        out = maybe_weave_question(conversation, bot_user, reply)
        assert out is reply
        mark.assert_not_called()
        assert read_pending(conversation.id) is None

    def test_existing_pending_skips_weave(self, monkeypatch, conversation, bot_user) -> None:
        elig = Mock()
        monkeypatch.setattr(memory_ask, "get_ask_eligibility", elig)
        memory_ask._write_pending(conversation.id, {"field": "diet_type", "hint": "h"})
        reply = DiscoveryReply(text="ответ")
        assert maybe_weave_question(conversation, bot_user, reply) is reply
        elig.assert_not_called()


class TestAnswer:
    def _ask(self, monkeypatch, bot_user, conversation, *, field: str, hint: str) -> None:
        monkeypatch.setattr(
            memory_ask,
            "get_ask_eligibility",
            lambda bu: _eligibility(should_ask=True, field=field, hint=hint),
        )
        monkeypatch.setattr(
            memory_ask,
            "mark_asked",
            lambda bu, f: SimpleNamespace(status=memory_ask.GateStatus.OK),
        )
        maybe_weave_question(conversation, bot_user, DiscoveryReply(text="r"))

    def test_no_pending_returns_none(self, conversation, bot_user) -> None:
        assert try_handle_answer(conversation, bot_user, "вечером") is None

    def test_acceptance_flow_answer_patches_memory(
        self, monkeypatch, conversation, bot_user
    ) -> None:
        """Сквозной сценарий: вопрос задан → ответ → PATCH (source: conversational)."""
        self._ask(
            monkeypatch,
            bot_user,
            conversation,
            field="preferred_time_slots",
            hint="Утром или вечером?",
        )
        patch = Mock(return_value=SimpleNamespace(status=memory_ask.GateStatus.OK))
        monkeypatch.setattr(memory_ask, "patch_declared_prefs", patch)

        out = try_handle_answer(conversation, bot_user, "мне удобнее вечером")

        assert out is not None
        assert "Записала" in out.text
        patch.assert_called_once_with(
            bot_user,
            [{"field": "preferred_time_slots", "value": ["evening"], "source": "conversational"}],
        )
        assert read_pending(conversation.id) is None

    def test_skip_calls_skip_once(self, monkeypatch, conversation, bot_user) -> None:
        self._ask(monkeypatch, bot_user, conversation, field="diet_type", hint="Диета?")
        skip = Mock(return_value=SimpleNamespace(status=memory_ask.GateStatus.OK, skip_count=1))
        patch = Mock()
        monkeypatch.setattr(memory_ask, "skip", skip)
        monkeypatch.setattr(memory_ask, "patch_declared_prefs", patch)

        out = try_handle_answer(conversation, bot_user, "не хочу отвечать")

        assert out is not None
        assert "не буду спрашивать" in out.text
        skip.assert_called_once_with(bot_user, "diet_type")
        patch.assert_not_called()
        assert read_pending(conversation.id) is None

    def test_unrelated_text_abandons_pending(self, monkeypatch, conversation, bot_user) -> None:
        """Off-topic reply (booking intent) must not be stored as a memory answer."""
        self._ask(monkeypatch, bot_user, conversation, field="busy_days", hint="Когда занято?")
        patch = Mock()
        monkeypatch.setattr(memory_ask, "patch_declared_prefs", patch)

        out = try_handle_answer(conversation, bot_user, "запиши меня завтра на массаж")

        assert out is None  # normal concierge turn proceeds
        patch.assert_not_called()
        assert read_pending(conversation.id) is None

    def test_patch_failure_keeps_pending(self, monkeypatch, conversation, bot_user) -> None:
        self._ask(monkeypatch, bot_user, conversation, field="diet_type", hint="Диета?")
        monkeypatch.setattr(
            memory_ask,
            "patch_declared_prefs",
            Mock(return_value=SimpleNamespace(status=memory_ask.GateStatus.ERROR)),
        )
        out = try_handle_answer(conversation, bot_user, "я веган")
        assert out is not None
        assert "повтори" in out.text
        assert read_pending(conversation.id) is not None


class TestParsers:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("утром", ["morning"]),
            ("вечером", ["evening"]),
            ("утром и вечером", ["morning", "evening"]),
            ("поздним вечером", ["late_evening"]),
            ("днём", ["afternoon"]),
        ],
    )
    def test_time_slots(self, text, expected) -> None:
        assert memory_ask._parse_time_slots(text) == expected

    def test_price_max(self) -> None:
        assert memory_ask._parse_price_max("до 2000 рублей") == "2000.00"
        assert memory_ask._parse_price_max("тысячи полторы") is memory_ask._UNPARSED

    def test_busy_days(self) -> None:
        assert memory_ask._parse_busy_days("по понедельникам и субботам") == ["mon", "sat"]
        assert memory_ask._parse_busy_days("выходные: сб, вс") == ["sat", "sun"]

    def test_rating(self) -> None:
        assert memory_ask._parse_rating("не ниже 4,5") == 4.5
        assert memory_ask._parse_rating("хочу 9") is memory_ask._UNPARSED

    def test_diet(self) -> None:
        assert memory_ask._parse_diet("я веган") == "vegan"
        assert memory_ask._parse_diet("вегетарианка") == "vegetarian"
        assert memory_ask._parse_diet("кошерное") == "kosher"

    def test_free_text(self) -> None:
        assert memory_ask._parse_free_text("  Западная поляна ") == "Западная поляна"
        assert memory_ask._parse_free_text("   ") is memory_ask._UNPARSED


class TestPendingStore:
    def test_roundtrip(self) -> None:
        memory_ask._write_pending("c9", {"field": "diet_type", "hint": "h"})
        assert read_pending("c9") == {"field": "diet_type", "hint": "h"}
        memory_ask._clear_pending("c9")
        assert read_pending("c9") is None

    def test_malformed_blob_returns_none(self) -> None:
        memory_ask._write_pending("c9", {"no_field": True})
        assert read_pending("c9") is None

    def test_raw_garbage_returns_none(self, _redis) -> None:
        _redis.setex("conv:c9:memory_ask_pending", 60, json.dumps(["not-a-dict"]))
        assert read_pending("c9") is None


class TestRollbackFlag:
    """CONCIERGE_MEMORY_ENABLED=false (runbook §7): ask flow fully bypassed."""

    def test_weave_bypassed_when_off(self, monkeypatch, settings, conversation, bot_user) -> None:
        settings.CONCIERGE_MEMORY_ENABLED = False
        elig = Mock()
        monkeypatch.setattr(memory_ask, "get_ask_eligibility", elig)
        reply = DiscoveryReply(text="ответ")
        assert maybe_weave_question(conversation, bot_user, reply) is reply
        elig.assert_not_called()

    def test_answer_capture_bypassed_when_off(
        self, monkeypatch, settings, conversation, bot_user
    ) -> None:
        settings.CONCIERGE_MEMORY_ENABLED = False
        memory_ask._write_pending(conversation.id, {"field": "diet_type", "hint": "h"})
        patch = Mock()
        monkeypatch.setattr(memory_ask, "patch_declared_prefs", patch)
        assert try_handle_answer(conversation, bot_user, "я веган") is None
        patch.assert_not_called()
