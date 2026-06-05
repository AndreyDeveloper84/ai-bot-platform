"""Shared autouse fixture for apps.eventbus tests — opt-out via marker.

Round-2 adversarial pass AS11: the previous autouse-everywhere bypass
meant the production code path through ``dispatch_with_timeout``
(ThreadPoolExecutor submit + future.result(timeout)) was NEVER tested
end-to-end. Real wrapper bugs — context-vars propagation, DB
connection close timing, semaphore release race — would land in
production unobserved.

Fix: autouse-but-opt-out via ``@pytest.mark.real_timeout``. Most
tests in this directory exercise DB-backed dispatcher paths under the
SQLite test backend, where the executor's worker thread deadlocks on
"database table is locked"; for those tests the bypass is still on.
Tests marked ``@pytest.mark.real_timeout`` exercise the actual
threadpool wrapper (typically with ``dispatch_envelope`` mocked away
to keep DB out of the worker thread).
"""

from __future__ import annotations

import pytest


def pytest_configure(config):
    """Register the ``real_timeout`` marker so pytest doesn't warn."""
    config.addinivalue_line(
        "markers",
        "real_timeout: exercise the actual dispatch_with_timeout threadpool "
        "wrapper (opt-out of the SQLite-bypass autouse fixture).",
    )


@pytest.fixture(autouse=True)
def _bypass_timeout_threadpool_under_sqlite(request, monkeypatch):
    """Bypass the timeout wrapper unless test marked ``real_timeout``.

    Without the bypass, the executor's worker thread uses a separate
    Django DB connection that escapes the test transaction → SQLite
    "database table is locked" + cross-test leakage. The bypass
    monkey-patches ``views.dispatch_with_timeout`` to call
    ``dispatch_envelope`` directly. The §8.10 timeout contract stays
    pinned by tests in ``test_ingest_timeout.py`` (mocked
    ``dispatch_envelope``, no DB) AND by the ``real_timeout``-marked
    tests in ``test_round2_a12_cluster.py``.
    """
    if not request.node.get_closest_marker("real_timeout"):
        from apps.eventbus import views as _views
        from apps.eventbus.ingest_dispatcher import dispatch_envelope as _direct

        monkeypatch.setattr(_views, "dispatch_with_timeout", _direct)
    yield
