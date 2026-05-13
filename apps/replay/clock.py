"""Replay-clock injection — frozen-time hook for deterministic replay (DRF-617 / A3).

Replay-mode determinism requires the same ``today`` / ``now`` across
runs of the same fixture. Without this, every replay re-renders the
system prompt with today's date, producing a different ``ReplayTrace``
content even when input + model + temperature are identical.

This module exposes a ``ContextVar``-backed clock that the Sprint 5
replay harness sets at fixture start. The ``apps.orchestrator.ayla_adapter``
(A1 / DRF-616) reads it via :func:`get_frozen_now` and forwards through
its ``frozen_now`` kwarg into ``build_safe_inputs``. Production callers
(live MAX traffic) leave the ContextVar unset and the adapter falls back
to ``date.today()``.

### Why ContextVar, not threading.local

`threading.local` works only across threads. The platform pipeline is
mixed sync (Django views, Celery workers) + async (channel adapters).
``contextvars.ContextVar`` propagates correctly through both — and
through `asyncio` task spawns — so a single replay invocation locks
the clock for the entire request scope.

### Why fail-soft (default None)

Forgetting to set the clock outside replay mode must NOT crash the
pipeline. Setting ``default=None`` makes :func:`get_frozen_now` return
``None`` outside replay, which the adapter treats as "use date.today()".
Replay harness is the only code that touches set/reset.
"""

from __future__ import annotations

import contextvars
from datetime import datetime

__all__ = [
    "get_frozen_now",
    "reset_frozen_now",
    "set_frozen_now",
]


_frozen_now: contextvars.ContextVar[datetime | None] = contextvars.ContextVar(
    "ayla_replay_frozen_now", default=None
)


def set_frozen_now(value: datetime | None) -> contextvars.Token[datetime | None]:
    """Pin the replay clock to ``value`` for the current async/sync context.

    Returns a token that the caller MUST eventually pass to
    :func:`reset_frozen_now` (typically in a ``try/finally`` so the
    clock is released even on exception).

    Passing ``None`` explicitly clears the clock for nested replay
    scenarios (rare).
    """

    return _frozen_now.set(value)


def reset_frozen_now(token: contextvars.Token[datetime | None]) -> None:
    """Restore the previous clock value tied to ``token``.

    Must be called once per :func:`set_frozen_now` to avoid leaking the
    pinned clock into subsequent unrelated requests on the same worker.
    """

    _frozen_now.reset(token)


def get_frozen_now() -> datetime | None:
    """Return the currently pinned replay clock, or ``None`` outside replay.

    Production callers expect ``None``. Adapter consumers treat ``None``
    as "use ``date.today()``".
    """

    return _frozen_now.get()
