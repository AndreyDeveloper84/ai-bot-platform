"""Replay-clock tests (DRF-617 / A3).

Verifies the ContextVar contract:
1. Default value is ``None`` (production fast-path).
2. ``set_frozen_now`` / ``reset_frozen_now`` round-trip correctly.
3. ContextVar isolation — concurrent requests don't see each other's clock.
4. Adapter integration — when clock is set, ``ayla_adapter.build_safe_inputs``
   reads it for the ``today`` field.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime

import pytest

from apps.orchestrator.ayla_adapter import build_safe_inputs
from apps.replay.clock import get_frozen_now, reset_frozen_now, set_frozen_now


class TestFrozenNow:
    def test_default_is_none(self):
        """Outside replay mode, the clock is unset."""
        assert get_frozen_now() is None

    def test_set_then_get(self):
        """set_frozen_now pins the clock; get_frozen_now returns it."""
        token = set_frozen_now(datetime(2026, 5, 13, 12, 0, 0))
        try:
            current = get_frozen_now()
            assert current == datetime(2026, 5, 13, 12, 0, 0)
        finally:
            reset_frozen_now(token)
        assert get_frozen_now() is None

    def test_reset_restores_previous_value(self):
        """Nested set/reset returns the outer pin."""
        outer = set_frozen_now(datetime(2026, 1, 1))
        try:
            inner = set_frozen_now(datetime(2027, 6, 6))
            try:
                assert get_frozen_now() == datetime(2027, 6, 6)
            finally:
                reset_frozen_now(inner)
            # After resetting inner, outer pin is restored.
            assert get_frozen_now() == datetime(2026, 1, 1)
        finally:
            reset_frozen_now(outer)
        assert get_frozen_now() is None

    def test_explicit_none_clears_for_nested_scope(self):
        """set_frozen_now(None) explicitly unsets for the current scope."""
        outer = set_frozen_now(datetime(2026, 1, 1))
        try:
            inner = set_frozen_now(None)
            try:
                assert get_frozen_now() is None
            finally:
                reset_frozen_now(inner)
            assert get_frozen_now() == datetime(2026, 1, 1)
        finally:
            reset_frozen_now(outer)


class TestContextVarIsolation:
    @pytest.mark.asyncio
    async def test_concurrent_tasks_isolated(self):
        """Two concurrent asyncio tasks see independent clocks.

        Verifies that ContextVar (not threading.local) is the right
        primitive — replay fixture A running in task A must not leak
        its frozen clock into task B running a different fixture.
        """
        results: dict[str, datetime | None] = {}

        async def task_a():
            token = set_frozen_now(datetime(2026, 1, 1))
            try:
                # Yield control to let task_b run.
                await asyncio.sleep(0.01)
                results["a"] = get_frozen_now()
            finally:
                reset_frozen_now(token)

        async def task_b():
            token = set_frozen_now(datetime(2027, 12, 31))
            try:
                await asyncio.sleep(0.01)
                results["b"] = get_frozen_now()
            finally:
                reset_frozen_now(token)

        await asyncio.gather(task_a(), task_b())

        assert results["a"] == datetime(2026, 1, 1)
        assert results["b"] == datetime(2027, 12, 31)


class TestAdapterIntegration:
    """End-to-end: clock set → adapter consumes it via frozen_now kwarg."""

    def test_clock_unset_falls_back_to_today(self):
        """Without replay clock set, adapter uses date.today()."""
        # Clock is unset (default state in this test).
        assert get_frozen_now() is None
        result = build_safe_inputs(
            today=None,
            client_name="x",
            bookings_count=0,
            extra_hint="",
            frozen_now=get_frozen_now(),
        )
        # Falls back to date.today() — verify it's at least a real date.
        assert isinstance(result.today, date)

    def test_clock_set_propagates_to_adapter(self):
        """Adapter respects frozen_now from the ContextVar."""
        token = set_frozen_now(datetime(2026, 5, 13, 9, 30, 0))
        try:
            result = build_safe_inputs(
                today=None,
                client_name="x",
                bookings_count=0,
                extra_hint="",
                frozen_now=get_frozen_now(),
            )
            assert result.today == date(2026, 5, 13)
        finally:
            reset_frozen_now(token)

    def test_explicit_today_overrides_clock(self):
        """If caller passes explicit `today`, the pinned clock is ignored.

        This is the production-debugging escape hatch: ops can force a
        specific date for a single turn without touching the ContextVar.
        """
        token = set_frozen_now(datetime(2026, 5, 13))
        try:
            result = build_safe_inputs(
                today=date(2030, 1, 1),  # explicit override
                client_name="x",
                bookings_count=0,
                extra_hint="",
                frozen_now=get_frozen_now(),
            )
            assert result.today == date(2030, 1, 1)
        finally:
            reset_frozen_now(token)
