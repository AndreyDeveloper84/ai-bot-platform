"""DRF-1385 — the resolver records the concierge's tool choice (third path).

The concierge already CLASSIFIES intent by picking a tool; DRF-1273 then
threw that choice away and paid a second LLM call to re-derive the intent
from scratch. Behind ``INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED``
(default OFF) the resolver instead builds the Output Contract 0.5
deterministically from the tool trace the concierge carried out of the
turn — zero new model calls. Any gap (unmappable tool, rejected draft,
exception) falls back to the DRF-1273 LLM pass, which stays the default.

Layers under test, mirroring the design:

- ``build_draft_from_tool_choice`` — the fixed mapping table (tool →
  intent_type/status/slots) and the verbatim-evidence rules.
- ``resolve_and_log_turn_intent`` — flag gating, fallback polarity and
  the ``source=tool_choice`` log marker.
- trace plumbing — ``_concierge_turn`` records the trace,
  ``generate_concierge_reply`` keeps it across the outbound guard, and
  the turn seam carries it into ``TurnReply``.
"""

from __future__ import annotations

import json
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from django.test import override_settings

from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge, intent_resolution
from apps.orchestrator.discovery import DiscoveryReply
from apps.orchestrator.intent_resolution import (
    _validate_and_build,
    build_draft_from_tool_choice,
    resolve_and_log_turn_intent,
)
from apps.orchestrator.turn_seam import SURFACE_GLOBAL, TurnContext, orchestrate_turn

USER_TEXT = "покажи массажистов в Пензе"
MESSAGE_ID = "msg-42"
TRACE_ID = "trace-1"

CONTRACT_FIELDS = 16


def _build(tool: str, arguments: dict, user_text: str = USER_TEXT) -> dict | None:
    """draft → the SAME deterministic gate the LLM pass uses."""
    draft = build_draft_from_tool_choice(tool, arguments, user_text=user_text)
    if draft is None:
        return None
    return _validate_and_build(draft, user_text=user_text, message_id=MESSAGE_ID, trace_id=TRACE_ID)


class TestMappingTable:
    """Every row of the fixed tool → contract mapping (brief §2)."""

    def test_show_masters_with_specialization(self):
        contract = _build("show_masters", {"specialization": "массажистов"})
        assert contract is not None
        assert len(contract) == CONTRACT_FIELDS
        assert contract["intent_type"] == "FIND_SPECIALIST"
        assert contract["status"] == "resolved"
        assert contract["confidence"] == 0.9
        assert contract["contract_version"] == "0.5"
        slot = contract["slots"]["service_category"]
        assert slot["raw_value"] == "массажистов"
        assert slot["confirmation_status"] == "filled"
        assert slot["evidence_refs"]
        assert contract["unmet_slot_requirements"] == []

    def test_show_masters_with_master_name_maps_provider_name(self):
        contract = _build(
            "show_masters", {"master": "массажистов"}, user_text="покажи массажистов в Пензе"
        )
        assert contract is not None
        assert "provider_name" in contract["slots"]

    def test_show_masters_without_any_of_slots_marks_requirement_unmet(self):
        # «покажи мастеров» без специализации и имени — any_of не выполнен.
        contract = _build("show_masters", {"city": "Тверь"}, user_text="покажи мастеров")
        assert contract is not None
        assert contract["intent_type"] == "FIND_SPECIALIST"
        assert contract["status"] == "resolved"
        assert contract["slots"] == {}
        unmet = contract["unmet_slot_requirements"]
        assert len(unmet) == 1
        assert unmet[0]["requirement_type"] == "any_of"
        assert unmet[0]["candidate_slots"] == ["provider_name", "service_category"]
        assert unmet[0]["minimum_present"] == 1

    @pytest.mark.parametrize("tool", ["show_salons", "show_services"])
    def test_catalog_tools_map_discover_service(self, tool):
        contract = _build(tool, {"city": "Пензе"})
        assert contract is not None
        assert len(contract) == CONTRACT_FIELDS
        assert contract["intent_type"] == "DISCOVER_SERVICE"
        assert contract["status"] == "resolved"
        assert contract["confidence"] == 0.9
        assert contract["slots"] == {}

    def test_start_booking_maps_book_appointment(self):
        contract = _build(
            "start_booking",
            {"master": "Анне", "service": "массаж"},
            user_text="запиши к Анне на массаж",
        )
        assert contract is not None
        assert contract["intent_type"] == "BOOK_APPOINTMENT"
        assert contract["status"] == "resolved"
        assert contract["missing_required_slots"] == ["service_ref", "time_slot"]
        assert contract["slots"]["provider_name"]["raw_value"] == "Анне"
        assert contract["slots"]["service_interest"]["raw_value"] == "массаж"

    def test_ask_clarification_maps_unknown_needs_clarification(self):
        contract = _build(
            "ask_clarification",
            {"question": "Какая услуга нужна?", "options": ["массаж"]},
        )
        assert contract is not None
        assert contract["intent_type"] == "UNKNOWN"
        assert contract["status"] == "needs_clarification"
        assert contract["confidence"] == 0.4
        assert contract["requires_clarification"] is True
        assert contract["clarification_question"] == "Какая услуга нужна?"
        assert contract["clarification_reason"] == "intent_low_confidence"
        assert contract["clarification_effect"] == "blocks_current_action"

    @pytest.mark.parametrize(
        ("tool", "arguments", "text", "fact"),
        [
            (
                "health_screening",
                {"symptom_text": "болит спина"},
                "у меня болит спина",
                "болит спина",
            ),
            ("log_water", {"drink_text": "стакан воды"}, "я выпила стакан воды", "стакан воды"),
            ("clarify_food_entry", {"food_text": "борщ 300г"}, "съела борщ 300г", "борщ 300г"),
        ],
    )
    def test_nutrition_tools_map_provide_context(self, tool, arguments, text, fact):
        contract = _build(tool, arguments, user_text=text)
        assert contract is not None
        assert contract["intent_type"] == "PROVIDE_CONTEXT"
        assert contract["status"] == "resolved"
        assert contract["confidence"] == 0.9
        assert contract["slots"]["context_fact"]["raw_value"] == fact
        assert contract["missing_required_slots"] == []

    def test_nutrition_tool_without_verbatim_fact_marks_slot_missing(self):
        contract = _build("start_nutrition_anketa", {}, user_text="хочу пройти анкету")
        assert contract is not None
        assert contract["intent_type"] == "PROVIDE_CONTEXT"
        assert contract["missing_required_slots"] == ["context_fact"]

    def test_show_my_records_has_no_honest_mapping(self):
        assert build_draft_from_tool_choice("show_my_records", {}, user_text=USER_TEXT) is None

    def test_unknown_tool_has_no_honest_mapping(self):
        assert build_draft_from_tool_choice("order_pizza", {}, user_text=USER_TEXT) is None

    def test_malformed_arguments_have_no_honest_mapping(self):
        assert (
            build_draft_from_tool_choice("show_masters", "не-словарь", user_text=USER_TEXT) is None
        )

    def test_ask_clarification_without_question_has_no_honest_mapping(self):
        # Внутренний degrade-диспетчера (unknown tool / malformed args) —
        # это НЕ выбор модели, честного отображения нет.
        assert (
            build_draft_from_tool_choice(
                "ask_clarification", {"reason": "unknown_tool:x"}, user_text=USER_TEXT
            )
            is None
        )


class TestEvidenceRules:
    """Verbatim evidence is a HARD contract invariant — nothing invented."""

    def test_argument_value_found_in_text_yields_verbatim_fragment(self):
        # Регистр отличается — fragment всё равно дословная форма ИЗ ТЕКСТА.
        contract = _build(
            "show_masters", {"specialization": "маникюр"}, user_text="Хочу МАНИКЮР завтра"
        )
        assert contract is not None
        slot = contract["slots"]["service_category"]
        assert slot["raw_value"] == "МАНИКЮР"
        fragment = contract["evidence"][0]["fragment"]
        assert fragment == "МАНИКЮР"
        assert fragment in "Хочу МАНИКЮР завтра"

    def test_argument_value_not_in_text_drops_slot_and_uses_whole_text(self):
        contract = _build(
            "show_masters", {"specialization": "массаж"}, user_text="покажи кого-нибудь"
        )
        assert contract is not None
        # Слот без дословного raw НЕ выдумывается.
        assert "service_category" not in contract["slots"]
        # Evidence — один фрагмент: всё сообщение целиком.
        assert [e["fragment"] for e in contract["evidence"]] == ["покажи кого-нибудь"]

    def test_resolved_contract_never_has_empty_evidence(self):
        contract = _build("show_salons", {}, user_text="какие салоны у вас есть")
        assert contract is not None
        assert contract["evidence"]


def _llm_draft(user_text: str = USER_TEXT) -> dict:
    """Minimal VALID LLM-side resolved draft for the fallback pass."""
    return {
        "intent_id": "11111111-2222-3333-4444-555555555555",
        "intent_type": "FIND_SPECIALIST",
        "status": "resolved",
        "confidence": 0.9,
        "slots": {},
        "missing_required_slots": [],
        "evidence": [{"evidence_id": "ev-1", "message_id": "m", "fragment": user_text}],
        "requires_clarification": False,
        "clarification_question": None,
        "safety_flags": [],
        "unmet_slot_requirements": [],
        "contract_version": "0.5",
        "status_reason": None,
        "clarification_reason": None,
        "clarification_effect": None,
        "secondary_intents": [],
    }


def _client_returning(payload: str) -> Mock:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=payload))],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
    )
    # Форма клиента копирует RouterLLMClient (AsyncOpenAI-shaped) — как в
    # test_intent_resolution.py после DRF-1310.
    client = Mock(spec=["chat", "last_provider", "last_model"])
    client.chat = SimpleNamespace(
        completions=SimpleNamespace(create=AsyncMock(return_value=response))
    )
    client.last_provider = "openai"
    client.last_model = "gpt-4o-mini"
    return client


def _resolve(text: str = USER_TEXT, tool_trace=None, caplog=None):
    client = _client_returning(json.dumps(_llm_draft(text), ensure_ascii=False))
    with (
        patch("apps.orchestrator.concierge.RouterLLMClient", return_value=client) as client_cls,
        patch.object(intent_resolution, "_record_resolution_metric"),
    ):
        contract = resolve_and_log_turn_intent(
            text=text,
            bot_user=Mock(),
            conversation=Mock(),
            user_message_id=1,
            trace_id=TRACE_ID,
            tool_trace=tool_trace,
        )
    return contract, client, client_cls


SHOW_MASTERS_TRACE = [{"tool": "show_masters", "arguments": {"specialization": "массажистов"}}]


class TestDeterministicPath:
    @override_settings(INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED=True)
    def test_flag_on_show_masters_builds_contract_without_llm(self, caplog):
        with caplog.at_level(logging.INFO, logger="apps.orchestrator.intent_resolution"):
            contract, client, client_cls = _resolve(tool_trace=SHOW_MASTERS_TRACE)
        assert contract is not None
        assert contract["intent_type"] == "FIND_SPECIALIST"
        assert contract["status"] == "resolved"
        # НОЛЬ новых вызовов модели: ни клиент не создан, ни тем более вызван.
        client_cls.assert_not_called()
        client.chat.completions.create.assert_not_called()
        ok_records = [
            r for r in caplog.records if r.msg.startswith("orchestrator.intent_resolution.ok")
        ]
        assert len(ok_records) == 1
        assert "source=tool_choice" in ok_records[0].msg

    @override_settings(INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED=True)
    def test_flag_on_records_metric_with_zero_usage(self):
        with (
            patch("apps.orchestrator.concierge.RouterLLMClient") as client_cls,
            patch.object(intent_resolution, "_record_resolution_metric") as metric,
        ):
            contract = resolve_and_log_turn_intent(
                text=USER_TEXT,
                bot_user=Mock(),
                conversation=Mock(),
                user_message_id=1,
                trace_id=TRACE_ID,
                tool_trace=SHOW_MASTERS_TRACE,
            )
        assert contract is not None
        client_cls.assert_not_called()
        assert metric.call_args.kwargs["outcome"] == "success"
        assert metric.call_args.kwargs["usage"] is None

    @override_settings(INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED=True)
    @pytest.mark.parametrize("tool_trace", [None, (), []])
    def test_flag_on_empty_trace_falls_back_to_llm(self, tool_trace):
        # Пустая трасса (чистый разговор) — нынешний LLM-проход, как раньше.
        contract, client, _ = _resolve(tool_trace=tool_trace)
        assert contract is not None
        client.chat.completions.create.assert_awaited_once()

    @override_settings(INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED=True)
    def test_flag_on_show_my_records_falls_back_to_llm(self):
        contract, client, _ = _resolve(tool_trace=[{"tool": "show_my_records", "arguments": {}}])
        assert contract is not None
        client.chat.completions.create.assert_awaited_once()

    @override_settings(INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED=False)
    def test_flag_off_trace_falls_back_to_llm(self, caplog):
        # Умолчание ВЫКЛ — поведение байт-в-байт как сейчас.
        with caplog.at_level(logging.INFO, logger="apps.orchestrator.intent_resolution"):
            contract, client, _ = _resolve(tool_trace=SHOW_MASTERS_TRACE)
        assert contract is not None
        client.chat.completions.create.assert_awaited_once()
        ok_records = [
            r for r in caplog.records if r.msg.startswith("orchestrator.intent_resolution.ok")
        ]
        assert len(ok_records) == 1
        assert "source=tool_choice" not in ok_records[0].msg

    @override_settings(INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED=True)
    def test_rejected_draft_falls_back_to_llm(self):
        # _validate_and_build отверг драфт — НИЧЕГО не терять: LLM-проход.
        # Отвергается только ПЕРВЫЙ (детерминированный) драфт; валидатор
        # LLM-прохода работает как обычно.
        real_validate = intent_resolution._validate_and_build
        calls = {"n": 0}

        def _reject_first(raw, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return real_validate(raw, **kwargs)

        with patch.object(intent_resolution, "_validate_and_build", _reject_first):
            contract, client, _ = _resolve(tool_trace=SHOW_MASTERS_TRACE)
        assert contract is not None
        client.chat.completions.create.assert_awaited_once()

    @override_settings(INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED=True)
    def test_exception_in_deterministic_path_warns_and_falls_back(self, caplog):
        with (
            patch.object(
                intent_resolution,
                "build_draft_from_tool_choice",
                Mock(side_effect=RuntimeError("boom")),
            ),
            caplog.at_level(logging.WARNING, logger="apps.orchestrator.intent_resolution"),
        ):
            contract, client, _ = _resolve(tool_trace=SHOW_MASTERS_TRACE)
        # Ход не сломан: WARN + нынешний LLM-проход.
        assert contract is not None
        client.chat.completions.create.assert_awaited_once()
        assert any("tool_choice_failed" in r.msg for r in caplog.records)


class TestSecondaryIntents:
    @override_settings(INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED=True)
    def test_second_tool_becomes_secondary_intent(self):
        trace = [
            {"tool": "show_masters", "arguments": {"specialization": "массаж"}},
            {"tool": "show_salons", "arguments": {}},
        ]
        contract, _, _ = _resolve(text="хочу массаж и список салонов", tool_trace=trace)
        assert contract is not None
        assert contract["intent_type"] == "FIND_SPECIALIST"
        secondary = contract["secondary_intents"]
        assert len(secondary) == 1
        assert secondary[0]["intent_type"] == "DISCOVER_SERVICE"
        assert secondary[0]["message_position"] == 2
        assert secondary[0]["evidence_refs"]

    @override_settings(INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED=True)
    def test_unknown_is_never_a_secondary_intent(self):
        trace = [
            {"tool": "show_masters", "arguments": {"specialization": "массаж"}},
            {"tool": "ask_clarification", "arguments": {"question": "Уточните?"}},
        ]
        contract, _, _ = _resolve(text="хочу массаж", tool_trace=trace)
        assert contract is not None
        assert contract["secondary_intents"] == []


# ── Трасса проносится наружу (brief §5 п.9) ────────────────────────────

CONCIERGE_TRACE_ID = str(uuid.uuid4())


def _router_returning(provider: AsyncMock) -> Mock:
    router = Mock()
    router.get_provider.return_value = provider
    return router


def _clarify_result() -> CompletionResult:
    return CompletionResult(
        text="",
        tool_calls=[
            ToolCall(
                id="c1",
                name="ask_clarification",
                arguments={"question": "Какая услуга нужна?", "options": ["массаж"]},
            )
        ],
        prompt_tokens=10,
        completion_tokens=5,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="tool_calls",
    )


def _text_result(text: str) -> CompletionResult:
    return CompletionResult(
        text=text,
        prompt_tokens=20,
        completion_tokens=8,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="stop",
    )


def _bot_user_and_conversation():
    from apps.conversations.services import resolve_active_global_conversation
    from apps.identity.services import resolve_or_create_global_bot_user

    bot_user = resolve_or_create_global_bot_user(
        channel="max",
        channel_user_id="drf1385-uid",
        chat_id="drf1385-chat",
    )
    conversation = resolve_active_global_conversation(bot_user)
    return bot_user, conversation


@pytest.mark.django_db(transaction=True)
class TestToolTracePlumbing:
    def test_concierge_turn_records_tool_choice(self, monkeypatch):
        provider = AsyncMock()
        provider.complete.return_value = _clarify_result()
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = _bot_user_and_conversation()

        reply = concierge.generate_concierge_reply(
            "что-нибудь",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=CONCIERGE_TRACE_ID,
        )

        assert reply.tool_trace is not None
        assert len(reply.tool_trace) == 1
        assert reply.tool_trace[0]["tool"] == "ask_clarification"
        assert reply.tool_trace[0]["arguments"]["question"] == "Какая услуга нужна?"

    def test_text_only_turn_leaves_trace_empty(self, monkeypatch):
        provider = AsyncMock()
        provider.complete.return_value = _text_result("Просто ответ словами.")
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = _bot_user_and_conversation()

        reply = concierge.generate_concierge_reply(
            "привет",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=CONCIERGE_TRACE_ID,
        )

        assert reply.tool_trace is None

    def test_guard_outbound_rebuild_keeps_trace(self, monkeypatch):
        provider = AsyncMock()
        provider.complete.return_value = _clarify_result()
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(
            "apps.orchestrator.safety.gate.guard_outbound",
            lambda *a, **kw: SimpleNamespace(blocked=True, text="заменённая реплика"),
        )
        bot_user, conversation = _bot_user_and_conversation()

        reply = concierge.generate_concierge_reply(
            "что-нибудь",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=CONCIERGE_TRACE_ID,
        )

        assert reply.text == "заменённая реплика"
        assert reply.tool_trace is not None
        assert reply.tool_trace[0]["tool"] == "ask_clarification"

    def test_global_legacy_adapter_copies_trace(self, monkeypatch):
        trace = ({"tool": "show_salons", "arguments": {}},)
        monkeypatch.setattr(
            "apps.orchestrator.concierge.generate_concierge_reply",
            lambda text, **kwargs: DiscoveryReply(text="ок", persisted=True, tool_trace=trace),
        )
        reply = orchestrate_turn(
            TurnContext(
                surface=SURFACE_GLOBAL,
                conversation=SimpleNamespace(id=uuid.uuid4()),
                bot_user=SimpleNamespace(id=7),
                text="какие салоны",
                tenant=None,
            )
        )
        assert reply.tool_trace == trace

    def test_legacy_reply_without_trace_reads_as_none(self, monkeypatch):
        # Шов — переносчик: он не вправе требовать поле у прежних
        # производителей ответа (getattr с умолчанием).
        class _LegacyReply:
            text = "ок"
            action_data = None
            persisted = True

        monkeypatch.setattr(
            "apps.orchestrator.concierge.generate_concierge_reply",
            lambda text, **kwargs: _LegacyReply(),
        )
        reply = orchestrate_turn(
            TurnContext(
                surface=SURFACE_GLOBAL,
                conversation=SimpleNamespace(id=uuid.uuid4()),
                bot_user=SimpleNamespace(id=7),
                text="привет",
                tenant=None,
            )
        )
        assert reply.tool_trace is None
