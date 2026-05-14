"""Once-lock primitive for one-shot `configure_*()` functions.

Sprint 8 code review P1-3. ``configure_otel()`` and ``configure_sentry()``
each independently shipped the same double-checked-locking pattern:

  * module-level ``threading.Lock``
  * module-level ``_CONFIGURED = False``
  * ``if _CONFIGURED: return False``
  * ``with _lock: if _CONFIGURED: return False; ...; _CONFIGURED = True``
  * matching ``reset_*_for_tests()`` flipping the flag back to False

Two copies were tolerable; a third (Sprint 9 OTel metrics, Sprint 10
distributed tracing exporter, etc.) would make it three-way duplication.
Centralising the lock semantics here means future singletons get the
correct behaviour for free.

The class is intentionally small — no metaclass tricks, no abstract
base, no exceptions raised on misuse. Configure functions wrap their
body in ``OnceLock.run(...)``; tests call ``OnceLock.reset()``.
"""

from __future__ import annotations

import threading
from typing import Callable, TypeVar

_R = TypeVar("_R")


class OnceLock:
    """Thread-safe one-shot wrapper for ``configure_*()`` functions.

    Usage::

        _once = OnceLock()

        def configure_thing() -> bool:
            return _once.run(_do_configure_thing) is not None

        def _do_configure_thing() -> bool:
            # Real config work goes here. Runs at most once.
            ...
            return True

    .run() returns whatever the inner function returned, or ``None``
    when the lock was already armed by a previous call. Callers that
    need a boolean "did config run on this call" check ``is not None``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._done = False

    def run(self, fn: Callable[[], _R]) -> _R | None:
        """Invoke ``fn`` at most once. Subsequent calls return ``None``.

        Double-checked locking — the outer ``if`` avoids paying the
        lock acquisition on every subsequent call; the inner ``if``
        handles the race where two threads passed the outer check
        simultaneously.
        """
        if self._done:
            return None
        with self._lock:
            if self._done:
                return None
            result = fn()
            self._done = True
            return result

    def reset(self) -> None:
        """Test-only — pretend the inner function was never invoked.

        Production code MUST NOT call this. Tests that need to re-run
        a ``configure_*()`` use this to clear the latch between cases.
        """
        self._done = False

    @property
    def done(self) -> bool:
        """True once :meth:`run` has executed its inner function."""
        return self._done
