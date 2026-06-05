"""Tests for :mod:`apps.eventbus.startup_checks` (PR #507 A5).

The startup check logs a WARNING (not an exception) when
``ATOMIC_REQUESTS=True`` is configured on the default DB — the
ingest view's audit/DLQ persistence guarantees assume the request
transaction does NOT roll back on a 500 response. Today's code
catches exceptions internally so the warning is forward-defense.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

from apps.eventbus.startup_checks import warn_if_atomic_requests_true


def test_no_warning_when_atomic_requests_false(caplog) -> None:
    """Default Django setting — no warning."""
    with patch(
        "apps.eventbus.startup_checks.settings",
        DATABASES={"default": {"ATOMIC_REQUESTS": False}},
    ):
        with caplog.at_level(logging.WARNING, logger="apps.eventbus.startup_checks"):
            warn_if_atomic_requests_true()
    matching = [r for r in caplog.records if "atomic_requests_true_warning" in r.getMessage()]
    assert matching == []


def test_no_warning_when_atomic_requests_missing(caplog) -> None:
    """Setting absent (most deploys) — no warning."""
    with patch(
        "apps.eventbus.startup_checks.settings",
        DATABASES={"default": {}},
    ):
        with caplog.at_level(logging.WARNING, logger="apps.eventbus.startup_checks"):
            warn_if_atomic_requests_true()
    matching = [r for r in caplog.records if "atomic_requests_true_warning" in r.getMessage()]
    assert matching == []


def test_warning_when_atomic_requests_true(caplog) -> None:
    """The forward-defense case — log surfaces the misconfiguration."""
    with patch(
        "apps.eventbus.startup_checks.settings",
        DATABASES={"default": {"ATOMIC_REQUESTS": True}},
    ):
        with caplog.at_level(logging.WARNING, logger="apps.eventbus.startup_checks"):
            warn_if_atomic_requests_true()
    matching = [r for r in caplog.records if "atomic_requests_true_warning" in r.getMessage()]
    assert len(matching) == 1
    # PR reference in the warning so ops can find the context.
    assert "PR #507" in matching[0].getMessage()
