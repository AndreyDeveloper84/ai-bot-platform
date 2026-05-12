"""Tool invoker tests (DRF-541 / Sprint 6 / O7)."""

from __future__ import annotations

import pytest

from apps.orchestrator.tool_invoker import ToolCall, invoke_tool_calls
from apps.tools import registry
from apps.tools.registry import ToolResult


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test starts with an empty tool registry."""
    registry.reset_registry_cache()
    yield
    registry.reset_registry_cache()


def _make_tool(name: str, *, allowed_for: set[str], return_value=None, raises=None):
    """Build a tool class on the fly + register it."""

    captured: list[dict] = []

    class _Tool:
        pass

    _Tool.name = name  # type: ignore[attr-defined]
    _Tool.allowed_for = allowed_for  # type: ignore[attr-defined]

    def invoke(self, *, args, idempotency_key=None):
        captured.append({"args": args, "idempotency_key": idempotency_key})
        if raises:
            raise raises
        return return_value or ToolResult(ok=True, data={"echo": args})

    _Tool.invoke = invoke  # type: ignore[attr-defined]
    registry.register(_Tool)  # type: ignore[arg-type]
    return captured


class TestHappyPath:
    def test_single_tool_call(self):
        captured = _make_tool("search_kb", allowed_for={"faq"})
        results = invoke_tool_calls(
            [ToolCall(tool_name="search_kb", args={"q": "hours"})],
            skill_name="faq",
        )
        assert len(results) == 1
        assert results[0].ok is True
        assert captured[0]["args"] == {"q": "hours"}

    def test_idempotency_key_propagated(self):
        captured = _make_tool("create_booking", allowed_for={"booking"})
        invoke_tool_calls(
            [
                ToolCall(
                    tool_name="create_booking",
                    args={"slot": "10:00"},
                    idempotency_key="evt:abc123",
                )
            ],
            skill_name="booking",
        )
        assert captured[0]["idempotency_key"] == "evt:abc123"

    def test_multiple_calls_preserve_order(self):
        _make_tool("t1", allowed_for={"s"})
        _make_tool("t2", allowed_for={"s"})
        results = invoke_tool_calls(
            [
                ToolCall(tool_name="t1", args={"i": 1}),
                ToolCall(tool_name="t2", args={"i": 2}),
            ],
            skill_name="s",
        )
        assert len(results) == 2
        assert results[0].data == {"echo": {"i": 1}}
        assert results[1].data == {"echo": {"i": 2}}


class TestPermissionCheck:
    def test_skill_not_in_allowed_for(self):
        _make_tool("admin_action", allowed_for={"admin_skill"})
        results = invoke_tool_calls(
            [ToolCall(tool_name="admin_action", args={})],
            skill_name="random_skill",
        )
        assert results[0].ok is False
        assert results[0].error_code == "permission_denied"

    def test_empty_allowed_for_denies_all(self):
        _make_tool("locked", allowed_for=set())
        results = invoke_tool_calls(
            [ToolCall(tool_name="locked", args={})],
            skill_name="anything",
        )
        assert results[0].ok is False
        assert results[0].error_code == "permission_denied"


class TestErrorHandling:
    def test_unknown_tool(self):
        results = invoke_tool_calls(
            [ToolCall(tool_name="does_not_exist", args={})],
            skill_name="faq",
        )
        assert results[0].ok is False
        assert results[0].error_code == "unknown_tool"

    def test_tool_exception_caught(self):
        _make_tool("broken", allowed_for={"s"}, raises=RuntimeError("kaboom"))
        results = invoke_tool_calls(
            [ToolCall(tool_name="broken", args={})],
            skill_name="s",
        )
        assert results[0].ok is False
        assert results[0].error_code == "internal_error"
        assert "kaboom" in results[0].error_message

    def test_one_failure_does_not_break_chain(self):
        _make_tool("good_a", allowed_for={"s"})
        _make_tool("bad", allowed_for={"s"}, raises=ValueError("nope"))
        _make_tool("good_b", allowed_for={"s"})
        results = invoke_tool_calls(
            [
                ToolCall(tool_name="good_a", args={}),
                ToolCall(tool_name="bad", args={}),
                ToolCall(tool_name="good_b", args={}),
            ],
            skill_name="s",
        )
        assert results[0].ok is True
        assert results[1].ok is False
        assert results[2].ok is True

    def test_bad_return_type_caught(self):
        # Tool returns something that's not ToolResult.
        class _BadTool:
            name = "bad_return"
            allowed_for = {"s"}

            def invoke(self, *, args, idempotency_key=None):
                return {"not": "a ToolResult"}

        registry.register(_BadTool)
        results = invoke_tool_calls(
            [ToolCall(tool_name="bad_return", args={})],
            skill_name="s",
        )
        assert results[0].ok is False
        assert results[0].error_code == "bad_return_type"


class TestRegistry:
    def test_register_requires_name(self):
        class _NoName:
            allowed_for = set()

            def invoke(self, *, args, idempotency_key=None):
                return ToolResult(ok=True)

        # Missing `name` attribute → registration raises
        with pytest.raises(ValueError, match="must declare a non-empty `name`"):
            registry.register(_NoName)

    def test_re_registering_replaces(self):
        _make_tool("dup", allowed_for={"s"})

        class _NewImpl:
            name = "dup"
            allowed_for = {"s"}

            def invoke(self, *, args, idempotency_key=None):
                return ToolResult(ok=True, data={"version": 2})

        registry.register(_NewImpl)
        results = invoke_tool_calls(
            [ToolCall(tool_name="dup", args={})],
            skill_name="s",
        )
        assert results[0].data == {"version": 2}

    def test_get_returns_none_for_unknown(self):
        assert registry.get("does_not_exist") is None

    def test_registered_returns_copy(self):
        _make_tool("t1", allowed_for={"s"})
        snapshot = registry.registered()
        snapshot.clear()  # mutating copy must not wipe registry
        assert "t1" in registry.registered()


class TestEmpty:
    def test_empty_call_list_returns_empty(self):
        results = invoke_tool_calls([], skill_name="any")
        assert results == []
