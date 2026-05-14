"""OnceLock primitive tests (Sprint 8 review P1-3).

Sanity tests for ``apps.observability._once.OnceLock`` — the shared
double-checked locking helper used by ``configure_otel`` and
``configure_sentry``. Covers the basic contract plus a multi-threaded
race-protection check.
"""

from __future__ import annotations

import threading
import time

from apps.observability._once import OnceLock


class TestSingleThread:
    def test_first_call_runs_inner(self) -> None:
        lock = OnceLock()
        assert lock.run(lambda: "first") == "first"
        assert lock.done is True

    def test_second_call_returns_none(self) -> None:
        lock = OnceLock()
        lock.run(lambda: "first")
        assert lock.run(lambda: "second") is None
        assert lock.done is True

    def test_reset_re_arms_lock(self) -> None:
        lock = OnceLock()
        lock.run(lambda: "first")
        lock.reset()
        assert lock.done is False
        assert lock.run(lambda: "after-reset") == "after-reset"

    def test_inner_function_falsy_return_still_armed(self) -> None:
        """Inner returning False / None / 0 / "" still arms the latch —
        the latch tracks whether `fn` was invoked, not what it produced.
        """
        lock = OnceLock()
        assert lock.run(lambda: False) is False
        assert lock.done is True
        # Second call returns None (latch armed), NOT False from cached result.
        assert lock.run(lambda: True) is None


class TestThreadSafety:
    def test_only_one_thread_runs_inner_under_contention(self) -> None:
        """Spawn 8 threads racing on the same lock. Exactly one should
        execute the inner function; the other 7 should see ``done`` and
        bail. The inner function sleeps to widen the race window so a
        broken lock would let multiple threads in.
        """
        lock = OnceLock()
        run_count = 0
        run_count_lock = threading.Lock()

        def slow_inner() -> int:
            nonlocal run_count
            time.sleep(0.05)  # widen the race window
            with run_count_lock:
                run_count += 1
            return run_count

        threads = [threading.Thread(target=lambda: lock.run(slow_inner)) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert run_count == 1, f"expected 1 inner invocation under contention; got {run_count}"
        assert lock.done is True
