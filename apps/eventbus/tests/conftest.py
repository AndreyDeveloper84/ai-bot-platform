"""Shared autouse fixture for all apps.eventbus tests.

PR #507 A12 — the timeout wrapper submits ``dispatch_envelope`` to
a ThreadPoolExecutor. Under the SQLite test backend, the worker
thread's DB writes use a SEPARATE Django connection that escapes
the test transaction — rows leak across tests and downstream
modules see stale data (e.g. ``MultipleObjectsReturned`` on
``IngestDLQ.objects.get(event_id=...)``).

Monkey-patching ``apps.eventbus.views.dispatch_with_timeout`` to
call ``dispatch_envelope`` directly keeps the §8 status table
coverage on the view AND keeps the dispatcher's idempotency
contract under the test transaction. The actual timeout contract
is pinned by ``test_ingest_timeout.py`` (which mocks
``dispatch_envelope`` entirely, no DB involvement) so we don't
lose A12 coverage.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _bypass_timeout_threadpool_under_sqlite(monkeypatch):
    """Patch the view layer to skip the threadpool in all eventbus tests."""
    from apps.eventbus import views as _views
    from apps.eventbus.ingest_dispatcher import dispatch_envelope as _direct

    monkeypatch.setattr(_views, "dispatch_with_timeout", _direct)
    yield
