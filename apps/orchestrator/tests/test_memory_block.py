"""W5 task 2 — consent-gated memory block assembly (build_concierge_memory_block).

Hard pilot requirement (acceptance #7): БЕЗ memory_green the block is ""
and NOT A SINGLE FACT reaches the prompt.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from ayla_ai_core import INFERRED_MARK, MEMORY_BLOCK_HEADER, MEMORY_INFERRED_HEADER

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


class TestRollbackFlag:
    """CONCIERGE_MEMORY_ENABLED=false (runbook §7): the whole surface is off."""

    def test_flag_off_returns_empty_without_wire_call(self, monkeypatch, settings) -> None:
        settings.CONCIERGE_MEMORY_ENABLED = False
        get_prefs = Mock()
        monkeypatch.setattr(memory_block, "get_declared_prefs", get_prefs)
        assert build_concierge_memory_block(object()) == ""
        # Rollback avoids even the upstream (gated) call.
        get_prefs.assert_not_called()

    def test_flag_on_renders_normally(self, monkeypatch, settings) -> None:
        settings.CONCIERGE_MEMORY_ENABLED = True
        monkeypatch.setattr(
            memory_block,
            "get_declared_prefs",
            lambda bot_user: SimpleNamespace(
                status=memory_block.GateStatus.OK,
                context=SimpleNamespace(context={"diet_type": "vegan"}),
            ),
        )
        assert "Диета: vegan" in build_concierge_memory_block(object())


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
            "apps.identity.services.memory_key_policy.read_current_view",
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
            "apps.identity.services.memory_key_policy.read_current_view",
            lambda user_id: view,
        )
        block = build_concierge_memory_block(SimpleNamespace(ayla_user_id=uuid4()))
        assert "omnivore" in block
        assert "vegan" not in block


class TestInferredKeyTypeSafety:
    def test_unhashable_key_raises_typeerror(self, monkeypatch) -> None:
        """Malformed content (non-str "key") hits dict.get() unguarded —
        pinning the pre-existing runtime behaviour so a future type-only
        fix can't silently swallow it (see PR #1130 review)."""
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
            green_facts=[
                GreenFact(kind="lifestyle", content={"key": ["not", "hashable"], "value": "x"})
            ],
        )
        monkeypatch.setattr(
            "apps.identity.services.memory_key_policy.read_current_view",
            lambda user_id: view,
        )
        with pytest.raises(TypeError):
            build_concierge_memory_block(SimpleNamespace(ayla_user_id=uuid4()))


class TestDeclaredProvenance:
    """P0-3 — бэкендный вывод (`busy_days`, любимые мастера) не должен
    приходить модели в том же виде, что сказанное человеком."""

    def _ok(self, monkeypatch, context: dict, raw: dict | None = None) -> None:
        declared = (
            SimpleNamespace(context=context, raw=raw)
            if raw is not None
            else (SimpleNamespace(context=context))
        )
        monkeypatch.setattr(
            memory_block,
            "get_declared_prefs",
            lambda bot_user: _gated(memory_block.GateStatus.OK, declared),
        )

    def test_backend_inferred_field_is_marked_stated_one_is_not(self, monkeypatch) -> None:
        self._ok(
            monkeypatch,
            {"diet_type": "vegan", "busy_days": ["tue"]},
            raw={"data_sources": {"diet_type": "explicit", "busy_days": "inferred"}},
        )
        block = build_concierge_memory_block(object())
        assert "- Диета: vegan" in block
        assert f"- {INFERRED_MARK} Избегает: вторник" in block
        assert MEMORY_INFERRED_HEADER in block

    def test_unknown_backend_source_is_treated_as_derived(self, monkeypatch) -> None:
        """`behavioral`/`transactional`/что угодно незнакомое — не цитата."""
        self._ok(
            monkeypatch,
            {"busy_days": ["tue"]},
            raw={"data_sources": {"busy_days": "behavioral"}},
        )
        assert INFERRED_MARK in build_concierge_memory_block(object())

    def test_field_absent_from_data_sources_stays_stated(self, monkeypatch) -> None:
        """Legacy-строки без штампа писал человек через мобильное приложение."""
        self._ok(
            monkeypatch,
            {"diet_type": "vegan"},
            raw={"data_sources": {"busy_days": "inferred"}},
        )
        block = build_concierge_memory_block(object())
        assert "- Диета: vegan" in block
        assert INFERRED_MARK not in block

    def test_without_backend_data_sources_block_is_unchanged(self, monkeypatch) -> None:
        """Отрицательный: пока бэкенд не отдаёт поле — вывод прежний, до байта.

        Бот и бэкенд деплоятся независимо; порядок деплоя не имеет права
        превратить каждый заявленный факт в «догадку».
        """
        ctx = {"diet_type": "vegan", "busy_days": ["tue"], "preferred_time_slots": ["evening"]}
        self._ok(monkeypatch, ctx)
        no_field = build_concierge_memory_block(object())
        self._ok(monkeypatch, ctx, raw={"meta": {"filled_fields": 3}})
        wrong_shape = build_concierge_memory_block(object())
        self._ok(monkeypatch, ctx, raw={"data_sources": dict.fromkeys(ctx, "explicit")})
        all_stated = build_concierge_memory_block(object())

        assert INFERRED_MARK not in no_field
        assert MEMORY_INFERRED_HEADER not in no_field
        assert no_field == wrong_shape == all_stated


class TestLocalProvenance:
    """Тот же водораздел для локального MemoryEntry."""

    def _view(self, monkeypatch, source: str):
        from apps.identity.services.memory_reader import GreenFact, PersonalContextView

        monkeypatch.setattr(
            memory_block,
            "get_declared_prefs",
            lambda bot_user: _gated(memory_block.GateStatus.OK, SimpleNamespace(context={})),
        )
        monkeypatch.setattr("apps.consent.memory.can_store_green_memory", lambda bot_user: True)
        view = PersonalContextView(
            summary="",
            green_facts=[
                GreenFact(
                    kind="lifestyle",
                    content={"key": "diet", "value": "vegan"},
                    source=source,
                )
            ],
        )
        monkeypatch.setattr(
            "apps.identity.services.memory_key_policy.read_current_view", lambda user_id: view
        )
        return build_concierge_memory_block(SimpleNamespace(ayla_user_id=uuid4()))

    def test_user_stated_fact_is_not_marked_as_a_guess(self, monkeypatch) -> None:
        block = self._view(monkeypatch, "explicit")
        assert "Диета: vegan" in block
        assert INFERRED_MARK not in block

    def test_derived_fact_is_marked(self, monkeypatch) -> None:
        block = self._view(monkeypatch, "inferred")
        assert f"{INFERRED_MARK} кажется, Диета: vegan" in block

    def test_the_two_are_not_the_same_text(self, monkeypatch) -> None:
        assert self._view(monkeypatch, "explicit") != self._view(monkeypatch, "inferred")

    def test_multi_key_list_mixing_a_guess_is_labelled_a_guess(self, monkeypatch) -> None:
        """Одна отрендеренная строка — одно происхождение; смесь честнее пометить."""
        from apps.identity.services.memory_reader import GreenFact, PersonalContextView

        monkeypatch.setattr(
            memory_block,
            "get_declared_prefs",
            lambda bot_user: _gated(memory_block.GateStatus.OK, SimpleNamespace(context={})),
        )
        monkeypatch.setattr("apps.consent.memory.can_store_green_memory", lambda bot_user: True)
        view = PersonalContextView(
            summary="",
            green_facts=[
                GreenFact(
                    kind="preference",
                    content={"key": "preferred_districts", "value": "Центр"},
                    source="explicit",
                ),
                GreenFact(
                    kind="preference",
                    content={"key": "preferred_districts", "value": "Арбеково"},
                    source="inferred",
                ),
            ],
        )
        monkeypatch.setattr(
            "apps.identity.services.memory_key_policy.read_current_view", lambda user_id: view
        )
        block = build_concierge_memory_block(SimpleNamespace(ayla_user_id=uuid4()))
        assert "Центр" in block and "Арбеково" in block
        assert INFERRED_MARK in block
