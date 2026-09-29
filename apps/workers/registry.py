"""Handler registry — maps streams to TenantAwareTask handlers (DRF-425 / C3)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Iterator

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


@contextmanager
def emptied_registry_for_tests() -> Iterator[None]:
    """Test-only: an empty registry inside, the production one back after.

    DRF-2220. Fixtures used to ``clear_registry()`` on the way in AND on the
    way out, so every test after them in the same process saw no handlers at
    all — the ones ``apps.channels`` registers once, at app ready, and never
    again. Anything that reads the registry then saw nothing: the ingress
    purge of «забудь всё» scanned zero streams and reported zero deleted. The
    registry is process-global state; a test that empties it must put it back.
    """

    saved = dict(_HANDLERS)
    _HANDLERS.clear()
    try:
        yield
    finally:
        _HANDLERS.clear()
        _HANDLERS.update(saved)


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
