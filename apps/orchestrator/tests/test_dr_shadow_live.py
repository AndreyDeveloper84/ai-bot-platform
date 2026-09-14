"""Теневой DecisionReadiness в живом ходе (DRF-1882).

Три свойства, каждое своим тестом:

* флаг не задан → ноль работы: ни движка, ни Redis, ни строки лога;
* флаг включён → ровно одна строка лога на ход, и человек видит тот же ответ,
  что без флага (тень не влияет на ход);
* ход с показом мастеров → подпись кандидатов из трассы, и расхождение «путь
  показал мастеров, движок не вправе рекомендовать» названо в строке.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from django.utils import timezone

from apps.channels.max import handler as max_handler
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge, dr_shadow
from apps.orchestrator.decision_readiness import shadow as shadow_mod
from apps.orchestrator.decision_readiness import state as state_mod
from apps.orchestrator.decision_readiness.tests.fakes import FakeRedis
from apps.orchestrator.memory import short_term

pytestmark = pytest.mark.django_db(transaction=True)

PROSE = "Какой массаж тебе ближе — расслабляющий или спортивный?"


@pytest.fixture(autouse=True)
def _channel_harness(settings, monkeypatch):
    settings.GLOBAL_BOT_ONBOARDING = True
    monkeypatch.setattr(
        "apps.channels.max.outbound.send_chat_action", lambda **kwargs: {"ok": True}
    )
    monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock())
    monkeypatch.setattr(max_handler, "maybe_weave_question", lambda _c, _b, reply: reply)
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    monkeypatch.setattr(short_term, "_redis_client", lambda: _FakeRedis())


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"text": text})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def dr_redis(monkeypatch) -> FakeRedis:
    client = FakeRedis()
    monkeypatch.setattr(state_mod, "_redis_client", lambda: client)
    return client


def _completion(text: str = "", tool: tuple[str, dict] | None = None) -> CompletionResult:
    return CompletionResult(
        text=text,
        tool_calls=[ToolCall(id="c1", name=tool[0], arguments=tool[1])] if tool else [],
        prompt_tokens=20,
        completion_tokens=10,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="tool_calls" if tool else "stop",
    )


def _model(monkeypatch, *results: CompletionResult) -> None:
    provider = AsyncMock()
    if len(results) == 1:
        provider.complete.return_value = results[0]
    else:
        provider.complete.side_effect = list(results)
    router = Mock()
    router.get_provider.return_value = provider
    monkeypatch.setattr(concierge, "get_router", lambda: router)


_UID = iter(range(78801, 78999))


def _turn(sent, text: str) -> str:
    user_id = next(_UID)
    from apps.consent.services import record_global_consent

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), chat_id="8899"
    )
    bot_user.welcomed_at = timezone.now()
    bot_user.save(update_fields=["welcomed_at"])
    record_global_consent(
        bot_user, consent_type="personal_data", source="test:drf1882", document_version="v1"
    )
    resolve_active_global_conversation(bot_user)
    max_handler.handle_global_max_event(
        {
            "update_type": "message_created",
            "timestamp": 1731320000000,
            "message": {
                "sender": {"user_id": user_id, "name": "Андрей"},
                "recipient": {"chat_id": 8899, "chat_type": "dialog"},
                "body": {"mid": f"m{user_id}", "seq": 1, "text": text, "attachments": []},
            },
        }
    )
    return sent[-1]["text"]


def _shadow_lines(caplog) -> list[dict]:
    return [
        json.loads(r.getMessage().split(" ", 1)[1])
        for r in caplog.records
        if r.getMessage().startswith(dr_shadow.LIVE_LOG_EVENT + " ")
    ]


class TestFlagOffDoesNothing:
    def test_absent_flag_no_engine_no_redis_no_line(self, settings, monkeypatch, sent, caplog):
        if hasattr(settings, "DRE_SHADOW_ENABLED"):
            del settings.DRE_SHADOW_ENABLED
        engine_spy = MagicMock(side_effect=AssertionError("движок вызван при выключенном флаге"))
        monkeypatch.setattr(shadow_mod, "evaluate", engine_spy)

        def _no_redis():
            raise AssertionError("Redis тронут при выключенном флаге")

        monkeypatch.setattr(state_mod, "_redis_client", _no_redis)
        _model(monkeypatch, _completion(PROSE))

        with caplog.at_level(logging.INFO):
            screen = _turn(sent, "хочу массаж")

        assert screen == PROSE
        assert engine_spy.call_count == 0
        # empty-assert-ok: флаг не задан — тень не пишет ничего по построению; присутствие строки доказывает соседний тест с включённым флагом
        assert _shadow_lines(caplog) == []


class TestFlagOnObservesOnly:
    def test_one_line_per_turn_and_the_person_sees_the_same_reply(
        self, settings, monkeypatch, sent, dr_redis, caplog
    ):
        settings.DRE_SHADOW_ENABLED = True
        _model(monkeypatch, _completion(PROSE))

        with caplog.at_level(logging.INFO):
            screen = _turn(sent, "хочу массаж")

        assert screen == PROSE
        lines = _shadow_lines(caplog)
        assert len(lines) == 1
        line = lines[0]
        # Замер 14.09: вердикт безопасности с живого пути не пишется → честный блок.
        assert line["readiness_state"] == "blocked"
        assert "SAFETY_UNKNOWN" in line["reason_codes"]
        assert line["allow_recommend"] is False
        assert line["candidates"]["digest"] == dr_shadow.NOT_SEARCHED
        assert line["measures"]["separation"] is None
        assert line["current_path"]["cards_shown"] == 0
        assert line["path_recommended_without_readiness"] is False

    def test_shadow_run_does_not_hand_out_a_revision(self, settings, monkeypatch, sent, dr_redis):
        settings.DRE_SHADOW_ENABLED = True
        _model(monkeypatch, _completion(PROSE))
        incr = MagicMock(side_effect=AssertionError("тень выдала ревизию"))
        monkeypatch.setattr(state_mod, "next_revision", incr)

        screen = _turn(sent, "хочу массаж")

        assert screen == PROSE
        assert incr.call_count == 0


class TestShowMastersTurn:
    def test_signature_comes_from_the_trace_and_the_gap_is_named(
        self, settings, monkeypatch, sent, dr_redis, caplog
    ):
        settings.DRE_SHADOW_ENABLED = True
        _model(
            monkeypatch,
            _completion(tool=("show_masters", {"city": "Пенза", "specialization": "массаж"})),
            _completion(""),
        )
        cards = [
            SimpleNamespace(
                tenant_id="t1",
                master_id=f"m{i}",
                name=name,
                specialization="Массаж",
                rating=4.9,
                city="Пенза",
                service_id=None,
                service_name="",
            )
            for i, name in enumerate(("Анна", "Инна"))
        ]
        monkeypatch.setattr(concierge, "discover_masters", lambda **kw: cards)

        with caplog.at_level(logging.INFO):
            screen = _turn(sent, "массаж в пензе")

        assert "Анна" in screen
        lines = _shadow_lines(caplog)
        assert len(lines) == 1
        line = lines[0]
        assert line["current_path"]["tools"] == ["show_masters"]
        assert line["current_path"]["cards_shown"] == 2
        assert line["candidates"]["visible_count"] == 2
        assert line["candidates"]["eligible_count"] is None
        assert line["candidates"]["digest"] != dr_shadow.NOT_SEARCHED
        assert line["path_recommended_without_readiness"] is True


class TestCandidateSignature:
    def test_no_search_is_named(self):
        signature, searched = dr_shadow.candidate_signature(None)
        assert searched is False
        assert signature.digest == dr_shadow.NOT_SEARCHED
        assert signature.visible_count == 0

    def test_declined_search_is_not_a_search(self):
        trace = ({"tool": "show_masters", "result": "declined_prose", "result_count": 3},)
        signature, searched = dr_shadow.candidate_signature(trace)
        assert searched is False
        assert signature.digest == dr_shadow.NOT_SEARCHED

    def test_last_search_wins_and_separation_is_absent_not_zero(self):
        trace = (
            {"tool": "show_masters", "result_count": 5, "ordered_ids": ["a"] * 5},
            {"tool": "show_masters", "result_count": 2, "ordered_ids": ["x", "y"]},
        )
        signature, searched = dr_shadow.candidate_signature(trace)
        assert searched is True
        assert signature.visible_count == 2
        assert signature.ordered_ids == ("x", "y")
        assert signature.separation is None
        assert signature.recommendation_eligible_count is None
