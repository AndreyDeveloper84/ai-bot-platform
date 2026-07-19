"""W5 task 2 — consent-gated memory block assembly (build_concierge_memory_block).

Hard pilot requirement (acceptance #7): БЕЗ memory_green the block is ""
and NOT A SINGLE FACT reaches the prompt.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from ayla_ai_core import MEMORY_BLOCK_HEADER

from apps.orchestrator import memory_block
from apps.orchestrator.memory_block import build_concierge_memory_block


def _gated(status, context=None):
    return SimpleNamespace(status=status, context=context)


@pytest.fixture(autouse=True)
def _no_inferred(monkeypatch):
    """Default: inferred-surface consent closed (declared-only tests)."""
    monkeypatch.setattr(
        "apps.consent.memory.can_store_green_memory",
        lambda bot_user: False,
    )


class TestGate:
    def test_blocked_consent_returns_empty(self, monkeypatch) -> None:
        monkeypatch.setattr(
            memory_block,
            "get_declared_prefs",
            lambda bot_user: _gated(memory_block.GateStatus.BLOCKED_CONSENT),
        )
        assert build_concierge_memory_block(object()) == ""

    def test_error_returns_empty(self, monkeypatch) -> None:
        monkeypatch.setattr(
            memory_block,
            "get_declared_prefs",
            lambda bot_user: _gated(memory_block.GateStatus.ERROR),
        )
        assert build_concierge_memory_block(object()) == ""


class TestDeclaredFacts:
    def _ok(self, monkeypatch, context: dict) -> None:
        declared = SimpleNamespace(context=context)
        monkeypatch.setattr(
            memory_block,
            "get_declared_prefs",
            lambda bot_user: _gated(memory_block.GateStatus.OK, declared),
        )

    def test_block_rendered_with_header_and_facts(self, monkeypatch) -> None:
        self._ok(
            monkeypatch,
            {
                "preferred_time_slots": ["evening"],
                "price_range_max": "2000.00",
                "workplace_district": "Центр",
            },
        )
        block = build_concierge_memory_block(object())
        assert MEMORY_BLOCK_HEADER in block
        assert "вечер" in block
        assert "Бюджет до 2000" in block
        assert "Центр" in block

    def test_contract_slots_mapped_to_display_labels(self, monkeypatch) -> None:
        self._ok(monkeypatch, {"preferred_time_slots": ["afternoon", "late_evening"]})
        block = build_concierge_memory_block(object())
        assert "день" in block
        assert "поздний вечер" in block
        # Raw contract values never leak into the prompt.
        assert "afternoon" not in block
        assert "late_evening" not in block

    def test_empty_context_returns_empty(self, monkeypatch) -> None:
        self._ok(monkeypatch, {})
        assert build_concierge_memory_block(object()) == ""


class TestInferredMerge:
    def test_inferred_fact_softened(self, monkeypatch) -> None:
        from apps.identity.services.memory_reader import GreenFact, PersonalContextView

        monkeypatch.setattr(
            memory_block,
            "get_declared_prefs",
            lambda bot_user: _gated(memory_block.GateStatus.OK, SimpleNamespace(context={})),
        )
        monkeypatch.setattr(
            "apps.consent.memory.can_store_green_memory",
            lambda bot_user: True,
        )
        view = PersonalContextView(
            summary="",
            green_facts=[GreenFact(kind="lifestyle", content={"key": "diet", "value": "vegan"})],
        )
        monkeypatch.setattr(
            "apps.identity.services.memory_reader.read_personal_context",
            lambda user_id: view,
        )
        block = build_concierge_memory_block(SimpleNamespace(ayla_user_id=uuid4()))
        assert "кажется" in block  # inferred → confidence 0.6 → softened
        assert "Диета: vegan" in block

    def test_declared_wins_over_inferred(self, monkeypatch) -> None:
        from apps.identity.services.memory_reader import GreenFact, PersonalContextView

        monkeypatch.setattr(
            memory_block,
            "get_declared_prefs",
            lambda bot_user: _gated(
                memory_block.GateStatus.OK,
                SimpleNamespace(context={"diet_type": "omnivore"}),
            ),
        )
        monkeypatch.setattr(
            "apps.consent.memory.can_store_green_memory",
            lambda bot_user: True,
        )
        view = PersonalContextView(
            summary="",
            green_facts=[GreenFact(kind="lifestyle", content={"key": "diet", "value": "vegan"})],
        )
        monkeypatch.setattr(
            "apps.identity.services.memory_reader.read_personal_context",
            lambda user_id: view,
        )
        block = build_concierge_memory_block(SimpleNamespace(ayla_user_id=uuid4()))
        assert "omnivore" in block
        assert "vegan" not in block
