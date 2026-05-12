"""Tool registry (DRF-541 / Sprint 6 / O7 — supporting scaffold).

Skill-callable side-effecting operations live in `apps/tools/<tool_name>/`.
Each tool registers itself via :func:`register` at module-load time so the
orchestrator's tool invoker (O7) can resolve them by name without
import-time coupling.

### Pattern (mirror of apps.skills.registry)

    @register
    class SearchKBTool:
        name = "search_kb"
        allowed_for = {"faq"}  # skills allowed to invoke this tool

        def invoke(self, *, args, idempotency_key=None):
            ...
            return ToolResult(ok=True, data={"items": [...]})

### Test isolation

`reset_registry_cache()` empties the registry — used by tool tests that
swap implementations. Production code should never call it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """Return value of :meth:`Tool.invoke`. Skills consume this and decide
    whether to retry, hand off, or proceed.

    ``ok=False`` is an EXPECTED failure (timeout, validation, not-found).
    A tool implementation that raises an exception is treated as an
    unexpected failure by the invoker — the invoker catches and emits
    an `ok=False` ToolResult with `error_code='internal_error'`.
    """

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""


@runtime_checkable
class Tool(Protocol):
    """Duck-typed tool protocol.

    Implementations expose ``name`` (registry key), ``allowed_for``
    (set of skill names that may invoke), and ``invoke``.
    """

    name: ClassVar[str]
    allowed_for: ClassVar[set[str]]

    def invoke(
        self,
        *,
        args: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> ToolResult:  # pragma: no cover - Protocol
        ...


_tools: dict[str, "Tool"] = {}


def register(tool_cls: type["Tool"]) -> type["Tool"]:
    """Decorator: instantiate ``tool_cls`` and add to registry.

    Idempotent: re-registering with the same name replaces the previous
    instance (test scenarios). Logs a warning when this happens in case
    it was accidental.
    """

    instance = tool_cls()
    name = getattr(instance, "name", None)
    if not name:
        raise ValueError(f"Tool {tool_cls.__name__} must declare a non-empty `name`")
    if name in _tools:
        logger.warning("tools.register.replacing name=%s class=%s", name, tool_cls.__name__)
    _tools[name] = instance
    logger.debug("tools.register name=%s class=%s", name, tool_cls.__name__)
    return tool_cls


def get(name: str) -> "Tool | None":
    """Return registered tool by name, or None if unknown."""

    return _tools.get(name)


def registered() -> dict[str, "Tool"]:
    """Read-only snapshot of the current registry (copy)."""

    return dict(_tools)


def reset_registry_cache() -> None:
    """Test hook — drop every registered tool. Production code never calls this."""

    _tools.clear()
