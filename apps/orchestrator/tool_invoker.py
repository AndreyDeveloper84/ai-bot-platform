"""Tool invoker (DRF-541 / Sprint 6 / O7).

Step 11 of the orchestrator pipeline. Skills return zero or more
:class:`ToolCall`'s via ``SkillResult.tool_calls_made``; the invoker
resolves each name in the registry, enforces the skill's permission
(``allowed_for``), and invokes with an idempotency key.

### Contract

- Permission check (``tool.allowed_for``): a skill can only invoke
  tools it has been authorised for. Violation → ``ToolResult(ok=False,
  error_code='permission_denied')`` — never raises.
- Idempotency: caller supplies ``idempotency_key`` on the ``ToolCall``;
  invoker forwards. If a tool's implementation participates in the
  ``apps.tools.idempotency`` contract, repeated calls with the same
  key return the cached payload.
- Failure isolation: a tool that raises is caught — the invoker emits
  ``ToolResult(ok=False, error_code='internal_error', error_message=str(exc))``
  and the next tool in the chain still runs. Skill decides on retry/handoff
  by reading the result.

### Why not async

Phase 0 tools are sync (DB-backed). Async semantics happen at the
pipeline boundary (O1 `turn()` is async, calls `invoke_tool_calls`
via `sync_to_async`). Keeping the invoker sync simplifies tests
and lets tool implementations be plain Python.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from apps.tools import registry
from apps.tools.registry import ToolResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolCall:
    """One tool-call request emitted by a skill.

    A skill builds these into ``SkillResult.tool_calls_made``; the
    invoker iterates and calls each tool by name with the supplied args.
    ``idempotency_key`` is opaque to the invoker — tools that need
    single-winner semantics consume it via ``apps.tools.idempotency``.
    """

    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None


def invoke_tool_calls(
    tool_calls: list[ToolCall],
    *,
    skill_name: str,
) -> list[ToolResult]:
    """Invoke each tool call in order; return parallel-shaped result list.

    Args:
      tool_calls: list of :class:`ToolCall` from ``SkillResult.tool_calls_made``.
      skill_name: identifier of the dispatching skill. Used for
        permission check against ``Tool.allowed_for``.

    Returns:
      List of :class:`ToolResult` with one entry per call, preserving order.
      Always returns a list — never raises on tool failure.
    """

    results: list[ToolResult] = []
    for call in tool_calls:
        results.append(_invoke_one(call, skill_name=skill_name))
    return results


def _invoke_one(call: ToolCall, *, skill_name: str) -> ToolResult:
    """Resolve + permission-check + invoke a single tool call."""

    tool = registry.get(call.tool_name)
    if tool is None:
        logger.warning(
            "tool_invoker.unknown_tool skill=%s tool=%s",
            skill_name,
            call.tool_name,
        )
        return ToolResult(
            ok=False,
            error_code="unknown_tool",
            error_message=f"Tool {call.tool_name!r} not registered",
        )

    allowed: set[str] = getattr(tool, "allowed_for", set()) or set()
    if skill_name not in allowed:
        logger.warning(
            "tool_invoker.permission_denied skill=%s tool=%s allowed=%s",
            skill_name,
            call.tool_name,
            sorted(allowed),
        )
        return ToolResult(
            ok=False,
            error_code="permission_denied",
            error_message=(f"Skill {skill_name!r} not in allowed_for of tool {call.tool_name!r}"),
        )

    try:
        result = tool.invoke(args=call.args, idempotency_key=call.idempotency_key)
    except Exception as exc:  # noqa: BLE001 — invoker is safety boundary
        logger.exception(
            "tool_invoker.tool_raised skill=%s tool=%s err=%s",
            skill_name,
            call.tool_name,
            exc,
        )
        return ToolResult(
            ok=False,
            error_code="internal_error",
            error_message=f"{type(exc).__name__}: {exc}",
        )

    if not isinstance(result, ToolResult):
        # Defensive — tool returned the wrong type.
        logger.warning(
            "tool_invoker.bad_return_type skill=%s tool=%s got=%s",
            skill_name,
            call.tool_name,
            type(result).__name__,
        )
        return ToolResult(
            ok=False,
            error_code="bad_return_type",
            error_message=f"Tool returned {type(result).__name__}, expected ToolResult",
        )

    return result
