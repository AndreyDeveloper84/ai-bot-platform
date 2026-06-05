"""Handler registry — maps streams to TenantAwareTask handlers (DRF-425 / C3)."""

from __future__ import annotations

from typing import Callable

# Module-global registry: stream name → handler instance.
# A handler can register for multiple streams; we keep one entry per stream.
_HANDLERS: dict[str, "TenantAwareTask"] = {}  # type: ignore[name-defined]  # noqa: F821 — forward ref


def register(stream: str) -> Callable:
    """Decorator: register a TenantAwareTask subclass for a stream.

    Usage:
        from apps.workers.registry import register
        from apps.workers.base import TenantAwareTask

        @register("ingress:max")
        class MaxWebhookHandler(TenantAwareTask):
            def handle(self, payload):
                ...
    """

    def decorator(cls):
        _HANDLERS[stream] = cls()
        return cls

    return decorator


def lookup(stream: str):
    """Return the registered handler for a stream, or None."""

    return _HANDLERS.get(stream)


def clear_registry() -> None:
    """Test-only helper — wipe the registry between tests."""

    _HANDLERS.clear()


def registered_streams() -> list[str]:
    """Return the list of streams that have handlers registered."""

    return list(_HANDLERS.keys())


def iter_handlers() -> list[tuple[str, "TenantAwareTask"]]:  # type: ignore[name-defined]  # noqa: F821
    """Canonical accessor for ``(stream, handler_instance)`` pairs.

    Used by :mod:`apps.workers.subscriber_audit` (issue #502 boot-time
    inventory emit). Callers MUST NOT touch ``_HANDLERS`` directly — the
    module-private dict is an implementation detail.
    """

    return list(_HANDLERS.items())
