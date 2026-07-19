"""Shared fixtures for the e2e directory.

``apps.conversations.services.record_message`` enqueues the M6 auto-draft
Celery task on every inbound USER message (via ``transaction.on_commit``).
In unit-env there is no AMQP broker, so the publish raises
``kombu.OperationalError`` and fails the very turn the e2e is exercising
(the handler's message never lands).

Mock ONLY the publish boundary for this directory — the task's internals
(debounce, flag checks, draft generation) stay covered by the dedicated
suites under ``apps/master_api/tests/``. Everything else in the pipeline
runs for real.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _no_auto_draft_publish():
    """Swallow the auto-draft enqueue for e2e (no broker in unit-env)."""
    with patch("apps.master_api.tasks.auto_generate_draft_for_inbound.delay"):
        yield
