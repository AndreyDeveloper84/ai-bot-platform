"""Сторож полей шва (DRF-1419).

Шов ``apps.orchestrator.turn_seam`` переносит поля ПЕРЕЧИСЛЕНИЕМ, и до этого
теста поле, которого в перечислении нет, терялось МОЛЧА — потребитель получал
``None`` и читал его как «пусто» вместо «не приехало». Дважды за одни сутки
это стоило красного теста, написанного до реализации: ``tool_trace``
(DRF-1385) и ``confidence`` (DRF-1209).

Здесь три разных вопроса, и путать их нельзя:

1. **Контракт полон** — у типа-производителя нет поля, о котором шов не
   заявил ни «переношу», ни «намеренно нет». Ловится на CI, рантайм не
   трогает (рекомендация задачи, вариант 1).
2. **Контракт не врёт** — каждое заявленное «переношу» адаптер и правда
   выполняет. Иначе декларация стала бы вторым перечислением, способным
   разойтись с первым.
3. **Отказ ШУМНЫЙ и ИМЕНОВАННЫЙ** — поле, в которое производитель положил
   значение, а шов его не переносит, роняет ход с именем
   ``seam_field_loss``; поле, которое законно НЕ положили, не роняет ничего.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from apps.orchestrator.discovery import DiscoveryReply
from apps.orchestrator.turn_seam import (
    DISCOVERY_NOT_CARRIED,
    DISCOVERY_TO_TURN,
    REASON_FIELD_DROPPED,
    REASON_FIELD_UNWIRED,
    SEAM_FIELD_LOSS,
    SKILL_RESULT_NOT_CARRIED,
    SKILL_RESULT_TO_TURN,
    SURFACE_GLOBAL,
    SURFACE_PER_TENANT,
    TurnContext,
    TurnReply,
    TurnSeamFieldLoss,
    orchestrate_turn,
    unmapped_fields,
)
from apps.skills.base import SkillResult

SEAM_LOGGER = "apps.orchestrator.turn_seam"


def _ctx(**overrides) -> TurnContext:
    kwargs: dict[str, Any] = dict(
        surface=SURFACE_PER_TENANT,
        conversation=SimpleNamespace(id=uuid.uuid4()),
        bot_user=SimpleNamespace(id=1),
        text="привет",
        channel="max",
        trace_id="t-guard",
    )
    kwargs.update(overrides)
    return TurnContext(**kwargs)


def _run_global(monkeypatch, reply: Any) -> TurnReply:
    monkeypatch.setattr(
        "apps.orchestrator.concierge.generate_concierge_reply",
        lambda text, **kwargs: reply,
    )
    return orchestrate_turn(_ctx(surface=SURFACE_GLOBAL, tenant=None))


def _run_per_tenant(monkeypatch, result: Any) -> TurnReply:
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda skill_ctx: result)
    from apps.tenancy.context import tenant_scope

    with tenant_scope(SimpleNamespace(id=uuid.uuid4())):
        return orchestrate_turn(_ctx())


#: Значение-проба на каждое ЗАЯВЛЕННОЕ переносимое поле — заметно отличное от
#: умолчания, чтобы «доехало» нельзя было спутать с «совпало случайно».
DISCOVERY_PROBES: dict[str, Any] = {
    "text": "проба текста",
    "action_data": {"probe": "discovery"},
    "persisted": True,
    "outage": True,
    "tool_trace": ({"tool": "search_masters", "arguments": {"city": "Пенза"}},),
}

SKILL_RESULT_PROBES: dict[str, Any] = {
    "reply_text": "проба ответа",
    "action_type": "probe_action",
    "action_data": {"probe": "skill"},
    "should_send": False,
    "should_handoff": True,
    "handoff_reason": "probe_reason",
    "new_state": "PROBE_STATE",
    "should_close_conversation": True,
    "meta": {"probe": "meta"},
    "confidence": 0.37,
}


class TestContractIsComplete:
    """1. Ни одно поле производителя не осталось вне контракта шва."""

    def test_discovery_reply_has_no_undeclared_field(self):
        present = {f.name for f in dataclasses.fields(DiscoveryReply)}
        # Счётчик ВПЕРЕДИ отрицания: контракт смотрит на живой набор полей —
        # каждое заявленное имя у типа есть. Без этого «расхождений нет»
        # значило бы лишь, что сравнивать было не с чем.
        assert len(present & set(DISCOVERY_TO_TURN)) == len(DISCOVERY_TO_TURN)
        gaps = unmapped_fields(
            DiscoveryReply, carried=DISCOVERY_TO_TURN, not_carried=DISCOVERY_NOT_CARRIED
        )
        assert gaps == (), gaps

    def test_skill_result_has_no_undeclared_field(self):
        present = {f.name for f in dataclasses.fields(SkillResult)}
        assert len(present & set(SKILL_RESULT_TO_TURN)) == len(SKILL_RESULT_TO_TURN)
        gaps = unmapped_fields(
            SkillResult, carried=SKILL_RESULT_TO_TURN, not_carried=SKILL_RESULT_NOT_CARRIED
        )
        assert gaps == (), gaps

    def test_declared_names_exist_on_both_sides(self):
        """Контракт называет реальные поля, а не выдуманные."""

        turn_fields = {f.name for f in dataclasses.fields(TurnReply)}
        for source_type, carried, not_carried in (
            (DiscoveryReply, DISCOVERY_TO_TURN, DISCOVERY_NOT_CARRIED),
            (SkillResult, SKILL_RESULT_TO_TURN, SKILL_RESULT_NOT_CARRIED),
        ):
            source_fields = {f.name for f in dataclasses.fields(source_type)}
            for src_name, dst_name in carried.items():
                assert src_name in source_fields, (source_type.__name__, src_name)
                assert dst_name in turn_fields, dst_name
            for src_name in not_carried:
                assert src_name in source_fields, (source_type.__name__, src_name)
            assert not set(carried) & set(not_carried)


class TestContractDoesNotLie:
    """2. Каждое заявленное «переношу» адаптер выполняет на деле."""

    @pytest.mark.parametrize("field_name", sorted(DISCOVERY_TO_TURN))
    def test_declared_discovery_field_arrives(self, monkeypatch, field_name):
        probe = DISCOVERY_PROBES[field_name]
        reply = _run_global(monkeypatch, DiscoveryReply(**{"text": "x", field_name: probe}))
        assert getattr(reply, DISCOVERY_TO_TURN[field_name]) == probe

    @pytest.mark.parametrize("field_name", sorted(SKILL_RESULT_TO_TURN))
    def test_declared_skill_result_field_arrives(self, monkeypatch, field_name):
        probe = SKILL_RESULT_PROBES[field_name]
        reply = _run_per_tenant(monkeypatch, SkillResult(**{field_name: probe}))
        assert getattr(reply, SKILL_RESULT_TO_TURN[field_name]) == probe


@dataclass(frozen=True)
class _DiscoveryReplyWithNewField(DiscoveryReply):
    """Ровно то, что случилось дважды за сутки: у производителя завели поле,
    а провести его через шов забыли."""

    audience_hint: str = ""


@dataclass
class _SkillResultWithNewField(SkillResult):
    """То же самое на per-tenant стороне — случай `confidence` (DRF-1209)."""

    audience_hint: str = ""


class TestLossRefusesByName:
    """3a. Поле ПОЛОЖИЛИ и оно теряется — шумный именованный отказ."""

    def test_lost_discovery_field_raises_named_state(self, monkeypatch):
        with pytest.raises(TurnSeamFieldLoss) as excinfo:
            _run_global(
                monkeypatch,
                _DiscoveryReplyWithNewField(text="ответ", audience_hint="важное значение"),
            )
        assert excinfo.value.state == SEAM_FIELD_LOSS
        assert excinfo.value.lost == ("audience_hint",)
        assert excinfo.value.producer == "DiscoveryReply"
        assert SEAM_FIELD_LOSS in str(excinfo.value)

    def test_lost_skill_result_field_raises_named_state(self, monkeypatch):
        with pytest.raises(TurnSeamFieldLoss) as excinfo:
            _run_per_tenant(
                monkeypatch,
                _SkillResultWithNewField(reply_text="ответ", audience_hint="важное значение"),
            )
        assert excinfo.value.state == SEAM_FIELD_LOSS
        assert excinfo.value.lost == ("audience_hint",)
        assert excinfo.value.producer == "SkillResult"

    def test_loss_writes_its_own_reason(self, monkeypatch, caplog):
        """Наружу одно имя, внутрь — причина «мы теряем поля»."""

        caplog.set_level(logging.DEBUG, logger=SEAM_LOGGER)
        with pytest.raises(TurnSeamFieldLoss):
            _run_global(
                monkeypatch, _DiscoveryReplyWithNewField(text="ответ", audience_hint="значение")
            )
        messages = [r.getMessage() for r in caplog.records if r.name == SEAM_LOGGER]
        assert any(REASON_FIELD_DROPPED in m for m in messages), messages
        assert not any(REASON_FIELD_UNWIRED in m for m in messages), messages


class TestSilenceWhereItIsLegitimate:
    """3b. Обратная сторона: «не положили» — законно и отказа не вызывает."""

    def test_unpopulated_new_field_does_not_refuse(self, monkeypatch, caplog):
        caplog.set_level(logging.DEBUG, logger=SEAM_LOGGER)
        reply = _run_global(monkeypatch, _DiscoveryReplyWithNewField(text="ответ"))
        assert reply.reply_text == "ответ"
        messages = [r.getMessage() for r in caplog.records if r.name == SEAM_LOGGER]
        # Другой счётчик: «их просто не кладут», а не «мы их теряем».
        assert any(REASON_FIELD_UNWIRED in m for m in messages), messages
        assert not any(REASON_FIELD_DROPPED in m for m in messages), messages

    def test_legacy_producer_without_the_field_does_not_refuse(self, monkeypatch, caplog):
        """Оговорка DRF-1419: производитель без `outage`/`tool_trace` цел.

        Сторож — про НАБОР ПОЛЕЙ ТИПА, а не про конкретный экземпляр, и
        `getattr` в глобальном адаптере продолжает работать как работал.
        """

        caplog.set_level(logging.DEBUG, logger=SEAM_LOGGER)
        legacy = SimpleNamespace(text="старый ответ", action_data=None, persisted=False)
        reply = _run_global(monkeypatch, legacy)
        assert reply.reply_text == "старый ответ"
        assert reply.outage is False
        assert reply.tool_trace is None
        assert [r.getMessage() for r in caplog.records if r.name == SEAM_LOGGER] == []

    def test_declared_not_carried_field_is_silent_even_when_populated(self, monkeypatch, caplog):
        """`tool_calls_made` объявлен намеренно непереносимым — намерение
        явное, значит и молчание законно."""

        from apps.llm.protocol import ToolCall

        caplog.set_level(logging.DEBUG, logger=SEAM_LOGGER)
        reply = _run_per_tenant(
            monkeypatch,
            SkillResult(reply_text="ответ", tool_calls_made=[ToolCall(id="1", name="search_kb")]),
        )
        assert reply.reply_text == "ответ"
        assert [r.getMessage() for r in caplog.records if r.name == SEAM_LOGGER] == []
