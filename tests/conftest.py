"""Project-wide pytest fixtures (Sprint 1 / A3, decision 5C).

Tests run with ``STRICT_TENANT_SCOPE=strict`` so any code path that
forgets to set tenant context fails fast in CI. Prod/staging stay in
``audit`` mode (the env default in `config/settings/base.py`) — the
asymmetry between tests and runtime is documented in ADR-0001.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _strict_tenant_scope_for_tests(settings):
    """Force strict tenant scope for every test.

    Individual tests can override with ``settings.STRICT_TENANT_SCOPE = "audit"``
    or ``"off"`` for cases that need to test the alternative modes.
    """

    settings.STRICT_TENANT_SCOPE = "strict"
