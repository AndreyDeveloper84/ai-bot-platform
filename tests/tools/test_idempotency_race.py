"""G3 (mandatory test gap) — IdempotencyKey single-winner under race.

Two threads acquire the same key at the same time. One must win, one
must lose. If both succeed, the idempotency contract is broken and
every "exactly-once" property in the platform built on top of it
becomes a coin flip.

We use real ``threading.Thread`` to exercise the actual concurrent
INSERT path. Django's test database opens a connection per thread —
the unique constraint on ``IdempotencyKey.key`` is the contract being
verified.
"""

from __future__ import annotations

import threading

import pytest
from django.db import connection, connections

from apps.tools.idempotency import AlreadyClaimed, with_idempotency
from apps.tools.models import IdempotencyKey


pytestmark = [pytest.mark.django_db(transaction=True)]


def _on_sqlite() -> bool:
    return connections["default"].vendor == "sqlite"


@pytest.mark.skipif(
    _on_sqlite(),
    reason=(
        "SQLite serializes writes — flaky 'database table is locked' under "
        "concurrent INSERT. Postgres (prod + CI service) uses MVCC and handles "
        "this cleanly. Unique-constraint enforcement is already covered by "
        "test_idempotency.py::test_second_caller_within_ttl_raises_already_claimed; "
        "the race tests assert thread-level semantics that need Postgres."
    ),
)
def test_concurrent_acquire_exactly_one_winner():
    """Two threads acquire the same key — one wins, one gets AlreadyClaimed."""

    barrier = threading.Barrier(2)
    results: dict[str, str] = {}
    lock = threading.Lock()

    def acquire(name: str) -> None:
        # Each thread gets its own connection in Django's test runner.
        barrier.wait()
        try:
            with with_idempotency("race:contended", ttl_seconds=60):
                with lock:
                    results[name] = "won"
        except AlreadyClaimed:
            with lock:
                results[name] = "lost"
        finally:
            connection.close()

    t1 = threading.Thread(target=acquire, args=("a",))
    t2 = threading.Thread(target=acquire, args=("b",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not t1.is_alive() and not t2.is_alive(), "thread timeout"
    outcomes = sorted(results.values())
    assert outcomes == ["lost", "won"], f"Expected one winner and one loser, got {outcomes}"
    # Exactly one row in the DB.
    assert IdempotencyKey.objects.filter(key="race:contended").count() == 1


@pytest.mark.skipif(
    _on_sqlite(),
    reason=(
        "SQLite serializes writes — table-level lock under heavy concurrent "
        "INSERT raises OperationalError(database table is locked) instead of "
        "the IntegrityError our code expects. Postgres (prod + CI service) "
        "uses MVCC and handles this cleanly. The 2-thread test above already "
        "validates the core contract; this stress variant is Postgres-only."
    ),
)
def test_many_threads_one_wins():
    """20 threads, same key — exactly one wins; the rest get AlreadyClaimed.

    Stress test of the unique-constraint resolution under heavier
    contention than the 2-thread case.
    """

    N = 20
    barrier = threading.Barrier(N)
    results: list[str] = []
    lock = threading.Lock()

    def acquire() -> None:
        barrier.wait()
        try:
            with with_idempotency("race:many", ttl_seconds=60):
                with lock:
                    results.append("won")
        except AlreadyClaimed:
            with lock:
                results.append("lost")
        finally:
            connection.close()

    threads = [threading.Thread(target=acquire) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert results.count("won") == 1, f"Expected exactly 1 winner, got {results.count('won')}"
    assert results.count("lost") == N - 1
    assert IdempotencyKey.objects.filter(key="race:many").count() == 1
