"""Fixtures for the audit app's own tests.

One fixture, autouse (DRF-2706).

### Why

Tests here assert on the audit table as a whole — ``AuditLog.all_tenants``
counted, filtered by an admin filter, swept by the retention task. That is the
right way to test those things and it assumes the table holds only what the
test put there.

It does not always. Audit and telemetry rows written through
``sync_to_async(..., thread_sensitive=False)`` — about 45 call sites in the
LLM router, the cost tracker and the pipeline — are written from a worker
thread on that thread's own connection. They commit outside the transaction of
the test that triggered them and survive its rollback, so they are still in
the table for the next test of the same xdist worker.

Measured on `dev` f8579a5e: with one committed foreign row of each leaking
kind present before setup, four tests of this directory fail (``assert 5 ==
2``, ``assert 4 == 1``); without it all pass. A writer of such rows lives in
the same CI shard.

### What it does

The same thing ``audit_log`` in ``apps/orchestrator/tests/
test_readyz_extended.py`` has done since DRF-1336, for the same reason: empty
the table at the start of the test. For an ordinary ``django_db`` test that
happens inside the test's transaction and is rolled back with it — the foreign
rows come back for whoever is next. This establishes a precondition; it does
not clean up after anyone.

Only tests that use the database are touched: the fixture asks for it only
when the test already carries the ``django_db`` mark.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _audit_table_is_this_tests_own(request: pytest.FixtureRequest) -> None:
    marker = request.node.get_closest_marker("django_db")
    if marker is None:
        return
    transactional = bool(marker.kwargs.get("transaction", False))
    request.getfixturevalue("transactional_db" if transactional else "db")

    from apps.audit.models import AuditLog

    AuditLog.all_tenants.all().delete()
